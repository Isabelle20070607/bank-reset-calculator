"""Incremental quota-only log reader and rolling consumption model."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
WEEK = 7*86400


def quota_row(timestamp, rate):
    if not rate or rate.get('limit_id', rate.get('limitId', 'codex')) != 'codex':
        return None
    for key in ('primary', 'secondary'):
        window = rate.get(key)
        if not window or window.get('window_minutes', window.get('windowDurationMins')) != 10080:
            continue
        used = window.get('used_percent', window.get('usedPercent'))
        refresh = window.get('resets_at', window.get('resetsAt'))
        if used is not None and refresh is not None and 0 <= used <= 100:
            return (float(timestamp), float(used), float(refresh), rate.get('plan_type', rate.get('planType')) or '')
    return None


class UsageStore:
    def __init__(self, database, codex_home=None):
        self.codex_home = Path(codex_home) if codex_home else Path.home()/'.codex'
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshots (
                ts REAL, used REAL, refresh REAL, plan TEXT,
                PRIMARY KEY (ts,used,refresh,plan));
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER);
        ''')

    def add(self, row):
        if row:
            self.db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)', row)

    def scan(self, now):
        cutoff = now-35*86400
        source_files, bytes_read, rows = 0, 0, 0
        for directory in ('sessions', 'archived_sessions'):
            root = self.codex_home/directory
            if not root.exists():
                continue
            for path in root.rglob('*.jsonl'):
                try:
                    stat = path.stat()
                    if stat.st_mtime < cutoff:
                        continue
                    source_files += 1
                    previous = self.db.execute('SELECT offset FROM files WHERE path=?', (str(path),)).fetchone()
                    offset = previous[0] if previous and previous[0] <= stat.st_size else 0
                    if offset == stat.st_size:
                        continue
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        while True:
                            position = stream.tell()
                            line = stream.readline()
                            if not line:
                                break
                            if not line.endswith(b'\n'):
                                stream.seek(position)
                                break
                            bytes_read += len(line)
                            if b'"rate_limits"' not in line or b'"token_count"' not in line:
                                continue
                            try:
                                event = json.loads(line)
                                if event.get('type') != 'event_msg' or event.get('payload', {}).get('type') != 'token_count':
                                    continue
                                timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
                                row = quota_row(timestamp, event['payload'].get('rate_limits'))
                            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                                continue
                            if row and row[0] >= cutoff:
                                self.add(row)
                                rows += 1
                        offset = stream.tell()
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?)', (str(path), offset))
                except FileNotFoundError:
                    # Codex can archive a file while the scan is in progress.
                    continue
        self.db.execute('DELETE FROM snapshots WHERE ts<?', (cutoff,))
        self.db.commit()
        return {'files': source_files, 'bytesRead': bytes_read, 'newSnapshots': rows}

    def fit(self, now, plan):
        local = datetime.fromtimestamp(now, TZ)
        cutoff = (local.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=27)).timestamp()
        # Extra week permits calculating a delta across the beginning of the fit window.
        raw = self.db.execute('SELECT ts,used,refresh,plan FROM snapshots WHERE ts>=? AND ts<=? AND (plan=? OR plan=\'\') ORDER BY ts,used',
                              (cutoff-WEEK, now, plan or '')).fetchall()
        anchors = []
        for anchor in sorted({r[2] for r in raw}):
            if not anchors or anchor-anchors[-1][0] > 300:
                anchors.append([anchor, anchor])
            else:
                anchors[-1][1] = anchor
        groups = [[] for _ in anchors]
        for row in raw:
            for k, (lo, hi) in enumerate(anchors):
                if lo <= row[2] <= hi:
                    groups[k].append(row)
                    break
        amounts, exposure = [[0.0]*8 for _ in range(7)], [[0.0]*8 for _ in range(7)]
        daily = {}

        def add_span(start, end, amount, observable):
            if end <= start or end <= cutoff:
                return
            original_duration = end-start
            current = max(start, cutoff)
            while current < end-1e-6:
                d = datetime.fromtimestamp(current, TZ)
                boundary = d.replace(hour=d.hour//3*3, minute=0, second=0, microsecond=0)+timedelta(hours=3)
                nxt = min(end, boundary.timestamp())
                duration = nxt-current
                part = amount*duration/original_duration
                kind, slot = d.weekday(), d.hour//3
                day = d.strftime('%Y-%m-%d')
                stats = daily.setdefault(day, {'day': day, 'weekday': kind, 'used': 0., 'observedHours': 0.})
                stats['used'] += part
                if observable:
                    hours = duration/3600
                    amounts[kind][slot] += part
                    exposure[kind][slot] += hours
                    stats['observedHours'] += hours
                current = nxt

        for k, group in enumerate(groups):
            if not group:
                continue
            previous, running = group[0][0], group[0][1]
            origin = anchors[k][0]-WEEK
            if origin >= cutoff-WEEK and previous >= origin:
                # The allowance was full at this inferred window start.
                add_span(origin, previous, running, True)
            for timestamp, used, _, _ in group[1:]:
                if used < running-1:
                    # Larger counter rollbacks also occur without a new deadline.
                    # Skip the unknown adjustment interval and resume from the new
                    # counter; tolerate the one-point stale readings in Codex logs.
                    previous, running = timestamp, used
                    continue
                corrected = max(running, used)
                if timestamp > previous:
                    add_span(previous, timestamp, corrected-running, running < 100)
                previous, running = timestamp, corrected
            if k+1 < len(groups) and running >= 100:
                add_span(previous, groups[k+1][0][0], 0, False)
        if any(hours <= 0 for row in exposure for hours in row):
            missing = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
            missing = [missing[g] for g in range(7) if any(hours <= 0 for hours in exposure[g])]
            return None, '尚缺'+ '、'.join(missing) +'的部分时段记录；积累后会自动开始预测。'
        hourly = [[amounts[g][s]/exposure[g][s] for s in range(8)] for g in range(7)]
        daily_rates = [3*sum(row) for row in hourly]
        full_days = [d['used'] for d in daily.values() if d['observedHours'] >= 23.9]
        return {
            'schema': 2, 'fittedAt': now,
            'periodStart': datetime.fromtimestamp(cutoff, TZ).strftime('%Y-%m-%d'),
            'periodEnd': local.strftime('%Y-%m-%d %H:%M'),
            'snapshotCount': sum(r[0] >= cutoff for r in raw),
            'hourlyByThreeHours': hourly, 'dailyRates': daily_rates,
            'weekdayProfiles': [{'weekday': g, 'dailyRate': daily_rates[g],
                'observedHours': sum(exposure[g])}
                for g in range(7)],
            'observedDailyRange': [min(full_days), max(full_days)] if full_days else None,
            'daily': [{**d, 'used': round(d['used'], 3), 'observedHours': round(d['observedHours'], 3)} for _, d in sorted(daily.items())],
        }, None

    def close(self):
        self.db.close()

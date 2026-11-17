"""Incremental quota-only log reader and rolling consumption model."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
WEEK = 7*86400
EPOCH_MONDAY = date(1970, 1, 5)


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
            CREATE TABLE IF NOT EXISTS holidays (start REAL PRIMARY KEY, end REAL);
        ''')

    def set_mode(self, mode, return_at, now):
        active = self.db.execute('SELECT start,end FROM holidays WHERE start<=? AND (end IS NULL OR end>?) ORDER BY start DESC LIMIT 1',
                                 (now, now)).fetchone()
        if mode == 'holiday':
            if active:
                self.db.execute('UPDATE holidays SET end=? WHERE start=?', (return_at, active[0]))
            else:
                self.db.execute('INSERT INTO holidays VALUES (?,?)', (now, return_at))
        elif active:
            self.db.execute('UPDATE holidays SET end=? WHERE start=?', (now, active[0]))
        self.db.commit()

    def mode_cutoff(self, now, mode, days):
        periods = [(start, end if end is not None else float('inf')) for start, end in
                   self.db.execute('SELECT start,end FROM holidays WHERE start<=? ORDER BY start', (now,))]
        current = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        if not periods:
            return None if mode == 'holiday' else (current-timedelta(days=days-1)).timestamp()
        count = 0
        while True:
            start, end = current.timestamp(), (current+timedelta(days=1)).timestamp()
            covered = sum(max(0, min(end, hi)-max(start, lo)) for lo, hi in periods)
            has_mode = covered > 0 if mode == 'holiday' else covered < 86400
            if has_mode:
                count += 1
                if count == days:
                    return start
            if mode == 'holiday' and start <= periods[0][0]:
                return start
            current -= timedelta(days=1)

    def add(self, row):
        if row:
            self.db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)', row)

    def scan(self, now):
        normal_cutoff = self.mode_cutoff(now, 'normal', 35)
        holiday_cutoff = self.mode_cutoff(now, 'holiday', 35)
        cutoff = min(normal_cutoff, holiday_cutoff) if holiday_cutoff is not None else normal_cutoff
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
        # Keep each mode's own history when the other mode remains active.
        self.db.execute('''DELETE FROM snapshots WHERE
            (ts<? AND NOT EXISTS (SELECT 1 FROM holidays h WHERE ts>=h.start AND (h.end IS NULL OR ts<h.end)))
            OR (ts<? AND EXISTS (SELECT 1 FROM holidays h WHERE ts>=h.start AND (h.end IS NULL OR ts<h.end)))''',
            (normal_cutoff, holiday_cutoff if holiday_cutoff is not None else now))
        self.db.commit()
        return {'files': source_files, 'bytesRead': bytes_read, 'newSnapshots': rows}

    def fit(self, now, plan, cycle_days=7, mode='normal'):
        local = datetime.fromtimestamp(now, TZ)
        holiday = mode == 'holiday'
        cycle_days, slot_hours = (1, 1) if holiday else (cycle_days, 3)
        slots = 24//slot_hours
        cutoff = self.mode_cutoff(now, mode, 28)
        if cutoff is None:
            return None, '假期样本尚未积累；切到假期后会按小时单独学习。'
        holidays = [(start, end if end is not None else float('inf')) for start, end in
                    self.db.execute('SELECT start,end FROM holidays WHERE start<=? AND (end IS NULL OR end>=?)', (now, cutoff-WEEK))]

        def in_mode(timestamp):
            inside = any(start <= timestamp < end for start, end in holidays)
            return inside if holiday else not inside
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
        amounts = [[0.0]*slots for _ in range(cycle_days)]
        exposure = [[0.0]*slots for _ in range(cycle_days)]
        observed_dates = [[set() for _ in range(slots)] for _ in range(cycle_days)]
        daily = {}

        def add_span(start, end, amount, observable):
            if end <= start or end <= cutoff:
                return
            if holiday:
                if not any(lo <= start and end <= hi for lo, hi in holidays):
                    return
            elif any(lo < end and hi > start for lo, hi in holidays):
                return
            original_duration = end-start
            current = max(start, cutoff)
            while current < end-1e-6:
                d = datetime.fromtimestamp(current, TZ)
                boundary = d.replace(hour=d.hour//slot_hours*slot_hours, minute=0, second=0, microsecond=0)+timedelta(hours=slot_hours)
                nxt = min(end, boundary.timestamp())
                duration = nxt-current
                part = amount*duration/original_duration
                kind, slot = (d.date()-EPOCH_MONDAY).days % cycle_days, d.hour//slot_hours
                day = d.strftime('%Y-%m-%d')
                stats = daily.setdefault(day, {'day': day, 'weekday': d.weekday(), 'used': 0., 'observedHours': 0.})
                stats['used'] += part
                if observable:
                    hours = duration/3600
                    amounts[kind][slot] += part
                    exposure[kind][slot] += hours
                    observed_dates[kind][slot].add(day)
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
        labels = [('A' if g < 7 else 'B')+'周'+'一二三四五六日'[g % 7] if cycle_days == 14
                  else '周'+'一二三四五六日'[g] for g in range(cycle_days)] if not holiday else [f'{h:02d}时' for h in range(24)]
        filled = sum(hours > 0 for row in exposure for hours in row)
        if filled < cycle_days*slots:
            if holiday:
                return None, f'假期小时样本已覆盖 {filled}/24 个时段；其余时段积累后会自动预测。'
            missing = [labels[g] for g in range(cycle_days) if any(hours <= 0 for hours in exposure[g])]
            return None, f'常规样本已覆盖 {filled}/{cycle_days*slots} 个时段；尚缺'+ '、'.join(missing) +'的部分记录。'
        hourly = [[amounts[g][s]/exposure[g][s] for s in range(slots)] for g in range(cycle_days)]
        daily_rates = [slot_hours*sum(row) for row in hourly]
        full_days = [d['used'] for d in daily.values() if d['observedHours'] >= 23.9]
        return {
            'schema': 3, 'fittedAt': now, 'mode': mode,
            'cycleDays': cycle_days, 'slotHours': slot_hours,
            'cycleStart': (local.date()-timedelta(days=(local.date()-EPOCH_MONDAY).days % cycle_days)).isoformat(),
            'periodStart': datetime.fromtimestamp(cutoff, TZ).strftime('%Y-%m-%d'),
            'periodEnd': local.strftime('%Y-%m-%d %H:%M'),
            'snapshotCount': sum(r[0] >= cutoff and in_mode(r[0]) for r in raw),
            'hourlyRates': hourly, 'dailyRates': daily_rates,
            'profiles': [{'index': h, 'label': labels[h], 'rate': hourly[0][h], 'observedHours': exposure[0][h]}
                         for h in range(24)] if holiday else
                        [{'index': g, 'label': labels[g], 'rate': daily_rates[g], 'observedHours': sum(exposure[g])}
                         for g in range(cycle_days)],
            'coverage': {'filled': filled, 'total': cycle_days*slots,
                         'minDatesPerSlot': min(len(dates) for row in observed_dates for dates in row),
                         'maxDatesPerSlot': max(len(dates) for row in observed_dates for dates in row)},
            'observedDailyRange': [min(full_days), max(full_days)] if full_days else None,
            'daily': [{**d, 'used': round(d['used'], 3), 'observedHours': round(d['observedHours'], 3)} for _, d in sorted(daily.items())],
        }, None

    def close(self):
        self.db.close()

"""Quota collection shared by the native desktop interface."""
import json
import logging
import threading
import time
from pathlib import Path

from codex_client import CodexClient
from usage_store import UsageStore, quota_row
from app_paths import RUNTIME



def account_state(result, now):
    bucket = (result.get('rateLimitsByLimitId') or {}).get('codex') or result.get('rateLimits')
    row = quota_row(now, bucket)
    if not row:
        raise ValueError('当前账户没有可读取的周额度窗口。')
    resets = result.get('rateLimitResetCredits')
    count = resets.get('availableCount') if resets else None
    details = (resets or {}).get('credits')
    credits = []
    if details is not None:
        for credit in details:
            expiry = credit.get('expiresAt')
            if credit.get('status') == 'available' and (expiry is None or expiry > now):
                credits.append({'expiresAt': expiry, 'title': credit.get('title') or 'Full reset'})
        credits.sort(key=lambda c: c['expiresAt'] if c['expiresAt'] is not None else float('inf'))
    known = count == len(credits) and all(c['expiresAt'] is not None for c in credits)
    has_short_window = any(w and w.get('windowDurationMins') != 10080 for w in [bucket.get('primary'), bucket.get('secondary')])
    return {
        'capturedAt': now, 'remaining': max(0., min(100., 100-row[1])),
        'nextRefresh': row[2], 'windowHours': 168, 'plan': row[3],
        'availableCount': count, 'resetDetailsKnown': known, 'credits': credits,
        'expiries': [c['expiresAt'] for c in credits if c['expiresAt'] is not None],
        'hasShortWindow': has_short_window,
    }, row


class Tracker:
    def __init__(self, runtime=RUNTIME, interval=30, codex_home=None):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.store = UsageStore(self.runtime/'usage.sqlite3', codex_home)
        self.client = CodexClient(self.runtime)
        self.interval = interval
        self.lock = threading.Lock()
        self.state = {'loading': True, 'accountFresh': False, 'model': None,
                      'account': None, 'refreshSeconds': interval, 'revision': 0}
        self.last_account = None
        self.account_updated = None
        self.scan_stats = None

    def poll(self):
        with self.lock:
            now = time.time()
            account_error, history_error = None, None
            try:
                result = self.client.read_limits()
                now = time.time()
                self.last_account, row = account_state(result, now)
                self.account_updated = now
                self.store.add(row)
                self.store.db.commit()
            except Exception as error:
                account_error = str(error) or type(error).__name__
                logging.warning('Account refresh: %s', account_error)
            try:
                self.scan_stats = self.store.scan(now)
            except Exception as error:
                history_error = str(error) or type(error).__name__
                logging.exception('History scan failed')
            plan = self.last_account.get('plan') if self.last_account else ''
            model, model_error = self.store.fit(now, plan)
            account = dict(self.last_account) if self.last_account else None
            if model and account:
                model['account'] = account
            self.state = {
                'loading': False, 'accountFresh': account_error is None and account is not None,
                'accountUpdatedAt': self.account_updated, 'checkedAt': now,
                'accountError': account_error, 'historyError': history_error,
                'modelError': model_error, 'model': model, 'account': account,
                'scan': self.scan_stats, 'refreshSeconds': self.interval,
                'revision': self.state['revision']+1,
            }
            (self.runtime/'latest-state.json').write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
            logging.info('Update %s: remaining=%s snapshots=%s newBytes=%s',
                self.state['revision'], account.get('remaining') if account else None,
                model.get('snapshotCount') if model else None, (self.scan_stats or {}).get('bytesRead'))
            return self.state

    def close(self):
        with self.lock:
            self.client.close()
            self.store.close()

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_client import CodexClient
from tracker import Tracker, account_state
from usage_store import UsageStore

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 16, 18, tzinfo=TZ).timestamp()


def rate(used, refresh):
    return {'limit_id': 'codex', 'primary': {'used_percent': used, 'window_minutes': 10080,
            'resets_at': refresh}, 'secondary': None, 'plan_type': 'prolite'}


def event(ts, used, refresh):
    return json.dumps({'timestamp': datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': rate(used, refresh)}}).encode()+b'\n'


def rpc(used=5, reset_count=3):
    return {'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'planType': 'prolite',
        'primary': {'usedPercent': used, 'windowDurationMins': 10080, 'resetsAt': NOW+7*86400-18*3600}, 'secondary': None}},
        'rateLimitResetCredits': {'availableCount': reset_count, 'credits': [
            {'status': 'available', 'expiresAt': NOW+(i+7)*86400, 'title': 'Full reset'} for i in range(reset_count)]}}


def populate(store):
    origin = datetime(2026,11,2,tzinfo=TZ).timestamp()
    # Distinct consumption for every day catches accidental weekday/weekend pooling.
    # Daily totals from Monday to Sunday: 2, 3, 4, 5, 6, 7, 8 percentage points.
    for week in range(2):
        used = 0
        refresh = origin+(week+1)*7*86400
        for slot in range(57):
            ts = origin+(week*56+slot)*3*3600
            if slot:
                day = datetime.fromtimestamp(ts-1,TZ).weekday()
                used += (day+2)/8
            store.add((ts,used,refresh,'prolite'))
    store.add((origin+14*86400,0,origin+21*86400,'prolite'))
    store.db.commit()


class StoreTests(unittest.TestCase):
    def test_incremental_partial_lines_archive_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); logs=root/'sessions';logs.mkdir()
            path=logs/'rollout.jsonl';refresh=NOW+86400
            complete=event(NOW-100,20,refresh)
            partial=event(NOW-50,25,refresh)
            path.write_bytes(b'{"type":"user","text":"private prompt"}\n'+complete+partial[:-1])
            db=root/'quota.sqlite3';store=UsageStore(db,root)
            first=store.scan(time.time())
            self.assertEqual(first['newSnapshots'],1)
            self.assertEqual(store.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)
            with path.open('ab') as out:out.write(b'\n')
            self.assertEqual(store.scan(time.time())['newSnapshots'],1)
            self.assertEqual(store.scan(time.time())['bytesRead'],0)
            store.close();store=UsageStore(db,root)
            self.assertEqual(store.scan(time.time())['bytesRead'],0)
            archived=root/'archived_sessions';archived.mkdir();path.rename(archived/path.name)
            store.scan(time.time())
            self.assertEqual(store.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],2)
            store.close()

    def test_model_refits_when_new_quota_arrives(self):
        with tempfile.TemporaryDirectory() as folder:
            store=UsageStore(Path(folder)/'usage.sqlite3',folder);populate(store)
            model,error=store.fit(NOW,'prolite')
            self.assertIsNone(error)
            self.assertEqual(model['schema'],2)
            self.assertEqual(len(model['hourlyByThreeHours']),7)
            for actual, expected in zip(model['dailyRates'],range(2,9)):
                self.assertAlmostEqual(actual,expected,places=6)
            self.assertEqual([p['weekday'] for p in model['weekdayProfiles']],list(range(7)))
            self.assertAlmostEqual(model['weekdayProfiles'][0]['observedHours'],48)
            store.add((NOW,30,NOW-NOW%1+7*86400-18*3600,'prolite'));store.db.commit()
            changed,error=store.fit(NOW,'prolite')
            self.assertIsNone(error)
            self.assertGreater(changed['dailyRates'][0],model['dailyRates'][0])
            self.assertEqual(changed['dailyRates'][1:],model['dailyRates'][1:])
            self.assertGreater(changed['snapshotCount'],model['snapshotCount'])
            store.close()

    def test_capped_time_is_not_idle_demand(self):
        with tempfile.TemporaryDirectory() as folder:
            store=UsageStore(Path(folder)/'usage.sqlite3',folder);populate(store)
            midnight=datetime(2026,11,16,tzinfo=TZ).timestamp();refresh=midnight+7*86400
            store.add((midnight+3600,100,refresh,'prolite'))
            store.add((NOW,100,refresh,'prolite'));store.db.commit()
            model,error=store.fit(NOW,'prolite')
            self.assertIsNone(error)
            day=next(d for d in model['daily'] if d['day']=='2026-11-16')
            self.assertAlmostEqual(day['observedHours'],1)
            store.close()

    def test_refill_with_unchanged_deadline_starts_new_consumption(self):
        for before,after in ((36,18),(100,0)):
            with self.subTest(before=before,after=after), tempfile.TemporaryDirectory() as folder:
                store=UsageStore(Path(folder)/'usage.sqlite3',folder);populate(store)
                try:
                    midnight=datetime(2026,11,16,tzinfo=TZ).timestamp();refresh=midnight+7*86400
                    # The counter falls while its deadline stays fixed, then consumption resumes.
                    # The three hours spanning the adjustment have unknown usage.
                    for hour, used in [(3,before),(6,after),(9,after+3),(12,after+6)]:
                        store.add((midnight+hour*3600,used,refresh,'prolite'))
                    store.db.commit()
                    model,error=store.fit(midnight+12*3600,'prolite')
                    self.assertIsNone(error)
                    day=next(d for d in model['daily'] if d['day']=='2026-11-16')
                    self.assertAlmostEqual(day['used'],before+6)
                    self.assertAlmostEqual(day['observedHours'],9)
                finally:
                    store.close()

    def test_one_point_stale_reading_does_not_add_consumption(self):
        with tempfile.TemporaryDirectory() as folder:
            store=UsageStore(Path(folder)/'usage.sqlite3',folder);populate(store)
            midnight=datetime(2026,11,16,tzinfo=TZ).timestamp();refresh=midnight+7*86400
            for hour, used in [(3,36),(6,35),(9,36),(12,37)]:
                store.add((midnight+hour*3600,used,refresh,'prolite'))
            store.db.commit()
            model,error=store.fit(midnight+12*3600,'prolite')
            self.assertIsNone(error)
            day=next(d for d in model['daily'] if d['day']=='2026-11-16')
            self.assertAlmostEqual(day['used'],37)
            self.assertAlmostEqual(day['observedHours'],12)
            store.close()


class LiveTests(unittest.TestCase):
    def test_account_and_deadline_fields(self):
        account,row=account_state(rpc(used=93),NOW)
        self.assertEqual(account['remaining'],7)
        self.assertEqual(account['availableCount'],3)
        self.assertTrue(account['resetDetailsKnown'])
        unknown=rpc();unknown['rateLimitResetCredits']=None
        account,_=account_state(unknown,NOW)
        self.assertIsNone(account['availableCount'])
        self.assertFalse(account['resetDetailsKnown'])

    def test_collector_changes_without_model_or_assistant(self):
        class Client:
            def __init__(self):self.calls=0
            def read_limits(self):
                self.calls+=1
                return rpc(used=50+self.calls,reset_count=4-self.calls)
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            tracker=Tracker(folder,codex_home=Path(folder)/'empty');populate(tracker.store)
            tracker.client=Client()
            with patch('tracker.time.time',return_value=NOW):first=tracker.poll()
            with patch('tracker.time.time',return_value=NOW+30):second=tracker.poll()
            self.assertEqual(first['account']['remaining'],49)
            self.assertEqual(second['account']['remaining'],48)
            self.assertEqual(first['account']['availableCount'],3)
            self.assertEqual(second['account']['availableCount'],2)
            self.assertEqual(second['revision'],first['revision']+1)
            self.assertGreater(second['model']['snapshotCount'],first['model']['snapshotCount'])
            tracker.close()

    def test_connection_failure_marks_cached_account_stale(self):
        class Client:
            def read_limits(self):raise RuntimeError('connection lost')
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            tracker=Tracker(folder,codex_home=Path(folder)/'empty');populate(tracker.store)
            tracker.client=Client();tracker.last_account=account_state(rpc(),NOW)[0];tracker.account_updated=NOW
            with patch('tracker.time.time',return_value=NOW+30):state=tracker.poll()
            self.assertFalse(state['accountFresh'])
            self.assertEqual(state['accountUpdatedAt'],NOW)
            self.assertEqual(state['accountError'],'connection lost')
            tracker.close()

    def test_unscheduled_refill_uses_reported_clock_and_preserves_resets(self):
        for clock_changes in (False,True):
            with self.subTest(clock_changes=clock_changes), tempfile.TemporaryDirectory() as folder:
                before,after=rpc(used=99),rpc(used=0)
                old_clock=before['rateLimitsByLimitId']['codex']['primary']['resetsAt']
                new_clock=NOW+30+7*86400 if clock_changes else old_clock
                after['rateLimitsByLimitId']['codex']['primary']['resetsAt']=new_clock

                class Client:
                    def __init__(self):self.replies=iter((before,after))
                    def read_limits(self):return next(self.replies)
                    def close(self):pass

                tracker=Tracker(folder,codex_home=Path(folder)/'empty');populate(tracker.store)
                tracker.client=Client()
                with patch('tracker.time.time',return_value=NOW):first=tracker.poll()
                with patch('tracker.time.time',return_value=NOW+30):second=tracker.poll()
                self.assertEqual(first['account']['remaining'],1)
                self.assertEqual(second['account']['remaining'],100)
                self.assertEqual(second['account']['nextRefresh'],new_clock)
                self.assertEqual(second['model']['account'],second['account'])
                self.assertEqual(second['account']['availableCount'],3)
                tracker.close()

    def test_rpc_cannot_redeem(self):
        client=CodexClient(Path(tempfile.gettempdir()))
        with self.assertRaises(ValueError):client._request('account/rateLimitResetCredit/consume')

if __name__=='__main__':unittest.main()

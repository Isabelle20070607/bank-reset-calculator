import tempfile
import os
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop import Collector

from test_live import NOW, TZ, populate, rpc
from tracker import Tracker
from usage_store import EPOCH_MONDAY, UsageStore


def holiday_samples(store):
    start = datetime(2026, 11, 16, tzinfo=TZ).timestamp()
    populate(store)
    store.set_mode('holiday', start+48*3600, start+1800)
    # The first interval crosses the mode boundary and must not train either
    # profile. Subsequent hours consume exactly one point per hour.
    for hour in range(1, 49):
        store.add((start+hour*3600, 19+hour, start+7*86400, 'prolite'))
    store.db.commit()
    return start


class ModeFitTests(unittest.TestCase):
    def test_biweekly_fit_keeps_fourteen_distinct_days(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UsageStore(Path(folder)/'usage.sqlite3', folder)
            day = datetime(2026, 10, 20, tzinfo=TZ)
            day -= timedelta(days=(day.date()-EPOCH_MONDAY).days % 14)
            origin = day.timestamp()
            for week in range(4):
                used = 0.
                refresh = origin+(week+1)*7*86400
                for slot in range(57):
                    timestamp = origin+(week*56+slot)*3*3600
                    if slot:
                        position = (week*7+(slot-1)//8) % 14
                        used += (position+1)/8
                    store.add((timestamp, used, refresh, 'prolite'))
            store.db.commit()
            model, error = store.fit(origin+28*86400, 'prolite', 14)
            self.assertIsNone(error)
            self.assertEqual(model['dailyRates'], list(range(1, 15)))
            self.assertEqual(model['coverage']['filled'], 112)
            self.assertEqual(model['profiles'][7]['label'], 'B周一')
            store.close()

    def test_holiday_daily_hours_are_separate_and_cross_boundary_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UsageStore(Path(folder)/'usage.sqlite3', folder)
            start = holiday_samples(store)
            holiday, error = store.fit(start+48*3600, 'prolite', 7, 'holiday')
            self.assertIsNone(error)
            self.assertEqual((holiday['cycleDays'], holiday['slotHours']), (1, 1))
            self.assertEqual(holiday['hourlyRates'], [[1]*24])
            normal, error = store.fit(start+48*3600, 'prolite')
            self.assertIsNone(error)
            self.assertEqual(normal['dailyRates'], list(range(2, 9)))
            store.close()

    def test_holiday_samples_survive_a_long_normal_period(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UsageStore(Path(folder)/'usage.sqlite3', folder)
            start = holiday_samples(store)
            later = start+60*86400
            store.set_mode('normal', None, later)
            store.scan(later)
            holiday, error = store.fit(later, 'prolite', 7, 'holiday')
            self.assertIsNone(error)
            self.assertEqual(holiday['hourlyRates'], [[1]*24])
            store.close()

    def test_new_holiday_mode_does_not_substitute_normal_data(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UsageStore(Path(folder)/'usage.sqlite3', folder)
            populate(store)
            now = datetime(2026, 11, 16, 18, tzinfo=TZ).timestamp()
            store.set_mode('holiday', None, now)
            model, error = store.fit(now, 'prolite', 7, 'holiday')
            self.assertIsNone(model)
            self.assertIn('0/24', error)
            store.close()

    def test_normal_history_is_preserved_during_a_long_holiday(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UsageStore(Path(folder)/'usage.sqlite3', folder)
            populate(store)
            start = datetime(2026, 11, 16, tzinfo=TZ).timestamp()
            store.set_mode('holiday', start+60*86400, start+3600)
            later = start+59*86400
            store.scan(later)
            normal, error = store.fit(later, 'prolite')
            self.assertIsNone(error)
            self.assertEqual(normal['dailyRates'], list(range(2, 9)))
            store.close()


class ModeTrackerTests(unittest.TestCase):
    def test_return_date_is_in_forecast_and_expired_mode_returns_to_normal(self):
        class Client:
            def read_limits(self): return rpc(used=50, reset_count=0)
            def close(self): pass

        with tempfile.TemporaryDirectory() as folder:
            tracker = Tracker(folder, codex_home=Path(folder)/'empty')
            start = holiday_samples(tracker.store)
            tracker.client = Client()
            now, until = start+42*3600, start+48*3600
            options = {'mode': 'holiday', 'normalCycleDays': 7, 'returnAt': until}
            with patch('tracker.time.time', return_value=now): state = tracker.poll(options)
            self.assertEqual(state['mode'], 'holiday')
            self.assertEqual(state['model']['switchAt'], until)
            self.assertEqual(state['model']['normalAfter']['mode'], 'normal')
            with patch('tracker.time.time', return_value=until+30): state = tracker.poll(options)
            self.assertEqual(state['mode'], 'normal')
            self.assertEqual(state['model']['mode'], 'normal')
            self.assertNotIn('switchAt', state['model'])
            tracker.close()

    def test_worker_applies_mode_changes_before_recalculating(self):
        class Client:
            def read_limits(self): return rpc(used=50, reset_count=0)
            def close(self): pass

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            tracker = Tracker(folder, codex_home=Path(folder)/'empty')
            populate(tracker.store)
            tracker.client = Client()
            normal = {'mode': 'normal', 'normalCycleDays': 7, 'returnAt': None}
            holiday = dict(normal, mode='holiday')
            replies = []

            def await_reply(offset, options):
                deadline = time.monotonic()+8
                while time.monotonic() < deadline:
                    for reply in replies[offset:]:
                        if reply['settings'][2] == options:
                            return reply
                    app.processEvents()
                    QTest.qWait(10)
                self.fail('Worker did not apply the requested mode.')

            with patch('desktop.Tracker', return_value=tracker), patch('tracker.time.time', return_value=NOW):
                worker = Collector(14, 1, 60, normal)
                worker.updated.connect(replies.append)
                worker.start()
                try:
                    self.assertIsNotNone(await_reply(0, normal)['result'])
                    offset = len(replies)
                    worker.request(14, 1, False, holiday)
                    result = await_reply(offset, holiday)
                    self.assertEqual(result['state']['mode'], 'holiday')
                    self.assertIsNone(result['result'])
                    self.assertIn('0/24', result['state']['modelError'])
                    offset = len(replies)
                    worker.request(14, 1, False, normal)
                    self.assertIsNotNone(await_reply(offset, normal)['result'])
                finally:
                    worker.finish()
                    self.assertTrue(worker.wait(5000))


if __name__ == '__main__':
    unittest.main()

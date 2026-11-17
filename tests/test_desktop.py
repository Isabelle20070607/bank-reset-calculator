"""Native work-return states use synthetic data and no account integration."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
import desktop
from calculator import Calculator


class IdleCollector(QObject):
    updated = Signal(object)
    busy = Signal(bool)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, *args): super().__init__()
    def start(self): pass
    def request(self, *args): pass
    def finish(self): pass
    def isRunning(self): return False


class WorkReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        start = 1794787200
        account = {'capturedAt': start, 'remaining': 50, 'nextRefresh': start+7*86400,
                   'availableCount': 0, 'credits': [], 'expiries': [],
                   'resetDetailsKnown': True, 'hasShortWindow': False}
        model = {'account': account, 'hourlyByThreeHours': [[1/24]*8 for _ in range(7)],
                 'dailyRates': [1]*7, 'weekdayProfiles': [{'weekday': day, 'dailyRate': 1,
                     'observedHours': 48} for day in range(7)], 'daily': [],
                 'periodStart': '2026-10-20', 'periodEnd': '2026-11-16', 'snapshotCount': 100}
        cls.payload = {'state': {'account': account, 'model': model, 'accountFresh': True,
                                'accountUpdatedAt': start},
                       'result': Calculator().optimize(model), 'error': None, 'settings': [28, 1]}

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.patches = [patch('desktop.Collector', IdleCollector),
                        patch('desktop.RUNTIME', Path(self.folder.name)),
                        patch.object(desktop.QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.window = desktop.Window(self.app)
        self.addCleanup(self.close_window)
        self.window.update_state(copy.deepcopy(self.payload))

    def close_window(self):
        self.window.closing = True
        self.window.close()
        self.window.deleteLater()

    def test_one_percent_is_full_weekly_allowance_and_counted_once(self):
        self.assertEqual(self.window.marginal_value.text(), '周限额的 1%')
        self.assertIn('1%', self.window.comparison.text())
        self.assertEqual(self.window.marginal_table.item(0, 1).text(), '0.28')
        self.assertEqual(self.window.marginal_table.item(0, 2).text(), '0.29')
        self.assertEqual(self.window.marginal_chart.marginal['futureGain'], 0)

    def test_changing_period_and_losing_connection_clear_old_return(self):
        self.window.days.setCurrentIndex(2)
        self.assertEqual(self.window.marginal_value.text(), '正在重算…')
        self.assertIsNone(self.window.marginal_chart.marginal)
        self.window.update_state(copy.deepcopy(self.payload))
        self.assertEqual(self.window.marginal_value.text(), '正在重算…')
        offline = copy.deepcopy(self.payload)
        offline['settings'] = [42, 1]
        offline['result'] = None
        offline['state']['accountFresh'] = False
        offline['state']['accountError'] = 'connection lost'
        self.window.update_state(offline)
        self.assertIsNone(self.window.marginal_chart.marginal)
        self.assertEqual(self.window.marginal_table.rowCount(), 0)
        self.assertEqual(self.window.comparison.text(), '')

    def test_less_than_one_point_shows_unavailable_instead_of_old_gain(self):
        payload = copy.deepcopy(self.payload)
        payload['result']['marginal'] = {'available': False, 'unit': 1, 'reason': 'insufficientQuota'}
        self.window.update_state(payload)
        self.assertEqual(self.window.marginal_value.text(), '剩余不足 1%')
        self.assertEqual(self.window.marginal_table.rowCount(), 0)
        self.assertFalse(self.window.marginal_chart.marginal['available'])


if __name__ == '__main__':
    unittest.main()

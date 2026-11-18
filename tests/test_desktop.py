"""Native planning and account states use synthetic data and no account integration."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtTest import QTest
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


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        start = 1794787200
        account = {'capturedAt': start, 'remaining': 50, 'nextRefresh': start+7*86400,
                   'availableCount': 0, 'credits': [], 'expiries': [],
                   'resetDetailsKnown': True, 'hasShortWindow': False}
        model = {'account': account, 'mode': 'normal', 'cycleDays': 7, 'slotHours': 3,
                 'hourlyRates': [[1/24]*8 for _ in range(7)],
                 'dailyRates': [1]*7, 'profiles': [{'index': day, 'label': '周'+'一二三四五六日'[day], 'rate': 1,
                     'observedHours': 48} for day in range(7)], 'daily': [],
                 'periodStart': '2026-10-20', 'periodEnd': '2026-11-16', 'snapshotCount': 100}
        cls.payload = {'state': {'account': account, 'model': model, 'accountFresh': True,
                                'accountUpdatedAt': start},
                       'result': Calculator().optimize(model), 'error': None, 'settings': [28, 1, dict(desktop.DEFAULT_FIT)]}

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

    def test_totals_subtract_one_full_allowance_per_week(self):
        for index, days, total, surplus in [(0, 14, '0.14', '-1.86'),
                                            (1, 28, '0.28', '-3.72'),
                                            (2, 42, '0.42', '-5.58')]:
            with self.subTest(days=days):
                self.window.days.setCurrentIndex(index)
                payload = copy.deepcopy(self.payload)
                payload['settings'] = [days, 1, dict(desktop.DEFAULT_FIT)]
                payload['result'] = Calculator().optimize(payload['state']['model'], days)
                self.window.update_state(payload)
                self.assertEqual(self.window.period_total.text(), total+' 份')
                self.assertEqual(self.window.regular_total.text(), str(days//7)+' 份')
                self.assertEqual(self.window.allowance_surplus.text(), surplus+' 份')

    def test_reset_surplus_uses_complete_allowance_not_predicted_no_reset_usage(self):
        payload = copy.deepcopy(self.payload)
        model = payload['state']['model']
        model['hourlyRates'] = [[100/24]*8 for _ in range(7)]
        model['dailyRates'] = [100]*7
        model['account']['expiries'] = [model['account']['capturedAt']+day*86400 for day in (10, 20)]
        payload['result'] = Calculator().optimize(model)
        self.window.update_state(payload)
        self.assertEqual(self.window.period_total.text(), '5.5 份')
        self.assertEqual(self.window.regular_total.text(), '4 份')
        self.assertEqual(self.window.allowance_surplus.text(), '+1.5 份')
        self.assertIn('多用 +2 份', self.window.gain.text())
        self.assertEqual(payload['result']['baseline']/100, 3.5)

    def test_changing_period_and_losing_connection_clear_old_return(self):
        self.window.days.setCurrentIndex(2)
        self.assertEqual(self.window.period_total.text(), '—')
        self.assertEqual(self.window.regular_total.text(), '—')
        self.assertEqual(self.window.allowance_surplus.text(), '—')
        self.assertEqual(self.window.first.text(), '正在重算…')
        self.window.update_state(copy.deepcopy(self.payload))
        self.assertEqual(self.window.first.text(), '正在重算…')
        self.assertEqual(self.window.allowance_surplus.text(), '—')
        offline = copy.deepcopy(self.payload)
        offline['settings'] = [42, 1, dict(desktop.DEFAULT_FIT)]
        offline['result'] = None
        offline['state']['accountFresh'] = False
        offline['state']['accountError'] = 'connection lost'
        self.window.update_state(offline)
        self.assertEqual(self.window.period_total.text(), '—')
        self.assertEqual(self.window.regular_total.text(), '—')
        self.assertEqual(self.window.allowance_surplus.text(), '—')

    def test_collector_failure_clears_old_totals(self):
        self.window.show_failure('collection stopped')
        self.assertEqual(self.window.period_total.text(), '—')
        self.assertEqual(self.window.regular_total.text(), '—')
        self.assertEqual(self.window.allowance_surplus.text(), '—')

    def test_strength_buttons_accept_mouse_clicks_and_update_preferences(self):
        self.window.show()
        self.app.processEvents()
        QTest.mouseClick(self.window.increase_strength, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(self.window.strength.value(),1.05)
        settings=json.loads((Path(self.folder.name)/'preferences.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(settings['strength'],1.05)
        self.assertEqual(self.window.first.text(),'正在重算…')
        self.assertEqual(self.window.allowance_surplus.text(), '—')
        QTest.mouseClick(self.window.decrease_strength, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(self.window.strength.value(),1)
        self.window.strength.setValue(self.window.strength.maximum())
        self.assertFalse(self.window.increase_strength.isEnabled())
        self.assertTrue(self.window.decrease_strength.isEnabled())
        QTest.mouseClick(self.window.decrease_strength, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(self.window.strength.value(),2.95)
        self.window.strength.setValue(self.window.strength.minimum())
        self.assertFalse(self.window.decrease_strength.isEnabled())
        self.assertTrue(self.window.increase_strength.isEnabled())
        QTest.mouseClick(self.window.increase_strength, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(self.window.strength.value(),.3)

    def test_holiday_daily_controls_and_scheduled_return_restore_normal_cycle(self):
        self.window.fit_cycle.setCurrentIndex(self.window.fit_cycle.findData(14))
        self.window.usage_mode.setCurrentIndex(self.window.usage_mode.findData('holiday'))
        self.assertEqual(self.window.fit_cycle.currentData(), 1)
        self.assertFalse(self.window.fit_cycle.isEnabled())
        self.assertEqual(self.window.slot_info.text(), '每小时')
        self.window.update_state(copy.deepcopy(self.payload))
        self.assertEqual(self.window.profiles.rowCount(), 0)
        self.assertEqual(self.window.weekly_chart.profiles, [])
        self.assertIn('小时', self.window.profiles.horizontalHeaderItem(1).text())
        self.window.auto_return.setChecked(True)
        until = self.window.fit_options()['returnAt']
        self.assertIsNotNone(until)
        offline = copy.deepcopy(self.payload)
        offline['state']['model'] = None
        offline['state']['modelError'] = '假期小时样本已覆盖 0/24 个时段'
        offline['result'] = None
        offline['settings'] = [28, 1, self.window.fit_options()]
        self.window.update_state(offline)
        self.assertEqual(self.window.profiles.rowCount(), 0)
        self.assertEqual(self.window.weekly_chart.profiles, [])
        with patch('desktop.time.time', return_value=until+1):
            self.window.return_if_due()
        self.assertEqual(self.window.usage_mode.currentData(), 'normal')
        self.assertEqual(self.window.fit_cycle.currentData(), 14)
        self.assertFalse(self.window.auto_return.isChecked())
        saved = json.loads((Path(self.folder.name)/'preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['mode'], 'normal')

    def test_reopen_after_scheduled_date_does_not_reenter_holiday(self):
        self.window.usage_mode.setCurrentIndex(self.window.usage_mode.findData('holiday'))
        self.window.auto_return.setChecked(True)
        until = self.window.fit_options()['returnAt']
        with patch('desktop.time.time', return_value=until+1):
            reopened = desktop.Window(self.app)
        try:
            self.assertEqual(reopened.usage_mode.currentData(), 'normal')
            self.assertFalse(reopened.auto_return.isChecked())
        finally:
            reopened.closing = True
            reopened.close()
            reopened.deleteLater()


if __name__ == '__main__':
    unittest.main()

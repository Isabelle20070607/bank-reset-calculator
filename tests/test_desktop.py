"""Native totals and work-return states use synthetic data and no account integration."""
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

    def test_curve_mouse_and_keyboard_selection_update_net_return_and_totals(self):
        self.window.tabs.setCurrentIndex(1)
        self.window.show()
        self.app.processEvents()
        chart = self.window.marginal_chart
        self.assertIn('0–43%', self.window.comparison.text())
        self.assertEqual(chart.curve['points'][0]['usage'], 0)
        self.assertAlmostEqual(chart.curve['points'][-1]['usage'], 43, places=2)
        plot = chart.plot_rect()
        target = plot.center()
        target.setX(plot.right()-1)
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=target.toPoint())
        self.assertAlmostEqual(chart.selected_usage, 43, places=2)
        self.assertEqual(self.window.marginal_value.text(), '净收益 +43% 周限额')
        self.assertEqual(self.window.marginal_table.item(0, 1).text(), '0.28')
        self.assertEqual(self.window.marginal_table.item(0, 2).text(), '0.71')
        self.assertAlmostEqual(chart.curve['points'][-1]['futureGain'], 0)
        QTest.keyClick(chart, Qt.Key.Key_Home)
        self.assertEqual(chart.selected_usage, 0)
        self.assertEqual(self.window.marginal_value.text(), '净收益 0% 周限额')
        self.assertEqual(self.window.marginal_table.item(0, 2).text(), '0.28')
        QTest.keyClick(chart, Qt.Key.Key_Right)
        self.assertAlmostEqual(chart.selected_usage, 2.15, places=2)
        self.assertEqual(self.window.marginal_value.text(), '净收益 +2.15% 周限额')
        self.assertEqual(self.window.marginal_table.item(0, 2).text(), '0.3015')

    def test_totals_subtract_one_full_allowance_per_week(self):
        for index, days, total, surplus in [(0, 14, '0.14', '-1.86'),
                                            (1, 28, '0.28', '-3.72'),
                                            (2, 42, '0.42', '-5.58')]:
            with self.subTest(days=days):
                self.window.days.setCurrentIndex(index)
                payload = copy.deepcopy(self.payload)
                payload['settings'] = [days, 1]
                payload['result'] = Calculator().optimize(payload['state']['model'], days)
                self.window.update_state(payload)
                self.assertEqual(self.window.period_total.text(), total+' 份')
                self.assertEqual(self.window.regular_total.text(), str(days//7)+' 份')
                self.assertEqual(self.window.allowance_surplus.text(), surplus+' 份')

    def test_reset_surplus_uses_complete_allowance_not_predicted_no_reset_usage(self):
        payload = copy.deepcopy(self.payload)
        model = payload['state']['model']
        model['hourlyByThreeHours'] = [[100/24]*8 for _ in range(7)]
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
        self.assertEqual(self.window.marginal_value.text(), '正在重算…')
        self.assertIsNone(self.window.marginal_chart.curve)
        self.window.update_state(copy.deepcopy(self.payload))
        self.assertEqual(self.window.marginal_value.text(), '正在重算…')
        self.assertEqual(self.window.allowance_surplus.text(), '—')
        offline = copy.deepcopy(self.payload)
        offline['settings'] = [42, 1]
        offline['result'] = None
        offline['state']['accountFresh'] = False
        offline['state']['accountError'] = 'connection lost'
        self.window.update_state(offline)
        self.assertIsNone(self.window.marginal_chart.curve)
        self.assertEqual(self.window.marginal_table.rowCount(), 0)
        self.assertEqual(self.window.comparison.text(), '')
        self.assertEqual(self.window.period_total.text(), '—')
        self.assertEqual(self.window.regular_total.text(), '—')
        self.assertEqual(self.window.allowance_surplus.text(), '—')

    def test_collector_failure_clears_old_totals(self):
        self.window.show_failure('collection stopped')
        self.assertEqual(self.window.period_total.text(), '—')
        self.assertEqual(self.window.regular_total.text(), '—')
        self.assertEqual(self.window.allowance_surplus.text(), '—')

    def test_sub_one_percent_quota_still_has_a_curve(self):
        payload = copy.deepcopy(self.payload)
        payload['state']['model']['account']['remaining'] = .5
        payload['result'] = Calculator().optimize(payload['state']['model'])
        self.window.update_state(payload)
        self.assertTrue(self.window.marginal_chart.curve['available'])
        self.assertEqual(self.window.marginal_chart.curve['availableUsage'], .5)
        self.assertEqual(self.window.marginal_chart.curve['maxUsage'], 0)
        self.assertEqual(self.window.marginal_value.text(), '净收益 0% 周限额')
        self.assertEqual(self.window.marginal_table.rowCount(), 3)

    def test_empty_quota_shows_unavailable_instead_of_old_curve(self):
        payload = copy.deepcopy(self.payload)
        payload['result']['marginal'] = {'available': False, 'maxUsage': 0, 'points': [], 'reason': 'insufficientQuota'}
        self.window.update_state(payload)
        self.assertEqual(self.window.marginal_value.text(), '当前额度已耗尽')
        self.assertEqual(self.window.marginal_table.rowCount(), 0)
        self.assertFalse(self.window.marginal_chart.curve['available'])

    def test_strength_buttons_accept_mouse_clicks_and_update_preferences(self):
        self.window.show()
        self.app.processEvents()
        QTest.mouseClick(self.window.increase_strength, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(self.window.strength.value(),1.05)
        settings=json.loads((Path(self.folder.name)/'preferences.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(settings['strength'],1.05)
        self.assertEqual(self.window.marginal_value.text(),'正在重算…')
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


if __name__ == '__main__':
    unittest.main()

"""Render the public README preview using only synthetic data."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDate, QObject, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication
import desktop
from calculator import Calculator


class PreviewCollector(QObject):
    updated = Signal(object)
    busy = Signal(bool)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, *args):
        super().__init__()

    def start(self): pass
    def request(self, *args): pass
    def finish(self): pass
    def isRunning(self): return False


def main():
    start = 1794790800  # Fixed example date, unrelated to any account.
    rates = [18, 22, 35, 15, 29, 8, 6]
    account = {'capturedAt': start, 'remaining': 12, 'nextRefresh': start+2*86400,
               'availableCount': 3, 'resetDetailsKnown': True, 'hasShortWindow': False,
               'credits': [{'expiresAt': start+day*86400} for day in (10, 17, 27)],
               'expiries': [start+day*86400 for day in (10, 17, 27)]}
    model = {'account': account, 'mode': 'normal', 'cycleDays': 7, 'slotHours': 3,
             'hourlyRates': [[rate/24]*8 for rate in rates],
             'dailyRates': rates, 'profiles': [{'index': day, 'label': '周'+'一二三四五六日'[day], 'rate': rate,
                 'observedHours': 72} for day, rate in enumerate(rates)],
             'daily': [], 'periodStart': '2026-10-20', 'periodEnd': '2026-11-16',
             'snapshotCount': 3600}
    state = {'account': account, 'model': model, 'accountFresh': True, 'accountUpdatedAt': start}
    app = QApplication([])
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts/msyh.ttc'))
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, '#141923'),
                        (QPalette.ColorRole.Base, '#192230'),
                        (QPalette.ColorRole.Button, '#243046'),
                        (QPalette.ColorRole.Text, '#e9eef8'),
                        (QPalette.ColorRole.WindowText, '#e9eef8'),
                        (QPalette.ColorRole.ButtonText, '#e9eef8')):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    with tempfile.TemporaryDirectory() as folder, \
            patch('desktop.time.time', return_value=start), \
            patch('desktop.Collector', PreviewCollector), \
            patch('desktop.RUNTIME', Path(folder)), \
            patch.object(desktop.QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
        window = desktop.Window(app)
        window.update_mode_controls()
        window.return_date.setDate(QDate(2026, 11, 23))
        result = Calculator().optimize(model)
        window.update_state({'state': state, 'result': result, 'error': None, 'settings': [28, 1, window.fit_options()]})
        window.status.setText('虚构示例数据 · 实际使用时每 30 秒自动更新')
        window.resize(930, 790)
        window.show()
        app.processEvents()
        target = ROOT/'docs'/'preview.png'
        target.parent.mkdir(exist_ok=True)
        if not window.grab().save(str(target)):
            raise RuntimeError('Could not save README preview.')
        window.tabs.setCurrentIndex(1)
        app.processEvents()
        if not window.grab().save(str(ROOT/'docs'/'work-return.png')):
            raise RuntimeError('Could not save work-return preview.')
        window.usage_mode.setCurrentIndex(window.usage_mode.findData('holiday'))
        window.auto_return.setChecked(True)
        hourly = [.02]*8+[.12]*10+[.25]*6
        holiday = dict(model, mode='holiday', cycleDays=1, slotHours=1,
                       hourlyRates=[hourly], dailyRates=[sum(hourly)],
                       profiles=[{'index': h, 'label': f'{h:02d}时', 'rate': rate, 'observedHours': 3}
                                 for h, rate in enumerate(hourly)],
                       switchAt=window.fit_options()['returnAt'], normalAfter=model)
        window.update_state({'state': dict(state, model=holiday),
                             'result': Calculator().optimize(holiday), 'error': None,
                             'settings': [28, 1, window.fit_options()]})
        window.tabs.setCurrentIndex(2)
        app.processEvents()
        if not window.grab().save(str(ROOT/'docs'/'holiday-mode.png')):
            raise RuntimeError('Could not save holiday-mode preview.')
        window.quit()


if __name__ == '__main__':
    main()

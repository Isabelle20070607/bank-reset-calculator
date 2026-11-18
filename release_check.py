"""Smoke check used by the Windows release builder; never reads user data."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path


def check(report_path):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication, QMainWindow
    from calculator import Calculator
    from desktop_visuals import build_interface

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.app = app
    window.request = lambda **kwargs: None
    window.quit = app.quit
    build_interface(window)
    if window.windowIcon().isNull():
        raise RuntimeError('Release icon is missing.')
    window.increase_strength.click()
    if abs(window.strength.value()-1.05) > 1e-6:
        raise RuntimeError('Release increase button is unavailable.')
    window.decrease_strength.click()
    if abs(window.strength.value()-1) > 1e-6:
        raise RuntimeError('Release decrease button is unavailable.')
    start = 1794787200
    model = {'cycleDays': 7, 'slotHours': 3, 'hourlyRates': [[100/24]*8 for _ in range(7)],
             'dailyRates': [100]*7, 'account': {'capturedAt': start,
             'remaining': 0, 'nextRefresh': start+12*3600, 'expiries': [start+10*86400]}}
    calculator = Calculator()
    result = calculator.optimize(model, days=1)
    if abs(result['baseline']-50) > 1e-6 or abs(result['now']['served']-100) > 1e-6:
        raise RuntimeError('Release optimizer returned an unexpected result.')
    window.forecast_chart.set_result(result)
    window.weekly_chart.set_profiles([{'index': day, 'label': '周'+'一二三四五六日'[day], 'rate': 10+day}
                                     for day in range(7)])
    window.resize(930, 790)
    window.show()
    app.processEvents()
    if window.grab().isNull():
        raise RuntimeError('Release window could not render.')
    expected = ['重置建议', '用量习惯', '观测记录']
    if [window.tabs.tabText(i) for i in range(window.tabs.count())] != expected:
        raise RuntimeError('Unexpected desktop tabs.')
    for index in range(1, window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        if window.grab().isNull():
            raise RuntimeError('Release tab could not render.')
    with tempfile.TemporaryDirectory() as folder:
        with sqlite3.connect(Path(folder)/'check.sqlite3') as database:
            database.execute('create table check_runtime (value integer)')
            database.execute('insert into check_runtime values (1)')
            if database.execute('select value from check_runtime').fetchone() != (1,):
                raise RuntimeError('Release SQLite runtime is unavailable.')
        database.close()
    window.close()
    Path(report_path).write_text(json.dumps({'ok': True, 'qt': True, 'sqlite': True,
        'optimizer': True, 'tabs': True, 'strengthButtons': True,
        'node': calculator.node}, indent=2), encoding='utf-8')

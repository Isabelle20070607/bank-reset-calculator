"""Native Qt Widgets calculator with automatic collection and a tray icon."""
import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QDate, QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon
from desktop_visuals import build_interface, fill_table, points


from calculator import Calculator
from tracker import RUNTIME, Tracker

SERVER_NAME = 'bank-reset-calculator'
TZ = timezone(timedelta(hours=8))
DEFAULT_FIT = {'mode': 'normal', 'normalCycleDays': 7, 'returnAt': None}


def number(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def date(timestamp, seconds=False):
    return datetime.fromtimestamp(timestamp, TZ).strftime('%m/%d %H:%M:%S' if seconds else '%m/%d %H:%M')


def ready(state):
    account = state.get('account')
    return bool(state.get('model') and account and state.get('accountFresh') and
                not state.get('historyError') and account['resetDetailsKnown'] and
                not account['hasShortWindow'] and account['nextRefresh'] > account['capturedAt'])


class Collector(QThread):
    updated = Signal(object)
    busy = Signal(bool)
    failed = Signal(str)

    def __init__(self, days=28, strength=1, interval=30, fit_options=None):
        super().__init__()
        self.condition = threading.Condition()
        self.settings = (days, strength, dict(fit_options or DEFAULT_FIT))
        self.interval = interval
        self.refresh_requested = True
        self.calculation_requested = True
        self.stopping = False

    def request(self, days, strength, refresh=False, fit_options=None):
        with self.condition:
            self.settings = (days, strength, dict(fit_options or DEFAULT_FIT))
            self.refresh_requested |= refresh
            self.calculation_requested = True
            self.condition.notify()

    def finish(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()

    def run(self):
        tracker, calculator = None, None
        try:
            tracker, calculator = Tracker(interval=self.interval), Calculator()
            state, next_refresh, used_fit = None, 0., None
            while True:
                with self.condition:
                    while not self.stopping and not self.calculation_requested and time.monotonic() < next_refresh:
                        self.condition.wait(next_refresh-time.monotonic())
                    if self.stopping:
                        break
                    wanted_fit = self.settings[2]
                    refresh = self.refresh_requested or time.monotonic() >= next_refresh or wanted_fit != used_fit
                    self.refresh_requested = False
                    self.calculation_requested = False
                self.busy.emit(True)
                if refresh:
                    state = tracker.poll(wanted_fit)
                    used_fit = wanted_fit
                    next_refresh = time.monotonic()+self.interval
                with self.condition:
                    days, strength, fit_options = self.settings
                    if fit_options != used_fit:
                        self.calculation_requested = True
                        continue
                    self.calculation_requested = False
                result, error = None, None
                if ready(state):
                    try:
                        result = calculator.optimize(state['model'], days, strength)
                    except Exception as exception:
                        logging.exception('Calculation failed')
                        error = str(exception)
                self.updated.emit({'state': state, 'result': result, 'error': error,
                                   'settings': [days, strength, fit_options]})
                self.busy.emit(False)
        except Exception as exception:
            logging.exception('Collector failed')
            self.failed.emit(str(exception))
        finally:
            logging.info('Collector stopping')
            if tracker:
                tracker.close()
            del calculator
            logging.info('Collector stopped')




class Window(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.closing = False
        self.last_payload = None
        self.normal_cycle_days = 7
        build_interface(self)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu(self)
            for text, action in [('显示窗口', self.show_window), ('立即更新', lambda: self.request(refresh=True)), ('退出', self.quit)]:
                item = QAction(text, self)
                item.triggered.connect(action)
                menu.addAction(item)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip('Banked reset：自动更新中')
            self.tray.activated.connect(lambda reason: self.show_window() if reason in
                (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
            self.tray.show()
            self.status.setToolTip('关闭窗口后在托盘继续采集；点击“退出”可停止。')

        self.restore_preferences()
        self.worker = Collector(self.days.currentData(), self.strength.value(), 30, self.fit_options())
        self.worker.updated.connect(self.update_state)
        self.worker.busy.connect(lambda busy: self.refresh.setEnabled(not busy and not self.closing))
        self.worker.failed.connect(self.show_failure)
        self.worker.finished.connect(self.worker_finished)
        self.days.currentIndexChanged.connect(self.changed)
        self.strength.valueChanged.connect(self.changed)
        self.usage_mode.currentIndexChanged.connect(self.mode_changed)
        self.fit_cycle.currentIndexChanged.connect(self.cycle_changed)
        self.auto_return.toggled.connect(self.return_changed)
        self.return_date.dateChanged.connect(self.changed)
        self.return_timer = QTimer(self)
        self.return_timer.setInterval(30000)
        self.return_timer.timeout.connect(self.return_if_due)
        self.return_timer.start()
        self.worker.start()

    def restore_preferences(self):
        path = RUNTIME/'preferences.json'
        if not path.exists():
            return
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
            if settings.get('days') in (14, 28, 42):
                self.days.setCurrentIndex(self.days.findData(settings['days']))
            if isinstance(settings.get('strength'), (int, float)) and .25 <= settings['strength'] <= 3:
                self.strength.setValue(settings['strength'])
            if settings.get('normalCycleDays') in (7, 14):
                self.normal_cycle_days = settings['normalCycleDays']
            mode = settings.get('mode', 'normal')
            saved_date = QDate.fromString(settings.get('holidayReturnDate', ''), Qt.DateFormat.ISODate)
            auto = bool(settings.get('autoReturn', False)) and mode == 'holiday' and saved_date.isValid()
            expired = mode == 'holiday' and auto and saved_date.isValid() and self.date_timestamp(saved_date) <= time.time()
            if expired:
                mode, auto = 'normal', False
            if saved_date.isValid() and not expired:
                self.return_date.setDate(saved_date)
            self.usage_mode.setCurrentIndex(self.usage_mode.findData('holiday' if mode == 'holiday' else 'normal'))
            self.auto_return.setChecked(auto)
            self.update_mode_controls()
            if expired:
                self.save_preferences()
        except (ValueError, OSError):
            logging.warning('Could not restore calculator preferences', exc_info=True)

    @staticmethod
    def date_timestamp(value):
        return datetime(value.year(), value.month(), value.day(), tzinfo=TZ).timestamp()

    def fit_options(self):
        return {'mode': self.usage_mode.currentData(), 'normalCycleDays': self.normal_cycle_days,
                'returnAt': self.date_timestamp(self.return_date.date())
                if self.usage_mode.currentData() == 'holiday' and self.auto_return.isChecked() else None}

    def update_mode_controls(self):
        holiday = self.usage_mode.currentData() == 'holiday'
        with QSignalBlocker(self.fit_cycle):
            self.fit_cycle.setCurrentIndex(self.fit_cycle.findData(1 if holiday else self.normal_cycle_days))
        self.fit_cycle.setEnabled(not holiday)
        self.slot_info.setText('每小时' if holiday else '每 3 小时')
        self.auto_return.setEnabled(holiday)
        self.return_date.setEnabled(holiday and self.auto_return.isChecked())
        today = datetime.fromtimestamp(time.time(), TZ).date()
        with QSignalBlocker(self.return_date):
            self.return_date.setMinimumDate(QDate(today.year, today.month, today.day).addDays(1))

    def mode_changed(self):
        if self.usage_mode.currentData() == 'normal':
            with QSignalBlocker(self.auto_return):
                self.auto_return.setChecked(False)
        self.update_mode_controls()
        self.changed()

    def cycle_changed(self):
        if self.usage_mode.currentData() == 'normal':
            self.normal_cycle_days = self.fit_cycle.currentData()
        self.changed()

    def return_changed(self):
        self.update_mode_controls()
        self.changed()

    def return_if_due(self):
        until = self.fit_options()['returnAt']
        if until is not None and until <= time.time():
            with QSignalBlocker(self.usage_mode), QSignalBlocker(self.auto_return):
                self.usage_mode.setCurrentIndex(self.usage_mode.findData('normal'))
                self.auto_return.setChecked(False)
            self.update_mode_controls()
            self.changed()

    def save_preferences(self):
        RUNTIME.mkdir(exist_ok=True)
        (RUNTIME/'preferences.json').write_text(json.dumps({'days': self.days.currentData(),
            'strength': self.strength.value(), 'normalCycleDays': self.normal_cycle_days,
            'mode': self.usage_mode.currentData(), 'autoReturn': self.auto_return.isChecked(),
            'holidayReturnDate': self.return_date.date().toString(Qt.DateFormat.ISODate)}), encoding='utf-8')

    def changed(self):
        self.save_preferences()
        self.first.setText('正在重算…')
        self.update_totals(None)
        self.request()

    def request(self, refresh=False):
        if not self.closing:
            self.worker.request(self.days.currentData(), self.strength.value(), refresh, self.fit_options())

    def update_state(self, payload):
        self.last_payload = payload
        self.return_if_due()
        state = payload['state']
        account, model = state.get('account'), state.get('model')
        same_fit = payload['settings'][2] == self.fit_options()
        if not same_fit:
            model = None
        if account:
            self.quota.setValue(round(account['remaining']*10))
            self.quota.setFormat(number(account['remaining'])+'%')
            self.next_refresh.setText(date(account['nextRefresh'])+'（北京时间）')
            expiries = '、'.join(date(c['expiresAt']) if c['expiresAt'] else '到期时间未知' for c in account['credits'])
            self.reset_count.setText(str(account['availableCount'] if account['availableCount'] is not None else '未知')+' 次')
            self.expiries.setText(('到期 '+expiries) if expiries else '无可用重置')
            if self.tray:
                self.tray.setToolTip('Banked reset：周额度剩余 '+number(account['remaining'])+'%')
        if model:
            holiday = model['mode'] == 'holiday'
            unit = '小时' if holiday else '天'
            self.weekly_chart.set_profiles(model['profiles'], unit)
            self.profiles.setHorizontalHeaderLabels(['时段' if holiday else '周期位置', f'耗用估计（百分点/{unit}）', f'有效观测（{unit}）'])
            fill_table(self.profiles, [[p['label'], number(p['rate']),
                number(p['observedHours'] if holiday else p['observedHours']/24)] for p in model['profiles']])
            fill_table(self.history, [[d['day'], number(d['used']), number(d['observedHours'])] for d in model['daily']])
            kind = '假期 · 每天循环，每小时拟合' if holiday else ('常规 · 双周周期，A周起于 '+model['cycleStart'] if model['cycleDays'] == 14 else '常规 · 每周七天独立拟合')
            self.fit_info.setText(model['periodStart']+' 至 '+model['periodEnd']+' · '+str(model['snapshotCount'])+' 条快照 · '+kind)
        else:
            self.weekly_chart.set_profiles([])
            unit = '小时' if self.usage_mode.currentData() == 'holiday' else '天'
            self.profiles.setHorizontalHeaderLabels(['时段' if unit == '小时' else '周期位置', f'耗用估计（百分点/{unit}）', f'有效观测（{unit}）'])
            self.profiles.setRowCount(0)
            self.history.setRowCount(0)
            self.fit_info.setText(str((state.get('modelError') if same_fit else None) or '正在拟合当前模式的用量习惯…'))
        updated = state.get('accountUpdatedAt')
        self.status.setText(('已更新 ' if state.get('accountFresh') else '上次有效用量 ')+date(updated, True)+' · 每 30 秒采集' if updated else '尚未取得账户用量')
        problem = ((payload.get('error') if payload['settings'] == [self.days.currentData(), self.strength.value(), self.fit_options()] else None)
                   or state.get('accountError') or state.get('historyError') or (state.get('modelError') if same_fit else None))
        if not problem and account:
            if not account['resetDetailsKnown']:
                problem = '尚未取得完整的重置到期信息，连接恢复后自动更新。'
            elif account['hasShortWindow']:
                problem = '当前另有短期额度限制，暂不生成完整安排。'
            elif account['nextRefresh'] <= account['capturedAt']:
                problem = '等待账户返回新的额度窗口。'
        self.error.setVisible(bool(problem))
        self.error.setText(str(problem or ''))
        if payload['settings'] != [self.days.currentData(), self.strength.value(), self.fit_options()]:
            return
        result = payload.get('result')
        self.update_totals(result)
        if result is None:
            self.first.setText('暂无法规划')
            self.first_note.setText('状态说明见下方提示')
            self.forecast_chart.set_result(None)
            self.gain.setText('—')
            self.met.setText('—')
            self.naturals.clear()
            self.plan.setRowCount(0)
            return
        first = result['optimal']['schedule'][0] if result['optimal']['schedule'] else None
        self.first.setText(date(first['time']) if first else '暂不需要')
        self.first_note.setText('届时预计剩余 '+number(first['before'])+'%' if first else '期内使用需求无需额外重置')
        self.forecast_chart.set_result(result)
        self.gain.setText('相对不重置多用 '+points((result['optimal']['served']-result['baseline'])/100, signed=True)+' 份 · 重置 '+str(result['optimal']['count'])+' 次')
        met = 100*result['optimal']['served']/result['demand'] if result['demand'] else 100
        baseline = 100*result['baseline']/result['demand'] if result['demand'] else 100
        self.met.setText('需求覆盖 '+number(met)+'%；不重置 '+number(baseline)+'%')
        fill_table(self.plan, [[date(step['time']), number(step['before'])+'%', date(step['expiry'])] for step in result['optimal']['schedule']])
        natural = [date(e['time']) for e in result['events'] if e['type'] == 'natural']
        self.naturals.setText('本方案期内自然刷新：'+('、'.join(natural) if natural else '无'))

    def update_totals(self, result):
        if result is None:
            self.period_total.setText('—')
            self.regular_total.setText('—')
            self.allowance_surplus.setText('—')
            return
        total = result['optimal']['served']/100
        regular = self.days.currentData()/7
        self.period_total.setText(points(total)+' 份')
        self.regular_total.setText(number(regular)+' 份')
        self.allowance_surplus.setText(points(total-regular, signed=True)+' 份')

    def show_failure(self, message):
        self.error.setText(message)
        self.error.show()
        self.status.setText('采集已停止，请重新打开计算器。')
        self.update_totals(None)

    def show_window(self):
        if self.closing:
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.status.setText('正在停止采集并退出…')
        self.centralWidget().setEnabled(False)
        self.worker.finish()
        if not self.worker.isRunning():
            self.app.quit()

    def worker_finished(self):
        if self.closing:
            self.app.quit()

    def closeEvent(self, event):
        if self.closing:
            if self.worker.isRunning():
                event.ignore()
            else:
                event.accept()
            return
        event.ignore()
        if self.tray:
            self.hide()
        else:
            self.quit()

    def summary(self):
        payload = self.last_payload
        state = payload['state'] if payload else {}
        account = state.get('account') or {}
        return {'app': SERVER_NAME, 'pid': os.getpid(), 'loading': payload is None,
                'revision': state.get('revision', 0), 'accountFresh': state.get('accountFresh', False),
                'remaining': account.get('remaining'), 'resets': account.get('availableCount'),
                'dailyRates': (state.get('model') or {}).get('dailyRates'),
                'settings': [self.days.currentData(), self.strength.value()],
                'resultReady': bool(payload and payload.get('result')), 'visible': self.isVisible(),
                'closing': self.closing}


def communicate(command):
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(800):
        return None
    socket.write((command+'\n').encode())
    socket.flush()
    if not socket.waitForReadyRead(2500):
        socket.abort()
        raise TimeoutError('桌面计算器暂未响应。')
    response = bytes(socket.readLine()).decode('utf-8')
    socket.disconnectFromServer()
    return json.loads(response)


def wait_for_exit(pid):
    deadline = time.monotonic()+35
    while time.monotonic() < deadline:
        response = communicate('status')
        if response is None or response['pid'] != pid:
            return
        time.sleep(.15)
    raise TimeoutError('正在等待采集请求结束，请稍后再打开。')


def serve_commands(server, window):
    def connected():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.disconnected.connect(socket.deleteLater)

            def receive(client=socket):
                if not client.canReadLine():
                    return
                command = bytes(client.readLine()).decode('utf-8').strip()
                if command == 'show':
                    window.show_window()
                response = window.summary() if command in ('show', 'status', 'quit') else {'error': 'Unknown command'}
                client.write((json.dumps(response, ensure_ascii=False)+'\n').encode('utf-8'))
                client.flush()
                client.disconnectFromServer()
                if command == 'quit':
                    window.quit()

            socket.readyRead.connect(receive)
            if socket.bytesAvailable():
                receive()
    server.newConnection.connect(connected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--quit', action='store_true')
    parser.add_argument('--self-test', type=Path, metavar='REPORT_JSON', help='Check release resources without reading account data')
    args = parser.parse_args()
    if args.self_test:
        from release_check import check
        check(args.self_test)
        return 0
    if args.status or args.quit:
        application = QCoreApplication(sys.argv)
        response = communicate('quit' if args.quit else 'status')
        if response is None:
            print('桌面计算器未运行。')
            return 1
        if args.quit:
            wait_for_exit(response['pid'])
            response['stopped'] = True
        if sys.stdout is not None:
            print(json.dumps(response, ensure_ascii=False))
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=RUNTIME/'calculator.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    application = QApplication(sys.argv)
    application.setApplicationName('Banked reset')
    application.setQuitOnLastWindowClosed(False)
    existing = communicate('show')
    if existing is not None:
        if existing.get('closing'):
            wait_for_exit(existing['pid'])
        else:
            return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(SERVER_NAME):
        if communicate('show') is not None:
            return 0
        raise RuntimeError(server.errorString())
    window = Window(application)
    serve_commands(server, window)
    window.show()
    return application.exec()


if __name__ == '__main__':
    raise SystemExit(main())

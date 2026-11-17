"""Native desktop styling and data-driven charts."""
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

ASSETS = Path(__file__).parent/'assets'


def points(value, signed=False):
    value = 0. if abs(value) < 1e-6 else value
    digits = 4 if 0 < abs(value) < .01 else 2
    result = f'{value:.{digits}f}'.rstrip('0').rstrip('.')
    return ('+' if signed and value > 0 else '')+result


def theme(app):
    dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        return dict(bg='#101620', surface='#192230', alternate='#1e2939', text='#e9eef8',
                    muted='#a3b1c8', border='#303e53', accent='#9ab4ff', soft='#293c63',
                    button='#b7c9ff', button_text='#192849', amber='#dbb980',
                    demand='#98a6bd', success='#8bd5ba', danger='#ffb4b0', error='#422b33')
    return dict(bg='#f2f5fa', surface='#ffffff', alternate='#f7f9fd', text='#22304a',
                muted='#60708a', border='#dce3ee', accent='#4566cc', soft='#e9efff',
                button='#4566cc', button_text='#ffffff', amber='#a27630',
                demand='#7c889d', success='#247b61', danger='#a53639', error='#fff0f0')


def app_icon():
    return QIcon(str(ASSETS/'bank-reset.svg'))


def text(value='', name='', bold=False):
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    if bold:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    return label


def panel(name):
    frame = QFrame()
    frame.setObjectName(name)
    return frame


def table(headers, name):
    widget = QTableWidget(0, len(headers))
    widget.setObjectName(name)
    widget.setFrameShape(QFrame.Shape.NoFrame)
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(34)
    widget.horizontalHeader().setFixedHeight(32)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setShowGrid(False)
    widget.setAlternatingRowColors(False)
    return widget


def fill_table(widget, rows):
    widget.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter |
                (Qt.AlignmentFlag.AlignRight if column in (1, 2) else Qt.AlignmentFlag.AlignLeft))
            widget.setItem(row, column, item)


class QuotaGauge(QProgressBar):
    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.setRange(0, 1000)
        self.setFixedSize(90, 90)
        self.setAccessibleName('周额度剩余')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width()/108, self.height()/108)
        arc = QRectF(9, 9, 90, 90)
        painter.setPen(QPen(QColor(self.colors['border']), 7))
        painter.drawArc(arc, 0, 360*16)
        pen = QPen(QColor(self.colors['accent']), 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        fraction = max(0., self.value()/self.maximum())
        painter.drawArc(arc, 90*16, round(-360*16*fraction))
        font = QFont(self.font())
        font.setPixelSize(31)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self.colors['text']))
        painter.drawText(QRectF(5, 29, 98, 37), Qt.AlignmentFlag.AlignCenter,
                         self.text() if self.value() >= 0 else '—')
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(self.colors['muted']))
        painter.drawText(QRectF(8, 66, 92, 20), Qt.AlignmentFlag.AlignCenter, '本周剩余')


class ForecastChart(QWidget):
    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.result = None
        self.setMinimumHeight(155)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('规划期累计用量比较')

    def set_result(self, result):
        self.result = result
        self.setMinimumHeight(155 if result else 110)
        if result:
            self.setAccessibleDescription('预计需求 %.2f 份周额度，不使用重置 %.2f 份，推荐安排 %.2f 份。' %
                (result['demand']/100, result['baseline']/100, result['optimal']['served']/100))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        if not self.result:
            p.setPen(QColor(self.colors['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '连接用量后显示预测曲线')
            return
        width, height = self.width(), self.height()
        left, right, top, bottom = 36, 12, 46, 28
        plot_w, plot_h = width-left-right, height-top-bottom
        maximum = max(1., self.result['demand']/100)
        days = (self.result['end']-self.result['start'])/86400
        legend = [('预计需求', 'demand', Qt.PenStyle.DashLine),
                  ('自然刷新', 'amber', Qt.PenStyle.SolidLine),
                  ('推荐安排', 'accent', Qt.PenStyle.SolidLine)]
        x = left
        for label, color, style in legend:
            p.setPen(QPen(QColor(self.colors[color]), 2, style))
            p.drawLine(x, 10, x+16, 10)
            p.setPen(QColor(self.colors['muted']))
            p.drawText(x+22, 14, label)
            x += 22+p.fontMetrics().horizontalAdvance(label)+16
        p.drawText(left, 34, '份周额度')
        for tick in range(4):
            value = maximum*tick/3
            y = top+plot_h*(1-tick/3)
            p.setPen(QPen(QColor(self.colors['border']), 1))
            p.drawLine(left, round(y), width-right, round(y))
            p.setPen(QColor(self.colors['muted']))
            p.drawText(QRectF(0, y-8, left-7, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f'{value:.1f}'.rstrip('0').rstrip('.'))
        for tick in range(5):
            x = left+plot_w*tick/4
            p.drawText(QRectF(x-25, height-22, 50, 18), Qt.AlignmentFlag.AlignCenter, f'{days*tick/4:g} 天')
        for column, color, style in [(1, 'demand', Qt.PenStyle.DashLine),
                                     (2, 'amber', Qt.PenStyle.SolidLine),
                                     (3, 'accent', Qt.PenStyle.SolidLine)]:
            path = QPainterPath()
            for index, row in enumerate(self.result['rows']):
                x, y = left+plot_w*row[0]/days, top+plot_h*(1-row[column]/maximum)
                path.lineTo(x, y) if index else path.moveTo(x, y)
            pen = QPen(QColor(self.colors[color]), 2.4 if column == 3 else 1.8, style)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)


class MarginalChart(QWidget):
    point_selected = Signal(object)

    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.curve = None
        self.selected_index = None
        self.selected_usage = None
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('净收益随额外用量变化的折线图')

    def set_curve(self, curve):
        self.curve = curve
        self.selected_index = None
        self.setMinimumHeight(150 if curve and curve['available'] else 95)
        if curve and curve['available']:
            samples = curve['points']
            if self.selected_usage is None:
                index = max(range(len(samples)), key=lambda i: (round(samples[i]['totalGain'], 6), -samples[i]['usage']))
            else:
                index = min(range(len(samples)), key=lambda i: abs(samples[i]['usage']-self.selected_usage))
            self.select_point(index)
        else:
            self.setAccessibleDescription('当前没有可比较的用量。')
        self.update()

    def select_point(self, index):
        if index == self.selected_index:
            return
        self.selected_index = index
        sample = self.curve['points'][index]
        self.selected_usage = sample['usage']
        self.setAccessibleDescription('现在额外使用 '+points(sample['usage'])+'%，累计净收益 '+
                                      points(sample['totalGain'], True)+'%；均为满周限额的比例。左右方向键可切换用量。')
        self.point_selected.emit(sample)
        self.update()

    def plot_rect(self):
        return QRectF(53, 32, self.width()-69, self.height()-82)

    def select_at(self, position):
        if not self.curve or not self.curve['available'] or not self.plot_rect().contains(position):
            return
        plot = self.plot_rect()
        usage = (position.x()-plot.left())/plot.width()*self.curve['maxUsage']
        self.select_point(min(range(len(self.curve['points'])),
                              key=lambda i: abs(self.curve['points'][i]['usage']-usage)))

    def mouseMoveEvent(self, event):
        self.select_at(event.position())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.select_at(event.position())
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self.selected_index is not None:
            last = len(self.curve['points'])-1
            targets = {Qt.Key.Key_Left: max(0, self.selected_index-1),
                       Qt.Key.Key_Right: min(last, self.selected_index+1),
                       Qt.Key.Key_Home: 0, Qt.Key.Key_End: last}
            if event.key() in targets:
                self.select_point(targets[event.key()])
                return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        data = self.curve
        if not data or not data['available']:
            p.setPen(QColor(self.colors['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '当前额度已耗尽，刷新后可比较' if data else '取得有效用量后显示收益曲线')
            return
        plot = self.plot_rect()
        maximum = max(sample['totalGain'] for sample in data['points'])
        if maximum > 1e-6:
            step = 10**math.floor(math.log10(maximum))/2
            maximum = math.ceil(maximum/step)*step
        else:
            maximum = 1.
        x = lambda value: plot.left()+plot.width()*value/data['maxUsage'] if data['maxUsage'] else plot.left()
        y = lambda value: plot.bottom()-plot.height()*value/maximum
        p.setPen(QColor(self.colors['muted']))
        p.drawText(round(plot.left()), 14, '累计净收益（周限额 %）')
        p.drawText(QRectF(self.width()-133, 0, 117, 18), Qt.AlignmentFlag.AlignRight,
                   '后续变化归零' if data['limit'] == 'futureZero' else '当前余额上限')
        for tick in range(3):
            value = maximum*tick/2
            level = y(value)
            p.setPen(QPen(QColor(self.colors['border']), 1))
            p.drawLine(round(plot.left()), round(level), round(plot.right()), round(level))
            p.setPen(QColor(self.colors['muted']))
            p.drawText(QRectF(0, level-8, plot.left()-7, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, points(value)+'%')
        for tick in range(5 if data['maxUsage'] else 1):
            value = data['maxUsage']*tick/4
            p.drawText(QRectF(x(value)-27, plot.bottom()+5, 54, 18),
                       Qt.AlignmentFlag.AlignCenter, points(value)+'%')
        p.drawText(QRectF(plot.left(), self.height()-21, plot.width(), 18),
                   Qt.AlignmentFlag.AlignCenter, '现在额外用量（周限额 %）')
        if not data['maxUsage']:
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, '额外用量会减少后续可用量')
        path = QPainterPath()
        for index, sample in enumerate(data['points']):
            px, py = x(sample['usage']), y(sample['totalGain'])
            path.lineTo(px, py) if index else path.moveTo(px, py)
        area = QPainterPath(path)
        area.lineTo(x(data['maxUsage']), y(0))
        area.lineTo(x(0), y(0))
        area.closeSubpath()
        tint = QColor(self.colors['accent'])
        tint.setAlpha(24)
        p.fillPath(area, tint)
        pen = QPen(QColor(self.colors['accent']), 2.4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)
        sample = data['points'][self.selected_index]
        px, py = x(sample['usage']), y(sample['totalGain'])
        p.setPen(QPen(QColor(self.colors['muted']), 1, Qt.PenStyle.DashLine))
        p.drawLine(round(px), round(plot.top()), round(px), round(plot.bottom()))
        p.setPen(QPen(QColor(self.colors['surface']), 2))
        p.setBrush(QColor(self.colors['accent']))
        p.drawEllipse(QRectF(px-4.5, py-4.5, 9, 9))
        if self.hasFocus():
            p.setPen(QPen(QColor(self.colors['accent']), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)


class WeeklyChart(QWidget):
    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.profiles = []
        self.setMinimumHeight(150)
        self.setMaximumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('周一到周日的日耗柱状图')

    def set_profiles(self, profiles):
        self.profiles = profiles
        self.setAccessibleDescription('；'.join('周%s %.2f 个百分点/天' %
            ('一二三四五六日'[p['weekday']], p['dailyRate']) for p in profiles))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(12)
        p.setFont(font)
        if not self.profiles:
            p.setPen(QColor(self.colors['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '读取历史记录后显示每周节奏')
            return
        left, right, top, bottom = 36, 10, 47, 29
        w, h = self.width()-left-right, self.height()-top-bottom
        maximum = max(10, math.ceil(max(r['dailyRate'] for r in self.profiles)/10)*10)
        today = datetime.now(timezone(timedelta(hours=8))).weekday()
        p.setPen(QColor(self.colors['muted']))
        p.drawText(left, 15, '日耗估计 / 周额度百分点')
        p.drawText(QRectF(self.width()-120, 0, 110, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   '今天 · 周'+'一二三四五六日'[today])
        for tick in range(3):
            y = top+h*(1-tick/2)
            p.setPen(QPen(QColor(self.colors['border']), 1))
            p.drawLine(left, round(y), self.width()-right, round(y))
            p.setPen(QColor(self.colors['muted']))
            p.drawText(QRectF(0, y-8, left-8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       str(round(maximum*tick/2)))
        for index, profile in enumerate(self.profiles):
            x = left+w*(index+.5)/7
            bar_w = min(48, w/7*.48)
            bar_h = h*profile['dailyRate']/maximum
            color = QColor(self.colors['accent'])
            color.setAlpha(230 if profile['weekday'] == today else 155)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x-bar_w/2, top+h-bar_h, bar_w, bar_h), 4, 4)
            p.setPen(QColor(self.colors['text']))
            value = f"{profile['dailyRate']:.1f}".rstrip('0').rstrip('.')
            p.drawText(QRectF(x-35, top+h-bar_h-23, 70, 20), Qt.AlignmentFlag.AlignCenter, value)
            p.setPen(QColor(self.colors['accent'] if profile['weekday'] == today else self.colors['muted']))
            p.drawText(QRectF(x-35, self.height()-24, 70, 20), Qt.AlignmentFlag.AlignCenter,
                       '周'+'一二三四五六日'[profile['weekday']])


def build_interface(window):
    colors = theme(window.app)
    window.colors = colors
    window.setWindowTitle('Banked reset 计算器')
    window.setWindowIcon(app_icon())
    available = window.app.primaryScreen().availableGeometry()
    window.resize(min(930, available.width()-60), min(790, available.height()-65))
    window.setMinimumSize(820, 650)
    window.setStyleSheet('''
        QWidget { color: %(text)s; font-family: "Microsoft YaHei UI"; font-size: 13px; }
        QWidget#central { background: %(bg)s; }
        QLabel { background: transparent; }
        QLabel#title { font-size: 23px; font-weight: 600; }
        QLabel#caption, QLabel#status { color: %(muted)s; font-size: 12px; }
        QLabel#firstValue { font-size: 25px; font-weight: 600; color: %(accent)s; }
        QLabel#marginalValue { font-size: 30px; font-weight: 600; color: %(accent)s; }
        QLabel#metricValue { font-size: 14px; font-weight: 500; }
        QLabel#totalValue { font-size: 20px; font-weight: 600; }
        QLabel#section { font-size: 14px; font-weight: 600; }
        QFrame#accountPanel, QFrame#recommendationPanel, QFrame#chartPanel, QFrame#planPanel, QFrame#motivationPanel {
            background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px;
        }
        QPushButton { background: %(surface)s; border: 1px solid %(border)s; border-radius: 7px; padding: 7px 14px; }
        QPushButton:hover { background: %(soft)s; }
        QPushButton:focus, QComboBox:focus, QDoubleSpinBox:focus { border: 1px solid %(accent)s; }
        QPushButton#refresh { background: %(button)s; color: %(button_text)s; border: 1px solid %(button)s; font-weight: 500; }
        QPushButton:disabled { color: %(muted)s; background: %(alternate)s; border-color: %(border)s; }
        QPushButton#refresh:disabled { color: %(muted)s; background: %(alternate)s; border-color: %(border)s; }
        QPushButton#strengthStep { padding: 0; font-size: 18px; }
        QComboBox, QDoubleSpinBox { background: %(surface)s; border: 1px solid %(border)s; border-radius: 7px; padding: 5px 9px; min-height: 22px; }
        QComboBox QAbstractItemView { background: %(surface)s; selection-background-color: %(soft)s; }
        QTabWidget::pane { border: none; margin-top: 10px; }
        QTabBar::tab { background: transparent; color: %(muted)s; border-radius: 7px; padding: 8px 18px; margin-right: 5px; }
        QTabBar::tab:selected { background: %(soft)s; color: %(text)s; font-weight: 600; }
        QTabBar::tab:hover { color: %(text)s; }
        QTableWidget { background: %(surface)s; border: none; selection-background-color: %(soft)s; selection-color: %(text)s; }
        QTableWidget::item { padding: 5px 9px; border-bottom: 1px solid %(border)s; }
        QHeaderView::section { background: %(alternate)s; color: %(muted)s; border: none; padding: 7px 9px; font-weight: 400; }
        QTableCornerButton::section { background: %(surface)s; border: none; }
        QMenu { background: %(surface)s; border: 1px solid %(border)s; padding: 5px; }
        QMenu::item { padding: 7px 20px; }
        QMenu::item:selected { background: %(soft)s; }
        QLabel#errorBanner { background: %(error)s; color: %(danger)s; padding: 8px 12px; border-radius: 7px; }
    ''' % colors)
    container = QWidget()
    container.setObjectName('central')
    window.setCentralWidget(container)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(18, 14, 18, 12)
    layout.setSpacing(9)
    heading = QHBoxLayout()
    icon = QLabel()
    icon.setPixmap(app_icon().pixmap(32, 32))
    heading.addWidget(icon)
    brand = QVBoxLayout()
    brand.setSpacing(1)
    brand.addWidget(text('Banked reset', 'title'))
    heading.addLayout(brand, 1)
    window.refresh = QPushButton('立即更新')
    window.refresh.setObjectName('refresh')
    window.refresh.clicked.connect(lambda: window.request(refresh=True))
    heading.addWidget(window.refresh)
    layout.addLayout(heading)

    account = panel('accountPanel')
    account_layout = QHBoxLayout(account)
    account_layout.setContentsMargins(15, 8, 20, 8)
    account_layout.setSpacing(24)
    window.quota = QuotaGauge(colors)
    account_layout.addWidget(window.quota)
    facts = QVBoxLayout()
    facts.setSpacing(5)
    facts.addWidget(text('自然刷新', 'caption'))
    window.next_refresh = text('读取中…', 'metricValue')
    facts.addWidget(window.next_refresh)
    account_layout.addLayout(facts, 1)
    credits = QVBoxLayout()
    credits.setSpacing(5)
    credits.addWidget(text('可用重置', 'caption'))
    window.reset_count = text('—', 'metricValue')
    credits.addWidget(window.reset_count)
    window.expiries = text('正在读取到期时间', 'caption')
    credits.addWidget(window.expiries)
    account_layout.addLayout(credits, 2)
    layout.addWidget(account)

    controls = QHBoxLayout()
    controls.setSpacing(10)
    controls.addWidget(text('规划周期', 'caption'))
    window.days = QComboBox()
    for days in (14, 28, 42):
        window.days.addItem(f'{days} 天', days)
    window.days.setCurrentIndex(1)
    window.days.setAccessibleName('比较未来')
    controls.addWidget(window.days)
    controls.addSpacing(18)
    controls.addWidget(text('工作强度', 'caption'))
    window.strength = QDoubleSpinBox()
    window.strength.setRange(.25, 3)
    window.strength.setSingleStep(.05)
    window.strength.setDecimals(2)
    window.strength.setValue(1)
    window.strength.setSuffix(' 倍近期习惯')
    window.strength.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    window.strength.setAccessibleName('工作强度')
    controls.addWidget(window.strength)
    window.decrease_strength = QPushButton('−')
    window.increase_strength = QPushButton('+')
    for button, label in ((window.decrease_strength, '减少工作强度'),
                          (window.increase_strength, '增加工作强度')):
        button.setObjectName('strengthStep')
        button.setFixedSize(34, 34)
        button.setAccessibleName(label)
        button.setToolTip(label+'，每次 0.05 倍')
        controls.addWidget(button)
    window.decrease_strength.clicked.connect(window.strength.stepDown)
    window.increase_strength.clicked.connect(window.strength.stepUp)

    def update_strength_buttons(value):
        window.decrease_strength.setEnabled(value > window.strength.minimum())
        window.increase_strength.setEnabled(value < window.strength.maximum())

    window.strength.valueChanged.connect(update_strength_buttons)
    update_strength_buttons(window.strength.value())
    controls.addStretch()
    layout.addLayout(controls)

    window.tabs = QTabWidget()
    window.tabs.setAccessibleName('计算器视图')
    plan = QWidget()
    plan_layout = QVBoxLayout(plan)
    plan_layout.setContentsMargins(0, 0, 0, 0)
    plan_layout.setSpacing(10)
    recommendation = panel('recommendationPanel')
    summary = QHBoxLayout(recommendation)
    summary.setContentsMargins(18, 9, 18, 9)
    first = QVBoxLayout()
    first.setSpacing(3)
    first.addWidget(text('建议首次重置 · 估算', 'caption'))
    window.first = text('读取中…', 'firstValue')
    first.addWidget(window.first)
    window.first_note = text('正在连接用量并计算', 'caption')
    first.addWidget(window.first_note)
    summary.addLayout(first, 1)
    metrics = QVBoxLayout()
    metrics.setSpacing(7)
    totals = QHBoxLayout()
    totals.setSpacing(16)
    window.period_total = text('—', 'totalValue')
    window.regular_total = text('—', 'totalValue')
    window.allowance_surplus = text('—', 'totalValue')
    for title, value in [('预计可用总量', window.period_total),
                         ('常规完整周额度', window.regular_total),
                         ('超出常规额度', window.allowance_surplus)]:
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addWidget(text(title, 'caption'))
        value.setAccessibleName(title)
        column.addWidget(value)
        totals.addLayout(column, 1)
    window.period_total.setToolTip('按当前工作强度和最优重置安排，规划期内预计实际能用的总量；1 份等于一周满额。')
    window.regular_total.setToolTip('每 7 天按 1 份完整周额度计：14 / 28 / 42 天分别为 2 / 4 / 6 份。')
    window.allowance_surplus.setToolTip('预计可用总量减去规划天数 ÷ 7；低于常规完整周额度时显示负值。')
    metrics.addLayout(totals)
    details = QHBoxLayout()
    details.setSpacing(12)
    window.gain, window.met = text('—', 'caption'), text('—', 'caption')
    details.addWidget(window.gain, 1)
    details.addWidget(window.met, 1)
    metrics.addLayout(details)
    summary.addLayout(metrics, 2)
    plan_layout.addWidget(recommendation)
    middle = QHBoxLayout()
    middle.setSpacing(10)
    chart_panel = panel('chartPanel')
    chart_layout = QVBoxLayout(chart_panel)
    chart_layout.setContentsMargins(14, 12, 14, 9)
    chart_layout.setSpacing(4)
    chart_layout.addWidget(text('累计用量对比', 'section'))
    window.forecast_chart = ForecastChart(colors)
    chart_layout.addWidget(window.forecast_chart, 1)
    middle.addWidget(chart_panel, 3)
    schedule_panel = panel('planPanel')
    schedule_layout = QVBoxLayout(schedule_panel)
    schedule_layout.setContentsMargins(12, 12, 12, 10)
    schedule_layout.setSpacing(8)
    schedule_layout.addWidget(text('使用安排', 'section'))
    window.plan = table(['建议时间', '剩余', '到期'], 'planTable')
    window.plan.setAccessibleName('推荐使用安排')
    window.plan.verticalHeader().setDefaultSectionSize(32)
    window.plan.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    window.plan.setColumnWidth(1, 66)
    schedule_layout.addWidget(window.plan, 1)
    window.naturals = text('', 'caption')
    schedule_layout.addWidget(window.naturals)
    middle.addWidget(schedule_panel, 2)
    plan_layout.addLayout(middle, 1)
    window.comparison = text('', 'caption')
    plan_layout.addWidget(window.comparison)
    window.tabs.addTab(plan, '重置建议')

    motivation = QWidget()
    motivation_layout = QVBoxLayout(motivation)
    motivation_layout.setContentsMargins(0, 0, 0, 0)
    motivation_layout.setSpacing(10)
    motivation_hero = panel('motivationPanel')
    motivation_summary = QVBoxLayout(motivation_hero)
    motivation_summary.setContentsMargins(18, 11, 18, 11)
    motivation_summary.setSpacing(4)
    window.marginal_period = text('额外用量与累计净收益', 'caption')
    window.marginal_value = text('读取中…', 'marginalValue')
    window.marginal_note = text('固定后续用量习惯，比较使用前后的最优安排。', 'caption')
    motivation_summary.addWidget(window.marginal_period)
    motivation_summary.addWidget(window.marginal_value)
    motivation_summary.addWidget(window.marginal_note)
    motivation_layout.addWidget(motivation_hero)
    motivation_middle = QHBoxLayout()
    motivation_middle.setSpacing(10)
    breakdown = panel('chartPanel')
    breakdown_layout = QVBoxLayout(breakdown)
    breakdown_layout.setContentsMargins(14, 12, 14, 10)
    breakdown_layout.addWidget(text('净收益随额外用量变化', 'section'))
    window.marginal_chart = MarginalChart(colors)
    breakdown_layout.addWidget(window.marginal_chart, 1)
    motivation_middle.addWidget(breakdown, 3)
    comparison = panel('planPanel')
    comparison_layout = QVBoxLayout(comparison)
    comparison_layout.setContentsMargins(12, 12, 12, 10)
    comparison_layout.addWidget(text('选中用量的安排对比', 'section'))
    window.marginal_table = table(['对比', '当前计划', '选中用量后'], 'marginalTable')
    window.marginal_table.setAccessibleName('选中边际用量前后的计划比较')
    window.marginal_table.verticalHeader().setDefaultSectionSize(32)
    comparison_layout.addWidget(window.marginal_table, 1)
    motivation_middle.addWidget(comparison, 2)
    motivation_layout.addLayout(motivation_middle, 1)
    motivation_layout.addWidget(text('净收益＝现在额外使用＋调整后的后续可用量−当前计划后续可用量。假设额外用量现在完成，未来习惯固定。', 'caption'))
    window.tabs.addTab(motivation, '工作收益')

    profile = QWidget()
    profile_layout = QVBoxLayout(profile)
    profile_layout.setContentsMargins(0, 0, 0, 0)
    profile_layout.setSpacing(8)
    window.fit_info = text('周一到周日独立拟合，使用最近 28 天的记录。', 'caption')
    profile_layout.addWidget(window.fit_info)
    window.weekly_chart = WeeklyChart(colors)
    profile_layout.addWidget(window.weekly_chart, 1)
    window.profiles = table(['星期', '日耗估计（百分点）', '有效观测（天）'], 'profileTable')
    window.profiles.verticalHeader().setDefaultSectionSize(30)
    window.profiles.setAccessibleName('每周七天用量')
    profile_layout.addWidget(window.profiles, 1)
    profile_layout.addWidget(text('有效观测天数由可观测小时折算；耗尽后的时段不计为低需求。', 'caption'))
    window.tabs.addTab(profile, '每周习惯')

    history = QWidget()
    history_layout = QVBoxLayout(history)
    history_layout.setContentsMargins(0, 0, 0, 0)
    window.history_info = text('跨记录的消耗按间隔均摊；预测日期仅作估算。', 'caption')
    history_layout.addWidget(window.history_info)
    window.history = table(['日期', '消耗（百分点）', '可观测时长（小时）'], 'historyTable')
    history_layout.addWidget(window.history)
    window.tabs.addTab(history, '观测记录')
    layout.addWidget(window.tabs, 1)
    window.error = text('', 'errorBanner')
    window.error.hide()
    layout.addWidget(window.error)
    footer = QHBoxLayout()
    window.status = text('正在连接 Codex 用量…', 'status')
    footer.addWidget(window.status, 1)
    footer.addWidget(text('关闭窗口后留在托盘', 'caption'))
    exit_button = QPushButton('退出')
    exit_button.clicked.connect(window.quit)
    footer.addWidget(exit_button)
    layout.addLayout(footer)

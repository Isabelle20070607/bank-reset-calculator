"""Native desktop styling and data-driven charts."""
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
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
    def __init__(self, colors):
        super().__init__()
        self.colors = colors
        self.marginal = None
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('现在使用百分之一的收益拆分')

    def set_marginal(self, marginal):
        self.marginal = marginal
        self.setMinimumHeight(130 if marginal and marginal['available'] else 95)
        description = ''
        if marginal and marginal['available']:
            description = f"现在使用1%，后续变化{points(marginal['futureGain'], True)}%，累计净增{points(marginal['totalGain'], True)}%；均为满周限额的百分比。"
        self.setAccessibleDescription(description)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        data = self.marginal
        if not data or not data['available']:
            p.setPen(QColor(self.colors['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '剩余不足1%，暂无法比较' if data else '取得有效用量后显示收益拆分')
            return
        unit, total = data['unit'], data['totalGain']
        left, right, top, bottom = 44, 15, 36, 32
        w, h = self.width()-left-right, self.height()-top-bottom
        maximum = max(1., math.ceil(max(unit, total)*2)/2)
        y = lambda value: top+h*(1-value/maximum)
        p.setPen(QColor(self.colors['muted']))
        p.drawText(left, 14, '均为满周限额的百分比')
        for tick in range(3):
            value = maximum*tick/2
            level = y(value)
            p.setPen(QPen(QColor(self.colors['border']), 1))
            p.drawLine(left, round(level), self.width()-right, round(level))
            p.setPen(QColor(self.colors['muted']))
            p.drawText(QRectF(0, level-8, left-6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, points(value)+'%')
        spans = [(0, unit), (unit, total), (0, total)]
        labels = ['现在使用', '后续变化', '累计净增']
        values = [unit, data['futureGain'], total]
        bar_w = min(58, w/3*.48)
        centers = [left+w*(index+.5)/3 for index in range(3)]
        p.setPen(QPen(QColor(self.colors['muted']), 1, Qt.PenStyle.DashLine))
        for index, level in ((0, unit), (1, total)):
            p.drawLine(round(centers[index]+bar_w/2), round(y(level)),
                       round(centers[index+1]-bar_w/2), round(y(level)))
        for index, ((begin, end), label, value) in enumerate(zip(spans, labels, values)):
            color = 'amber' if index == 1 and value < -1e-6 else ('success' if index == 1 else 'accent')
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.colors[color]))
            high, low = y(max(begin, end)), y(min(begin, end))
            if abs(begin-end) < 1e-6:
                p.setPen(QPen(QColor(self.colors[color]), 2))
                p.drawLine(round(centers[index]-bar_w/2), round(high), round(centers[index]+bar_w/2), round(high))
            else:
                p.drawRoundedRect(QRectF(centers[index]-bar_w/2, high, bar_w, max(1., low-high)), 4, 4)
            p.setPen(QColor(self.colors['text']))
            p.drawText(QRectF(centers[index]-70, high-22, 140, 20),
                       Qt.AlignmentFlag.AlignCenter, points(value, True)+'%')
            p.setPen(QColor(self.colors['muted']))
            p.drawText(QRectF(centers[index]-60, self.height()-25, 120, 20),
                       Qt.AlignmentFlag.AlignCenter, label)


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
    window.strength.setAccessibleName('工作强度')
    controls.addWidget(window.strength)
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
    metrics = QFormLayout()
    metrics.setVerticalSpacing(9)
    window.gain, window.met = text('—', 'metricValue'), text('—', 'metricValue')
    metrics.addRow(text('额外可用', 'caption'), window.gain)
    metrics.addRow(text('需求覆盖', 'caption'), window.met)
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
    window.marginal_period = text('现在用掉周限额的 1% · 累计可用量净增估算', 'caption')
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
    breakdown_layout.addWidget(text('这 1% 的收益拆分', 'section'))
    window.marginal_chart = MarginalChart(colors)
    breakdown_layout.addWidget(window.marginal_chart, 1)
    motivation_middle.addWidget(breakdown, 3)
    comparison = panel('planPanel')
    comparison_layout = QVBoxLayout(comparison)
    comparison_layout.setContentsMargins(12, 12, 12, 10)
    comparison_layout.addWidget(text('前后安排对比', 'section'))
    window.marginal_table = table(['对比', '维持当前', '现在用1%'], 'marginalTable')
    window.marginal_table.setAccessibleName('使用周限额百分之一前后的计划比较')
    window.marginal_table.verticalHeader().setDefaultSectionSize(32)
    comparison_layout.addWidget(window.marginal_table, 1)
    motivation_middle.addWidget(comparison, 2)
    motivation_layout.addLayout(motivation_middle, 1)
    motivation_layout.addWidget(text('计入现在这 1%，并分别采用最优重置安排。后续用量习惯固定，仅适用于当前这一步。', 'caption'))
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

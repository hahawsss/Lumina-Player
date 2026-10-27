"""播放控制条 + 视频浮层。

mpv 用 --wid 内嵌时是一张原生子视图，会盖在同级 Qt 绘制之上，所以控制条不能当
视频的普通子控件，得放进一个跟随视频区几何的无边框 Qt.Tool 窗口里。
浮层自己吃掉鼠标事件：单击=播放/暂停，双击=全屏，拖动进度条=定位。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from . import theme
from .. import i18n

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


def fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class SeekBar(QWidget):
    seek_requested = Signal(float)       # 绝对秒
    hover_changed = Signal(float)        # 悬停位置，-1 表示离开

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._duration = 0.0
        self._position = 0.0
        self._buffered = 0.0
        self._hover = -1.0
        self._dragging = False
        self._drag_value = 0.0
        self._chapters: list[float] = []
        self.accent = QColor("#4fa8ff")
        self._down_pos = 0.0

    def set_colors(self, accent: str) -> None:
        self.accent = QColor(accent)
        self.update()

    def set_range(self, duration: float | None) -> None:
        self._duration = max(0.0, float(duration or 0.0))
        self.update()

    def set_position(self, position: float | None, buffered: float = 0.0) -> None:
        if self._dragging:
            return
        self._position = max(0.0, float(position or 0.0))
        self._buffered = max(0.0, float(buffered or 0.0))
        self.update()

    def set_chapters(self, chapters: list[float]) -> None:
        self._chapters = chapters
        self.update()

    def _value_at(self, x: int) -> float:
        if self.width() <= 0 or self._duration <= 0:
            return 0.0
        ratio = min(1.0, max(0.0, (x - 8) / max(1.0, self.width() - 16)))
        return ratio * self._duration

    def _x_at(self, seconds: float) -> float:
        if self._duration <= 0:
            return 8.0
        ratio = min(1.0, max(0.0, seconds / self._duration))
        return 8.0 + ratio * (self.width() - 16)

    def pixel_for(self, seconds: float) -> float:
        return self._x_at(seconds)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        track_y = self.height() / 2.0
        left, right = 8.0, self.width() - 8.0
        width = max(1.0, right - left)
        shown = self._drag_value if self._dragging else self._position

        def groove(color: QColor, height: float) -> None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(left, track_y - height / 2, width, height),
                                    height / 2, height / 2)

        groove(QColor(255, 255, 255, 46), 4.0)
        if self._duration > 0 and self._buffered > self._position:
            span = max(0.0, min(width, self._x_at(self._buffered) - left))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 88))
            painter.drawRoundedRect(QRectF(left, track_y - 2, span, 4.0), 2.0, 2.0)
        played_width = max(0.0, min(width, self._x_at(shown) - left))
        groove(self.accent, 4.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(left, track_y - 2, played_width, 4.0), 2.0, 2.0)

        for mark in self._chapters:
            x = self._x_at(mark)
            painter.setPen(QPen(QColor(255, 255, 255, 110), 1.0))
            painter.drawLine(QPointF(x, track_y - 4.5), QPointF(x, track_y + 4.5))

        knob_x = self._x_at(shown)
        radius = 6.5 if (self._dragging or self._hover >= 0) else 4.5
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(QPointF(knob_x, track_y), radius, radius)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        value = self._value_at(event.position().x())
        if self._dragging:
            self._drag_value = value
            self.hover_changed.emit(value)
        else:
            self._hover = value
            self.hover_changed.emit(value)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1.0
        self.hover_changed.emit(-1.0)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_value = self._value_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        self._dragging = False
        value = self._value_at(event.position().x())
        self.seek_requested.emit(value)
        self.hover_changed.emit(-1.0)
        self.update()


class ControlBar(QWidget):
    toggle_play = Signal()
    seek_absolute = Signal(float)
    seek_delta = Signal(float)
    speed_step = Signal(float)
    speed_picked = Signal(float)
    stop_clicked = Signal()
    volume_changed = Signal(int)
    mute_toggled = Signal()
    fullscreen_toggled = Signal()
    next_item = Signal()
    prev_item = Signal()
    open_tracks = Signal()
    open_subtitles = Signal()
    open_ai = Signal()
    open_settings = Signal()
    open_playlist = Signal()
    screenshot = Signal()

    def __init__(self, accent: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ControlBar")
        self.accent = accent
        self._drag_guard = False
        self._tips: list[tuple[QWidget, str]] = []
        self._volume_value = 100
        self._volume_muted = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 8, 16, 12)
        outer.setSpacing(4)

        panel = QWidget(self)
        panel.setObjectName("BarPanel")
        outer.addWidget(panel)
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.seek = SeekBar(panel)
        self.time_label = QLabel("--:-- / --:--", panel)
        self.time_label.setObjectName("SubText")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setMinimumWidth(128)
        top.addWidget(self.seek, 1)
        top.addWidget(self.time_label)
        box.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.play_button = self._button("play", "播放/暂停 (空格)", panel)
        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button = self._button("stop", "停止（回到打开界面）", panel)
        self.stop_button.clicked.connect(self.stop_clicked)
        self.prev_button = self._button("prev", "上一个 (← 双击)", panel)
        self.prev_button.clicked.connect(self.prev_item)
        self.next_button = self._button("next", "下一个", panel)
        self.next_button.clicked.connect(self.next_item)
        back = self._button("back10", "后退 10 秒 (←)", panel)
        back.clicked.connect(lambda: self.seek_delta.emit(-10))
        fwd = self._button("fwd30", "前进 30 秒 (→)", panel)
        fwd.clicked.connect(lambda: self.seek_delta.emit(30))
        for widget in (self.prev_button, self.play_button, self.stop_button,
                       self.next_button, back, fwd):
            row.addWidget(widget)

        self.speed_button = QToolButton(panel)
        self.speed_button.setText("1.00×")
        self._tip(self.speed_button, "播放速度；点击循环，长按选择具体倍速")
        self.speed_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.speed_button)
        for value in SPEEDS:
            action = menu.addAction(f"{value:g}×")
            action.triggered.connect(lambda _=False, v=value: self.speed_picked.emit(v))
        self.speed_button.setMenu(menu)
        self.speed_button.clicked.connect(lambda: self.speed_step.emit(0.25))
        row.addWidget(self.speed_button)

        row.addSpacing(10)
        self.mute_button = self._button("volume2", "静音 (M)", panel)
        self.mute_button.clicked.connect(self.mute_toggled)
        row.addWidget(self.mute_button)
        self.volume_slider = QSlider(Qt.Horizontal, panel)
        self.volume_slider.setRange(0, 160)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(96)
        self._tip(self.volume_slider, "音量 (↑↓)")
        self.volume_slider.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume_slider)
        self.volume_label = QLabel("100", panel)
        self.volume_label.setObjectName("ValueLabel")
        self.volume_label.setFixedWidth(30)
        row.addWidget(self.volume_label)

        row.addStretch(1)
        self.playlist_button = self._button("playlist", "播放列表 (L)", panel)
        self.playlist_button.clicked.connect(self.open_playlist)
        self.audio_button = self._button("wave", "音轨 (A)", panel)
        self.audio_button.clicked.connect(self.open_tracks)
        self.sub_button = self._button("subtitle", "字幕 (S)", panel)
        self.sub_button.clicked.connect(self.open_subtitles)
        self.ai_button = self._button("sparkle", "AI 字幕 (T)", panel)
        self.ai_button.clicked.connect(self.open_ai)
        shot = self._button("folder", "截图到工作目录", panel)
        shot.clicked.connect(self.screenshot)
        self.settings_button = self._button("sliders", "设置 (⌘,)", panel)
        self.settings_button.clicked.connect(self.open_settings)
        self.full_button = self._button("fullscreen", "全屏 (F)", panel)
        self.full_button.clicked.connect(self.fullscreen_toggled)
        for widget in (self.playlist_button, self.audio_button, self.sub_button,
                       self.ai_button, shot, self.settings_button, self.full_button):
            row.addWidget(widget)
        box.addLayout(row)

        self.seek.seek_requested.connect(self.seek_absolute)
        i18n.bind(self, self._apply_texts)

    def _tip(self, widget: QWidget, key: str) -> None:
        self._tips.append((widget, key))
        widget.setToolTip(i18n.t(key))

    def _apply_texts(self) -> None:
        for widget, key in self._tips:
            widget.setToolTip(i18n.t(key))
        self._refresh_volume_text()

    def _button(self, icon_name: str, tip: str, parent) -> QToolButton:
        button = QToolButton(parent)
        button.setIcon(theme.icon(icon_name, "#eaeef5", 22))
        button.setIconSize(QSize(22, 22))
        self._tip(button, tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoRaise(True)
        return button

    def set_paused(self, paused: bool) -> None:
        name = "play" if paused else "pause"
        self.play_button.setIcon(theme.icon(name, "#eaeef5", 22))

    def set_time(self, position, duration) -> None:
        self.time_label.setText(f"{fmt_time(position)} / {fmt_time(duration)}")

    def set_volume(self, value: int, muted: bool) -> None:
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(value))
        self.volume_slider.blockSignals(False)
        self._volume_value = int(value)
        self._volume_muted = bool(muted)
        self._refresh_volume_text()
        name = "mute" if muted or value == 0 else ("volume1" if value < 70 else "volume2")
        self.mute_button.setIcon(theme.icon(name, "#eaeef5", 22))

    def _refresh_volume_text(self) -> None:
        self.volume_label.setText(i18n.t("静音") if self._volume_muted
                                  else str(self._volume_value))

    def set_speed(self, value: float) -> None:
        self.speed_button.setText(f"{value:.2f}×")

    def _on_volume(self, value: int) -> None:
        self.volume_changed.emit(int(value))


class VideoOverlay(QWidget):
    """无边框透明浮层窗口：贴在视频区上，负责控制条、顶部信息和 OSD 提示。"""

    hide_timer_ms = 2600

    def __init__(self, main_window, accent: str, host: QWidget | None = None):
        # host 给了就是「子控件模式」：画面已经在自己窗口里，不需要独立浮层窗口
        self.host = host
        if host is None:
            super().__init__(main_window, Qt.Tool | Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        else:
            super().__init__(host)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QWidget(self)
        header.setObjectName("BarPanel")
        header.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 别让悬停检测在标题处断掉
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        self.title_label = QLabel("Lumina", header)
        self.title_label.setObjectName("TitleText")
        self.badge_label = QLabel("", header)
        self.badge_label.setObjectName("Badge")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.badge_label)

        self.osd_label = QLabel("", self)
        self.osd_label.setObjectName("ToastCard")
        self.osd_label.setAlignment(Qt.AlignCenter)
        self.osd_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.osd_label.hide()

        self.hover_label = QLabel("", self)
        self.hover_label.setObjectName("ToastCard")
        self.hover_label.setAlignment(Qt.AlignCenter)
        self.hover_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hover_label.hide()

        layout.addWidget(header, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addStretch(1)
        self.osd_holder = QWidget(self)
        osd_box = QHBoxLayout(self.osd_holder)
        osd_box.setContentsMargins(0, 0, 0, 0)
        osd_box.addStretch(1)
        osd_box.addWidget(self.osd_label)
        osd_box.addStretch(1)
        layout.addWidget(self.osd_holder)
        self.bar = ControlBar(accent, self)
        layout.addWidget(self.bar)

        self._osd_timer = QTimer(self)
        self._osd_timer.setSingleShot(True)
        self._osd_timer.timeout.connect(self.osd_label.hide)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self._autohide = True
        self._hovering = False
        self.bar.seek.hover_changed.connect(self._on_seek_hover)
        self.setMouseTracking(True)      # 不按键也能收到 mouseMove，鼠标一进来就出控制条
        self.setAcceptDrops(True)
        self._welcome: QWidget | None = None

    def show_welcome(self, widget: QWidget | None) -> None:
        if self._welcome is not None and self._welcome is not widget:
            self._welcome.hide()
        self._welcome = widget
        if widget is None:
            return
        widget.show()
        widget.raise_()
        self.place_welcome()

    def place_welcome(self) -> None:
        """首屏卡片居中。构造时浮层还没有真实尺寸，所以每次几何变化都要重新摆一次，
        否则它会停在 (0,0) 和标题挤在一起、还被按 sizeHint 之前的旧高度压扁。"""
        widget = self._welcome
        if widget is None:
            return
        widget.adjustSize()
        widget.move(max(0, (self.width() - widget.width()) // 2),
                    max(0, (self.height() - widget.height()) // 2))
        widget.raise_()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self.main_window.dropEvent(event)

    def sync_geometry(self, target: QWidget) -> None:
        if not target.isVisible():
            return
        if self.host is not None:
            self.setGeometry(self.host.rect())
            self.raise_()
        else:
            origin = target.mapToGlobal(QPoint(0, 0))
            self.setGeometry(QRect(origin, target.size()))
            self.bar.raise_()
        self.place_welcome()

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_badge(self, text: str) -> None:
        self.badge_label.setText(text)
        self.badge_label.setVisible(bool(text))

    def show_osd(self, text: str, ms: int = 1500) -> None:
        self.osd_label.setText(text)
        self.osd_label.adjustSize()
        self.osd_label.show()
        self.osd_label.raise_()
        self._osd_timer.start(ms)

    def _on_seek_hover(self, value: float) -> None:
        if value < 0:
            self.hover_label.hide()
            return
        self.hover_label.setText(fmt_time(value))
        self.hover_label.adjustSize()
        origin = self.bar.seek.mapTo(self, QPoint(0, 0))
        knob_x = origin.x() + self.bar.seek.pixel_for(value)
        size = self.hover_label.size()
        x = max(4, min(self.width() - size.width() - 4, int(knob_x - size.width() / 2)))
        self.hover_label.move(x, origin.y() - size.height() - 10)
        self.hover_label.show()
        self.hover_label.raise_()

    def _maybe_hide(self, ignore_pointer: bool = False) -> None:
        if not self._autohide:
            return
        if not ignore_pointer and (self._hovering or self.bar.underMouse()):
            return
        self.bar.setVisible(False)

    def poke(self) -> None:
        self.bar.setVisible(True)
        self._hide_timer.start(self.hide_timer_ms)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovering = True
        self.poke()
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.main_window.toggle_play()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.main_window.toggle_fullscreen()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._hovering = True
        self.poke()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovering = False
        self._hide_timer.start(400)

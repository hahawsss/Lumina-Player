"""外观：QSS 配色 + 代码画的矢量图标，不依赖任何外部图片资源。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QWidget


def build_qss(accent: str = "#4fa8ff") -> str:
    return f"""
* {{ font-family: "PingFang SC", "Helvetica Neue", sans-serif; font-size: 13px; }}
/* QFormLayout 自己造的标签没有 objectName，不兜底就吃系统默认调色板 —— 深色底上是纯黑 */
QLabel {{ color: #c3cbd8; }}
#Root, #CentralArea {{ background: #14161a; }}
#VideoHost {{ background: #000000; }}

#Sidebar {{ background: #1b1e24; border-left: 1px solid #262a32; }}
#SidebarTitle {{ color: #f2f4f8; font-size: 15px; font-weight: 600; padding: 14px 16px 6px 16px; }}
#SectionLabel {{ color: #8b93a3; font-size: 11px; font-weight: 600; letter-spacing: .6px;
                 padding: 12px 0 4px 0; }}
#HintLabel {{ color: #7d8696; font-size: 11.5px; }}
#ValueLabel {{ color: {accent}; font-size: 12px; font-weight: 600; }}
#Badge {{ color: #cfd6e2; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.10);
          border-radius: 7px; padding: 2px 8px; font-size: 11px; }}
#ToastCard {{ background: rgba(18,20,24,0.86); color: #ffffff; border: 1px solid rgba(255,255,255,0.12);
              border-radius: 10px; padding: 7px 14px; font-size: 13px; }}
#EmptyHint {{ color: #6f7887; font-size: 12px; }}

#ControlBar {{ background: transparent; }}
#BarPanel {{ background: rgba(20,22,27,0.82); border: 1px solid rgba(255,255,255,0.08);
             border-radius: 12px; }}
#TitleText {{ color: #f5f7fa; font-size: 13.5px; font-weight: 600; }}
#SubText {{ color: #a8b1c0; font-size: 11.5px; }}

QPushButton {{ background: transparent; border: none; border-radius: 8px; padding: 5px;
               color: #e8ecf3; }}
QPushButton:hover {{ background: rgba(255,255,255,0.12); }}
QPushButton:pressed {{ background: rgba(255,255,255,0.20); }}
QPushButton:checked {{ background: rgba(79,168,255,0.22); }}
QPushButton:disabled {{ color: #5c6472; }}
QPushButton[role="primary"] {{ background: {accent}; color: #0b0d10; font-weight: 700;
                               border-radius: 9px; padding: 7px 14px; }}
QPushButton[role="primary"]:hover {{ background: #6fbaff; }}
QPushButton[role="ghost"] {{ border: 1px solid rgba(255,255,255,0.14); border-radius: 9px;
                             padding: 7px 12px; color: #dbe2ec; }}
QPushButton[role="ghost"]:hover {{ background: rgba(255,255,255,0.10); }}

QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 4px;
               color: #e8ecf3; }}
QToolButton:hover {{ background: rgba(255,255,255,0.12); }}
QToolButton:checked {{ background: rgba(79,168,255,0.24); }}
QToolButton[role="chip"] {{ background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.12);
                            border-radius: 9px; padding: 6px 10px; color: #dbe2ec; }}
QToolButton[role="chip"]:checked {{ background: {accent}; color: #0b0d10; border-color: {accent};
                                    font-weight: 700; }}

QListWidget {{ background: #15181d; border: 1px solid #262a32; border-radius: 10px;
               color: #dfe4ec; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 7px 9px; border-radius: 7px; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.06); }}
QListWidget::item:selected {{ background: rgba(79,168,255,0.20); color: #ffffff; }}

QComboBox {{ background: #15181d; border: 1px solid #2b3038; border-radius: 9px;
             padding: 6px 10px; color: #e6ebf2; }}
QComboBox:hover {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: #1b1e24; border: 1px solid #2b3038; color: #e6ebf2;
                               selection-background-color: rgba(79,168,255,0.24); outline: none;
                               padding: 4px; }}

QLineEdit {{ background: #15181d; border: 1px solid #2b3038; border-radius: 9px;
             padding: 6px 9px; color: #eef2f7; selection-background-color: {accent}; }}
QLineEdit:focus {{ border-color: {accent}; }}

QCheckBox {{ color: #d7dde6; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px;
                        border: 1px solid #39404b; background: #15181d; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent};
                                image: none; }}

QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: rgba(255,255,255,0.16); }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 13px; height: 13px; margin: -5px 0; border-radius: 6px;
                              background: #ffffff; }}
QSlider::handle:horizontal:hover {{ background: {accent}; }}

QProgressBar {{ background: rgba(255,255,255,0.10); border: none; border-radius: 9px;
                min-height: 18px; text-align: center; color: #eaf0f8; font-size: 11px; }}
QProgressBar::chunk {{ border-radius: 9px; background: {accent}; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.30); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-width: 24px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: #97a1b1; padding: 8px 12px; border-radius: 8px; }}
QTabBar::tab:selected {{ background: rgba(79,168,255,0.18); color: #ffffff; }}
QTabBar::tab:hover {{ color: #ffffff; }}

QGroupBox {{ border: none; margin-top: 4px; }}
QToolTip {{ background: #23262d; color: #f0f3f8; border: 1px solid #343a44; padding: 5px 8px;
            border-radius: 6px; }}
/* QMessageBox 用的是系统浅色面板：全局那条 QPushButton（透明底 + 近白字）套上去
   就是白底白字，「确定/取消」看着像不存在。对话框这一块必须自己给底色和按钮样式。 */
QMessageBox, QDialog {{ background: #23262d; }}
QMessageBox QLabel, QDialog QLabel {{ color: #eef2f7; }}
QMessageBox QPushButton, QDialog QPushButton {{ background: rgba(255,255,255,0.12);
                                     border: 1px solid rgba(255,255,255,0.24);
                                     border-radius: 8px; padding: 6px 14px;
                                     color: #f5f8fc; min-width: 56px; }}
QMessageBox QPushButton:hover, QDialog QPushButton:hover {{ background: rgba(255,255,255,0.22); }}
/* 默认键只是「回车会按到」，不该抢主操作的视觉：只给它一圈更亮的描边 */
QMessageBox QPushButton:default, QDialog QPushButton:default {{
    border-color: rgba(255,255,255,0.55); }}
QMenu {{ background: #23262d; color: #eef2f7; border: 1px solid #343a44; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: rgba(79,168,255,0.28); color: #ffffff; }}
"""


def _prepare(painter: QPainter, color: str, width: float = 1.7) -> None:
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)


def _fill(painter: QPainter, color: str) -> None:
    """填色图形必须给 brush：只有 NoPen 而 brush 仍是 NoBrush 时，Qt 什么都不画。"""
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))


def _triangle(painter: QPainter, color: str, filled=True) -> None:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(6, 4), QPointF(6, 20), QPointF(19, 12)]))
    path.closeSubpath()
    if filled:
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, QColor(color))
    else:
        _prepare(painter, color)
        painter.drawPath(path)


def _two_triangles(painter, color, reverse=False) -> None:
    _fill(painter, color)
    for offset in (2, 11):
        poly = QPolygonF([
            QPointF(offset + 8, 5), QPointF(offset + 8, 19), QPointF(offset, 12)
        ]) if reverse else QPolygonF([
            QPointF(offset, 5), QPointF(offset, 19), QPointF(offset + 8, 12)
        ])
        painter.drawPolygon(poly)


def _skip(painter, color, forward=True) -> None:
    _two_triangles(painter, color, reverse=not forward)
    _fill(painter, color)
    bar = QRectF(20.5 if forward else 3.5, 5, 2, 14)
    painter.drawRoundedRect(bar, 1, 1)


def _speaker(painter, color, waves=2, cross=False) -> None:
    painter.setPen(Qt.NoPen)
    body = QPainterPath()
    body.moveTo(4, 10)
    body.lineTo(8, 10)
    body.lineTo(13, 6)
    body.lineTo(13, 18)
    body.lineTo(8, 14)
    body.lineTo(4, 14)
    body.closeSubpath()
    painter.fillPath(body, QColor(color))
    _prepare(painter, color, 1.5)
    if cross:
        painter.drawLine(QPointF(16.5, 9), QPointF(21.5, 15))
        painter.drawLine(QPointF(21.5, 9), QPointF(16.5, 15))
        return
    for index in range(waves):
        radius = 5.5 + index * 3.2
        rect = QRectF(13 - radius, 12 - radius, radius * 2, radius * 2)
        painter.drawArc(rect, -45 * 16, 90 * 16)


def _subtitle_icon(painter, color) -> None:
    _prepare(painter, color, 1.5)
    painter.drawRoundedRect(QRectF(3, 5, 21, 14), 3.5, 3.5)
    painter.drawLine(QPointF(7, 12.5), QPointF(12, 12.5))
    painter.drawLine(QPointF(15, 12.5), QPointF(20, 12.5))
    painter.drawLine(QPointF(7, 16), QPointF(17, 16))


def _sliders(painter, color) -> None:
    _prepare(painter, color, 1.6)
    for y, knob in ((8.5, 9.0), (12.5, 16.0), (16.5, 7.0)):
        painter.drawLine(QPointF(4, y), QPointF(20, y))
        _fill(painter, color)
        painter.drawEllipse(QPointF(knob, y), 2.3, 2.3)
        _prepare(painter, color, 1.6)


def _gauge(painter, color) -> None:
    _prepare(painter, color, 1.6)
    rect = QRectF(4, 6, 16, 16)
    painter.drawArc(rect, 200 * 16, 140 * 16)
    painter.drawLine(QPointF(12, 14), QPointF(16.5, 9.5))


def _sparkle(painter, color) -> None:
    painter.setPen(Qt.NoPen)
    def star(cx, cy, r):
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.cubicTo(cx + r * 0.18, cy - r * 0.18, cx + r * 0.18, cy - r * 0.18, cx + r, cy)
        path.cubicTo(cx + r * 0.18, cy + r * 0.18, cx + r * 0.18, cy + r * 0.18, cx, cy + r)
        path.cubicTo(cx - r * 0.18, cy + r * 0.18, cx - r * 0.18, cy + r * 0.18, cx - r, cy)
        path.cubicTo(cx - r * 0.18, cy - r * 0.18, cx - r * 0.18, cy - r * 0.18, cx, cy - r)
        painter.fillPath(path, QColor(color))
    star(11, 12, 7.0)
    star(19.5, 6, 3.2)


def _expand(painter, color, shrink=False) -> None:
    _prepare(painter, color, 1.8)
    painter.drawLine(QPointF(4, 9.5), QPointF(4, 4.5))
    painter.drawLine(QPointF(4, 4.5), QPointF(9.5, 4.5))
    painter.drawLine(QPointF(19.5, 4.5), QPointF(25, 4.5))
    painter.drawLine(QPointF(25, 4.5), QPointF(25, 9.5))
    painter.drawLine(QPointF(25, 18.5), QPointF(25, 23.5))
    painter.drawLine(QPointF(25, 23.5), QPointF(19.5, 23.5))
    painter.drawLine(QPointF(9.5, 23.5), QPointF(4, 23.5))
    painter.drawLine(QPointF(4, 23.5), QPointF(4, 18.5))


def _folder(painter, color) -> None:
    _prepare(painter, color, 1.6)
    path = QPainterPath()
    path.moveTo(4, 8)
    path.lineTo(9, 8)
    path.lineTo(11, 10.5)
    path.lineTo(21, 10.5)
    path.lineTo(21, 19)
    path.lineTo(4, 19)
    path.closeSubpath()
    painter.drawPath(path)


def _wave(painter, color) -> None:
    """音轨：一排不等高的竖条，避免和音量图标撞脸。"""
    _prepare(painter, color, 2.2)
    for x, half in ((5.5, 3.0), (9.5, 6.5), (13.5, 4.0), (17.5, 7.0), (21, 3.5)):
        painter.drawLine(QPointF(x, 12 - half), QPointF(x, 12 + half))


def _list_icon(painter, color) -> None:
    _prepare(painter, color, 1.7)
    for y in (7.0, 12.0):
        painter.drawLine(QPointF(4, y), QPointF(13, y))
    painter.drawLine(QPointF(4, 17), QPointF(9, 17))
    _fill(painter, color)
    painter.drawPolygon(QPolygonF([QPointF(15.5, 8.5), QPointF(15.5, 15.5), QPointF(21.5, 12)]))


def _close(painter, color) -> None:
    _prepare(painter, color, 1.9)
    painter.drawLine(QPointF(6, 6), QPointF(18, 18))
    painter.drawLine(QPointF(18, 6), QPointF(6, 18))


def _download(painter, color) -> None:
    _prepare(painter, color, 1.7)
    painter.drawLine(QPointF(12, 4), QPointF(12, 15))
    painter.drawLine(QPointF(6.5, 10), QPointF(12, 15.5))
    painter.drawLine(QPointF(17.5, 10), QPointF(12, 15.5))
    painter.drawLine(QPointF(5, 20), QPointF(19, 20))


_SHAPES = {
    "play": lambda p, c: _triangle(p, c),
    "pause": lambda p, c: _pause(p, c),
    "stop": lambda p, c: _stop_shape(p, c),
    "prev": lambda p, c: _skip(p, c, forward=False),
    "next": lambda p, c: _skip(p, c, forward=True),
    "back10": lambda p, c: _round_arrow(p, c, left=True),
    "fwd30": lambda p, c: _round_arrow(p, c, left=False),
    "volume2": lambda p, c: _speaker(p, c, waves=2),
    "volume1": lambda p, c: _speaker(p, c, waves=1),
    "mute": lambda p, c: _speaker(p, c, cross=True),
    "subtitle": _subtitle_icon,
    "wave": _wave,
    "sliders": _sliders,
    "gauge": _gauge,
    "sparkle": _sparkle,
    "fullscreen": _expand,
    "folder": _folder,
    "playlist": _list_icon,
    "close": _close,
    "download": _download,
}


def _pause(painter, color) -> None:
    _fill(painter, color)
    painter.drawRoundedRect(QRectF(7, 5, 3.4, 14), 1.6, 1.6)
    painter.drawRoundedRect(QRectF(15.6, 5, 3.4, 14), 1.6, 1.6)


def _stop_shape(painter, color) -> None:
    _fill(painter, color)
    painter.drawRoundedRect(QRectF(6.5, 6.5, 11.0, 11.0), 2.0, 2.0)


def _round_arrow(painter, color, left=True) -> None:
    _prepare(painter, color, 1.8)
    rect = QRectF(6, 6, 12, 12)
    start, span = (60 * 16, -300 * 16) if left else (120 * 16, 300 * 16)
    painter.drawArc(rect, start, span)
    tip_x = 6.0 if left else 18.0
    painter.drawLine(QPointF(tip_x + (1 if left else -1), 7.5), QPointF(tip_x + (4.5 if left else -4.5), 6.5))
    painter.drawLine(QPointF(tip_x + (1 if left else -1), 7.5), QPointF(tip_x + (2.0 if left else -2.0), 11.2))


def icon(name: str, color: str = "#e9edf4", size: int = 24, ratio: float = 2.0) -> QIcon:
    image = QImage(int(size * ratio), int(size * ratio), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(ratio * size / 24.0, ratio * size / 24.0)
    drawer = _SHAPES.get(name)
    if drawer:
        drawer(painter, color)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def set_role(widget: QWidget, role: str) -> QWidget:
    """QSS 的 [role="..."] 选择器要在 polish 前就位，否则不生效。"""
    widget.setProperty("role", role)
    widget.style().polish(widget)
    return widget


def colored_icon(name: str, accent: str = "#4fa8ff", active_accent: str | None = None):
    """给一个按钮做「常态灰 / 选中蓝」两态图标，选中时不会还是白的。"""
    normal = icon(name, "#e9edf4")
    if active_accent:
        pix = icon(name, active_accent).pixmap(24, 24)
        normal.addPixmap(pix, QIcon.Selected)
        normal.addPixmap(pix, QIcon.Active)
    return normal

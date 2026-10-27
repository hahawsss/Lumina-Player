"""侧栏面板：音轨/字幕/默认语言、AI 字幕、设置、播放列表。

面板直接持有 engine 与 pipeline，UI 只做「读状态 → 发指令 → 回显」。
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .. import asr, cloud, config as cfg, doctor, i18n, langtags, naming, storage, translate

FONTS = ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Songti SC",
         "Helvetica Neue", "Arial Unicode MS", "Noto Sans CJK SC"]

DISPLAY_MODES = [("original", "只看原文"), ("bilingual", "双语"), ("translated", "只看译文")]
DISPLAY_LABELS = dict(DISPLAY_MODES)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea { background: transparent; }"
                       "QScrollArea > QWidget > QWidget { background: transparent; }")
    area.setWidget(widget)
    return area


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label


class _Texts:
    """把「控件 + 中文原文」记成一本账，切语言时统一重刷。

    面板里的静态文案一律走这里：直接 QLabel("中文") 的话，切语言时没人知道该改哪。
    """

    def __init__(self, owner: QWidget):
        self._items: list[tuple[QWidget, str, str]] = []     # (控件, 方法名, 中文键)
        self._custom: list[tuple[QComboBox, int, str, tuple]] = []   # 带参数的下拉项
        self._owner = owner
        i18n.bind(owner, self.apply)

    def label(self, widget: QWidget, key: str, method: str = "setText") -> QWidget:
        self._items.append((widget, method, key))
        getattr(widget, method)(i18n.t(key))
        return widget

    def section(self, key: str) -> QLabel:
        label = _section(i18n.t(key))
        self._items.append((label, "section", key))
        return label

    def hint(self, key: str) -> QLabel:
        label = _hint(i18n.t(key))
        self._items.append((label, "setText", key))
        return label

    def new_label(self, key: str, parent: QWidget) -> QLabel:
        return self.label(QLabel(parent), key)      # type: ignore[return-value]

    def caption(self, key: str, widget: QWidget, box: QVBoxLayout) -> QWidget:
        label = self.new_label(key, None)
        label.setObjectName("HintLabel")
        box.addWidget(label)
        box.addWidget(widget)
        return widget

    def combo_item(self, combo: QComboBox, index: int, key: str) -> None:
        self._items.append((combo, f"__item{index}", key))
        combo.setItemText(index, i18n.t(key))

    def combo_item_fmt(self, combo: QComboBox, index: int, key: str, *args) -> None:
        self._custom.append((combo, index, key, args))
        combo.setItemText(index, i18n.t(key, *args))

    def tip(self, widget: QWidget, key: str) -> None:
        self._items.append((widget, "setToolTip", key))
        widget.setToolTip(i18n.t(key))

    def apply(self) -> None:
        for widget, method, key in self._items:
            try:
                if method.startswith("__item"):
                    widget.setItemText(int(method[6:]), i18n.t(key))
                elif method == "section":
                    widget.setText(i18n.t(key).upper())
                else:
                    getattr(widget, method)(i18n.t(key))
            except RuntimeError:        # 控件已销毁
                continue
        for combo, index, key, args in self._custom:
            try:
                combo.setItemText(index, i18n.t(key, *args))
            except RuntimeError:
                continue



def _combo(title: str, box: QVBoxLayout, parent: QWidget,
           texts: "_Texts | None" = None) -> QComboBox:
    """「标题 + 一个下拉」的标准一行。必须限制 sizeHint：侧栏只有 ~320 可用，
    长轨道名/模型名会把整块内容顶出去，左右两边的控件一起被裁。"""
    box.addWidget(texts.section(title) if texts is not None else _section(title))
    combo = QComboBox(parent)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(18)
    box.addWidget(combo)
    return combo


def _fill_llm_combo(combo: QComboBox, current: str) -> None:
    """GGUF 下拉只列目录里的型号名：整串说明有 40 个字符会被截断，权衡说明挂 tooltip。"""
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(18)
    combo.blockSignals(True)
    combo.clear()
    for filename, (label, note, _path) in translate.LLM_MODELS.items():
        combo.addItem(label, filename)
        combo.setItemData(combo.count() - 1, note, Qt.ToolTipRole)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.blockSignals(False)


def _captioned(title: str, widget: QWidget, box: QVBoxLayout) -> QWidget:
    """小标题在上、控件在下。QFormLayout 的左右两列会把字段挤得很窄（侧栏只有 348pt），
    所以输入框这类不用表单列。"""
    label = QLabel(title)
    label.setObjectName("HintLabel")
    box.addWidget(label)
    box.addWidget(widget)
    return widget


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("HintLabel")
    label.setWordWrap(True)
    return label


def _tiny_progress(parent: QWidget) -> QProgressBar:
    """下载专用的就地进度条：默认藏起来，别和转写进度条抢位置、也别让人误会。"""
    bar = QProgressBar(parent)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.hide()
    return bar


def track_row(track: dict) -> str:
    parts = []
    lang = track.get("lang")
    name = naming.lang_name(langtags.language_name(lang)) if lang else ""
    if name:
        parts.append(name)
    title = str(track.get("title") or "").strip()
    if title and title != name and title.lower() != str(lang or "").lower():
        parts.append(title[:34])
    if track.get("codec"):
        parts.append(str(track["codec"]).upper())
    extra = []
    if track.get("external"):
        extra.append(i18n.t("外挂"))
    if track.get("default"):
        extra.append(i18n.t("容器默认"))
    if extra:
        parts.append("/".join(extra))
    if not parts:
        parts.append(i18n.t("轨道 {}", track.get("id")))
    return " · ".join(parts)


# 侧栏固定宽度；台账行的宽度预算都从这里推
SIDEBAR_WIDTH = 348

# 页面名 -> 标签索引；115 浏览与 OpenList 管理合并在「网盘」里，用分段开关切
PAGE_INDEX = {"tracks": 0, "ai": 1, "settings": 2, "playlist": 3,
              "storage": 4, "cloud": 5, "115": 5, "cloud_manage": 5}
CLOUD_SUBPAGE = {"115": 0, "cloud": 0, "cloud_manage": 1}


class _ElidedLabel(QLabel):
    """宽度不够时中间省略。QLabel 默认只会把尾巴裁掉，长路径看不出还有下文。"""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = str(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(56)
        self._repaint_text()

    def setText(self, text) -> None:  # noqa: N802
        self._full = str(text)
        self._repaint_text()

    def full_text(self) -> str:
        """省略号只是显示用的；断言与提示都要看原文。"""
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._repaint_text()

    def _repaint_text(self) -> None:
        width = self.width() - 2
        shown = self._full
        if width > 0:
            shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, width)
        QLabel.setText(self, shown)


def _mini_button(text: str, handler, role: str = "ghost") -> QPushButton:
    button = QPushButton(text)
    theme.set_role(button, role)
    button.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
    button.clicked.connect(handler)
    return button


def _confirm_box(parent, title: str, body: str, ok_text: str | None = None) -> QMessageBox:
    """确认框单独造出来是为了能被测试量一眼（按钮文字、默认键、看不看得见）。

    主操作（确定删除）用主色填充，回车键却挂在「取消」上：视觉引导和误触保护分开。
    """
    box = QMessageBox(QMessageBox.Question, title, body,
                      QMessageBox.Yes | QMessageBox.No, parent)
    yes = box.button(QMessageBox.Yes)
    no = box.button(QMessageBox.No)
    yes.setText(ok_text or i18n.t("确定"))
    no.setText(i18n.t("取消"))
    theme.set_role(yes, "primary")
    box.setDefaultButton(no)
    return box


def _confirm(parent, title: str, body: str, ok_text: str | None = None) -> bool:
    return _confirm_box(parent, title, body, ok_text).exec() == QMessageBox.Yes


def _warn(parent, title: str, body: str) -> None:
    box = QMessageBox(QMessageBox.Warning, title, body, QMessageBox.Ok, parent)
    box.button(QMessageBox.Ok).setText(i18n.t("知道了"))
    box.exec()


def _path_label(path, parent: QWidget) -> QLabel:
    label = _ElidedLabel(storage.display_path(path), parent)
    label.setObjectName("HintLabel")
    label.setToolTip(str(path))
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class SubtitleLibrary(QDialog):
    """按片列出生成过的字幕文件夹，勾选后删除。可选范围只有输出根下面那一层。"""

    def __init__(self, root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.root = Path(root)
        self.setWindowTitle(i18n.t("清理生成的字幕"))
        self.resize(430, 380)
        box = QVBoxLayout(self)
        box.addWidget(_hint(str(self.root)))
        self.list = QListWidget(self)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        box.addWidget(self.list)
        self.reload()
        row = QHBoxLayout()
        remove = QPushButton(i18n.t("删除选中"))
        theme.set_role(remove, "ghost")
        remove.clicked.connect(self._remove)
        close = QPushButton(i18n.t("关闭"))
        theme.set_role(close, "ghost")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(remove)
        row.addWidget(close)
        box.addLayout(row)

    def reload(self) -> None:
        self.list.clear()
        if not self.root.is_dir():
            return
        import datetime
        for folder in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            files = [f for f in folder.iterdir() if f.is_file()]
            if not files:
                continue
            size = sum(f.stat().st_size for f in files)
            when = datetime.datetime.fromtimestamp(
                max(f.stat().st_mtime for f in files)).strftime("%m-%d %H:%M")
            item = QListWidgetItem(i18n.t("{}　　{} 个文件 · {} · {}", folder.name,
                                          len(files), storage.human_size(size), when))
            item.setData(Qt.UserRole, str(folder))
            self.list.addItem(item)

    def _remove(self) -> None:
        targets = [item.data(Qt.UserRole) for item in self.list.selectedItems()]
        if not targets:
            return
        if not _confirm(self, i18n.t("确认删除"),
                        i18n.t("删掉这 {} 个文件夹里的字幕？视频本身不动，删了可以重新转写。\n\n{}",
                               len(targets),
                               "\n".join(Path(t).name for t in targets[:6])),
                        i18n.t("确定删除")):
            return
        import shutil
        for path in targets:
            folder = Path(path)
            if folder.parent != self.root:        # 只认根目录下那一层，别顺着链接跑出去
                continue
            shutil.rmtree(folder, ignore_errors=True)
        self.reload()


class LanguagePreference(QWidget):
    """默认语言偏好：顺序即优先级，直接写进 config 并同步给 mpv 的 alang/slang。"""

    changed = Signal()

    def __init__(self, title: str, key: str, config: cfg.Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.key = key
        self._texts = _Texts(self)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(self._texts.section(title))
        self.list = QListWidget(self)
        self.list.setMinimumHeight(96)
        box.addWidget(self.list)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.combo = QComboBox(self)
        # 不收成最小内容宽度的话，这一行（下拉 + 4 个按钮）会把整个面板撑到 326+，越界被裁
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(6)
        for code, name in cfg.LANGUAGE_CHOICES:
            # 语言名用各语言的自称（日本語 / Français），这是语言选择器的通行做法，不随界面翻译
            self.combo.addItem(name, code)
        add = QToolButton(self)
        self._texts.label(add, "添加")
        add.clicked.connect(self._add)
        remove = QToolButton(self)
        self._texts.label(remove, "移除")
        remove.clicked.connect(self._remove)
        up = QToolButton(self)
        up.setText("↑")
        up.clicked.connect(lambda: self._move(-1))
        down = QToolButton(self)
        down.setText("↓")
        down.clicked.connect(lambda: self._move(1))
        for widget in (self.combo, add, remove, up, down):
            row.addWidget(widget)
        box.addLayout(row)
        box.addWidget(self._texts.hint("越靠上越优先；写进 alang/slang 时自动展开 zh/zho/chi/cmn 等变体。"))
        self.reload()
        i18n.bind(self, self.reload)      # 列表项里的语言名也要跟着换

    def reload(self) -> None:
        self.list.clear()
        for tag in (self.config.get(self.key) or []):
            name = naming.lang_name(langtags.language_name(tag))
            item = QListWidgetItem(f"{name}  ({tag})")
            item.setData(Qt.UserRole, tag)
            self.list.addItem(item)

    def value(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def _commit(self) -> None:
        self.config.set(self.key, self.value())
        self.changed.emit()

    def _add(self) -> None:
        code = self.combo.currentData()
        if any(langtags.same_lang(code, existing) for existing in self.value()):
            return
        self.list.addItem(QListWidgetItem(f"{langtags.language_name(code)}  ({code})"))
        self.list.item(self.list.count() - 1).setData(Qt.UserRole, code)
        self._commit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self._commit()

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._commit()


class DoctorCard(QWidget):
    """环境自检 + 一键补齐：缺 brew 公式时直接在应用里装。"""

    checked = Signal()
    polled = Signal(object)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._texts = _Texts(self)
        self.items: list = []
        self._checking = False
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        head = QHBoxLayout()
        title = self._texts.new_label("环境自检", self)
        title.setObjectName("SectionLabel")
        head.addWidget(title)
        head.addStretch(1)
        recheck = QToolButton(self)
        self._texts.label(recheck, "重新检测")
        theme.set_role(recheck, "ghost")
        recheck.clicked.connect(self.refresh)
        head.addWidget(recheck)
        box.addLayout(head)

        self.summary = _hint("")
        box.addWidget(self.summary)
        self.list_area = QVBoxLayout()
        self.list_area.setSpacing(3)
        box.addLayout(self.list_area)

        row = QHBoxLayout()
        self.install_button = QPushButton("")
        self._texts.label(self.install_button, "一键安装缺失组件")
        theme.set_role(self.install_button, "primary")
        self.install_button.clicked.connect(self._install)
        self.cancel_button = QPushButton("")
        self._texts.label(self.cancel_button, "取消")
        theme.set_role(self.cancel_button, "ghost")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        row.addWidget(self.install_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        box.addLayout(row)
        self.log = _hint("")
        self.log.setMaximumHeight(64)
        box.addWidget(self.log)

        self.installer = doctor.Installer(self)
        self.installer.line.connect(self._on_line)
        self.installer.done.connect(self._on_done)
        self.polled.connect(self._apply)
        i18n.bind(self, self.refresh)    # 体检结论是查出来的字符串：切语言要重查一遍

    def _clear_rows(self) -> None:
        while self.list_area.count():
            item = self.list_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            layout = item.layout()          # 每行是一个 QHBoxLayout，里面的标签也要删
            if layout is not None:
                while layout.count():
                    sub = layout.takeAt(0)
                    child = sub.widget()
                    if child is not None:
                        child.deleteLater()

    def refresh(self) -> None:
        """体检要跑子进程（whisper --help、security 查钥匙串），放后台做，
        否则启动那一下会把事件循环堵住，鼠标转圈。"""
        if getattr(self, "_checking", False):
            self._refresh_again = True      # 正在查的那一轮用的是旧语言，查完再补一轮
            return
        self._checking = True
        self._refresh_again = False
        self.summary.setText(i18n.t("检测中…"))

        def worker() -> None:
            # 信号跨线程排队回 GUI 线程；worker 里不许碰任何控件
            self.polled.emit(doctor.check(self.config))

        threading.Thread(target=worker, daemon=True, name="doctor-check").start()

    def _apply(self, items: list) -> None:
        self._checking = False
        self.items = items
        self._clear_rows()
        for item in items:
            row = QHBoxLayout()
            row.setSpacing(6)
            dot = QLabel("●" if item.ok else ("○" if not item.required else "✗"), self)
            dot.setStyleSheet(
                f"color: {'#7fdca0' if item.ok else ('#e0a94a' if not item.required else '#ff8a7d')};"
                "font-size: 12px;")
            dot.setFixedWidth(14)
            name = QLabel(item.label, self)
            name.setStyleSheet("color: #dfe4ec; font-size: 12.5px;")
            detail = QLabel(item.detail, self)
            detail.setObjectName("HintLabel")
            detail.setWordWrap(True)      # 不换行会把整个设置面板顶宽，右边被裁掉
            detail.setStyleSheet("color: #7d8696; font-size: 11px;")
            row.addWidget(dot)
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(detail)
            self.list_area.addLayout(row)
        self.summary.setText(doctor.summarize(items))
        installable = bool(self.installer.formulas(items))
        if getattr(self, "_refresh_again", False):
            self._refresh_again = False
            QTimer.singleShot(0, self.refresh)
        self.install_button.setVisible(installable and not self.installer.running())
        self.install_button.setEnabled(not self.installer.running())
        self.checked.emit()

    def _install(self) -> None:
        self.log.setText(i18n.t("开始安装…（首次可能要几分钟）"))
        self.cancel_button.show()
        self.install_button.hide()
        self.installer.start(self.items)

    def _cancel(self) -> None:
        self.installer.cancel()

    def _on_line(self, text: str) -> None:
        self.log.setText(text[-220:])

    def _on_done(self, ok: bool, message: str) -> None:
        self.log.setText(("✓ " if ok else "✗ ") + message)
        self.cancel_button.hide()
        QTimer.singleShot(0, self.refresh)


class TracksPanel(QWidget):
    """只放画面/声音/字幕的选择。翻译与转写都在 AI 栏，常规项在设置栏。"""

    subtitle_mode_changed = Signal(str)
    add_subtitle_file = Signal()

    def __init__(self, config, engine, pipeline, parent=None):
        super().__init__(parent)
        self.config = config
        self.engine = engine
        self.pipeline = pipeline
        self._texts = _Texts(self)
        self._menu_items: list[tuple[QAction, str]] = []
        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 20)
        box.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = self._texts.section("轨道")
        title.setContentsMargins(0, 0, 0, 0)
        head.addWidget(title, 1)
        more = QToolButton(body)
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        theme.set_role(more, "ghost")
        menu = QMenu(more)
        for key, slot in (("加载字幕文件…", self.add_subtitle_file.emit),
                          ("移除外挂字幕", self._remove_external)):
            action = menu.addAction(i18n.t(key), slot)
            self._menu_items.append((action, key))
        menu.addSeparator()
        action = menu.addAction(i18n.t("重新读取轨道"), self.reload)
        self._menu_items.append((action, "重新读取轨道"))
        more.setMenu(menu)
        head.addWidget(more)
        box.addLayout(head)

        self.video_combo = _combo("视频轨", box, body, self._texts)
        self.audio_combo = _combo("音轨", box, body, self._texts)
        self.sub_combo = _combo("字幕轨", box, body, self._texts)
        # 四个下拉一律听 activated（用户选的）而不是 currentIndexChanged（程序改的）：
        # reload() 每次回填都不该被当成一次「用户改了选择」，而用户重复选同一项时
        # currentIndexChanged 根本不发信号，想重试就没反应。
        self.video_combo.activated.connect(self._pick_video)
        self.audio_combo.activated.connect(self._pick_audio)
        self.sub_combo.activated.connect(self._pick_sub)
        self.mode_combo = _combo("显示方式", box, body, self._texts)
        for index, (value, label) in enumerate(DISPLAY_MODES):
            self.mode_combo.addItem(i18n.t(label), value)
            self._texts.combo_item(self.mode_combo, index, label)
        self.mode_combo.activated.connect(self._set_mode)
        box.addWidget(self._texts.hint("「只看原文」直接用内嵌/外挂轨，保留原样式；"
                                       "双语与译文由播放器生成一条新轨（在 AI 栏里做）。"))
        box.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))
        self.reload()
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        for action, key in self._menu_items:
            action.setText(i18n.t(key))
        self.reload()          # 轨名（语言名/外挂）与占位项跟着换

    def _fill(self, combo: QComboBox, tracks: list[dict], selected=None) -> None:
        """按 mpv 当前选中的轨复原：默认语言选出来的那条，面板上必须显示同一条。"""
        wanted = combo.currentData()
        combo.clear()
        for track in tracks:
            label = track_row(track)
            if track.get("external"):
                label = i18n.t("外挂 · {}").format(label)
            combo.addItem(label, track.get("id"))
        combo.setEnabled(combo.count() > 0)
        index = combo.findData(selected)
        if index < 0:
            index = combo.findData(wanted)
        combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1))

    def reload(self) -> None:
        self._fill(self.video_combo, self.engine.tracks_of("video"), self.engine.get("vid"))
        self._fill(self.audio_combo, self.engine.tracks_of("audio"), self.engine.get("aid"))
        if not self.engine.tracks_of("video"):
            self.video_combo.addItem(i18n.t("（没有视频轨）"))
        if not self.engine.tracks_of("audio"):
            self.audio_combo.addItem(i18n.t("（没有音轨）"))

        wanted = self.sub_combo.currentData()
        self.sub_combo.clear()
        self.sub_combo.addItem(i18n.t("关闭字幕"), "no")
        for track in self.engine.tracks_of("sub"):
            if self.pipeline.ai_pid is not None and track.get("id") == self.pipeline.ai_pid:
                continue        # 生成轨单独列一条，别和「外挂」重复
            label = track_row(track)
            if track.get("external"):
                label = i18n.t("外挂 · {}").format(label)
            self.sub_combo.addItem(label, track.get("id"))
        if self.pipeline.ai_pid is not None:
            self.sub_combo.addItem(i18n.t("AI/译文轨（本播放器生成）"), self.pipeline.ai_pid)
        sid = self.engine.get("sid")
        index = self.sub_combo.findData(sid if sid is not None else "no")
        if index < 0:
            index = self.sub_combo.findData(wanted) if wanted is not None else -1
        self.sub_combo.setCurrentIndex(index if index >= 0 else 0)

        mode_index = self.mode_combo.findData(self.pipeline.mode)
        if mode_index >= 0:
            self.mode_combo.setCurrentIndex(mode_index)

    def _pick_video(self) -> None:
        if self.video_combo.currentData() is None:
            return
        self.engine.select_track("video", self.video_combo.currentData())

    def _pick_audio(self) -> None:
        if self.audio_combo.currentData() is None:
            return
        self.engine.select_track("audio", self.audio_combo.currentData())

    def _pick_sub(self) -> None:
        track_id = self.sub_combo.currentData()
        if track_id is None:
            return
        if track_id == "no":
            self.engine.set("sid", "no")
            return
        self.engine.set("sid", track_id)
        if track_id == self.pipeline.ai_pid:
            # 生成轨是播放器写出来的，选它就该看见它，而不是停在原样式上
            self.engine.reload_subtitle()
            if self.pipeline.mode == "original":
                self.pipeline.mode = "translated"
        else:
            self.pipeline.native_sid = track_id
            if self.pipeline.mode == "original":
                self.engine.set("sub-visibility", True)

    def _set_mode(self) -> None:
        value = self.mode_combo.currentData()
        if not value:
            return
        self.pipeline.set_display(value)
        self.subtitle_mode_changed.emit(value)
        QTimer.singleShot(220, self.reload)

    def _remove_external(self) -> None:
        external = [t for t in self.engine.tracks_of("sub") if t.get("external")]
        if not external:
            self.pipeline.error.emit("没有外挂的字幕轨")
            return
        removed_ai = any(t.get("id") == self.pipeline.ai_pid for t in external)
        for track in external:
            self.engine.remove_subtitle(track.get("id"))
        if removed_ai:
            self.pipeline.stop_all()   # 别留下一个指向已删轨的 ai_pid
        self.pipeline.error.emit("")   # 让上层有机会清掉提示
        QTimer.singleShot(200, self.reload)


class AIPanel(QWidget):
    """语音转字幕 + 字幕翻译：凡是要模型、要接口、要下载的东西都在这一栏。"""

    request = Signal(bool)
    one_click_requested = Signal()
    translate_requested = Signal(int)
    # 字节数是 64 位：14B 模型 9 GB，Signal(int) 会在 2 GB 处溢出（进度 269%）
    download_tick = Signal(object, object)
    download_note = Signal(str)
    download_done = Signal(str)      # 空串 = 成功，否则是错误文本
    test_done = Signal(str)

    def __init__(self, config, engine, pipeline, parent=None):
        super().__init__(parent)
        self.config = config
        self.engine = engine
        self.pipeline = pipeline
        self._texts = _Texts(self)
        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 20)
        box.setSpacing(8)

        # ------------------------------------------------------------ 任务进度
        # 转写/翻译共用的进度条，单独一块；只有任务在跑的时候才出现
        self.task_box = QWidget(body)
        task_layout = QVBoxLayout(self.task_box)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(8)
        task_layout.addWidget(self._texts.section("任务进度"))
        self.progress = QProgressBar(self.task_box)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        task_layout.addWidget(self.progress)
        self.stage = _hint("")
        task_layout.addWidget(self.stage)
        self.task_box.hide()

        # ------------------------------------------------------------ 转写
        box.addWidget(self._texts.section("语音转字幕"))
        self.status = _hint(i18n.t("检查中…"))
        box.addWidget(self.status)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.full_button = QPushButton("")
        self._texts.label(self.full_button, "转字幕")
        self._texts.tip(self.full_button, "只把音频转成字幕，不翻译")
        theme.set_role(self.full_button, "ghost")
        self.full_button.clicked.connect(lambda: self.request.emit(False))
        self.translate_button = QPushButton("")
        self._texts.label(self.translate_button, "字幕翻译")
        self._texts.tip(self.translate_button, "翻译当前选中的字幕（原生或音频生成的都行）")
        theme.set_role(self.translate_button, "ghost")
        self.translate_button.clicked.connect(self._request_translate)
        self.one_click_button = QPushButton("")
        self._texts.label(self.one_click_button, "一键翻译")
        self._texts.tip(self.one_click_button, "转写 + 翻译一次完成")
        theme.set_role(self.one_click_button, "primary")
        self.one_click_button.clicked.connect(self.one_click_requested.emit)
        self.live_button = QPushButton("")
        self._texts.label(self.live_button, "实时翻译")
        self._texts.tip(self.live_button, "边播边转写并持续翻译；只用于本场播放，不保存字幕文件")
        theme.set_role(self.live_button, "ghost")
        self.live_button.setCheckable(True)
        self.live_button.clicked.connect(self._toggle_live)
        grid.addWidget(self.full_button, 0, 0)
        grid.addWidget(self.one_click_button, 0, 1)
        grid.addWidget(self.translate_button, 1, 0)
        grid.addWidget(self.live_button, 1, 1)
        box.addLayout(grid)
        stop_row = QHBoxLayout()
        stop_row.addStretch(1)
        self.stop_button = QPushButton("")
        self._texts.label(self.stop_button, "停止")
        theme.set_role(self.stop_button, "ghost")
        self.stop_button.clicked.connect(self._stop)
        stop_row.addWidget(self.stop_button)
        box.addLayout(stop_row)
        box.addWidget(self._texts.hint("转字幕只出原文 · 字幕翻译翻当前选中的轨 · "
                                       "一键翻译＝转写+翻译 · 实时翻译＝边播边转边翻（不保存字幕文件）。"))
        box.addWidget(self.task_box)      # 放在按钮下面：任务开始时按钮不跳位
        self.last_task = _hint("")
        self.last_task.hide()
        box.addWidget(self.last_task)

        self.model_combo = _combo("转写模型", box, body, self._texts)
        self.model_note = _hint("")
        box.addWidget(self.model_note)
        self._fill_model_combo()
        self.model_combo.currentIndexChanged.connect(self._pick_model)
        self.asr_dl_bar = _tiny_progress(body)
        self.asr_dl_note = _hint("")
        box.addWidget(self.asr_dl_bar)
        box.addWidget(self.asr_dl_note)
        self.download_button = QPushButton("")
        self._texts.label(self.download_button, "下载转写模型")
        theme.set_role(self.download_button, "ghost")
        self.download_button.clicked.connect(self._download)
        box.addWidget(self.download_button)
        box.addWidget(self._texts.hint("要删模型腾空间：侧栏「存储」栏里每个模型的体积都列出来了，可以直接删。"))

        self.lang_combo = _combo("识别语言", box, body, self._texts)
        self.lang_combo.addItem(i18n.t("自动检测（推荐）"), "auto")
        self._texts.combo_item(self.lang_combo, 0, "自动检测（推荐）")
        for code, name in cfg.LANGUAGE_CHOICES:
            self.lang_combo.addItem(name, langtags.base_of(code))
        self._texts.tip(self.lang_combo, "只对当前这部片生效；实时翻译时改完下一段就生效")
        self.lang_combo.currentIndexChanged.connect(self._pick_lang)
        self.lang_note = _hint("")
        box.addWidget(self.lang_note)
        box.addWidget(self._texts.hint("自动模式会先用语音检测校正音轨语言标记；"
                                       "识别不准就在上面指定本片语言。转写用 whisper.cpp 的 Metal 加速。"))

        # ------------------------------------------------------------ 翻译
        box.addWidget(self._texts.section("字幕翻译"))
        self.engine_combo = _combo("翻译引擎", box, body, self._texts)
        for index, (value, label) in enumerate(translate.engine_labels().items()):
            self.engine_combo.addItem(i18n.t(label), value)
            self._texts.combo_item(self.engine_combo, index, label)
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(
            self.config.get("translate.engine", "openai"))))
        self.engine_combo.currentIndexChanged.connect(self._pick_engine)

        self.target_combo = _combo("目标语言", box, body, self._texts)
        for code, name in cfg.LANGUAGE_CHOICES:
            self.target_combo.addItem(f"{name} → {code}", code)
        self.target_combo.setCurrentIndex(max(0, self.target_combo.findData(
            self.config.get("languages.translation_target", "zh-CN"))))
        self.target_combo.currentIndexChanged.connect(self._pick_target)

        self.context_combo = _combo("翻译上下文", box, body, self._texts)
        for label, lines in (("关闭（最快）", 0), ("3 条", 3),
                             ("6 条（推荐）", 6), ("12 条", 12)):
            self.context_combo.addItem(i18n.t(label), lines)
            self._texts.combo_item(self.context_combo, self.context_combo.count() - 1, label)
        current_context = int(self.config.get("translate.context_lines", 0) or 0)
        context_index = self.context_combo.findData(current_context)
        if context_index < 0:
            self.context_combo.addItem("", current_context)
            self._texts.combo_item_fmt(self.context_combo, self.context_combo.count() - 1,
                                       "{} 条", current_context)
            context_index = self.context_combo.count() - 1
        self.context_combo.setCurrentIndex(context_index)
        self.context_combo.currentIndexChanged.connect(self._pick_context)
        box.addWidget(self._texts.hint("保留前几句原文/译文给 LLM 当上下文，人称/术语更连贯但会慢一点；"
                                       "只对在线 OpenAI 和本地 GGUF 有效。"))

        # 本地引擎那一组（标题也要一起藏，否则只剩个光杆标题）
        self.llm_box = QWidget(body)
        llm_layout = QVBoxLayout(self.llm_box)
        llm_layout.setContentsMargins(0, 0, 0, 0)
        llm_layout.setSpacing(8)
        llm_layout.addWidget(self._texts.section("本地 GGUF 模型"))

        llm_layout.addWidget(self._texts.hint("一键 / 整片翻译（大模型，慢但更稳）"))
        self.llm_combo = QComboBox(self.llm_box)
        _fill_llm_combo(self.llm_combo, self.config.get("translate.llama_model", ""))
        self.llm_combo.currentIndexChanged.connect(self._pick_llm_model)
        llm_layout.addWidget(self.llm_combo)
        self.llm_note = _hint("")
        llm_layout.addWidget(self.llm_note)
        self.llm_dl_bar = _tiny_progress(self.llm_box)
        self.llm_dl_note = _hint("")
        llm_layout.addWidget(self.llm_dl_bar)
        llm_layout.addWidget(self.llm_dl_note)
        self.llm_download = QPushButton("")
        self._texts.label(self.llm_download, "下载整片模型")
        theme.set_role(self.llm_download, "ghost")
        self.llm_download.clicked.connect(
            lambda: self._download_llm(self.llm_combo, self.llm_download,
                                       self.llm_dl_bar, self.llm_dl_note))
        llm_layout.addWidget(self.llm_download)

        llm_layout.addWidget(self._texts.hint("实时翻译（小模型，快）"))
        self.llm_live_combo = QComboBox(self.llm_box)
        _fill_llm_combo(self.llm_live_combo,
                        self.config.get("translate.live_llama_model", ""))
        self.llm_live_combo.currentIndexChanged.connect(self._pick_live_llm_model)
        llm_layout.addWidget(self.llm_live_combo)
        self.llm_live_note = _hint("")
        llm_layout.addWidget(self.llm_live_note)
        self.llm_live_dl_bar = _tiny_progress(self.llm_box)
        self.llm_live_dl_note = _hint("")
        llm_layout.addWidget(self.llm_live_dl_bar)
        llm_layout.addWidget(self.llm_live_dl_note)
        self.llm_live_download = QPushButton("")
        self._texts.label(self.llm_live_download, "下载实时模型")
        theme.set_role(self.llm_live_download, "ghost")
        self.llm_live_download.clicked.connect(
            lambda: self._download_llm(self.llm_live_combo, self.llm_live_download,
                                       self.llm_live_dl_bar, self.llm_live_dl_note))
        llm_layout.addWidget(self.llm_live_download)
        llm_layout.addWidget(self._texts.hint(
            "两个模型分开：实时用小模型追速度，一键/整片用大模型慢慢翻；"
            "换模型时选未下载的型号，按钮会变成下载。"))
        box.addWidget(self.llm_box)

        # 在线接口的高级项：平铺在这一栏里（不做对话框），但只在选在线引擎时显示
        self.online_box = QWidget(body)
        online_layout = QVBoxLayout(self.online_box)
        online_layout.setContentsMargins(0, 0, 0, 0)
        online_layout.setSpacing(8)
        self.base_url = QLineEdit(str(self.config.get("translate.openai_base_url", "")),
                                  self.online_box)
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.base_url.editingFinished.connect(
            lambda: self.config.set("translate.openai_base_url", self.base_url.text().strip()))
        self._texts.caption("接口地址", self.base_url, online_layout)
        self.model_edit = QLineEdit(str(self.config.get("translate.openai_model", "")),
                                    self.online_box)
        self.model_edit.setPlaceholderText("gpt-4o-mini / deepseek-chat / …")
        self.model_edit.editingFinished.connect(
            lambda: self.config.set("translate.openai_model", self.model_edit.text().strip()))
        self._texts.caption("在线模型", self.model_edit, online_layout)
        # 用下拉而不是 QSpinBox：QSS 一旦接管 ::up-button，Qt 就不画箭头了
        self.batch_combo = QComboBox(self.online_box)
        for lines in (8, 12, 16, 24, 32, 48, 60):
            self.batch_combo.addItem("", lines)
            self._texts.combo_item_fmt(self.batch_combo, self.batch_combo.count() - 1,
                                       "{} 行 / 批", lines)
        wanted = int(self.config.get("translate.batch_lines", 24))
        index = self.batch_combo.findData(wanted)
        self.batch_combo.setCurrentIndex(index if index >= 0 else 0)
        self.batch_combo.currentIndexChanged.connect(
            lambda _: self.config.set("translate.batch_lines", self.batch_combo.currentData()))
        self._texts.caption("每批行数", self.batch_combo, online_layout)
        box.addWidget(self.online_box)

        self.key_edit = QLineEdit(body)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText(i18n.t("密钥只进 macOS 钥匙串"))
        save_key = QPushButton("")
        self._texts.label(save_key, "保存密钥")
        theme.set_role(save_key, "ghost")
        save_key.clicked.connect(self._save_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit)
        key_row.addWidget(save_key)
        self.key_box = QWidget(body)
        self.key_box.setLayout(key_row)
        box.addWidget(self.key_box)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.test_button = QPushButton("")
        self._texts.label(self.test_button, "测试翻译")
        theme.set_role(self.test_button, "ghost")
        self.test_button.clicked.connect(self._test)
        self.clear_cache = QPushButton("")
        self._texts.label(self.clear_cache, "清空缓存")
        theme.set_role(self.clear_cache, "ghost")
        self.clear_cache.clicked.connect(self._clear_cache)
        for widget in (self.test_button, self.clear_cache):
            actions.addWidget(widget)
        box.addLayout(actions)
        self.test_result = _hint("")
        box.addWidget(self.test_result)

        self._sync_engine_visibility()
        self.download_tick.connect(self._on_download_tick)
        self.download_note.connect(self._on_download_note)
        self.download_done.connect(self._on_download_done)
        self.test_done.connect(self._test_done)
        self.pipeline.busy.connect(self._on_task_busy)
        self.check_timer = QTimer(self)
        self.check_timer.setInterval(1500)
        self.check_timer.timeout.connect(self.refresh)
        self.refresh()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        self._texts.apply()
        self.key_edit.setPlaceholderText(i18n.t("密钥只进 macOS 钥匙串"))
        asr.clear_availability()      # 可用性串是拼好的，清了才会按新语言重查
        self._update_lang_note()
        self.refresh()          # 引擎可用性/模型说明是查出来的字符串，重查一遍才会换语言
        key = getattr(self, "_last_task_key", None)
        if key is not None and not self.pipeline.is_busy():
            self.last_task.setText(i18n.t(*key))

    # ------------------------------------------------------------ 可见性

    def note(self, text: str) -> None:
        """任务没能起来时的说明要留在面板上 —— OSD 几秒就没了。"""
        self.last_task.setText(text)
        self.last_task.show()

    def _sync_engine_visibility(self) -> None:
        engine = self.engine_combo.currentData()
        self.online_box.setVisible(engine == "openai")
        self.key_box.setVisible(engine in ("openai", "deepl"))
        self.llm_box.setVisible(engine == "llama")

    # ------------------------------------------------------------ 转写

    def _stop(self) -> None:
        self.pipeline.stop_all()
        self.live_button.setChecked(False)
        self._stage_stopped = True
        self.stage.setText(i18n.t("已停止"))

    def _toggle_live(self) -> None:
        """勾上=开始实时；取消勾选=停止。别把「取消实时」当成一次整片转写。"""
        if self.live_button.isChecked():
            self.request.emit(True)
        else:
            self._stop()

    def _fill_model_combo(self) -> None:
        """转写模型下拉：先列磁盘上已有的，再补目录里的型号（未下载的点按钮去下）。"""
        combo = self.model_combo
        wanted = str(self.config.get("asr.model", "") or "")
        combo.blockSignals(True)
        combo.clear()
        installed = asr.list_models()
        names = [*installed]
        if wanted and wanted not in names:
            names.append(wanted)
        for name in names:
            if name:
                combo.addItem(name)
        for filename, (label, _note) in asr.MODELS.items():
            if combo.findText(filename) < 0:
                combo.addItem(f"{filename}  ({label})")
        index = combo.findText(wanted)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        note = asr.MODELS.get(wanted)
        self.model_note.setText(i18n.t(note[1]) if note else "")

    def reload_models(self) -> None:
        """存储栏删过/下过模型后回来同步：下拉重建，选中项一律跟配置走。"""
        self._fill_model_combo()
        _fill_llm_combo(self.llm_combo, self.config.get("translate.llama_model", ""))
        _fill_llm_combo(self.llm_live_combo,
                        self.config.get("translate.live_llama_model", ""))
        self._sync_download_buttons()

    def _pick_model(self) -> None:
        text = self.model_combo.currentText().split("  ")[0]
        self.config.set("asr.model", text)
        note = asr.MODELS.get(text)
        self.model_note.setText(i18n.t(note[1]) if note else "")
        if getattr(self, "_download_button", None) is None:
            self.asr_dl_note.clear()      # 换了模型，上一次的「下载完成」就不作数了
        self.refresh()

    def _pick_lang(self) -> None:
        code = self.lang_combo.currentData() or "auto"
        self.config.set("asr.language", code)
        self.pipeline.set_language_hint(code)
        self._update_lang_note()

    def sync_language(self, code: str | None = None) -> None:
        """打开新片时切到这部片自己保存的识别语言。"""
        code = code or self.pipeline.language_hint()
        index = self.lang_combo.findData(code or "auto")
        if index < 0:
            index = 0
        self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentIndex(index)
        self.lang_combo.blockSignals(False)
        self._update_lang_note()

    def _update_lang_note(self) -> None:
        if not self.pipeline.current_media:
            self.lang_note.setText(i18n.t("打开视频后按本片选择；自动模式会用语音检测校正音轨标记"))
            return
        hint = self.pipeline.language_hint()
        tagged = self.pipeline.audio_language()
        if hint != "auto":
            note = i18n.t("本片指定：{}", naming.lang_name(cfg.lang_display(hint)))
            if tagged and not langtags.same_lang(hint, tagged):
                note += i18n.t("（覆盖音轨的{}标记）",
                               naming.lang_name(cfg.lang_display(tagged)))
        elif tagged:
            note = i18n.t("自动：音轨标记为{}；开始转写时会用语音检测校正",
                          naming.lang_name(cfg.lang_display(tagged)))
        else:
            note = i18n.t("自动：开始转写时用语音检测判断语言")
        self.lang_note.setText(note)

    def _download(self) -> None:
        name = self.model_combo.currentText().split("  ")[0]
        if not name.startswith("ggml"):
            self.asr_dl_note.setText(i18n.t("先在下拉里选一个未下载的模型"))
            return
        self._run_download(name, self.download_button, asr.download_model,
                           self.asr_dl_bar, self.asr_dl_note)

    def _download_llm(self, combo=None, button=None, bar=None, note=None) -> None:
        combo = combo or self.llm_combo
        button = button or self.llm_download
        bar = bar or self.llm_dl_bar
        note = note or self.llm_dl_note
        name = combo.currentData()
        if not name:
            note.setText(i18n.t("先选一个 GGUF 模型"))
            return
        if getattr(self, "_download_button", None) is not None:
            note.setText(i18n.t("另一个模型正在下载，等它结束"))
            return
        self._run_download(name, button, translate.download_llm_model, bar, note)

    def _run_download(self, name: str, button: QPushButton, fetch,
                      bar: QProgressBar, note: QLabel) -> None:
        self._download_name = name
        self._download_button = button
        self._download_bar = bar
        self._download_note = note
        button.setEnabled(False)
        for other in (getattr(self, "llm_download", None),
                      getattr(self, "llm_live_download", None)):
            if other is not None and other is not button:
                other.setEnabled(False)
        button.setText(i18n.t("下载中… {}%", 0))
        note.setText(i18n.t("正在下载 {}…", name))
        bar.setValue(0)
        bar.show()

        def worker() -> None:
            # 下载器在后台线程跑，只能发信号；直接 setText/setValue 会让进程 abort
            try:
                fetch(name,
                      on_progress=lambda done, total: self.download_tick.emit(done, total),
                      on_note=lambda text: self.download_note.emit(text))
            except Exception as exc:
                self.download_done.emit(str(exc))
            else:
                self.download_done.emit("")

        threading.Thread(target=worker, daemon=True, name="model-download").start()

    def _on_download_note(self, text: str) -> None:
        note = getattr(self, "_download_note", None)
        if note is not None:
            note.setText(text)

    def _on_download_tick(self, done, total) -> None:
        try:
            done, total = int(done), int(total)
        except (TypeError, ValueError):
            return
        pct = max(0, min(100, int(done * 100 / max(1, total))))
        bar = getattr(self, "_download_bar", None)
        if bar is not None:
            bar.setValue(pct)
        button = getattr(self, "_download_button", None)
        if button is not None:
            button.setText(i18n.t("下载中… {}%", pct))

    def _on_download_done(self, error: str) -> None:
        name = getattr(self, "_download_name", "")
        button = getattr(self, "_download_button", None)
        bar = getattr(self, "_download_bar", None)
        note = getattr(self, "_download_note", None)
        self._download_button = None
        self._download_bar = None
        self._download_note = None
        if bar is not None:
            if error:
                bar.hide()
            else:
                bar.setValue(100)
                QTimer.singleShot(800, bar.hide)   # 让 100% 停一下再收，证明真的走完了
        if note is not None:
            note.setText(i18n.t("下载失败：{}", error) if error
                         else i18n.t("{} 下载完成", name))
        if button is not None:
            button.setEnabled(True)
        QTimer.singleShot(100, self.refresh)

    def _on_task_busy(self, running: bool) -> None:
        """转写/翻译的进度块只在任务运行时出现；结束后留一行「上次任务」结果。"""
        if running:
            self._stage_stopped = False
            self.progress.setRange(0, 0)     # 具体进度还没来时先转圈
            self.progress.setFormat("")
            self.stage.setText(i18n.t("准备中…"))
            self.last_task.setText(i18n.t("任务进行中：{}", i18n.t("准备中…")))
            self.last_task.show()
            self.task_box.show()
        else:
            final_stage = self.stage.text().strip()
            final_value = self.progress.value()
            final_total = self.progress.maximum()
            self.task_box.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("")
            self.stage.clear()
            if getattr(self, "_stage_stopped", False):
                self._last_task_key = ("上次任务：已停止", ())
                self.last_task.setText(i18n.t(*self._last_task_key))
            elif final_stage:
                count = f"（{final_value}/{final_total}）" if final_total > 0 else ""
                self._last_task_key = ("上次任务：{} · 已结束{}", (final_stage, count))
                self.last_task.setText(i18n.t(*self._last_task_key))
            else:
                self._last_task_key = ("上次任务：已结束", ())
                self.last_task.setText(i18n.t(*self._last_task_key))
            self._stage_stopped = False
            if self.live_button.isChecked():
                self.live_button.setChecked(False)   # 实时任务自然结束，按钮别再显示“开”

    def set_progress(self, stage: str, done: int, total: int) -> None:
        if not self.pipeline.is_busy():
            return
        self.task_box.show()
        self.last_task.setText(i18n.t("任务进行中：{}", stage))
        self.last_task.show()
        self.stage.setText(stage)
        if total > 0 and done > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{done}/{total}")
        else:
            # 第一批还没完成（done=0）时转圈，别让进度条定在 0% 像卡死
            self.progress.setRange(0, 0)
            self.progress.setFormat("")

    def _sync_download_buttons(self) -> None:
        """按钮永远针对「下拉里当前选中的型号」：已就绪就写明，未下载就给出下载按钮。"""
        if getattr(self, "_download_button", None) is not None:
            return                       # 正在下载，别把「下载中…」覆盖掉
        chosen = self.model_combo.currentText().split("  ")[0]
        have = chosen in asr.list_models()
        self.download_button.setText(i18n.t("当前转写模型已就绪") if have
                                     else i18n.t("下载选中的转写模型"))
        self.download_button.setEnabled(not have)
        model = str(self.config.get("translate.llama_model", "") or "")
        ready = translate.llm_model_ready(model)
        label, note = translate.LLM_MODELS.get(model, (model, "", ""))[:2]
        size = note.split(" · ")[0] if note else ""
        if ready:
            self.llm_download.setText(i18n.t("当前整片模型已就绪"))
            self.llm_download.setToolTip(
                i18n.t("{} 已下载；换模型请在下拉里选未下载的型号", label))
        else:
            self.llm_download.setText(i18n.t("下载整片模型")
                                      + (i18n.t("（{}）", size) if size else ""))
            self.llm_download.setToolTip(i18n.t("下载 {}", label)
                                         + (f" · {size}" if size else ""))
        self.llm_download.setEnabled(not ready)

        live_model = str(self.config.get("translate.live_llama_model", "") or "")
        live_ready = translate.llm_model_ready(live_model)
        live_label, live_note = translate.LLM_MODELS.get(
            live_model, (live_model, "", ""))[:2]
        live_size = live_note.split(" · ")[0] if live_note else ""
        if live_ready:
            self.llm_live_download.setText(i18n.t("当前实时模型已就绪"))
            self.llm_live_download.setToolTip(
                i18n.t("{} 已下载；换模型请在下拉里选未下载的型号", live_label))
        else:
            self.llm_live_download.setText(
                i18n.t("下载实时模型") + (i18n.t("（{}）", live_size) if live_size else ""))
            self.llm_live_download.setToolTip(
                i18n.t("下载 {}", live_label) + (f" · {live_size}" if live_size else ""))
        self.llm_live_download.setEnabled(not live_ready)

    @staticmethod
    def _spec_text(note: str) -> str:
        """「0.46 GB · 最快…」—— 体积照原样，后半句按界面语言翻。"""
        size, sep, desc = note.partition(" · ")
        return f"{size}{sep}{i18n.t(desc)}" if sep else note

    def refresh(self) -> None:
        self._sync_download_buttons()
        hit = asr.availability_now(self.config)
        if hit is None:                 # 冷缓存：后台去问，别把 GUI 线程堵住
            self.status.setText(i18n.t("检测 whisper 中…"))
            asr.warm_async(self.config)
            QTimer.singleShot(1200, self.refresh)
            return
        ok, message = hit
        self.status.setText(("✓ " if ok else "✗ ") + message)
        self.status.setStyleSheet(f"color: {'#7fdca0' if ok else '#ff9a8c'}; font-size: 11.5px;")
        present = asr.list_models()
        self.full_button.setEnabled(ok)
        self.one_click_button.setEnabled(ok)
        self.live_button.setEnabled(ok)
        if present:
            self.model_note.setText(i18n.t("本机已有：{}", ", ".join(present)))
        else:
            self.model_note.setText(i18n.t("本机还没有转写模型，点下面下载"))
        binary = translate.find_llama_binary(self.config)
        model = str(self.config.get("translate.llama_model", "") or "")
        spec = self._spec_text(translate.LLM_MODELS.get(model, ("", "", ""))[1])
        prefix = f"{spec} · " if spec else ""
        live_model = str(self.config.get("translate.live_llama_model", "") or "")
        live_spec = self._spec_text(translate.LLM_MODELS.get(live_model, ("", "", ""))[1])
        live_prefix = f"{live_spec} · " if live_spec else ""
        if not binary:
            self.llm_note.setText(i18n.t("没装 llama.cpp：体检里一键装，或 brew install llama.cpp"))
            self.llm_live_note.setText(i18n.t("没装 llama.cpp：体检里一键装，或 brew install llama.cpp"))
        else:
            self.llm_note.setText(
                i18n.t("整片 {}llama-server 已就绪 · {}", prefix,
                       i18n.t("离线可用") if translate.llm_model_ready(model)
                       else i18n.t("缺模型，点下面下载")))
            self.llm_live_note.setText(
                i18n.t("实时 {}llama-server 已就绪 · {}", live_prefix,
                       i18n.t("离线可用") if translate.llm_model_ready(live_model)
                       else i18n.t("缺模型，点下面下载")))

    # ------------------------------------------------------------ 翻译

    def _pick_engine(self) -> None:
        self.config.set("translate.engine", self.engine_combo.currentData())
        self._sync_engine_visibility()
        self.test_result.setText(i18n.t("已切换引擎，点「测试翻译」验证"))

    def _pick_target(self) -> None:
        self.config.set("languages.translation_target", self.target_combo.currentData())
        self.pipeline.target = self.target_combo.currentData()

    def _pick_context(self) -> None:
        if self.context_combo.currentData() is not None:
            self.config.set("translate.context_lines", int(self.context_combo.currentData()))

    def _pick_llm_model(self) -> None:
        self.config.set("translate.llama_model", self.llm_combo.currentData())
        if getattr(self, "_download_button", None) is None:
            self.llm_dl_note.clear()
        self.refresh()

    def _pick_live_llm_model(self) -> None:
        self.config.set("translate.live_llama_model",
                        self.llm_live_combo.currentData())
        if getattr(self, "_download_button", None) is None:
            self.llm_live_dl_note.clear()
        self.refresh()
        if getattr(self, "_download_button", None) is None:
            self.llm_dl_note.clear()      # 换了模型，上一次的「下载完成」就不作数了
        self.refresh()

    def _save_key(self) -> None:
        engine = self.engine_combo.currentData()
        name = "deepl" if engine == "deepl" else "openai"
        value = self.key_edit.text().strip()
        if not value:
            self.test_result.setText("先输入密钥")
            return
        try:
            cfg.secret_set(name, value)
        except RuntimeError as exc:
            self.test_result.setText(str(exc))
            return
        self.key_edit.clear()
        self.test_result.setText(i18n.t("密钥已存入钥匙串（{}）", name))

    def _clear_cache(self) -> None:
        self.pipeline.cache.clear()
        self.test_result.setText(i18n.t("翻译缓存已清空"))

    def _test(self) -> None:
        self.test_button.setEnabled(False)
        self.test_result.setText(i18n.t("测试中…（本地模型第一次要等它加载，可能几十秒）"))

        def worker() -> None:
            try:
                translator = translate.build(self.config)
                result = f"✗ {translator}"
            except Exception as exc:
                result = f"✗ {exc}"
            else:
                probe = translator.probe(self.config.get("languages.translation_target", "zh-CN"))
                result = ("✓ " if probe.ok else "✗ ") + probe.detail
            self.test_done.emit(result)

        threading.Thread(target=worker, daemon=True, name="translate-test").start()

    def _test_done(self, text: str) -> None:
        self.test_result.setText(text)
        self.test_button.setEnabled(True)

    def _request_translate(self) -> None:
        """翻译轨道栏当前选中的字幕——原生的和音频生成的都行。"""
        sid = self.engine.get("sid")
        if sid in (None, False, "no"):
            self.pipeline.error.emit("先在轨道栏选一条要翻译的字幕轨")
            return
        if self.pipeline.ai_pid is not None and sid == self.pipeline.ai_pid:
            self.translate_requested.emit(sid)      # 生成轨用内存里的原文翻译
            return
        track = next((t for t in self.engine.tracks_of("sub") if t.get("id") == sid), None)
        if track is None:
            self.pipeline.error.emit("这条字幕已经不是内嵌/外挂轨了")
            return
        self.translate_requested.emit(sid)

    def showEvent(self, event) -> None:  # noqa: N802
        self.check_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.check_timer.stop()
        super().hideEvent(event)


class SettingsPanel(QWidget):
    restart_needed = Signal()

    def __init__(self, config, engine, pipeline, parent=None):
        super().__init__(parent)
        self.config = config
        self.engine = engine
        self.pipeline = pipeline
        self._texts = _Texts(self)
        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 24)
        box.setSpacing(8)

        box.addWidget(self._texts.section("界面"))
        self.language_combo = QComboBox(body)
        for value, label in i18n.LANGUAGES:
            self.language_combo.addItem(i18n.t(label), value)
            self._texts.combo_item(self.language_combo, self.language_combo.count() - 1, label)
        index = self.language_combo.findData(str(self.config.get("ui.language", "auto")))
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.currentIndexChanged.connect(self._pick_language)
        box.addWidget(self.language_combo)
        self.language_note = self._texts.hint("切换后立刻生效；进度与状态文字在下一次刷新时跟着换")
        box.addWidget(self.language_note)

        box.addWidget(self._texts.section("解码"))
        self.hwdec_combo = QComboBox(body)
        for label in cfg.hwdec_choices():
            self.hwdec_combo.addItem(i18n.t(label), label)
            self._texts.combo_item(self.hwdec_combo, self.hwdec_combo.count() - 1, label)
        current = cfg.hwdec_label(self.config.get("player.hwdec", "auto-safe"))
        hwdec_index = self.hwdec_combo.findData(current)
        self.hwdec_combo.setCurrentIndex(max(0, hwdec_index))
        self.hwdec_combo.currentIndexChanged.connect(self._pick_hwdec)
        box.addWidget(self.hwdec_combo)
        self.hwdec_actual = _hint("")
        box.addWidget(self.hwdec_actual)

        self.vo_combo = QComboBox(body)
        for key, data in (("渲染：gpu（自动，推荐）", ("gpu", "")),
                          ("渲染：gpu + macvk（Vulkan）", ("gpu", "macvk")),
                          ("渲染：gpu-next", ("gpu-next", "")),
                          ("渲染：null（不出画面，排查用）", ("null", ""))):
            self.vo_combo.addItem(i18n.t(key), data)
            self._texts.combo_item(self.vo_combo, self.vo_combo.count() - 1, key)
        wanted = (str(self.config.get("player.vo", "gpu")),
                  str(self.config.get("player.gpu_context", "cocoa")))
        index = self.vo_combo.findData(wanted)
        self.vo_combo.setCurrentIndex(index if index >= 0 else 0)
        self.vo_combo.currentIndexChanged.connect(self._pick_vo)
        box.addWidget(self.vo_combo)

        # QCheckBox 没有 wordWrap，标签一长就把整块面板撑宽、越界处被裁掉 → 短标签 + 悬浮说明
        self.passthrough = QCheckBox("", body)
        self._texts.label(self.passthrough, "音频直通 AC3/DTS")
        self._texts.tip(self.passthrough, "把 AC3/DTS 原样送给功放解码。改动后要重启播放器才生效")
        self.passthrough.setChecked(bool(self.config.get("player.audio_passthrough", False)))
        self.passthrough.toggled.connect(self._toggle_passthrough)
        box.addWidget(self.passthrough)
        self.embed = QCheckBox("", body)
        self._texts.label(self.embed, "画面画进本窗口")
        self._texts.tip(self.embed, "关掉退回「独立 mpv 播放窗 + 贴合视频区」那条退路：\n"
                                    "显卡驱动不支持 GL 内嵌、或画面出问题时用。改动后要重启播放器")
        self.embed.setChecked(str(self.config.get("player.backend", "libmpv")) == "libmpv")
        self.embed.toggled.connect(lambda value: (
            self.config.set("player.backend", "libmpv" if value else "ipc"),
            self.restart_needed.emit()))
        box.addWidget(self.embed)
        # vo/gpu-context 是子进程那条路的参数，libmpv 下画面由我们自己的控件渲染，改了无效
        self.vo_combo.setVisible(not self.embed.isChecked())
        self.embed.toggled.connect(lambda value: self.vo_combo.setVisible(not value))

        box.addWidget(self._texts.section("字幕外观"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.font_combo = QComboBox(body)
        self.font_combo.addItems(FONTS)
        self.font_combo.setCurrentText(self.config.get("subtitle.font", FONTS[0]))
        self.font_combo.currentTextChanged.connect(lambda value: self._set_style("font", value))
        form.addRow(self._texts.new_label("字体", body), self.font_combo)
        self.size_slider = QSlider(Qt.Horizontal, body)
        self.size_slider.setRange(24, 110)
        self.size_label = QLabel("", body)
        self.size_label.setObjectName("ValueLabel")
        self.size_slider.setValue(int(self.config.get("subtitle.size", 50)))
        self.size_slider.valueChanged.connect(self._pick_size)
        size_row = QHBoxLayout()
        size_row.addWidget(self.size_slider)
        size_row.addWidget(self.size_label)
        holder = QWidget(body)
        holder.setLayout(size_row)
        form.addRow(self._texts.new_label("字号", body), holder)
        self.margin_slider = QSlider(Qt.Horizontal, body)
        self.margin_slider.setRange(12, 160)
        self.margin_label = QLabel("", body)
        self.margin_label.setObjectName("ValueLabel")
        self.margin_slider.setValue(int(self.config.get("subtitle.margin", 42)))
        self.margin_slider.valueChanged.connect(self._pick_margin)
        margin_row = QHBoxLayout()
        margin_row.addWidget(self.margin_slider)
        margin_row.addWidget(self.margin_label)
        margin_holder = QWidget(body)
        margin_holder.setLayout(margin_row)
        form.addRow(self._texts.new_label("双语字幕高度", body), margin_holder)
        self.pos_slider = QSlider(Qt.Horizontal, body)
        self.pos_slider.setRange(0, 100)
        self.pos_label = QLabel("", body)
        self.pos_label.setObjectName("ValueLabel")
        self.pos_slider.setValue(int(self.config.get("subtitle.pos", 100)))
        self.pos_slider.valueChanged.connect(self._pick_pos)
        pos_row = QHBoxLayout()
        pos_row.addWidget(self.pos_slider)
        pos_row.addWidget(self.pos_label)
        pos_holder = QWidget(body)
        pos_holder.setLayout(pos_row)
        form.addRow(self._texts.new_label("原生字幕位置(mpv)", body), pos_holder)
        self.delay_slider = QSlider(Qt.Horizontal, body)
        self.delay_slider.setRange(-500, 500)
        self.delay_slider.setValue(int(float(self.config.get("subtitle.delay", 0)) * 100))
        self.delay_label = QLabel("", body)
        self.delay_label.setObjectName("ValueLabel")
        self.delay_slider.valueChanged.connect(self._pick_delay)
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.delay_slider)
        delay_row.addWidget(self.delay_label)
        delay_holder = QWidget(body)
        delay_holder.setLayout(delay_row)
        form.addRow(self._texts.new_label("时间延迟", body), delay_holder)
        box.addLayout(form)
        self._sync_style_labels()
        box.addWidget(self._texts.hint("生成的字幕遇到长句会自动折成两行、必要时缩小字号；"
                                      "原生字幕太宽可以调小上面的字号。"))

        box.addWidget(self._texts.section("字幕文件"))
        self.next_to_video = QCheckBox("", body)
        self._texts.label(self.next_to_video, "字幕放在视频旁边")
        self.next_to_video.setChecked(bool(self.config.get("subtitle.next_to_video", False)))
        self.next_to_video.toggled.connect(self._pick_next_to_video)
        box.addWidget(self.next_to_video)
        box.addWidget(self._texts.hint("勾选后每部片子的字幕落在视频所在目录下的同名子文件夹；"
                                      "视频目录写不进去时自动回退。"))
        self.dir_edit = QLineEdit(str(self.config.get("subtitle.output_dir", "") or ""), body)
        self.dir_edit.setPlaceholderText(i18n.t("留空 = {}", cfg.SUBTITLE_DIR))
        self.dir_edit.editingFinished.connect(self._apply_dir)
        self.pick_dir_button = QPushButton(i18n.t("选目录…"))
        self._texts.label(self.pick_dir_button, "选目录…")
        theme.set_role(self.pick_dir_button, "ghost")
        self.pick_dir_button.clicked.connect(self._choose_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(self.pick_dir_button)
        dir_holder = QWidget(body)
        dir_holder.setLayout(dir_row)
        self._texts.caption("输出目录", dir_holder, box)
        self.dir_note = _hint("")
        box.addWidget(self.dir_note)
        file_row = QHBoxLayout()
        self.open_dir_button = QPushButton("")
        self._texts.label(self.open_dir_button, "打开字幕文件夹")
        theme.set_role(self.open_dir_button, "ghost")
        self.open_dir_button.clicked.connect(self._open_subtitle_dir)
        self.library_button = QPushButton("")
        self._texts.label(self.library_button, "清理旧字幕…")
        theme.set_role(self.library_button, "ghost")
        self.library_button.clicked.connect(self._show_library)
        file_row.addWidget(self.open_dir_button)
        file_row.addWidget(self.library_button)
        box.addLayout(file_row)

        self._sync_dir_controls()
        self._sync_dir_note()

        box.addWidget(self._texts.section("实时翻译"))
        self.lead_combo = QComboBox(body)
        for seconds in (10, 20, 30, 45, 60):
            self.lead_combo.addItem("", seconds)
            self._texts.combo_item_fmt(self.lead_combo, self.lead_combo.count() - 1,
                                       "预读 {} 秒", seconds)
        self.lead_combo.addItem("", 0)
        self._texts.combo_item(self.lead_combo, self.lead_combo.count() - 1,
                               "整片（尽可能超前）")
        current_lead = int(float(self.config.get("asr.live_lead", 30.0)))
        lead_index = self.lead_combo.findData(current_lead)
        if lead_index < 0:
            self.lead_combo.addItem("", current_lead)
            self._texts.combo_item_fmt(self.lead_combo, self.lead_combo.count() - 1,
                                       "预读 {} 秒", current_lead)
            lead_index = self.lead_combo.count() - 1
        self.lead_combo.setCurrentIndex(lead_index)
        self.lead_combo.currentIndexChanged.connect(self._pick_live_lead)
        box.addWidget(self.lead_combo)
        box.addWidget(self._texts.hint("预读越多，实时翻译越可能在字幕播到之前准备好；"
                                       "「整片」会从当前位置一路转到片尾，最不容易漏翻但更吃 CPU。"
                                       "跳转后也会从新位置重新预读。"
                                       "115 云端实时翻译走单流代理，默认预读上限 60 秒，"
                                       "可在「云盘」页调整到 120 秒。"))

        box.addWidget(self._texts.section("默认语言偏好"))
        box.addWidget(self._texts.hint("打开片子时按这个顺序挑音轨/字幕；越靠上越优先。"))
        self.pref_audio = LanguagePreference("默认音轨语言", "languages.default_audio_langs",
                                             config, body)
        self.pref_audio.changed.connect(self._push_prefs)
        box.addWidget(self.pref_audio)
        self.pref_sub = LanguagePreference("默认字幕语言", "languages.default_subtitle_langs",
                                           config, body)
        self.pref_sub.changed.connect(self._push_prefs)
        box.addWidget(self.pref_sub)

        box.addWidget(self._texts.section("诊断与修复"))
        self.doctor = DoctorCard(config, body)
        box.addWidget(self.doctor)

        box.addWidget(self._texts.section("存储与清理"))
        box.addWidget(self._texts.hint("模型（可以删）、字幕、缓存、日志、OpenList 数据的具体路径和占用"
                                      "都列在「存储」标签页，那里每个目录都能直接打开。"))
        open_data = QPushButton("")
        self._texts.label(open_data, "打开应用数据文件夹")
        theme.set_role(open_data, "ghost")
        open_data.clicked.connect(lambda: storage.reveal(cfg.SUPPORT_DIR))
        box.addWidget(open_data)
        self.timer = QTimer(self)
        self.timer.setInterval(900)
        self.timer.timeout.connect(self._refresh_state)
        self._refresh_state()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        self._texts.apply()
        self.dir_edit.setPlaceholderText(i18n.t("留空 = {}", cfg.SUBTITLE_DIR))
        self._sync_dir_note()
        self._refresh_state()

    def _refresh_state(self) -> None:
        actual = self.engine.hwdec_actual()
        if actual:
            self.hwdec_actual.setText(i18n.t("当前实际解码：{}", actual))
        else:
            self.hwdec_actual.setText(i18n.t("当前实际解码：{}", i18n.t("未开始")))
        self._sync_dir_note()

    def _pick_next_to_video(self) -> None:
        self.config.set("subtitle.next_to_video", bool(self.next_to_video.isChecked()))
        self._sync_dir_controls()
        self._sync_dir_note()

    def _pick_live_lead(self) -> None:
        if self.lead_combo.currentData() is not None:
            self.config.set("asr.live_lead", float(self.lead_combo.currentData()))

    def _apply_dir(self) -> None:
        self.config.set("subtitle.output_dir", self.dir_edit.text().strip())
        self._sync_dir_note()

    def _choose_dir(self) -> None:
        current = cfg.subtitle_root(self.config.get("subtitle.output_dir", ""))
        picked = QFileDialog.getExistingDirectory(self, i18n.t("字幕放哪儿"), str(current))
        if not picked:
            return
        self.dir_edit.setText(picked)
        self._apply_dir()

    def _subtitle_root_now(self) -> Path:
        return cfg.subtitle_root_for(self.pipeline.current_media,
                                     self.config.get("subtitle.output_dir", ""),
                                     bool(self.config.get("subtitle.next_to_video", False)))

    def _open_subtitle_dir(self) -> None:
        target = (self.pipeline.sub_folder if self.pipeline.current_media
                  else self._subtitle_root_now())
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        subprocess.Popen(["open", str(target)])

    def _show_library(self) -> None:
        SubtitleLibrary(self._subtitle_root_now(), self).exec()
        self._sync_dir_note()

    def _sync_dir_controls(self) -> None:
        follow = bool(self.config.get("subtitle.next_to_video", False))
        self.dir_edit.setEnabled(not follow)
        self.pick_dir_button.setEnabled(not follow)
        self.library_button.setEnabled(not follow)
        self.library_button.setToolTip(
            i18n.t("跟随视频时没有统一根目录，不提供批量清理") if follow else "")

    def _sync_dir_note(self) -> None:
        stem = self.pipeline.media_stem
        media = self.pipeline.current_media
        example = naming.naming_example(stem)
        if media:
            note = i18n.t("这一部 → {}\n{}", self.pipeline.sub_folder, example)
            if self._subtitle_root_now() != self.pipeline.sub_folder.parent:
                note += "\n" + i18n.t("（目录/跟随设置从下一部片子生效）")
            self.dir_note.setText(note)
        elif bool(self.config.get("subtitle.next_to_video", False)):
            self.dir_note.setText(i18n.t("打开片子后，字幕存到视频所在目录下的同名子文件夹。"))
        else:
            self.dir_note.setText(i18n.t("打开片子后按「视频名 + 说明后缀」各存一份：\n{}",
                                        example))

    def _pick_hwdec(self) -> None:
        mode = cfg.hwdec_mode_for_label(str(self.hwdec_combo.currentData() or "auto-safe"))
        self.engine.set_hwdec(mode)
        self.timer.start()

    def _pick_language(self) -> None:
        value = str(self.language_combo.currentData() or "auto")
        self.config.set("ui.language", value)
        i18n.set_language(i18n.resolve(value))
        i18n.retranslate()

    def sync_language(self) -> None:
        """侧栏/首屏的开关改过语言后，把这里的选择框跟上去（不触发信号）。"""
        index = self.language_combo.findData(str(self.config.get("ui.language", "auto")))
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.blockSignals(False)

    def _pick_vo(self) -> None:
        vo, context = self.vo_combo.currentData()
        self.config.set("player.vo", vo)
        self.config.set("player.gpu_context", context)
        self.restart_needed.emit()

    def _toggle_passthrough(self, value: bool) -> None:
        self.config.set("player.audio_passthrough", bool(value))
        self.restart_needed.emit()

    def _set_style(self, key, value) -> None:
        self.config.set(f"subtitle.{key}", value)
        self.engine.apply_subtitle_style()

    def _pick_size(self, value: int) -> None:
        self.config.set("subtitle.size", value)
        self.engine.apply_subtitle_style()
        self._sync_style_labels()
        if self.pipeline.cues:
            self.pipeline._render(force=True)

    def _pick_margin(self, value: int) -> None:
        self.config.set("subtitle.margin", value)
        self._sync_style_labels()
        if self.pipeline.cues:
            self.pipeline._render(force=True)

    def _pick_pos(self, value: int) -> None:
        self.config.set("subtitle.pos", value)
        self.engine.apply_subtitle_style()
        self._sync_style_labels()
        if self.pipeline.cues:
            self.pipeline._render(force=True)

    def _pick_delay(self, value: int) -> None:
        self.config.set("subtitle.delay", value / 100.0)
        self.engine.apply_subtitle_style()
        self._sync_style_labels()

    def _sync_style_labels(self) -> None:
        self.size_label.setText(str(self.size_slider.value()))
        self.margin_label.setText(str(self.margin_slider.value()))
        self.pos_label.setText(f"{self.pos_slider.value()}%")
        self.delay_label.setText(f"{self.delay_slider.value() / 100:.2f}s")

    def _push_prefs(self) -> None:
        self.engine.set_lang_prefs(
            self.config.get("languages.default_audio_langs", []),
            self.config.get("languages.default_subtitle_langs", []))

    def showEvent(self, event) -> None:  # noqa: N802
        self.timer.start()
        self._refresh_state()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        super().hideEvent(event)


class PlaylistPanel(QWidget):
    play_path = Signal(str)
    play_folder = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._texts = _Texts(self)
        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 20)
        box.setSpacing(8)
        box.addWidget(self._texts.section("播放列表"))
        self.list = QListWidget(body)
        self.list.itemDoubleClicked.connect(self._play)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.installEventFilter(self)
        box.addWidget(self.list)
        row = QHBoxLayout()
        add_file = QToolButton(body)
        self._texts.label(add_file, "加文件")
        theme.set_role(add_file, "ghost")
        add_file.clicked.connect(self._add_files)
        add_dir = QToolButton(body)
        self._texts.label(add_dir, "加文件夹")
        theme.set_role(add_dir, "ghost")
        add_dir.clicked.connect(self._add_folder)
        remove = QToolButton(body)
        self._texts.label(remove, "移除")
        theme.set_role(remove, "ghost")
        self._texts.tip(remove, "只从播放列表移除，不会删除文件")
        remove.clicked.connect(self.remove_selected)
        clear = QToolButton(body)
        self._texts.label(clear, "清空")
        theme.set_role(clear, "ghost")
        clear.clicked.connect(self.list.clear)
        for widget in (add_file, add_dir, remove, clear):
            row.addWidget(widget)
        box.addLayout(row)

        box.addWidget(self._texts.section("最近打开"))
        self.recent = QListWidget(body)
        self.recent.setMinimumHeight(110)
        self.recent.itemDoubleClicked.connect(self._play)
        box.addWidget(self.recent)
        self.reload_recent()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))

    def _play(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.play_path.emit(str(path))

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is not None:
            self.list.setCurrentItem(item)
        menu = QMenu(self.list)
        remove = menu.addAction(i18n.t("移除这一项"), self.remove_selected)
        remove.setEnabled(self.list.currentItem() is not None)
        menu.addSeparator()
        menu.addAction(i18n.t("清空播放列表"), self.list.clear)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def remove_selected(self) -> None:
        """只动播放列表，不碰磁盘上的文件。"""
        item = self.list.currentItem()
        if item is None:
            return
        row = self.list.row(item)
        self.list.takeItem(row)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.list and event.type() == QEvent.KeyPress \
                and event.key() == Qt.Key_Delete:
            self.remove_selected()
            return True
        return super().eventFilter(obj, event)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, i18n.t("添加媒体"), str(Path.home()),
                                                cfg.media_filter())
        for path in paths:
            self.append(path)
        if paths:
            self.play_path.emit(paths[0])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, i18n.t("添加文件夹"), str(Path.home()))
        if not folder:
            return
        found = sorted(Path(folder).rglob("*"))
        added = 0
        for path in found:
            if path.suffix.lower() in cfg.MEDIA_EXTS:
                self.append(str(path))
                added += 1
        if added:
            self.play_path.emit(self.list.item(0).data(Qt.UserRole))

    def append(self, path: str) -> None:
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.list.addItem(item)

    def set_current(self, path: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.UserRole) == path:
                self.list.setCurrentRow(row)
                return
        self.append(path)
        self.list.setCurrentRow(self.list.count() - 1)

    def reload_recent(self) -> None:
        self.recent.clear()
        for path in self.config.recent:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.recent.addItem(item)


class AddStorageDialog(QDialog):
    """添加任意 OpenList 存储：字段按网关给出的驱动元数据现渲染。

    115 只是 90 多个驱动里的一个 —— 阿里云盘、夸克、S3、WebDAV、Google Drive……
    都能挂进来；OAuth 类驱动建议用「网页管理」在官方页里登录，这里贴 token 也行。
    """

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self._fields: dict[str, tuple[dict, QWidget]] = {}
        self.setWindowTitle(i18n.t("添加网盘存储"))
        self.resize(460, 540)
        box = QVBoxLayout(self)
        box.addWidget(_hint(i18n.t("字段来自网关的驱动定义；OAuth 类网盘建议用「网页管理」登录后再回来。")))
        self.driver_combo = QComboBox(self)
        self.driver_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._drivers: dict = {}
        self._load_error = ""
        try:
            self._drivers = client.driver_info()
        except Exception as exc:
            self._load_error = str(exc)
        for name in sorted(self._drivers):
            self.driver_combo.addItem(name)
        self.driver_combo.currentTextChanged.connect(self._build_fields)
        _captioned(i18n.t("驱动"), self.driver_combo, box)

        self.mount_edit = QLineEdit("/", self)
        self.mount_edit.setPlaceholderText(i18n.t("挂载路径，例如 /ali"))
        _captioned(i18n.t("挂载路径"), self.mount_edit, box)
        self.remark_edit = QLineEdit("", self)
        _captioned(i18n.t("备注"), self.remark_edit, box)

        self.form_box = QWidget(self)
        self.form = QFormLayout(self.form_box)
        self.form.setLabelAlignment(Qt.AlignLeft)
        box.addWidget(_scroll(self.form_box), 1)
        self.error = _hint(self._load_error)
        box.addWidget(self.error)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(i18n.t("取消"))
        theme.set_role(cancel, "ghost")
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton(i18n.t("添加"))
        theme.set_role(self.ok_button, "primary")
        self.ok_button.clicked.connect(self._create)
        row.addWidget(cancel)
        row.addWidget(self.ok_button)
        box.addLayout(row)
        self.ok_button.setEnabled(not self._load_error)
        self._build_fields(self.driver_combo.currentText())

    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields.clear()

    def _build_fields(self, driver: str) -> None:
        self._clear_form()
        info = self._drivers.get(driver) or {}
        for spec in info.get("additional") or []:
            name = str(spec.get("name") or "")
            if not name:
                continue
            kind = str(spec.get("type") or "string")
            default = str(spec.get("default") or "")
            label = name + (" *" if spec.get("required") else "")
            if kind == "bool":
                widget = QCheckBox(i18n.t("启用"))
                widget.setChecked(default.lower() == "true")
            elif kind == "select":
                widget = QComboBox(self.form_box)
                options = [o for o in str(spec.get("options") or "").split(",") if o]
                widget.addItems(options)
                if default in options:
                    widget.setCurrentText(default)
            else:
                widget = QLineEdit(default, self.form_box)
                if kind == "text":
                    widget.setMinimumHeight(52)
            tip = str(spec.get("help") or "")
            if tip:
                widget.setToolTip(tip)
            self._fields[name] = (spec, widget)
            self.form.addRow(label, widget)

    def _create(self) -> None:
        driver = self.driver_combo.currentText()
        addition = {}
        for name, (spec, widget) in self._fields.items():
            kind = str(spec.get("type") or "string")
            if kind == "bool":
                addition[name] = bool(widget.isChecked())
            elif kind == "select":
                addition[name] = widget.currentText()
            elif kind == "number":
                text = widget.text().strip() or str(spec.get("default") or "")
                try:
                    addition[name] = int(float(text)) if text else 0
                except ValueError:
                    addition[name] = text
            else:
                addition[name] = widget.text()
        try:
            self.client.create_storage(driver, self.mount_edit.text(),
                                       addition, self.remark_edit.text().strip())
        except Exception as exc:
            _warn(self, i18n.t("添加失败"), str(exc))
            return
        self.accept()


class CloudPanel(QWidget):
    """网盘目录：列出 OpenList 的每个挂载点，任意驱动的存储都能浏览与播放。"""

    open_source = Signal(object)
    entries_ready = Signal(str, object)
    load_failed = Signal(str)

    def __init__(self, config, client: cloud.OpenList | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.client = client or cloud.OpenList(config)
        self._texts = _Texts(self)
        self.path = "/"
        self._entries: list[dict] = []
        self._loading = False
        self.entries_ready.connect(self._on_entries)
        self.load_failed.connect(self._on_failed)
        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 20)
        box.setSpacing(8)
        box.addWidget(self._texts.section("网盘"))
        self.status = _hint("")
        box.addWidget(self.status)
        row = QHBoxLayout()
        self.up_button = QPushButton("")
        self._texts.label(self.up_button, "上一级")
        theme.set_role(self.up_button, "ghost")
        self.up_button.clicked.connect(self._up)
        self.refresh_button = QPushButton("")
        self._texts.label(self.refresh_button, "刷新")
        theme.set_role(self.refresh_button, "ghost")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.up_button)
        row.addWidget(self.refresh_button)
        box.addLayout(row)
        tools = QHBoxLayout()
        self.add_button = QPushButton("")
        self._texts.label(self.add_button, "添加存储…")
        theme.set_role(self.add_button, "ghost")
        self.add_button.clicked.connect(self._add_storage)
        self.web_button = QPushButton("")
        self._texts.label(self.web_button, "网页管理")
        theme.set_role(self.web_button, "ghost")
        self.web_button.clicked.connect(self._open_web_ui)
        tools.addWidget(self.add_button)
        tools.addWidget(self.web_button)
        box.addLayout(tools)
        self.crumb = _hint("/")
        box.addWidget(self.crumb)
        self.list = QListWidget(body)
        self.list.itemDoubleClicked.connect(self._open_item)
        box.addWidget(self.list, 1)
        box.addWidget(self._texts.hint(
            "双击目录进入、双击视频播放。任意 OpenList 存储都能这样用；"
            "要加新网盘点「添加存储」，OAuth 类网盘用「网页管理」登录。"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        self._texts.apply()
        if self._entries or self.path == "/":
            self._on_entries(self.path, self._entries)   # 状态行里的计数用当前语言重写

    def _add_storage(self) -> None:
        dialog = AddStorageDialog(self.client, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

    def _open_web_ui(self) -> None:
        if not self.client.healthy():
            self.status.setText(i18n.t("网关没在运行，先启动 OpenList 服务"))
            return
        webbrowser.open(self.client.open_web_ui())

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.status.setText(i18n.t("读取目录…"))
        path = self.path
        threading.Thread(target=self._load, args=(path,), daemon=True,
                         name="openlist-list").start()

    def _load(self, path: str) -> None:
        try:
            entries = self.client.list_dir(path)
        except Exception as exc:
            self.load_failed.emit(str(exc))
            return
        self.entries_ready.emit(path, entries)

    def _on_entries(self, path: str, entries) -> None:
        self._loading = False
        if path != self.path:
            return
        self._entries = list(entries or [])
        self.list.clear()
        dirs = [item for item in self._entries if item.get("is_dir")]
        files = [item for item in self._entries if not item.get("is_dir")
                 and Path(str(item.get("name") or "")).suffix.lower() in cfg.MEDIA_EXTS]
        for item in sorted(dirs, key=lambda x: str(x.get("name") or "").lower()):
            self._add_row(item)
        for item in sorted(files, key=lambda x: str(x.get("name") or "").lower()):
            self._add_row(item)
        self.status.setText(i18n.t("{} · {} 个目录 · {} 个视频", self.path or "/",
                                   len(dirs), len(files)))
        self.crumb.setText(self.path or "/")
        self.up_button.setEnabled(self.path.rstrip("/") != "")

    def _add_row(self, item: dict) -> None:
        name = str(item.get("name") or "")
        if not name:
            return
        row = QListWidgetItem(name)
        row.setData(Qt.UserRole, item)
        if not item.get("is_dir"):
            size = int(item.get("size") or 0)
            row.setText(f"{name}    {size / 1e6:.0f} MB")
        self.list.addItem(row)

    def _on_failed(self, text: str) -> None:
        self._loading = False
        self.status.setText(i18n.t("读取目录失败：{}（可在「OpenList 服务」里启动网关）", text))
        self.status.setToolTip(text)

    def _up(self) -> None:
        if self.path.rstrip("/") == "":
            return
        parent = str(Path(self.path).parent)
        self.path = "/" if parent in (".", "/") else parent
        self.refresh()

    def _open_item(self, row: QListWidgetItem) -> None:
        item = row.data(Qt.UserRole) or {}
        name = str(item.get("name") or "")
        if not name:
            return
        path = f"{self.path.rstrip('/')}/{name}"
        if item.get("is_dir"):
            self.path = path
            self.refresh()
            return
        try:
            source = self.client.source(path, name)
        except Exception as exc:
            self._on_failed(str(exc))
            return
        self.open_source.emit(source)


class CloudManagePanel(QWidget):
    """OpenList 与 115 账户的软件内管理页：状态/启停、扫码登录、存储与缓存设置。"""

    status_ready = Signal(object)
    status_failed = Signal(str)
    storage_ready = Signal(object)
    qr_ready = Signal(object)
    qr_state = Signal(str)
    qr_done = Signal(str)
    qr_failed = Signal(str)
    action_done = Signal(str)
    storage_changed = Signal()

    def __init__(self, config, pipeline, client, parent=None):
        super().__init__(parent)
        self.config = config
        self.pipeline = pipeline
        self.client = client
        self._storage_loaded = False
        self._loading_storage = False
        self._qr_cancel: threading.Event | None = None
        self._qr_active = False
        self._status: dict = {}
        self._texts = _Texts(self)

        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 20)
        box.setSpacing(8)
        box.addWidget(self._texts.section("OpenList 网关"))
        self.status_label = _hint(i18n.t("读取状态…"))
        box.addWidget(self.status_label)
        self.autostart_check = QCheckBox("", body)
        self._texts.label(self.autostart_check, "随软件启动，关窗自动停止")
        self.autostart_check.setChecked(
            str(self.config.get("cloud.start_mode", "auto")) != "manual")
        self._texts.tip(self.autostart_check,
                        "勾选：打开 Lumina 就把网关带起来，退出时一起收掉，网盘随时可用。\n"
                        "取消：不随软件启动，只用手动的「启动/停止」；外部自己跑的网关退出时不动。")
        self.autostart_check.toggled.connect(self._pick_autostart)
        box.addWidget(self.autostart_check)
        row = QHBoxLayout()
        self.start_button = QPushButton("")
        self._texts.label(self.start_button, "启动")
        self.stop_button = QPushButton("")
        self._texts.label(self.stop_button, "停止")
        self.restart_button = QPushButton("")
        self._texts.label(self.restart_button, "重启")
        self.refresh_button = QPushButton("")
        self._texts.label(self.refresh_button, "刷新")
        for button in (self.start_button, self.stop_button, self.restart_button,
                       self.refresh_button):
            theme.set_role(button, "ghost")
            row.addWidget(button)
        box.addLayout(row)
        row2 = QHBoxLayout()
        self.admin_button = QPushButton("")
        self._texts.label(self.admin_button, "管理页")
        self.folder_button = QPushButton("")
        self._texts.label(self.folder_button, "数据目录")
        self.log_button = QPushButton("")
        self._texts.label(self.log_button, "日志")
        for button in (self.admin_button, self.folder_button, self.log_button):
            theme.set_role(button, "ghost")
            row2.addWidget(button)
        box.addLayout(row2)
        box.addWidget(self._texts.hint("OpenList 只监听本机，是软件的内置网关。停止/重启会中断正在进行的"
                                       "网盘播放、字幕与实时翻译；有任务时请先停止任务。"))
        self.action_label = _hint("")
        self.action_label.setWordWrap(True)
        box.addWidget(self.action_label)

        box.addWidget(self._texts.section("115 账户"))
        self.login_label = _hint(i18n.t("未读取"))
        box.addWidget(self.login_label)
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedSize(220, 220)
        self.qr_label.setStyleSheet("background:#fff;border-radius:6px;")
        self.qr_label.hide()
        box.addWidget(self.qr_label, 0, Qt.AlignHCenter)
        qr_row = QHBoxLayout()
        self.scan_button = QPushButton("")
        self._texts.label(self.scan_button, "扫码登录 115")
        self.cancel_qr_button = QPushButton("")
        self._texts.label(self.cancel_qr_button, "取消")
        theme.set_role(self.scan_button, "primary")
        theme.set_role(self.cancel_qr_button, "ghost")
        self.cancel_qr_button.hide()
        qr_row.addWidget(self.scan_button)
        qr_row.addWidget(self.cancel_qr_button)
        box.addLayout(qr_row)
        self.qr_note = self._texts.hint("二维码来自 115 官方接口；扫码后需要重新载入 115 存储。")
        box.addWidget(self.qr_note)

        box.addWidget(self._texts.section("115 存储"))
        self.mount_edit = QLineEdit("/115", body)
        self.mount_edit.setReadOnly(True)
        self._texts.caption("挂载路径（建议保持 /115）", self.mount_edit, box)
        self.policy_combo = QComboBox(body)
        for index, (label, value) in enumerate((
                ("本地代理（拖动最稳）", "native_proxy"),
                ("Web 代理", "web_proxy"),
                ("302 跳转", "302_redirect"))):
            self.policy_combo.addItem(i18n.t(label), value)
            self._texts.combo_item(self.policy_combo, index, label)
        self._texts.caption("WebDAV 策略", self.policy_combo, box)
        self.web_proxy_check = QCheckBox("", body)
        self._texts.label(self.web_proxy_check, "启用 Web 代理（云端实时翻译依赖）")
        box.addWidget(self.web_proxy_check)
        self.range_check = QCheckBox("", body)
        self._texts.label(self.range_check, "允许 Range 代理")
        box.addWidget(self.range_check)
        self.cache_spin = QSpinBox(body)
        self.cache_spin.setRange(0, 600)
        self.cache_spin.setSuffix(i18n.t(" 秒"))
        self._texts.caption("直链缓存时间", self.cache_spin, box)
        self.save_storage_button = QPushButton("")
        self._texts.label(self.save_storage_button, "保存存储设置")
        theme.set_role(self.save_storage_button, "primary")
        box.addWidget(self.save_storage_button)
        box.addWidget(self._texts.hint("云端实时翻译的单流代理需要 Web 代理；WebDAV 播放建议用本地代理。"
                                       "改存储会重新载入 115，正在播放时可能短暂中断。"))

        box.addWidget(self._texts.section("云端播放缓存"))
        self.cache_combo = QComboBox(body)
        for mb in (64, 128, 256, 512):
            self.cache_combo.addItem("", mb)
            self._texts.combo_item_fmt(self.cache_combo, self.cache_combo.count() - 1,
                                       "内存缓存 {} MB", mb)
        current_mb = int(float(self.config.get("cloud.live_cache_mb", 128)))
        idx = self.cache_combo.findData(current_mb)
        self.cache_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self._texts.caption("单流代理内存缓存", self.cache_combo, box)
        self.lead_combo = QComboBox(body)
        for sec in (30, 60, 120):
            self.lead_combo.addItem("", sec)
            self._texts.combo_item_fmt(self.lead_combo, self.lead_combo.count() - 1,
                                       "云端预读上限 {} 秒", sec)
        current_lead = int(float(self.config.get("cloud.live_lead_max", 60)))
        idx = self.lead_combo.findData(current_lead)
        self.lead_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self._texts.caption("实时翻译预读上限", self.lead_combo, box)
        box.addWidget(self._texts.hint("缓存只占内存，不落视频盘。扩大缓存会让跳读和预读更稳，"
                                       "但会占用更多内存；预读上限下一次实时任务生效。"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))

        self.status_ready.connect(self._on_status)
        self.status_failed.connect(self._on_status_failed)
        self.storage_ready.connect(self._on_storage)
        self.qr_ready.connect(self._on_qr_ready)
        self.qr_state.connect(self._set_login_text)
        self.qr_done.connect(self._on_qr_done)
        self.qr_failed.connect(self._on_qr_failed)
        self.action_done.connect(self._on_action)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.restart_button.clicked.connect(self._restart)
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))
        self.admin_button.clicked.connect(self._open_admin)
        self.folder_button.clicked.connect(self._open_folder)
        self.log_button.clicked.connect(self._open_log)
        self.scan_button.clicked.connect(self._scan_115)
        self.cancel_qr_button.clicked.connect(self._cancel_qr)
        self.save_storage_button.clicked.connect(self._save_storage)
        self.cache_combo.currentIndexChanged.connect(self._pick_cache)
        self.lead_combo.currentIndexChanged.connect(self._pick_lead)
        self._update_buttons()
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        self._texts.apply()
        if self._status:
            self._on_status(self._status)      # 状态行是拼出来的，重排一遍
        else:
            self.status_label.setText(i18n.t("读取状态…"))
            if not self._storage_loaded:
                self.login_label.setText(i18n.t("未读取"))
        if getattr(self, "_storage", None):
            self._on_storage(self._storage)

    # ------------------------------------------------------------ 状态/生命周期

    def refresh(self, force: bool = False) -> None:
        if force:
            self._storage_loaded = False
        # 先就地写明「用的是哪一份网关」，再去后台问运行状态：
        # 端口/版本要等 HTTP，来源是本地文件检查，不该让人干等
        self.status_label.setText(i18n.t("读取 OpenList 状态…\n网关程序：{}", self._binary_note()))
        threading.Thread(target=self._load_status, daemon=True,
                         name="openlist-status").start()

    def _load_status(self) -> None:
        try:
            self.status_ready.emit(self.client.status())
        except Exception as exc:
            self.status_failed.emit(i18n.t("状态读取失败：{}", exc))

    def _binary_note(self) -> str:
        """网关用的是哪一份、在哪 —— 回答「到底有没有内嵌」。"""
        try:
            return f"{self.client.binary_origin()} · {storage.display_path(Path(self.client.binary()))}"
        except cloud.CloudError as exc:
            return str(exc)

    def _on_status_failed(self, text: str) -> None:
        """状态读不出来也要落在状态行上，别让人对着一句「读取中…」干等。"""
        self.status_label.setText(f"{text}\n" + i18n.t("网关程序：{}", self._binary_note()))

    def _on_status(self, info: dict) -> None:
        self._status = info
        running = bool(info.get("running"))
        binary = self._binary_note()
        if running:
            owned = i18n.t("本进程启动") if info.get("owned") else (
                i18n.t("外部进程 PID {}", ",".join(map(str, info.get("pids") or [])))
                if info.get("pids") else i18n.t("未知进程"))
            version = str(info.get("version") or "").split(" - ")[0]
            self.status_label.setText(
                i18n.t("运行中 · {} · 端口 {} · {}", version, info.get("port"), owned)
                + "\n" + i18n.t("网关程序：{}", binary))
        else:
            self.status_label.setText(i18n.t("已停止") + "\n" + i18n.t("网关程序：{}", binary))
        self.status_label.setToolTip(binary)
        self._update_buttons()
        if running and not self._storage_loaded:
            # 存储配置要先取一次管理员 token（子进程），给个中间态别写着「未读取」干等
            self.login_label.setText(i18n.t("读取 115 存储配置…"))
            threading.Thread(target=self._load_storage, daemon=True,
                             name="openlist-storage").start()
        if not running:
            self.login_label.setText(i18n.t("OpenList 未运行，先启动"))
            self.storage_ready.emit({})

    def _load_storage(self) -> None:
        try:
            self.storage_ready.emit(self.client.storage_115())
        except Exception as exc:
            self.qr_failed.emit(i18n.t("读取 115 存储失败：{}", exc))

    def _on_storage(self, storage: dict) -> None:
        self._loading_storage = True
        try:
            if not storage:
                self._storage_loaded = False
                self.save_storage_button.setEnabled(False)
                self.login_label.setText(i18n.t("OpenList 未运行，先启动"))
                return
            self._storage_loaded = True
            self._storage = storage
            self.save_storage_button.setEnabled(True)
            self.mount_edit.setText(str(storage.get("mount_path") or "/115"))
            policy = str(storage.get("webdav_policy") or "native_proxy")
            index = self.policy_combo.findData(policy)
            self.policy_combo.setCurrentIndex(index if index >= 0 else 0)
            self.web_proxy_check.setChecked(bool(storage.get("web_proxy", True)))
            self.range_check.setChecked(bool(storage.get("proxy_range", False)))
            self.cache_spin.setValue(int(storage.get("cache_expiration", 30) or 0))
            try:
                addition = json.loads(storage.get("addition") or "{}")
            except ValueError:
                addition = {}
            if str(addition.get("cookie") or "").strip():
                self.login_label.setText(i18n.t("已配置 115 Cookie；扫码可更新登录"))
            else:
                self.login_label.setText(i18n.t("未配置 115 Cookie，请扫码登录"))
        finally:
            self._loading_storage = False

    def _update_buttons(self) -> None:
        running = bool(self._status.get("running"))
        busy = self.pipeline.is_busy()
        self.start_button.setEnabled(not running and not busy)
        self.stop_button.setEnabled(running and not busy)
        self.restart_button.setEnabled(running and not busy)
        self.admin_button.setEnabled(running)
        self.scan_button.setEnabled(running and not busy and not self._qr_active)
        self.save_storage_button.setEnabled(running and self._storage_loaded and not busy)

    def _on_action(self, text: str) -> None:
        self.action_label.setText(text)
        self.refresh(force=True)

    def _ask_busy_or_playing(self, action: str) -> bool:
        if self.pipeline.is_busy():
            _warn(self, action, i18n.t("有转写/翻译任务在跑，先在 AI 栏停止任务再操作。"))
            return False
        source = self.pipeline.media_source
        if source is not None and source.is_remote:
            return _confirm(self, action,
                            i18n.t("当前正在播放网盘视频，操作会中断播放/实时翻译。"),
                            i18n.t("继续操作"))
        return True

    def _start(self) -> None:
        if self.pipeline.is_busy():
            _warn(self, i18n.t("启动 OpenList"), i18n.t("有任务在跑，先停止任务。"))
            return
        self.action_label.setText(i18n.t("启动 OpenList…"))
        def worker():
            try:
                self.client.ensure_running()
                self.client.token(force=True)
                self.action_done.emit(i18n.t("OpenList 已启动"))
            except Exception as exc:
                self.qr_failed.emit(i18n.t("启动失败：{}", exc))
        threading.Thread(target=worker, daemon=True, name="openlist-start").start()

    def _stop(self) -> None:
        if not self._ask_busy_or_playing(i18n.t("停止 OpenList")):
            return
        if not _confirm(self, i18n.t("停止 OpenList"),
                        i18n.t("停止后网盘播放、字幕和实时翻译都会中断。"), i18n.t("确定停止")):
            return
        self.action_label.setText(i18n.t("停止 OpenList…"))
        def worker():
            try:
                self.client.stop_server()
                self.action_done.emit(i18n.t("OpenList 已停止"))
            except Exception as exc:
                self.qr_failed.emit(i18n.t("停止失败：{}", exc))
        threading.Thread(target=worker, daemon=True, name="openlist-stop").start()

    def _restart(self) -> None:
        if not self._ask_busy_or_playing(i18n.t("重启 OpenList")):
            return
        if not _confirm(self, i18n.t("重启 OpenList"),
                        i18n.t("会中断当前播放/任务，重启后需要重新载入网盘。"), i18n.t("确定重启")):
            return
        self.action_label.setText(i18n.t("重启 OpenList…"))
        def worker():
            try:
                self.client.restart_server()
                self.action_done.emit(i18n.t("OpenList 已重启"))
            except Exception as exc:
                self.qr_failed.emit(i18n.t("重启失败：{}", exc))
        threading.Thread(target=worker, daemon=True, name="openlist-restart").start()

    def _open_admin(self) -> None:
        webbrowser.open(f"{self.client.base}/admin")

    def _open_folder(self) -> None:
        try:
            subprocess.run(["open", str(self.client.data_dir)], check=False)
        except OSError as exc:
            self.action_label.setText(i18n.t("打不开目录：{}", exc))

    def _open_log(self) -> None:
        log = Path(self.client.data_dir) / "server.out"
        if not log.exists():
            self.action_label.setText(i18n.t("还没有 OpenList 日志"))
            return
        try:
            subprocess.run(["open", str(log)], check=False)
        except OSError as exc:
            self.action_label.setText(i18n.t("打不开日志：{}", exc))

    # ------------------------------------------------------------ 存储设置

    def _save_storage(self) -> None:
        if not self._ask_busy_or_playing(i18n.t("保存 115 存储")):
            return
        policy = str(self.policy_combo.currentData() or "native_proxy")
        web_proxy = self.web_proxy_check.isChecked()
        if not web_proxy:
            if not _confirm(self, i18n.t("关闭 Web 代理"),
                            i18n.t("云端实时翻译的单流代理依赖 Web 代理；关闭后实时翻译可能不可用。"),
                            i18n.t("仍要关闭")):
                return
        if policy != "native_proxy":
            if not _confirm(self, i18n.t("改变 WebDAV 策略"),
                            i18n.t("非「本地代理」策略可能影响网盘拖动和字幕读取。"),
                            i18n.t("仍要保存")):
                return
        mount = self.mount_edit.text().strip() or "/115"
        cache = int(self.cache_spin.value())
        proxy_range = self.range_check.isChecked()
        self.action_label.setText(i18n.t("保存 115 存储设置…"))
        def worker():
            try:
                self.client.update_115_settings(
                    mount, web_proxy, policy, cache, proxy_range)
                self.storage_changed.emit()
                self.action_done.emit(i18n.t("115 存储设置已保存"))
            except Exception as exc:
                self.qr_failed.emit(i18n.t("保存存储失败：{}", exc))
        threading.Thread(target=worker, daemon=True, name="openlist-storage-save").start()

    # ------------------------------------------------------------ 115 扫码登录

    def _scan_115(self) -> None:
        if not self._status.get("running"):
            self.action_label.setText(i18n.t("先启动 OpenList"))
            return
        if not self._ask_busy_or_playing(i18n.t("登录 115")):
            return
        if self._qr_active:
            return
        self._qr_active = True
        cancel = threading.Event()
        self._qr_cancel = cancel
        self.qr_label.hide()
        self.cancel_qr_button.show()
        self.scan_button.setEnabled(False)
        self.login_label.setText(i18n.t("正在获取二维码…"))
        threading.Thread(target=self._qr_worker, args=(cancel,), daemon=True,
                         name="115-qr-login").start()

    def _qr_worker(self, cancel: threading.Event) -> None:
        try:
            token = self.client.qr_login_start()
        except Exception as exc:
            if not cancel.is_set():
                self.qr_failed.emit(str(exc))
            return
        if cancel.is_set():
            return
        self.qr_ready.emit(token)
        deadline = time.time() + 300.0
        last = ""
        while not cancel.is_set() and time.time() < deadline:
            try:
                data = self.client.qr_login_status(token)
            except Exception as exc:
                if cancel.is_set():
                    return
                msg = i18n.t("状态查询失败，重试…（{}）", str(exc)[:60])
                if msg != last:
                    self.qr_state.emit(msg)
                    last = msg
                time.sleep(3.0)
                continue
            status = int(data.get("status", 0) or 0)
            if status == 0:
                msg = i18n.t("等待扫码…")
            elif status == 1:
                msg = i18n.t("已扫码，请在手机上确认")
            elif status == 2:
                try:
                    cookie = self.client.qr_login_finish(token)
                    self.client.update_115_cookie(cookie)
                except Exception as exc:
                    if not cancel.is_set():
                        self.qr_failed.emit(i18n.t("115 登录失败：{}", exc))
                    return
                if not cancel.is_set():
                    self.qr_done.emit(i18n.t("115 登录成功，存储已刷新"))
                return
            elif status == -1:
                if not cancel.is_set():
                    self.qr_failed.emit(i18n.t("二维码已过期，请重新获取"))
                return
            elif status == -2:
                if not cancel.is_set():
                    self.qr_failed.emit(i18n.t("已取消扫码"))
                return
            else:
                msg = i18n.t("扫码状态 {}", status)
            if msg != last:
                self.qr_state.emit(msg)
                last = msg
        if not cancel.is_set():
            self.qr_failed.emit(i18n.t("二维码等待超时，请重新获取"))

    def _on_qr_ready(self, token: dict) -> None:
        pix = QPixmap()
        if not pix.loadFromData(token.get("png") or b""):
            self._on_qr_failed(i18n.t("二维码图片解析失败"))
            return
        self.qr_label.setPixmap(pix.scaled(210, 210, Qt.KeepAspectRatio,
                                           Qt.FastTransformation))
        self.qr_label.show()
        self.login_label.setText(i18n.t("等待扫码…（二维码几分钟内有效）"))

    def _set_login_text(self, text: str) -> None:
        self.login_label.setText(text)

    def _qr_finish_ui(self) -> None:
        self._qr_active = False
        self._qr_cancel = None
        self.qr_label.clear()
        self.qr_label.hide()
        self.cancel_qr_button.hide()
        self._update_buttons()

    def _on_qr_done(self, text: str) -> None:
        self._qr_finish_ui()
        self.action_label.setText(text)
        self.login_label.setText(i18n.t("已配置 115 Cookie"))
        self.storage_changed.emit()
        self.refresh(force=True)

    def _on_qr_failed(self, text: str) -> None:
        self._qr_finish_ui()
        self.login_label.setText(text)
        self.action_label.setText(text)

    def _cancel_qr(self) -> None:
        if self._qr_cancel is not None:
            self._qr_cancel.set()
        self._qr_finish_ui()
        self.login_label.setText(i18n.t("已取消扫码"))

    # ------------------------------------------------------------ 云端缓存设置

    def _pick_autostart(self) -> None:
        mode = "auto" if self.autostart_check.isChecked() else "manual"
        self.config.set("cloud.start_mode", mode)
        self.action_label.setText(
            i18n.t("网关将随软件启动、关窗一起停止") if mode == "auto"
            else i18n.t("已改为手动：启动/停止由这一页负责，退出软件不动外部进程"))

    def _pick_cache(self) -> None:
        if self._loading_storage:
            return
        value = self.cache_combo.currentData()
        if value is not None:
            self.config.set("cloud.live_cache_mb", int(value))
            self.action_label.setText(
                i18n.t("云端缓存改为 {} MB（下一次实时翻译生效）", value))

    def _pick_lead(self) -> None:
        value = self.lead_combo.currentData()
        if value is not None:
            self.config.set("cloud.live_lead_max", int(value))
            self.action_label.setText(i18n.t("云端预读上限改为 {} 秒", value))


CLEAN_CONSEQUENCES = {
    "live": "实时翻译的临时字幕；已经存好的正式字幕不受影响。",
    "work": "转写用的临时音频和从容器里抽出来的内嵌字幕。",
    "transcripts": "转写标记 —— 删掉后一键翻译会把这部片整个重转一遍。",
    "translations": "翻译缓存 —— 删掉后同样的句子要重翻。",
    "language_hints": "每部片单独指定的识别语言 —— 删掉后回到自动检测。",
    "parts": "只删没下完的 .part 断点，已经下好的模型不动。",
    "logs": "应用日志清空、播放器日志删除；正在跑的进程会继续写。",
    "build_cache": "编译网关用的 Go 工具链、源码与 module 缓存 —— "
                   "删了下次跑 scripts/build_openlist.sh 要重新下载（约 20 分钟）。",
    "portable_venv": "可分发打包用的独立 Python 环境（distvenv*）—— "
                     "本机运行不依赖它，下次打可分发包会重建。",
}


class StoragePanel(QWidget):
    """存储与清理：模型可以删，每个产出/下载/缓存的路径都摊开显示、都能直接打开。"""

    models_changed = Signal()
    _sizes_ready = Signal(object)

    def __init__(self, config, pipeline, client=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.pipeline = pipeline
        self.client = client
        self._texts = _Texts(self)
        self._sizes: dict[str, QLabel] = {}
        self._titles: dict[str, QLabel] = {}
        self._scanning = False

        body = QWidget(self)
        box = QVBoxLayout(body)
        box.setContentsMargins(16, 10, 16, 24)
        box.setSpacing(8)
        box.addWidget(self._texts.section("占用概览"))
        self.total_label = _hint(i18n.t("统计中…"))
        box.addWidget(self.total_label)
        self.gateway_label = _hint("")
        self.gateway_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.gateway_label)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        box.addLayout(self.rows)
        box.addWidget(self._texts.hint("删除与清理只动上面这些路径，绝不碰视频本身；"
                                       "有任务在跑时会拒绝清理。"))
        self.note_label = QLabel("", body)
        self.note_label.setObjectName("ValueLabel")
        self.note_label.setWordWrap(True)
        box.addWidget(self.note_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.rescan_button = _mini_button("", lambda: self.reload())
        self._texts.label(self.rescan_button, "重新统计")
        buttons.addWidget(self.rescan_button)
        self.clean_all_button = QPushButton("")
        self._texts.label(self.clean_all_button, "一键清理临时与缓存…")
        theme.set_role(self.clean_all_button, "ghost")
        self.clean_all_button.clicked.connect(self._clean_all)
        buttons.addWidget(self.clean_all_button)
        buttons.addStretch(1)
        box.addLayout(buttons)

        self._sizes_ready.connect(self._on_sizes)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(body))
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        self._texts.apply()
        self._render_total()       # 概览是拼出来的字符串，用上一次的数字重写
        self.reload()              # 台账分类/条目名都在这时候重算

    # ------------------------------------------------------------ 行

    def _clear_rows(self) -> None:
        self._sizes.clear()
        self._titles.clear()
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _row(self, title: str, path: Path,
             actions: list[tuple[str, object]] = (), size: int | None = None) -> QWidget:
        """一行台账：左边标题 + 路径，右边体积 + 动作按钮。"""
        card = QWidget(self)
        line = QHBoxLayout(card)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(1)
        title_label = _ElidedLabel(title, card)
        title_label.setToolTip(f"{title}\n{path}")
        left.addWidget(title_label)
        left.addWidget(_path_label(path, card))
        line.addLayout(left, 1)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(3)
        size_label = QLabel(i18n.t("统计中…") if size is None
                            else storage.human_size(size), card)
        size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        size_label.setObjectName("HintLabel")
        size_label.setToolTip(str(path))
        right.addWidget(size_label)
        row_buttons = QHBoxLayout()
        row_buttons.setContentsMargins(0, 0, 0, 0)
        row_buttons.setSpacing(4)
        for text, handler in actions:
            row_buttons.addWidget(_mini_button(text, handler))
        right.addLayout(row_buttons)
        line.addLayout(right)
        self.rows.addWidget(card)
        self._sizes[str(path)] = size_label
        self._titles[str(path)] = title_label
        return card

    def _build_rows(self) -> None:
        self._clear_rows()
        for section, items in storage.sections(self.config):
            self.rows.addWidget(_section(i18n.t(section)))
            for item in items:
                actions = [(i18n.t("打开"), lambda _=False, p=item.path: storage.reveal(p))]
                if item.clean:
                    actions.append(
                        (i18n.t("清理"),
                         lambda _=False, k=item.clean, n=item.label: self._clean(k, n)))
                if item.label == "字幕输出目录":
                    actions.append((i18n.t("字幕库…"), self._open_library))
                self._row(i18n.t(item.label), item.path, actions)
                if item.note:
                    self.rows.addWidget(_hint(i18n.t(item.note)))
        self.rows.addWidget(_section(i18n.t("模型（可删除）")))
        self._row(i18n.t("转写模型目录"), cfg.ASR_MODEL_DIR,
                  [(i18n.t("打开"), lambda _=False: storage.reveal(cfg.ASR_MODEL_DIR))])
        self._row(i18n.t("翻译模型目录"), cfg.LLM_MODEL_DIR,
                  [(i18n.t("打开"), lambda _=False: storage.reveal(cfg.LLM_MODEL_DIR))])
        for model in storage.installed_models(self.config):
            # 用途放前面：中间省略会砍掉长文件名的中段，型号后缀先保住
            usage = "、".join(i18n.t(u) for u in model.usage)
            title = (f"[{usage}] {model.name}" if model.usage else model.name)
            self._row(title, model.path,
                      [(i18n.t("删除"),
                        lambda _=False, m=model: self._delete_model(m))],
                      size=model.size)
        parts = storage.leftover_parts(self.config)
        if parts:
            self.rows.addWidget(_section(i18n.t("没下完的断点")))
            for part in parts:
                self._row(part.name, part,
                          [(i18n.t("删除"),
                            lambda _=False, p=part: self._delete_part(p))],
                          size=storage.path_size(part))

    def reload(self) -> None:
        self._build_rows()
        self._sync_gateway_line()
        self._scan_sizes()

    def _sync_gateway_line(self) -> None:
        """网关到底用的是哪一份 —— 界面上必须能直接回答这个问题。"""
        if self.client is None:
            self.gateway_label.setText("")
            return
        try:
            path = Path(self.client.binary())
            origin = self.client.binary_origin()
            size = storage.human_size(storage.path_size(path))
            self.gateway_label.setText(i18n.t("网关程序：{} · {}\n{}", origin, size,
                                              storage.display_path(path)))
            self.gateway_label.setToolTip(str(path))
        except cloud.CloudError as exc:
            self.gateway_label.setText(i18n.t("网关程序：{}", exc))

    def on_show(self) -> None:
        self.reload()

    # ------------------------------------------------------------ 体积

    def _scan_sizes(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        targets = [item for _section, items in storage.sections(self.config)
                   for item in items]

        def worker() -> None:
            # 一行一行往外发：整个数据目录要扫好几秒，别让人对着满屏「统计中…」
            for item in targets:
                self._sizes_ready.emit(
                    {"rows": [(str(item.path), storage.target_size(item, self.config))]})
            self._sizes_ready.emit({
                "rows": [(str(cfg.ASR_MODEL_DIR), storage.path_size(cfg.ASR_MODEL_DIR)),
                         (str(cfg.LLM_MODEL_DIR), storage.path_size(cfg.LLM_MODEL_DIR))],
                "total": storage.path_size(cfg.SUPPORT_DIR),
                "models": sum(model.size
                              for model in storage.installed_models(self.config)),
                "scratch": (storage.path_size(cfg.WORK_DIR)
                            + storage.path_size(cfg.LIVE_DIR)
                            + storage.path_size(cfg.CACHE_DIR)
                            + storage.path_size(storage.build_dir())
                            + storage.path_size(storage.gateway_legacy_path())),
            })
        threading.Thread(target=worker, daemon=True, name="storage-scan").start()

    def _on_sizes(self, result: dict) -> None:
        for path, size in result.get("rows") or []:
            label = self._sizes.get(path)
            if label is not None:
                label.setText(storage.human_size(size))
                label.setToolTip(f"{storage.human_size(size)} · {path}")
        if "total" not in result:
            return
        self._scanning = False
        self._last_total = result
        self._render_total()

    def _render_total(self) -> None:
        result = getattr(self, "_last_total", None)
        if not result:
            return
        self.total_label.setText(
            i18n.t("应用数据目录共 {}，其中模型 {}、缓存/临时/构建 {}",
                   storage.human_size(result.get("total") or 0),
                   storage.human_size(result.get("models") or 0),
                   storage.human_size(result.get("scratch") or 0)))

    # ------------------------------------------------------------ 删除/清理

    def _busy_guard(self, action: str) -> bool:
        if self.pipeline.is_busy():
            _warn(self, action, i18n.t("有转写/翻译任务在跑，先在 AI 栏停止任务。"))
            return False
        return True

    def _delete_model(self, model: storage.ModelFile) -> None:
        if not self._busy_guard(i18n.t("删除模型")):
            return
        usage = (i18n.t("\n它正在被使用：{}", "、".join(i18n.t(u) for u in model.usage))
                 if model.usage else "")
        if not _confirm(self, i18n.t("删除模型"),
                        i18n.t("要从磁盘删除：\n{}\n{}{}\n\n删除后要用就得重新下载。",
                               model.path, storage.human_size(model.size), usage),
                        i18n.t("确定删除")):
            return
        try:
            note = storage.delete_model(model.kind, model.name, self.config)
        except storage.StorageError as exc:
            _warn(self, i18n.t("删除失败"), str(exc))
            return
        self.note_label.setText(note)
        self.models_changed.emit()
        self.reload()

    def _delete_part(self, part: Path) -> None:
        if not self._busy_guard(i18n.t("删除断点")):
            return
        try:
            size = part.stat().st_size if part.is_file() else 0
            part.unlink(missing_ok=True)
        except OSError as exc:
            _warn(self, i18n.t("删除失败"), i18n.t("删不掉 {}：{}", part.name, exc))
            return
        self.note_label.setText(i18n.t("已删除断点 {}（{}）", part.name,
                                       storage.human_size(size)))
        self.reload()

    def _clean(self, key: str, label: str) -> None:
        if not self._busy_guard(i18n.t("清理")):
            return
        if key == storage.GATEWAY_LEGACY_KEY and self.client is not None:
            try:
                if Path(self.client.binary()) == storage.gateway_legacy_path():
                    _warn(self, i18n.t("清理"),
                          i18n.t("当前网关跑的就是数据目录里那一份旧副本；"
                                 "先用「网盘 → 网关服务」重启一次让它换到 App 内置的，再删。"))
                    return
            except cloud.CloudError:
                pass
        count, size = storage.preview(key, self.config)
        if not count:
            self.note_label.setText(i18n.t("「{}」没有可清理的文件", i18n.t(label)))
            return
        if not _confirm(self, i18n.t("清理"),
                        i18n.t("将清理「{}」：{} 个文件 · 约 {}\n{}", i18n.t(label), count,
                               storage.human_size(size),
                               i18n.t(CLEAN_CONSEQUENCES.get(key, ""))),
                        i18n.t("确定清理")):
            return
        removed, freed = storage.clean(key, self.config)
        cfg.append_log(f"清理「{label}」({key})：{removed} 个文件 · "
                       f"{storage.human_size(freed)}")
        self.note_label.setText(
            i18n.t("已清理「{}」{} 个文件 · 释放 {}", i18n.t(label), removed,
                   storage.human_size(freed)))
        self.models_changed.emit()
        self.reload()

    def _clean_all(self) -> None:
        if not self._busy_guard(i18n.t("一键清理")):
            return
        count, size = storage.clean_all(self.config, dry_run=True)
        if not count:
            self.note_label.setText(i18n.t("临时文件与缓存都是干净的"))
            return
        if not _confirm(self, i18n.t("一键清理"),
                        i18n.t("将清理临时音频、实时字幕、转写标记、翻译缓存、语言台账、"
                               "下载断点与日志：\n{} 个文件 · 约 {}\n"
                               "模型和已生成的字幕不会动。", count, storage.human_size(size)),
                        i18n.t("确定清理")):
            return
        removed, freed = storage.clean_all(self.config)
        cfg.append_log(f"一键清理临时与缓存：{removed} 个文件 · "
                       f"{storage.human_size(freed)}")
        self.note_label.setText(
            i18n.t("已清理 {} 个文件 · 释放 {}", removed, storage.human_size(freed)))
        self.models_changed.emit()
        self.reload()

    def _open_library(self) -> None:
        SubtitleLibrary(storage.subtitle_root(self.config), self).exec()
        self.reload()


class CloudTab(QWidget):
    """网盘一栏：「浏览 115」与「OpenList 管理」用分段开关切，省掉两个挤在一起的标签。"""

    def __init__(self, config, pipeline, client=None, parent=None):
        super().__init__(parent)
        self.client = client or cloud.OpenList(config)
        self._texts = _Texts(self)
        self.browser = CloudPanel(config, self.client, self)
        self.manage = CloudManagePanel(config, pipeline, self.client, self)
        self.manage.storage_changed.connect(self.browser.refresh)
        switch = QWidget(self)
        row = QHBoxLayout(switch)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(4)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.manage)
        self.buttons: list[QToolButton] = []
        for index, label in enumerate(("网盘文件", "网关服务")):
            button = QToolButton(switch)
            self._texts.label(button, label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            theme.set_role(button, "chip")
            button.clicked.connect(lambda _=False, i=index: self.show_subpage(i))
            row.addWidget(button)
            self.buttons.append(button)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(switch)
        box.addWidget(self.stack, 1)
        self.show_subpage(0)

    def show_subpage(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.buttons):
            button.setChecked(button_index == index)
        # 只从「网盘」整页进来才会走 on_show；在页内切分段开关也得触发一次读取，
        # 否则服务页会一直停在初始的「读取状态…」不动
        current = self.stack.widget(index)
        if hasattr(current, "refresh"):
            current.refresh()

    def on_show(self) -> None:
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            current.refresh()


class Sidebar(QWidget):
    """标签式侧栏；主窗口控制显隐，显隐时重排视频区，浮层跟着走。

    标签一律走网格排布：六个标签一行放不下会被 QToolButton 省略成「…」，
    3 列 2 行才给得起每个标签的正常宽度。
    """

    close_requested = Signal()
    language_toggle_requested = Signal()

    PAGES = ("轨道", "AI", "设置", "列表", "存储", "网盘")
    PAGE_HINTS = ("音轨 / 字幕轨 / 显示方式", "转写与翻译任务、模型选择",
                  "解码、字幕外观、字幕文件、默认语言", "播放列表与打开历史",
                  "模型删除、所有产出与缓存路径、清理", "115 浏览与 OpenList 管理")

    def __init__(self, config, engine, pipeline, client=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        tabs = QWidget(self)
        tabs.setObjectName("SidebarTitle")
        grid = QGridLayout(tabs)
        grid.setContentsMargins(12, 10, 12, 6)
        grid.setSpacing(4)
        self.stack = QStackedWidget(self)
        self.tracks = TracksPanel(config, engine, pipeline, self)
        self.ai = AIPanel(config, engine, pipeline, self)
        self.settings = SettingsPanel(config, engine, pipeline, self)
        self.playlist = PlaylistPanel(config, self)
        self.storage = StoragePanel(config, pipeline, client, self)
        self.storage.models_changed.connect(self.ai.reload_models)
        self.cloud = CloudTab(config, pipeline, client, self)
        self.cloud_manage = self.cloud.manage
        self.cloud_browser = self.cloud.browser
        for widget in (self.tracks, self.ai, self.settings, self.playlist,
                       self.storage, self.cloud):
            self.stack.addWidget(widget)

        self.buttons: list[QToolButton] = []
        for index, label in enumerate(self.PAGES):
            button = QToolButton(tabs)
            button.setText(label)
            button.setToolTip(self.PAGE_HINTS[index])
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            # Expanding：撑满所在格子，布局就不敢把它压到省略号
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            theme.set_role(button, "chip")
            button.clicked.connect(lambda _=False, i=index: self.show_page(i))
            grid.addWidget(button, index // 3, index % 3)
            self.buttons.append(button)
        close = QToolButton(tabs)
        close.setIcon(theme.icon("close", "#cfd6e2", 18))
        close.setToolTip("收起侧栏")
        close.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        close.clicked.connect(self.close_requested)
        grid.addWidget(close, 0, 3)
        self.close_button = close
        for column in range(3):
            grid.setColumnStretch(column, 1)
        grid.setColumnStretch(3, 0)

        box.addWidget(tabs)

        # 语言开关放这儿而不是埋在设置里：中文界面下按钮写「English」，
        # 认不出中文的人也能一眼找到；不占标签网格的宽度
        self.language_button = QToolButton(self)
        self.language_button.setObjectName("LanguageChip")
        self.language_button.setCursor(Qt.PointingHandCursor)
        self.language_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.language_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.language_button.clicked.connect(self.language_toggle_requested)
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(12, 0, 12, 6)
        lang_row.addWidget(self.language_button)
        box.addLayout(lang_row)

        box.addWidget(self.stack, 1)
        self.show_page(0)
        i18n.bind(self, self._apply_texts)

    def _apply_texts(self) -> None:
        for button, label, hint in zip(self.buttons, self.PAGES, self.PAGE_HINTS):
            button.setText(i18n.t(label))
            button.setToolTip(i18n.t(hint))
        self.close_button.setToolTip(i18n.t("收起侧栏"))
        other = "English" if i18n.current() == "zh" else "中文"
        self.language_button.setText(other)
        self.language_button.setToolTip(
            "Switch to English" if other == "English" else "切换成中文")

    def show_page(self, index: int, sub: int | None = None) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.buttons):
            button.setChecked(button_index == index)
        page = self.stack.widget(index)
        if sub is not None and hasattr(page, "show_subpage"):
            page.show_subpage(sub)
        if hasattr(page, "on_show"):
            page.on_show()

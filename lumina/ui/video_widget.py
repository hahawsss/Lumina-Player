"""把 mpv 的画面直接画进 Qt 控件的 GL widget —— 真单窗口的关键一块。

要点（都是实测出来的）：
  · libmpv 的 OpenGL 渲染路径用的是**调用方当前的 GL 上下文**，所以不需要 mpv 自带
    的 cocoa GL 上下文（那个在 0.37 之后被上游删了也不影响这里）。
  · get_proc_address 回调可能在 mpv 的线程上被调用，解析要能在无当前上下文时兜底
    （回落到 OpenGL.framework 的符号）。
  · 渲染尺寸必须乘 devicePixelRatio，否则 Retina 上只有一半分辨率。
  · FBO 朝向：实测 flip_y=1 是对的（Qt 的 FBO 原点在左下），用「上红下蓝」素材
    由 scripts/verify_embed.py 常驻判定，改坏了会直接红。
"""
from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..mpv_lib import (GET_PROC, P_API_TYPE, P_BLOCK_TARGET, P_FLIP_Y, P_GL_FBO, P_GL_INIT,
                       GLFbo, GLInit, RenderParam, UPDATE_CB)


def ensure_gl_format() -> None:
    """macOS 上 Qt 默认给的是 GL 2.1 兼容上下文，mpv 会打印
    「GLSL version too old / need >= OpenGL 3.0 for core rectangle texture」
    然后硬解整条路都起不来 —— 必须在建控件之前把默认格式抬到 3.3 core。
    """
    current = QSurfaceFormat.defaultFormat()
    if current.profile() == QSurfaceFormat.CoreProfile and current.majorVersion() >= 3:
        return
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)


def _params(*pairs):
    arr = (RenderParam * (len(pairs) + 1))()
    keep: list = []
    for index, (kind, data) in enumerate(pairs):
        arr[index].type = kind
        if data is None:
            arr[index].data = 0
        elif isinstance(data, int):
            arr[index].data = data
        else:
            keep.append(data)
            arr[index].data = ctypes.addressof(data)
    arr[len(pairs)].type = 0
    arr[len(pairs)].data = 0
    return arr, keep


class MpvVideoWidget(QOpenGLWidget):
    """mpv 的渲染目标控件。"""

    frame_ready = Signal()
    context_ready = Signal()

    def __init__(self, player, parent=None):
        ensure_gl_format()
        super().__init__(parent)
        self.player = player
        self.lib = None
        self.ctx = None
        self.qctx: QOpenGLContext | None = None
        self.gl = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
        self._proc_cb = GET_PROC(self._resolve_proc)
        self._update_cb = UPDATE_CB(self._on_update)
        self._keep: list = []
        self.frames = 0
        self.update_hits = 0
        self.paint_ms = 0.0
        self._no_block = ctypes.c_int(0)
        self.errors: list[str] = []
        self.flip_y = 1
        self._disposed = False
        self.setMouseTracking(True)
        self._driver = QTimer(self)
        self._driver.setTimerType(Qt.PreciseTimer)
        # 正常节奏靠 mpv 的 update 回调（每帧一次）；这个定时器是掉帧兜底，
        # 33ms ≈ 30fps 的下限，够 25fps 的片子即使一次回调都没收到也不至于卡住。
        self._driver.setInterval(33)
        self._driver.timeout.connect(self.update)
        self.frame_ready.connect(self.update)
        # 首帧往往比 mpv 起来得早：那时没有 handle，上下文建不了，而没有上下文就
        # 永远等不到 mpv 的 update 回调 —— 所以引擎就绪时必须再催一次重绘。
        player.connected.connect(self.update)

    # ------------------------------------------------------------ GL 生命周期

    def _resolve_proc(self, _ctx, name):
        key = name.decode() if isinstance(name, bytes) else str(name)
        addr = 0
        context = QOpenGLContext.currentContext() or self.qctx
        if context is not None:
            try:
                addr = int(context.getProcAddress(key) or 0)
            except Exception as exc:
                self.errors.append(f"getProcAddress({key}): {exc}")
        if not addr:
            try:
                symbol = getattr(self.gl, key, None)
                if symbol:
                    addr = ctypes.cast(symbol, ctypes.c_void_p).value or 0
            except Exception:
                addr = 0
        if not addr:
            self.errors.append(f"解析不到 GL 函数 {key}")
        return addr

    def _ensure_context(self) -> bool:
        if self._disposed or not self.player.handle:
            return False
        if self.ctx:
            return True
        self.lib = self.player.lib
        self.qctx = QOpenGLContext.currentContext()
        init = GLInit(ctypes.cast(self._proc_cb, ctypes.c_void_p).value, None)
        api = ctypes.create_string_buffer(b"opengl")
        arr, keep = _params((P_API_TYPE, api), (P_GL_INIT, init), (0, None))
        self._keep = [api, init, keep]
        out = ctypes.c_void_p()
        rc = self.lib.mpv_render_context_create(ctypes.byref(out), self.player.handle, arr)
        if rc != 0:
            self.errors.append(f"render_context_create rc={rc}")
            return False
        self.ctx = out.value
        self.lib.mpv_render_context_set_update_callback(self.ctx, self._update_cb, None)
        self._driver.start()
        self.context_ready.emit()
        return True

    def _on_update(self, _ptr):
        self.update_hits += 1
        self.frame_ready.emit()

    def initializeGL(self) -> None:
        self._ensure_context()

    def resizeGL(self, _w: int, _h: int) -> None:
        self.update()

    def paintGL(self) -> None:
        if not self._ensure_context() or not self.lib:
            return
        ratio = self.devicePixelRatioF() or 1.0
        fbo = GLFbo(int(self.defaultFramebufferObject()),
                    int(self.width() * ratio), int(self.height() * ratio), 0)
        flip = ctypes.c_int(self.flip_y)
        # 默认 mpv 会把 render() 阻塞到「这一帧该显示的时刻」，等于在 paintGL 里睡 33ms，
        # 整个 Qt 事件循环跟着卡；关掉它，节奏交给 mpv 的 update 回调 + 我们的 16ms 驱动。
        arr, keep = _params((P_GL_FBO, fbo), (P_FLIP_Y, flip),
                            (P_BLOCK_TARGET, self._no_block), (0, None))
        started = time.perf_counter()
        rc = self.lib.mpv_render_context_render(self.ctx, arr)
        del keep
        self.paint_ms = (time.perf_counter() - started) * 1000.0
        if rc != 0:
            self.errors.append(f"render rc={rc}")
            return
        self.lib.mpv_render_context_report_swap(self.ctx)
        self.frames += 1

    def grab_frame(self) -> QImage:
        """内嵌模式下的截图只能自己来：mpv 的 screenshot 命令在 vo=libmpv 上会等不到
        帧（实测 8s 无回复），而 grabFramebuffer() 抓到的正是用户此刻看到的画面（含字幕）。"""
        self.repaint()                 # 不等事件循环，保证抓到最新一帧
        return self.grabFramebuffer()

    def dispose(self) -> None:
        self._disposed = True          # 关闭途中来一帧 paint 就会再造一个上下文，mpv 销毁时直接 assert
        self._driver.stop()
        if self.ctx and self.lib:
            try:
                # 传 None 会让 ctypes 直接抛 ArgumentError（它不接受函数指针的 None），
                # 结果 free 被跳过，mpv 销毁时以「Broken API use」abort —— 必须显式 cast 出空指针
                null_cb = ctypes.cast(None, UPDATE_CB)
                self.lib.mpv_render_context_set_update_callback(self.ctx, null_cb, None)
                self.lib.mpv_render_context_free(self.ctx)
            except Exception as exc:
                self.errors.append(f"释放渲染上下文失败：{exc}")
        self.ctx = None

    # ------------------------------------------------------------ 鼠标：交给浮层处理

    def mousePressEvent(self, event) -> None:  # noqa: N802
        handler = getattr(self.parentWidget(), "media_mouse_press", None)
        if handler:
            handler(event)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        handler = getattr(self.parentWidget(), "media_mouse_double", None)
        if handler:
            handler(event)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        handler = getattr(self.parentWidget(), "media_mouse_move", None)
        if handler:
            handler(event)
        super().mouseMoveEvent(event)

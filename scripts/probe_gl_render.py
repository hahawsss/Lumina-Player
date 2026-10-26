#!/usr/bin/env python
"""GL 内嵌可行性探针：用 brew 现成的 libmpv + QOpenGLWidget 走 MPV_RENDER_API_TYPE_OPENGL。

要回答的问题：
  1. mpv_render_context_create(opengl) 在 macOS 上能不能成功（不需要 0.36 那个 cocoa 上下文？）
  2. 渲染进 QOpenGLWidget 后 grab() 出来是不是真的有画面（含字幕）
  3. 帧率多少 —— update 回调驱动的连续渲染能跑多快
  4. vo=libmpv + GL 下 VideoToolbox 硬解还在不在（hwdec-current）

需要真实窗口，所以用 cocoa 平台跑（屏幕上会闪一个小窗）。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QOpenGLContext  # noqa: E402
from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

LIB = "/opt/homebrew/lib/libmpv.2.dylib"
MEDIA = str(Path.home() / "Library/Application Support/Lumina/work/fixture/multi.mkv")
P_INVALID, P_API_TYPE, P_GL_INIT, P_GL_FBO, P_FLIP_Y = 0, 1, 2, 3, 4

GET_PROC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
UPDATE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class InitParams(ctypes.Structure):
    _fields_ = [("get_proc_address", GET_PROC), ("get_proc_address_ctx", ctypes.c_void_p)]


class FBO(ctypes.Structure):
    _fields_ = [("fbo", ctypes.c_int), ("w", ctypes.c_int),
                ("h", ctypes.c_int), ("internal_format", ctypes.c_int)]


class Param(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


def load():
    lib = ctypes.CDLL(LIB)
    lib.mpv_create.restype = ctypes.c_void_p
    lib.mpv_initialize.argtypes = [ctypes.c_void_p]
    lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.mpv_set_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.mpv_get_property_string.restype = ctypes.c_char_p
    lib.mpv_get_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.mpv_free.argtypes = [ctypes.c_void_p]
    lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
    lib.mpv_render_context_create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                              ctypes.POINTER(Param)]
    lib.mpv_render_context_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(Param)]
    lib.mpv_render_context_set_update_callback.argtypes = [ctypes.c_void_p, UPDATE_CB,
                                                           ctypes.c_void_p]
    lib.mpv_render_context_report_swap.argtypes = [ctypes.c_void_p]
    lib.mpv_render_context_free.argtypes = [ctypes.c_void_p]
    return lib


def params(*pairs):
    """返回 (数组, 保引用列表)：ctypes 结构体要留在 Python 侧活着，
    否则临时对象被回收，mpv 读到的就是悬垂指针。"""
    arr = (Param * (len(pairs) + 1))()
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
    arr[len(pairs)].type = P_INVALID
    arr[len(pairs)].data = 0
    return arr, keep


class GLPlayer(QOpenGLWidget):
    # mpv 的 update 回调跑在它自己的线程上，只能靠信号排队回 GUI 线程
    update_requested = Signal()

    def __init__(self, lib, handle):
        super().__init__()
        self.lib = lib
        self.handle = handle
        self.ctx = None
        self.qctx: QOpenGLContext | None = None
        self.gl = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL",
                              use_errno=True)
        self.frames = 0
        self.started = 0.0
        self.errors: list[str] = []
        self._proc_cb = GET_PROC(self._resolve)
        self._update_cb = UPDATE_CB(self._on_update)
        self.update_requested.connect(self.update)
        self._driver = QTimer(self)
        self._driver.setTimerType(Qt.PreciseTimer)
        self._driver.setInterval(16)
        self._driver.timeout.connect(self.update)
        self.resize(960, 540)

    def _resolve(self, _ctx, name):
        key = name.decode() if isinstance(name, bytes) else str(name)
        addr = 0
        if self.qctx is not None:
            try:
                addr = int(self.qctx.getProcAddress(key) or 0)
            except Exception as exc:
                self.errors.append(f"getProcAddress({key}): {exc}")
        if not addr:
            try:
                addr = ctypes.cast(getattr(self.gl, key, None), ctypes.c_void_p).value or 0
            except Exception:
                addr = 0
        if not addr:
            self.errors.append(f"解析不到 GL 函数 {key}")
        return addr

    def _on_update(self, _ptr):
        self.update_requested.emit()

    def initializeGL(self):
        self.qctx = QOpenGLContext.currentContext()
        print(f"  GL 上下文: {'有' if self.qctx else '无'}  "
              f"格式版本={self.qctx.format().majorVersion() if self.qctx else '?'}."
              f"{self.qctx.format().minorVersion() if self.qctx else '?'}", flush=True)
        init = InitParams(self._proc_cb, None)
        out = ctypes.c_void_p()
        arr, keep = params((P_API_TYPE, ctypes.create_string_buffer(b"opengl")),
                           (P_GL_INIT, init), (P_INVALID, None))
        rc = self.lib.mpv_render_context_create(ctypes.byref(out), self.handle, arr)
        print(f"  mpv_render_context_create(opengl) → {rc}", flush=True)
        del keep
        if rc != 0:
            self.errors.append(f"render_context_create 返回 {rc}")
            return
        self.ctx = out.value
        self.lib.mpv_render_context_set_update_callback(self.ctx, self._update_cb, None)
        self._driver.start()
        self.started = time.perf_counter()
        argv = (ctypes.c_char_p * 3)(b"loadfile", MEDIA.encode(), None)
        print(f"  loadfile → {self.lib.mpv_command(self.handle, argv)}", flush=True)

    def paintGL(self):
        if not self.ctx:
            return
        ratio = self.devicePixelRatioF()
        fbo = FBO(int(self.defaultFramebufferObject()),
                  int(self.width() * ratio), int(self.height() * ratio), 0)
        arr, keep = params((P_GL_FBO, fbo), (P_INVALID, None))
        rc = self.lib.mpv_render_context_render(self.ctx, arr)
        del keep
        if self.frames == 0:
            print(f"  首帧 render rc={rc}  fbo={fbo.fbo} 尺寸={fbo.w}x{fbo.h}", flush=True)
        if rc != 0:
            self.errors.append(f"render 返回 {rc}")
            return
        self.lib.mpv_render_context_report_swap(self.ctx)
        self.frames += 1
        if self.frames == 40:
            self.grab().save("/tmp/gl_probe.png")
            elapsed = time.perf_counter() - self.started
            hwdec = self.lib.mpv_get_property_string(self.handle, b"hwdec-current")
            pos = self.lib.mpv_get_property_string(self.handle, b"time-pos")
            print(f"  40 帧用了 {elapsed:.2f}s → {40/elapsed:.1f} fps", flush=True)
            print(f"  hwdec-current = {hwdec.decode() if hwdec else 'None'}", flush=True)
            print(f"  time-pos = {pos.decode() if pos else 'None'}", flush=True)
            print(f"  存图 /tmp/gl_probe.png  错误 {len(self.errors)} 条", flush=True)
            for line in sorted(set(self.errors))[:6]:
                print(f"    · {line}", flush=True)
            QApplication.instance().quit()


def main() -> int:
    app = QApplication(sys.argv)
    lib = load()
    handle = lib.mpv_create()
    for key, value in ((b"vo", b"libmpv"), (b"config", b"no"), (b"terminal", b"no"),
                       (b"hwdec", b"auto-safe"), (b"sub-auto", b"fuzzy"),
                       (b"keep-open", b"yes"), (b"idle", b"yes")):
        lib.mpv_set_option_string(handle, key, value)
    print(f"mpv_initialize → {lib.mpv_initialize(handle)}")
    widget = GLPlayer(lib, handle)
    widget.setWindowTitle("Lumina GL 探针")
    widget.show()
    QTimer.singleShot(25000, app.quit)        # 兜底超时
    app.exec()
    elapsed = max(0.001, time.perf_counter() - widget.started) if widget.started else 0.001
    print(f"  总帧数={widget.frames}  用时={elapsed:.2f}s  "
          f"{'≈%.1f fps' % (widget.frames / elapsed) if widget.frames else ''}", flush=True)
    for line in sorted(set(widget.errors))[:8]:
        print(f"    · {line}", flush=True)
    if widget.ctx:
        lib.mpv_render_context_free(widget.ctx)
    lib.mpv_terminate_destroy(handle)
    ok = widget.frames >= 40
    print("GLPROBE PASS" if ok else "GLPROBE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

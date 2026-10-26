#!/usr/bin/env python
"""libmpv 软件渲染可行性探针。

要回答三个问题，答不上来就不该去换引擎：
  1. ctypes 能不能建起 MPV_RENDER_API_TYPE_SW 的 render context 并真的画出帧？
  2. 能不能零拷贝渲染进 QImage 自己的缓冲（拿得到它的内存地址吗）？
  3. 每帧多少毫秒 —— 720p/1080p 下撑不撑得起 24/30 fps？
另外顺带看 vo=libmpv 时 VideoToolbox 硬解还在不在。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

LIB = "/opt/homebrew/lib/libmpv.2.dylib"
MEDIA = str(Path.home() / "Library/Application Support/Lumina/work/fixture/multi.mkv")

P_INVALID, P_API_TYPE = 0, 1
P_SW_SIZE, P_SW_FORMAT, P_SW_STRIDE, P_SW_POINTER = 17, 18, 19, 20
FMT_RGB0 = b"rgb0"


class RenderParam(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


def load_lib():
    lib = ctypes.CDLL(LIB)
    lib.mpv_create.restype = ctypes.c_void_p
    lib.mpv_initialize.argtypes = [ctypes.c_void_p]
    lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.mpv_set_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.mpv_get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    lib.mpv_free.argtypes = [ctypes.c_void_p]
    lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
    lib.mpv_render_context_create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                              ctypes.POINTER(RenderParam)]
    lib.mpv_render_context_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(RenderParam)]
    lib.mpv_render_context_set_update_callback.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                           ctypes.c_void_p]
    lib.mpv_render_context_report_swap.argtypes = [ctypes.c_void_p]
    lib.mpv_render_context_free.argtypes = [ctypes.c_void_p]
    return lib


def _addr(obj) -> int:
    """参数里的 data 一律要裸地址：ctypes 对象取 addressof，已经是 int 的直接用。"""
    return obj if isinstance(obj, int) else ctypes.addressof(obj)


def params(*pairs):
    arr = (RenderParam * (len(pairs) + 1))()
    for index, (kind, pointer) in enumerate(pairs):
        arr[index].type = kind
        arr[index].data = _addr(pointer) if pointer is not None else None
    arr[len(pairs)].type = P_INVALID
    arr[len(pairs)].data = None
    return arr


def main() -> int:
    app = QApplication([])          # QImage 需要 GUI 栈
    lib = load_lib()
    handle = lib.mpv_create()
    if not handle:
        print("mpv_create 返回空")
        return 1
    for key, value in ((b"vo", b"libmpv"), (b"config", b"no"), (b"idle", b"yes"),
                       (b"term_display", b"no"), (b"hwdec", b"auto-safe"),
                       (b"sub-auto", b"fuzzy"), (b"keep-open", b"yes")):
        rc = lib.mpv_set_option_string(handle, key, value)
        if rc < 0:
            print(f"  set_option {key.decode()} → {rc}")
    print(f"mpv_initialize → {lib.mpv_initialize(handle)}")

    ctx_pp = ctypes.c_void_p()
    api = ctypes.create_string_buffer(b"sw")
    rc = lib.mpv_render_context_create(ctypes.byref(ctx_pp), handle,
                                       params((P_API_TYPE, api), (P_INVALID, None)))
    print(f"render_context_create(sw) → {rc}")
    if rc < 0 or not ctx_pp.value:
        print("软件渲染上下文建不起来，这条路到此为止")
        lib.mpv_terminate_destroy(handle)
        return 1
    ctx = ctx_pp.value

    argv = (ctypes.c_char_p * 3)(b"loadfile", MEDIA.encode(), None)
    print(f"loadfile → {lib.mpv_command(handle, argv)}")
    time.sleep(1.6)

    width, height = 640, 360
    stride = width * 4
    buffer = (ctypes.c_ubyte * (stride * height))()
    buf_addr = ctypes.addressof(buffer)
    QT_FORMAT = QImage.Format_RGBX8888        # 字节序 R,G,B,X，正好对上 mpv 的 "rgb0"

    def render_into(pointer: int) -> int:
        size = (ctypes.c_int * 2)(width, height)
        fmt = ctypes.create_string_buffer(FMT_RGB0)
        pitch = ctypes.c_int(stride)
        return lib.mpv_render_context_render(
            ctx, params((P_SW_SIZE, size), (P_SW_FORMAT, fmt),
                        (P_SW_STRIDE, pitch), (P_SW_POINTER, pointer), (P_INVALID, None)))

    # ① 渲染进自持缓冲，再用 QImage 包同一块内存（不拷贝）
    rc = render_into(buf_addr)
    print(f"① 渲染到自有缓冲 rc={rc}")
    wrapped = None
    for attempt in (lambda: QImage(buf_addr, width, height, stride, QT_FORMAT),
                    lambda: QImage(buf_addr, width, height, QT_FORMAT)):
        try:
            wrapped = attempt()
            if not wrapped.isNull():
                break
        except Exception as exc:
            print(f"   QImage(地址) 构造失败：{type(exc).__name__}: {exc}")
    if wrapped and not wrapped.isNull():
        wrapped.save("/tmp/sw_zero_copy.png")
        print(f"   零拷贝包装成功 {wrapped.width()}x{wrapped.height()} "
              f"非黑采样={non_black_ratio(wrapped):.1%} → /tmp/sw_zero_copy.png")
    else:
        print("   QImage 包外部内存失败，只能每帧拷一次")

    # ② 帧率：连续渲染 40 帧
    start = time.perf_counter()
    frames = 40
    ok_frames = 0
    for _ in range(frames):
        if render_into(buf_addr) == 0:
            ok_frames += 1
        lib.mpv_render_context_report_swap(ctx)
        app.processEvents()
    took = (time.perf_counter() - start) / frames * 1000
    print(f"② 640x360 每帧 {took:.2f} ms（{ok_frames}/{frames} 帧成功）→ "
          f"上限 {1000/max(took,0.01):.0f} fps")
    scale = (1920 * 1080) / (width * height)
    print(f"   线性外推 1080p ≈ {took * scale:.1f} ms/帧（{1000/max(took*scale,0.01):.0f} fps）")

    value = ctypes.c_double()
    if lib.mpv_get_property(handle, b"time-pos", 4, ctypes.byref(value)) == 0:
        print(f"播放位置 = {value.value:.2f}s（说明解码在跑）")
    hwdec = ctypes.c_char_p()
    if lib.mpv_get_property(handle, b"hwdec-current", 5, ctypes.byref(hwdec)) == 0 and hwdec.value:
        print(f"hwdec-current = {hwdec.value.decode()}")
    else:
        print("hwdec-current 读不到（vo=libmpv 下硬解可能不生效）")
    lib.mpv_render_context_free(ctx)
    lib.mpv_terminate_destroy(handle)
    return 0


def non_black_ratio(image: QImage) -> float:
    total = image.width() * image.height()
    colored = 0
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            color = image.pixelColor(x, y)
            if color.red() + color.green() + color.blue() > 30:
                colored += 1
    sampled = len(range(0, image.height(), 4)) * len(range(0, image.width(), 4))
    return colored / max(sampled, 1)


if __name__ == "__main__":
    raise SystemExit(main())

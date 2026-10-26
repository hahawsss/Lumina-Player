"""本机单流 Range 代理：让 mpv 与实时 ASR 共用同一条 115 上游连接。

115 CDN 对同一文件的两条并发流会 403（`115 pmt 3-2`）。这里只向 OpenList
开一条顺序连接，把字节放进内存块缓存；mpv 和 ffmpeg 都向 127.0.0.1 发
Range 请求，从同一份缓存取数据。视频字节不落盘，缓存只保留播放头附近。
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx

from . import config as cfg
from .media import MediaSource

BLOCK = 1 << 20                 # 1 MiB
BEHIND_KEEP = 32 * BLOCK        # 播放头后面最多留 32 MiB，供回跳/重读
JUMP_AHEAD = 24 * BLOCK         # 客户端一次跳这么远就不补中间空洞，直接从新位置读
STALE_MARGIN = 8 * BLOCK        # 旧连接落后新播放位置这么多，就直接掐掉，别来回重置
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class StreamProxyError(RuntimeError):
    pass


def _close_quietly(response) -> None:
    try:
        response.close()
    except Exception:
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, proxy: "RangeCacheProxy"):
        self.proxy = proxy
        super().__init__(address, handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:      # 别把每个 Range 都写进 stderr
        pass

    def do_HEAD(self) -> None:  # noqa: N802
        self._head()

    def do_GET(self) -> None:  # noqa: N802
        self._get()

    def _head(self) -> None:
        proxy: RangeCacheProxy = self.server.proxy
        if not proxy.authorized(self.path):
            self.send_error(404)
            return
        try:
            total = proxy.total()
        except StreamProxyError as exc:
            self.send_error(503, str(exc))
            return
        self.send_response(200)
        self.send_header("Content-Type", proxy.content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total))
        self.end_headers()

    def _get(self) -> None:
        proxy: RangeCacheProxy = self.server.proxy
        if not proxy.authorized(self.path):
            self.send_error(404)
            return
        try:
            total = proxy.total()
        except StreamProxyError as exc:
            self.send_error(503, str(exc))
            return
        header = self.headers.get("Range")
        if header:
            parsed = _RANGE.match(header.strip())
            if not parsed:
                self.send_error(416)
                return
            raw_start, raw_end = parsed.groups()
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else total - 1
            else:
                # bytes=-N：最后 N 字节
                if not raw_end:
                    self.send_error(416)
                    return
                start = max(0, total - int(raw_end))
                end = total - 1
            if start >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, total - 1)
            if end < start:
                self.send_error(416)
                return
            status = 206
        else:
            start, end, status = 0, total - 1, 200
        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", proxy.content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.end_headers()
            self.wfile.flush()
            proxy.serve(start, end, self.wfile.write)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except StreamProxyError as exc:
            cfg.append_log(f"单流代理：发送失败：{exc}")
            try:
                self.close_connection = True
            except Exception:
                pass
        except OSError:
            pass


class RangeCacheProxy:
    """单上游 + 内存块缓存 + 多客户端 Range 服务。"""

    def __init__(self, source: MediaSource, cache_mb: int = 128,
                 pin_head_mb: int = 32, pin_tail_mb: int = 16):
        self.source = source
        self.cache_bytes = max(32, int(cache_mb)) * BLOCK
        self.pin_head_bytes = max(1, int(pin_head_mb)) * BLOCK
        self.pin_tail_bytes = max(1, int(pin_tail_mb)) * BLOCK
        self._token = secrets.token_urlsafe(24)
        self._total = -1
        self._content_type = "application/octet-stream"
        self._upstream_lock = threading.Lock()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._gen = 0
        self._pinned: dict[int, bytes] = {}
        self._blocks: dict[int, bytes] = {}
        self._available_end = 0
        self._trim_start = 0
        self._target_block: int | None = None
        self._response: httpx.Response | None = None
        self._readers: dict[int, int] = {}
        self._reader_seq = 0
        self._last_reader_pos = 0
        self._error = ""
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._url = ""
        self._headers: dict[str, str] = {}
        self._headers_at = 0.0
        self._upstream = ""
        self._upstream_at = 0.0
        self._requests = 0
        self._resets = 0
        self._reset_window = 0.0
        self._resets_in_window = 0

    # ------------------------------------------------------------ 生命周期

    def start(self) -> str:
        if self._server is not None:
            return self._url
        started = time.time()
        self._probe()
        self._pin_ranges()
        self._server = _Server(("127.0.0.1", 0), _Handler, self)
        self._thread = threading.Thread(target=self._fetch_loop, daemon=True,
                                        name="stream-proxy")
        self._thread.start()
        port = self._server.server_address[1]
        self._url = f"http://127.0.0.1:{port}/stream/{self._token}"
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="stream-proxy-http").start()
        cfg.append_log(
            f"单流代理已启动：{self.source.display_name} · "
            f"{self._total / 1e9:.2f} GB · 缓存 {self.cache_bytes // BLOCK} MB · "
            f"{time.time() - started:.1f}s")
        return self._url

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self._cond:
            self._gen += 1
            response = self._response
            self._response = None
            self._error = "已关闭"
            self._cond.notify_all()
        if response is not None:
            threading.Thread(target=_close_quietly, args=(response,),
                             daemon=True).start()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._cond:
            self._pinned.clear()
            self._blocks.clear()
        cfg.append_log("单流代理已停止")

    @property
    def url(self) -> str:
        return self._url

    def total(self) -> int:
        if self._total < 0:
            raise StreamProxyError("还没拿到文件大小")
        return self._total

    def max_lead_seconds(self, duration: float, fraction: float = 0.5) -> float:
        """按字节算的预读上限；算不出来返回 0（= 不加这层限制）。

        单流只有一条上游：ASR 读点离播放头太远，缓存就装不下这两头，
        两边互相把上游拽到对方位置 → 重置风暴 → 代理自保暂停 → 实时死掉。
        高码率片子里 60 秒可能就是 140 MB，比整个缓存还大，所以秒数必须换成字节再夹。
        """
        if duration <= 0 or self._total <= 0:
            return 0.0
        rate = self._total / float(duration)          # 平均每秒字节数
        if rate <= 0:
            return 0.0
        return max(0.0, self.cache_bytes * max(0.1, fraction)) / rate

    @property
    def content_type(self) -> str:
        return self._content_type

    def authorized(self, path: str) -> bool:
        return urlparse(path).path == f"/stream/{self._token}"

    def stats(self) -> dict:
        with self._cond:
            return {"requests": self._requests, "resets": self._resets,
                    "cached_bytes": sum(len(b) for b in self._blocks.values()),
                    "total": self._total}

    # ------------------------------------------------------------ 上游

    def _headers_for_upstream(self, force: bool = False) -> dict[str, str]:
        now = time.time()
        if force or not self._headers or now - self._headers_at > 60.0:
            refresh = getattr(self.source, "refresh", None)
            if callable(refresh):
                try:
                    fresh = refresh()
                    if fresh:
                        self.source.headers.clear()
                        self.source.headers.update(fresh)
                except Exception as exc:
                    cfg.append_log(f"单流代理：刷新 token 失败：{exc}")
            self._headers = dict(self.source.headers)
            self._headers_at = now
        return dict(self._headers)

    def _upstream_uri(self, force: bool = False) -> str:
        now = time.time()
        if force or not self._upstream or now - self._upstream_at > 300.0:
            getter = getattr(self.source, "upstream", None)
            if callable(getter):
                try:
                    fresh = getter()
                    if fresh:
                        self._upstream = fresh
                        self._upstream_at = now
                except Exception as exc:
                    cfg.append_log(f"单流代理：获取 /p/ 下载地址失败：{exc}")
            if not self._upstream:
                self._upstream = self.source.uri
                self._upstream_at = now
        return self._upstream or self.source.uri

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(connect=10.0, read=60.0,
                                                  write=15.0, pool=15.0),
                            trust_env=False)

    def _probe(self) -> None:
        """用 Range 0-0 拿总大小和 Content-Type，不读正文。"""
        for attempt in range(2):
            headers = self._headers_for_upstream(force=attempt > 0)
            headers["Range"] = "bytes=0-0"
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            if self.source.user_agent:
                headers["User-Agent"] = self.source.user_agent
            try:
                with self._client() as client:
                    with client.stream("GET", self._upstream_uri(force=attempt > 0),
                                   headers=headers) as reply:
                        if reply.status_code == 401 and attempt == 0:
                            continue
                        if reply.status_code not in (200, 206):
                            raise StreamProxyError(
                                f"上游返回 HTTP {reply.status_code}（探测文件大小）")
                        content_range = reply.headers.get("content-range") or ""
                        match = re.search(r"/(\d+)$", content_range)
                        if match:
                            self._total = int(match.group(1))
                        elif reply.headers.get("content-length"):
                            self._total = int(reply.headers["content-length"])
                        if self._total <= 0:
                            raise StreamProxyError("上游没有给出文件大小")
                        self._content_type = (reply.headers.get("content-type")
                                              or "application/octet-stream")
                        break
            except httpx.HTTPError as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise StreamProxyError(f"探测上游失败：{exc}") from exc
        if self._total <= 0:
            raise StreamProxyError("探测上游失败：没有拿到文件大小")
        self._available_end = 0
        self._trim_start = 0

    def _fetch_pinned(self, start: int, end: int, label: str) -> None:
        """顺序读一小段（片头/片尾索引），只读这一次，避免打断主上游流。

        start 必须按块对齐；片尾最后一块可以是短块，但一定从块首开始，
        否则 Range 缓存按块索引会错位。
        """
        start = max(0, start)
        end = min(self._total - 1, end)
        if end < start:
            return
        cfg.append_log(f"单流代理：预读上游 {label} Range {start}-{end}")
        data = b""
        for attempt in range(2):
            headers = self._headers_for_upstream(force=attempt > 0)
            headers["Range"] = f"bytes={start}-{end}"
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            if self.source.user_agent:
                headers["User-Agent"] = self.source.user_agent
            buf = bytearray()
            try:
                with self._client() as client:
                    with client.stream("GET", self._upstream_uri(force=attempt > 0),
                                   headers=headers) as reply:
                        if reply.status_code == 401 and attempt == 0:
                            continue
                        if reply.status_code not in (200, 206):
                            raise StreamProxyError(
                                f"上游返回 HTTP {reply.status_code}（预读{label}）")
                        if start > 0 and reply.status_code == 200:
                            raise StreamProxyError(
                                f"上游忽略了 Range（预读{label}）")
                        for chunk in reply.iter_bytes(65536):
                            buf.extend(chunk)
                            if len(buf) >= end - start + 1:
                                break
                data = bytes(buf[:end - start + 1])
                break
            except httpx.HTTPError as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise StreamProxyError(f"预读{label}失败：{exc}") from exc
        if not data:
            raise StreamProxyError(f"预读{label}为空")
        with self._cond:
            for block_index in range(start // BLOCK, (end // BLOCK) + 1):
                block_start = block_index * BLOCK
                lo = max(start, block_start)
                hi = min(end + 1, block_start + BLOCK)
                piece = data[lo - start:hi - start]
                if piece:
                    self._pinned[block_index] = piece
        cfg.append_log(f"单流代理：已预读{label} {len(data) / BLOCK:.1f} MB")

    def _pin_ranges(self) -> None:
        head_len = min(self._total, self.pin_head_bytes)
        head_end = min(self._total - 1,
                       ((head_len + BLOCK - 1) // BLOCK) * BLOCK - 1)
        self._fetch_pinned(0, head_end, "片头")
        tail_start = max(0, self._total - self.pin_tail_bytes)
        tail_start = (tail_start // BLOCK) * BLOCK
        self._fetch_pinned(tail_start, self._total - 1, "片尾索引")

    def _fetch_loop(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while not self._stop.is_set() and self._target_block is None:
                    self._cond.wait(0.5)
                if self._stop.is_set():
                    return
                start_block = self._target_block
                gen = self._gen
            self._read_upstream(int(start_block), gen)

    def _read_upstream(self, start_block: int, gen: int) -> None:
        attempts = 0
        while not self._stop.is_set():
            with self._cond:
                if gen != self._gen:
                    return
                if self._target_block is None:
                    return
                # 网络断了/上游提前结束：从已经拿到的最后一个整块续读，别从头再来
                start_block = max(int(self._target_block),
                                  self._available_end // BLOCK)
                self._target_block = start_block
            headers = self._headers_for_upstream(force=attempts > 0)
            url = self._upstream_uri(force=attempts > 0)
            headers["Range"] = f"bytes={start_block * BLOCK}-"
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            if self.source.user_agent:
                headers["User-Agent"] = self.source.user_agent
            try:
                # 同一时刻只允许一条上游连接；重置时先让旧连接退出，避免 115 看到并发
                with self._upstream_lock:
                    with self._client() as client:
                        with client.stream("GET", url,
                                           headers=headers) as reply:
                            with self._cond:
                                if gen != self._gen:
                                    return
                                self._response = reply
                            self._pump_upstream(reply, start_block, gen)
                return
            except StreamProxyError as exc:
                if "405" in str(exc) or ("403" in str(exc) and attempts >= 1):
                    # 115 的 WAF 限流：继续重试只会把账号打得更久
                    cfg.append_log(f"单流代理：上游被限流，停止重试：{exc}")
                    with self._cond:
                        if gen == self._gen:
                            self._error = str(exc)
                            self._cond.notify_all()
                    return
                attempts += 1
                cfg.append_log(f"单流代理：上游读取中断（{attempts}/3）：{exc}")
                if attempts >= 3:
                    with self._cond:
                        if gen == self._gen:
                            self._error = str(exc)
                            self._cond.notify_all()
                    return
                time.sleep(0.8)
            except (httpx.HTTPError, OSError) as exc:
                attempts += 1
                cfg.append_log(f"单流代理：网络中断（{attempts}/3）：{exc}")
                if attempts >= 3:
                    with self._cond:
                        if gen == self._gen:
                            self._error = str(exc)
                            self._cond.notify_all()
                    return
                time.sleep(0.8)
            finally:
                with self._cond:
                    self._response = None

    def _pump_upstream(self, reply: httpx.Response, start_block: int, gen: int) -> None:
        if reply.status_code not in (200, 206):
            raise StreamProxyError(f"上游返回 HTTP {reply.status_code}")
        content_range = reply.headers.get("content-range") or ""
        if reply.status_code == 206:
            match = re.match(r"bytes (\d+)-", content_range)
            if not match or int(match.group(1)) != start_block * BLOCK:
                raise StreamProxyError(f"上游 Range 起点不符：{content_range}")
        elif start_block != 0:
            raise StreamProxyError("上游忽略了 Range，无法从中间续读")
        offset = start_block * BLOCK
        buf = bytearray()
        for chunk in reply.iter_bytes(65536):
            if self._stop.is_set() or gen != self._gen:
                return
            buf.extend(chunk)
            while len(buf) >= BLOCK:
                block = bytes(buf[:BLOCK])
                del buf[:BLOCK]
                self._store(offset // BLOCK, block, gen)
                offset += BLOCK
                if not self._pace(gen):
                    return
        if buf:
            self._store(offset // BLOCK, bytes(buf), gen)
            offset += len(buf)
        with self._cond:
            self._available_end = max(self._available_end, offset)
            if offset >= self._total:
                # 读到片尾就停；否则 target 留在最后一块会让线程空转重连
                self._target_block = None
            self._cond.notify_all()
        if offset < self._total:
            raise StreamProxyError("上游提前结束")

    def _store(self, block_index: int, block: bytes, gen: int) -> None:
        with self._cond:
            if gen != self._gen or self._stop.is_set():
                return
            self._blocks[block_index] = block
            self._available_end = max(self._available_end,
                                      block_index * BLOCK + len(block))
            self._trim_locked()
            self._cond.notify_all()

    def _trim_locked(self) -> None:
        if not self._readers:
            keep_from = self._last_reader_pos - BEHIND_KEEP
        else:
            keep_from = min(self._readers.values()) - BEHIND_KEEP
        keep_from = max(self._trim_start, keep_from)
        if keep_from <= self._trim_start:
            return
        drop_before = keep_from // BLOCK
        for index in [i for i in self._blocks if i < drop_before]:
            self._blocks.pop(index, None)
        self._trim_start = drop_before * BLOCK

    def _pace(self, gen: int) -> bool:
        """缓存领先最慢读者太多就暂停上游；没有读者时也停下，别把整片拖下来。"""
        while not self._stop.is_set():
            with self._cond:
                if gen != self._gen:
                    return False
                if not self._readers:
                    self._target_block = None
                    self._cond.notify_all()
                    return False
                min_pos = min(self._readers.values())
                if self._available_end - min_pos <= self.cache_bytes:
                    return True
                self._cond.wait(0.5)
        return False

    # ------------------------------------------------------------ 读缓存

    def _new_reader_locked(self, pos: int) -> int:
        self._reader_seq += 1
        reader = self._reader_seq
        self._readers[reader] = pos
        self._last_reader_pos = pos
        return reader

    def serve(self, start: int, end: int, writer) -> None:
        pos = start
        reader: int | None = None
        reader_gen = self._gen
        with self._cond:
            self._requests += 1
            # 从片头/片尾预读区开始的多半是 ffprobe 的元数据请求：
            # 先不登记成“读者”，免得它把播放头的缓存窗口钉在 0。
            if start // BLOCK not in self._pinned:
                reader = self._new_reader_locked(start)
        try:
            while pos <= end:
                if self._stop.is_set():
                    raise StreamProxyError("已关闭")
                block_index = pos // BLOCK
                with self._cond:
                    block = self._pinned.get(block_index) or self._blocks.get(block_index)
                    if block is None and reader is None and block_index not in self._pinned:
                        reader = self._new_reader_locked(pos)
                        reader_gen = self._gen
                if block is None:
                    reader_gen = self._wait_for(pos, reader_gen)
                    continue
                if reader is None and block_index not in self._pinned:
                    with self._cond:
                        reader = self._new_reader_locked(pos)
                        reader_gen = self._gen
                block_start = block_index * BLOCK
                offset = pos - block_start
                if offset >= len(block):
                    raise StreamProxyError("缓存块长度异常")
                take = min(end - pos + 1, len(block) - offset)
                writer(block[offset:offset + take])
                pos += take
                if reader is not None:
                    with self._cond:
                        self._readers[reader] = pos
                        self._last_reader_pos = pos
                        self._trim_locked()
        finally:
            if reader is not None:
                with self._cond:
                    self._readers.pop(reader, None)
                    self._cond.notify_all()

    def _wait_for(self, pos: int, reader_gen: int) -> int:
        with self._cond:
            gen = self._gen
            while not self._stop.is_set():
                if self._error:
                    if (self._error.startswith("代理跳转太频繁")
                            and time.time() - self._reset_window >= 10.0):
                        self._error = ""
                        self._resets_in_window = 0
                        self._reset_window = time.time()
                    else:
                        raise StreamProxyError(self._error)
                block = self._pinned.get(pos // BLOCK)
                if block is not None:
                    return self._gen
                target_pos = (self._target_block or 0) * BLOCK
                behind = target_pos - pos
                stale = reader_gen != self._gen and behind > STALE_MARGIN
                if pos < self._trim_start:
                    if stale:
                        raise StreamProxyError("播放位置已切换")
                    if not self._reset_locked(pos // BLOCK):
                        raise StreamProxyError(self._error or "重置失败")
                    gen = self._gen
                    reader_gen = gen
                    continue
                block = self._blocks.get(pos // BLOCK)
                if block is not None:
                    return self._gen
                if pos < self._available_end:
                    # 块被 trim 掉了，或者正好在块边界之外；从头重新取
                    if stale:
                        raise StreamProxyError("播放位置已切换")
                    if not self._reset_locked(pos // BLOCK):
                        raise StreamProxyError(self._error or "重置失败")
                    gen = self._gen
                    reader_gen = gen
                    continue
                jump_limit = min(JUMP_AHEAD, max(BLOCK, self.cache_bytes // 2))
                if pos - self._available_end > jump_limit:
                    if not self._reset_locked(pos // BLOCK):
                        raise StreamProxyError(self._error or "重置失败")
                    gen = self._gen
                    reader_gen = gen
                    continue
                if self._target_block is None:
                    self._target_block = self._available_end // BLOCK
                    self._cond.notify_all()
                if gen != self._gen:
                    gen = self._gen
                self._cond.wait(0.5)
            raise StreamProxyError("已关闭")

    def _reset_locked(self, block_index: int) -> bool:
        # 已经在读这个位置就别再重置一次，避免同一请求互相打架
        if (block_index == self._target_block and self._response is not None
                and not self._error):
            return True
        now = time.time()
        if now - self._reset_window > 10.0:
            self._reset_window = now
            self._resets_in_window = 0
        self._resets_in_window += 1
        if self._resets_in_window > 4:
            # 多读者位置互相拉扯时，硬重置会向 115 连发取链请求；
            # 宁可让冲突的那一路失败，也不能把账号打到 405。
            self._error = "代理跳转太频繁，已暂停（保护 115 账号）"
            self._cond.notify_all()
            cfg.append_log(f"单流代理：10 秒内跳转超过 4 次，停止重置（{block_index * BLOCK / 1e6:.0f} MB）")
            return False
        self._gen += 1
        self._resets += 1
        # 同一次跳转如果有别的读者就在附近（比如引擎在关键帧、ASR 在播放头），
        # 从更靠前的位置开始取，一次把两边都喂饱，别 93/95 来回重置。
        target = block_index
        for pos in self._readers.values():
            other = pos // BLOCK
            if abs(other - block_index) <= STALE_MARGIN // BLOCK and other < target:
                target = other
        self._blocks.clear()
        self._available_end = target * BLOCK
        self._trim_start = target * BLOCK
        self._target_block = target
        self._error = ""
        response = self._response
        self._response = None
        self._cond.notify_all()
        if response is not None:
            # 不能在持锁时 close：关流可能等读线程，交给短命线程做
            threading.Thread(target=_close_quietly, args=(response,),
                             daemon=True).start()
        cfg.append_log(f"单流代理：跳到 {block_index * BLOCK / 1e6:.0f} MB 重新读上游")
        return True

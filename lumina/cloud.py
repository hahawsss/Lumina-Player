"""115 云盘桥：本机 OpenList 负责 115 登录/直链刷新，Lumina 只认 loopback WebDAV。

OpenList（内含 115driver）监听 127.0.0.1；播放器拿到的 URL 永远不变，
Bearer token 由本机的 `openlist admin token` 读取，不写进配置文件。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from . import config as cfg
from .media import MediaSource


class CloudError(RuntimeError):
    pass


APP_RES_ENV = "LUMINA_APP_RES"          # 启动器导出：Lumina.app/Contents/Resources


def bundle_binaries() -> list[Path]:
    """App 自带网关的候选落点（bundle 内只放程序，状态仍在 Application Support）。"""
    out: list[Path] = []
    raw = str(os.environ.get(APP_RES_ENV, "") or "")
    if raw:
        out.append(Path(raw) / "bin" / "openlist")
    # 源码直跑（没有启动器）时也认仓库 dist 里那份：cloud.py → lumina/ → 仓库根
    out.append(Path(__file__).resolve().parents[1] / "dist" / "Lumina.app"
               / "Contents" / "Resources" / "bin" / "openlist")
    return out


def legacy_binary() -> Path:
    return Path(cfg.SUPPORT_DIR) / "cloud" / "bin" / "openlist"


class OpenList:
    def __init__(self, config: cfg.Config):
        self.config = config
        self.data_dir = Path(cfg.SUPPORT_DIR) / "cloud" / "openlist"
        self.port = int(config.get("cloud.openlist_port", 15244) or 15244)
        self._token = ""
        self._token_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._adopted = False   # 启动时端口上已经有人：不是我们带起来的

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def binary(self) -> str:
        """找网关二进制：App 内置优先，外部装的只作退路。"""
        wanted = str(self.config.get("cloud.openlist_binary", "") or "")
        candidates = [wanted] if wanted else []
        candidates += [str(path) for path in bundle_binaries()]
        candidates += [
            shutil.which("openlist") or "",
            "/opt/homebrew/bin/openlist",
            "/usr/local/bin/openlist",
            str(legacy_binary()),
            "/tmp/openlist_probe/openlist",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        raise CloudError("App 里没有自带的 openlist，PATH 上也没找到；"
                         "跑一次 scripts/build_openlist.sh 再重装 App")

    def running_binary(self) -> str:
        """端口上那份网关自己的可执行路径 —— 用来判断「是不是我们想跑的那一份」。"""
        for pid in self.pids():
            try:
                out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                continue
            path = (out.stdout or "").strip()
            if path:
                return path
        return ""

    def migration_needed(self) -> bool:
        """自动模式下，端口上跑的不是我们选中的那一份就要换血。

        不换的话，老版本留在数据目录里的那份副本会被一直认领，
        「已经编译进 App」这件事就永远跑不到实际进程上。
        """
        if not self.autostart_wanted():
            return False
        try:
            want = Path(self.binary()).resolve()
        except CloudError:
            return False
        have = self.running_binary()
        if not have or not self.healthy():
            return False
        try:
            return Path(have).resolve() != want
        except OSError:
            return True

    def binary_origin(self) -> str:
        """这一份是从哪来的 —— 界面上要能回答「网关到底在不在 App 里」。"""
        try:
            path = Path(self.binary()).resolve()
        except CloudError:
            return "找不到"
        for bundled in bundle_binaries():
            if path == bundled.resolve():
                return "App 内置"
        if path == legacy_binary().resolve():
            return "数据目录里的旧副本"
        wanted = str(self.config.get("cloud.openlist_binary", "") or "")
        if wanted and Path(wanted).resolve() == path:
            return "设置里指定的路径"
        return "系统 PATH"

    def healthy(self) -> bool:
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                return client.get(f"{self.base}/api/public/settings").status_code == 200
        except Exception:
            return False

    def ensure_running(self) -> None:
        if self.healthy():
            if self._proc is None:
                # 别人（另一个实例、或用户自己开的）已经开着了：认领下来，但退出时不去抢着停
                self._adopted = True
            return
        binary = self.binary()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.data_dir / "server.out"
        with log_path.open("a", encoding="utf-8") as log:
            self._proc = subprocess.Popen(
                [binary, "server", "--data", str(self.data_dir), "--log-std"],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        self._adopted = False
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise CloudError(f"OpenList 启动失败（退出码 {self._proc.returncode}）")
            if self.healthy():
                return
            time.sleep(0.3)
        raise CloudError("OpenList 启动超时（看 cloud/openlist/server.out）")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    # ------------------------------------------------------------ 随软件同生同死

    def autostart_wanted(self) -> bool:
        """auto = 打开软件就把网关带起来；manual = 只在管理页手动点。"""
        return str(self.config.get("cloud.start_mode", "auto") or "auto") != "manual"

    def autostart(self) -> tuple[bool, str]:
        """启动期带起服务。缺二进制/没初始化都不算致命：网盘打不开，本地播放照旧。"""
        if not self.autostart_wanted():
            return (False, "手动模式：不随软件启动 OpenList")
        try:
            if self.migration_needed():
                have = self.running_binary()
                cfg.append_log(f"网关换血：{have} → {self.binary()}（换到软件自己要用的那一份）")
                self.stop_server()
            self.ensure_running()
            self.token(force=True)
        except (CloudError, OSError, subprocess.SubprocessError) as exc:
            return (False, f"OpenList 没能随软件启动：{exc}")
        return (True, f"OpenList 已就绪（{self.base}）")

    def should_stop_on_quit(self) -> bool:
        """只停我们带起来的网关。

        自动模式下退出要收掉，免得留孤儿；但如果启动时端口上已经有一份（用户自己跑的、
        或另一个实例先起的），退出时不去动它 —— 抢着停别人的服务不是集成，是事故。
        """
        if self._proc is not None and self._proc.poll() is None:
            return True
        return self.autostart_wanted() and not self._adopted

    def shutdown_for_quit(self) -> None:
        """关窗路径上的快速停机：只等一小会儿，绝不把退出卡在健康探测上。"""
        if not self.should_stop_on_quit():
            return
        self.stop_server(patience=1.5)

    def token(self, force: bool = False) -> str:
        with self._token_lock:
            if self._token and not force:
                return self._token
            self.ensure_running()
            try:
                proc = subprocess.run(
                    [self.binary(), "admin", "token", "--data", str(self.data_dir)],
                    capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CloudError(f"读取 OpenList token 失败：{exc}") from exc
            for line in (proc.stdout or "").splitlines():
                if line.startswith("Admin token:"):
                    self._token = line.split("Admin token:", 1)[1].strip()
                    if self._token:
                        return self._token
            raise CloudError("OpenList 还没有管理员 token：先完成一次初始化/登录")

    def api_headers(self) -> dict[str, str]:
        return {"Authorization": self.token()}

    def headers(self, force: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(force=force)}"}

    def fresh_headers(self) -> dict[str, str]:
        """OpenList 换 token / 重置数据后，内存里的旧 token 会 401；重新读一次。"""
        return self.headers(force=True)

    def list_dir(self, path: str = "/") -> list[dict]:
        self.ensure_running()
        body = {"path": path or "/", "password": "", "page": 1,
                "per_page": 0, "refresh": True}
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                reply = client.post(f"{self.base}/api/fs/list", json=body,
                                    headers=self.api_headers())
        except httpx.HTTPError as exc:
            raise CloudError(f"读取 115 目录失败：{exc}") from exc
        if reply.status_code >= 400:
            raise CloudError(f"读取 115 目录失败：HTTP {reply.status_code}")
        data = reply.json()
        if data.get("code") != 200:
            raise CloudError(f"读取 115 目录失败：{data.get('message') or data.get('code')}")
        content = (data.get("data") or {}).get("content") or []
        return content

    # ------------------------------------------------------------ 管理 API

    def pids(self) -> list[int]:
        """找本机正在用这个数据目录的 openlist server（包括不是本进程拉起的）。"""
        try:
            proc = subprocess.run(
                ["pgrep", "-f", f"openlist server --data {self.data_dir}"],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return []
        out = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit() and int(line) != os.getpid():
                out.append(int(line))
        return out

    def status(self) -> dict:
        info = {
            "running": self.healthy(),
            "base": self.base,
            "port": self.port,
            "data_dir": str(self.data_dir),
            "owned": bool(self._proc is not None and self._proc.poll() is None),
            "pids": self.pids(),
            "version": "",
            "site_title": "",
        }
        if info["running"]:
            try:
                with httpx.Client(timeout=4.0, trust_env=False) as client:
                    data = client.get(f"{self.base}/api/public/settings").json().get("data") or {}
                info["version"] = str(data.get("version") or "")
                info["site_title"] = str(data.get("site_title") or "")
            except (httpx.HTTPError, ValueError):
                pass
        return info

    def stop_server(self, patience: float = 6.0) -> None:
        """停掉本数据目录的 OpenList（本进程拉起的和外部同数据目录的都算）。

        patience 是给「等端口真的没了」留的预算：管理页可以等久一点，
        关窗路径必须短，否则退出会被健康探测拖住。
        """
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=max(1.0, min(5.0, patience)))
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        for pid in self.pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + patience
        while time.time() < deadline:
            if not self.healthy():
                break
            time.sleep(0.2)
        self._token = ""

    def restart_server(self) -> None:
        self.stop_server()
        self._token = ""
        self.ensure_running()
        self.token(force=True)

    def _api(self, method: str, path: str, body: dict | None = None,
             params: dict | None = None) -> dict:
        self.ensure_running()
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                if method == "GET":
                    reply = client.get(f"{self.base}{path}", params=params,
                                       headers=self.api_headers())
                else:
                    reply = client.post(f"{self.base}{path}", json=body or {},
                                        headers=self.api_headers())
        except httpx.HTTPError as exc:
            raise CloudError(f"OpenList 管理接口失败：{exc}") from exc
        if reply.status_code >= 400:
            raise CloudError(f"OpenList 管理接口 HTTP {reply.status_code}")
        data = reply.json()
        if data.get("code") != 200:
            raise CloudError(f"OpenList 管理接口失败：{data.get('message') or data.get('code')}")
        return data

    def storage_list(self) -> list[dict]:
        return list((self._api("GET", "/api/admin/storage/list").get("data") or {})
                    .get("content") or [])

    def storage_115(self) -> dict:
        for item in self.storage_list():
            if str(item.get("driver") or "") == "115 Cloud" or                     str(item.get("mount_path") or "").rstrip("/") == "/115":
                return item
        raise CloudError("OpenList 里没有 115 Cloud 存储")

    def storage(self, storage_id: int) -> dict:
        return (self._api("GET", "/api/admin/storage/get",
                          params={"id": int(storage_id)}).get("data") or {})

    def update_storage(self, storage: dict) -> None:
        self._api("POST", "/api/admin/storage/update", storage)

    def update_115_cookie(self, cookie: str) -> None:
        if not cookie.strip():
            raise CloudError("扫码结果里没有 cookie")
        storage = self.storage_115()
        try:
            addition = json.loads(storage.get("addition") or "{}")
        except ValueError as exc:
            raise CloudError(f"115 存储配置不是合法 JSON：{exc}") from exc
        addition["cookie"] = cookie
        addition["qrcode_token"] = ""
        addition["qrcode_source"] = "tv"
        storage["addition"] = json.dumps(addition, ensure_ascii=False)
        self.update_storage(storage)

    def update_115_settings(self, mount_path: str, web_proxy: bool,
                            webdav_policy: str, cache_expiration: int,
                            proxy_range: bool) -> None:
        storage = self.storage_115()
        storage["mount_path"] = "/" + mount_path.strip("/")
        storage["web_proxy"] = bool(web_proxy)
        storage["webdav_policy"] = webdav_policy
        storage["cache_expiration"] = int(cache_expiration)
        storage["proxy_range"] = bool(proxy_range)
        self.update_storage(storage)

    # ------------------------------------------------------------ 115 扫码登录

    _115_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def qr_login_start(self) -> dict:
        headers = {"User-Agent": self._115_UA, "Referer": "https://115.com/"}
        try:
            with httpx.Client(timeout=20.0, trust_env=False,
                              follow_redirects=True, headers=headers) as client:
                reply = client.get(
                    "https://qrcodeapi.115.com/api/1.0/web/1.0/token/")
                payload = reply.json()
                data = payload.get("data") or {}
                uid = str(data.get("uid") or "")
                if reply.status_code >= 400 or not uid:
                    raise CloudError(
                        f"115 二维码接口失败：HTTP {reply.status_code} {payload.get('message') or ''}")
                image = client.get(
                    "https://qrcodeapi.115.com/api/1.0/mac/1.0/qrcode",
                    params={"uid": uid})
                if image.status_code >= 400 or not image.content:
                    raise CloudError(f"115 二维码图片失败：HTTP {image.status_code}")
        except httpx.HTTPError as exc:
            raise CloudError(f"连接 115 二维码接口失败：{exc}") from exc
        return {"uid": uid, "time": str(data.get("time") or ""),
                "sign": str(data.get("sign") or ""),
                "qrcode": str(data.get("qrcode") or ""),
                "png": image.content}

    def qr_login_status(self, token: dict) -> dict:
        # 115 这个接口是 30 秒长轮询，超时要给足；返回里没 status 表示仍在等待
        headers = {"User-Agent": self._115_UA, "Referer": "https://115.com/"}
        try:
            with httpx.Client(timeout=45.0, trust_env=False,
                              follow_redirects=True, headers=headers) as client:
                reply = client.get(
                    "https://qrcodeapi.115.com/get/status/",
                    params={"uid": token["uid"], "time": token["time"],
                            "sign": token["sign"], "_": int(time.time() * 1000)})
                payload = reply.json()
        except httpx.HTTPError as exc:
            raise CloudError(f"查询 115 扫码状态失败：{exc}") from exc
        data = payload.get("data") or {}
        try:
            data["status"] = int(data.get("status", 0) or 0)
        except (TypeError, ValueError):
            data["status"] = 0
        return data

    def qr_login_finish(self, token: dict) -> str:
        headers = {"User-Agent": self._115_UA, "Referer": "https://115.com/"}
        try:
            with httpx.Client(timeout=20.0, trust_env=False,
                              follow_redirects=True, headers=headers) as client:
                reply = client.post(
                    "https://passportapi.115.com/app/1.0/tv/1.0/login/qrcode/",
                    data={"app": "tv", "account": token["uid"]})
                payload = reply.json()
        except httpx.HTTPError as exc:
            raise CloudError(f"完成 115 登录失败：{exc}") from exc
        data = payload.get("data") or {}
        cookie = data.get("cookie") or {}
        if not cookie:
            raise CloudError(f"115 登录没有返回 cookie：{payload.get('message') or payload.get('state')}")
        return "; ".join(f"{key}={value}" for key, value in cookie.items())

    def raw_url(self, path: str) -> str:
        """OpenList 的 /p/ 签名下载地址：单流代理用它，能跨过 115 单链 100MB 上限。"""
        self.ensure_running()
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                reply = client.post(f"{self.base}/api/fs/get",
                                    json={"path": path or "/", "password": ""},
                                    headers=self.api_headers())
        except httpx.HTTPError as exc:
            raise CloudError(f"读取 115 下载地址失败：{exc}") from exc
        if reply.status_code >= 400:
            raise CloudError(f"读取 115 下载地址失败：HTTP {reply.status_code}")
        data = reply.json()
        if data.get("code") != 200:
            raise CloudError(f"读取 115 下载地址失败：{data.get('message') or data.get('code')}")
        url = str((data.get("data") or {}).get("raw_url") or "")
        if not url:
            raise CloudError("OpenList 没有返回下载地址（raw_url 为空）")
        return url

    def source(self, path: str, display_name: str | None = None) -> MediaSource:
        """OpenList 路径 -> 可拖动的 WebDAV URL。"""
        path = "/" + path.strip("/")
        encoded = "/".join(quote(part, safe="") for part in path.split("/") if part)
        uri = f"{self.base}/dav/{encoded}"
        name = display_name or Path(path).name
        return MediaSource(
            uri=uri, display_name=name, key=f"115:{path}", origin="115",
            headers=self.headers(),
            # ffmpeg/ffprobe 的默认 Lavf UA 在 115/OSS 上偶尔 403；统一用浏览器 UA
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            refresh=self.fresh_headers,
            upstream=lambda p=path: self.raw_url(p),
        )

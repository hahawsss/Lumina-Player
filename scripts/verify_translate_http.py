#!/usr/bin/env python
"""翻译接口的线格式验证：用本地假服务器跑通 OpenAI 兼容与 DeepL 两条路。

不需要任何真实密钥；顺带证明「本机开着系统代理时，指向 127.0.0.1 的请求不会被代理劫走」。
跑法：uv run python scripts/verify_translate_http.py
"""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lumina import config as cfg, translate  # noqa: E402

FAILURES: list[str] = []
SEEN: list[dict] = []
STATE = {"mode": "numbered", "status": 200}


def expect(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静音
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw or "{}")
        SEEN.append({"path": self.path, "auth": self.headers.get("Authorization"),
                     "body": body})
        if STATE["status"] != 200:
            self.send_response(STATE["status"])
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"nope!")
            return

        if self.path.endswith("/v2/translate"):
            texts = body.get("text") or []
            payload = {"translations": [{"text": f"【{t}】"} for t in texts]}
        else:
            user = (body.get("messages") or [{}])[-1].get("content", "")
            lines = re.findall(r"^\s*(\d+)\.\s*(.*)$", user, flags=re.MULTILINE)
            keep = max(1, len(lines) - 1) if STATE["mode"] == "merged" else len(lines)
            content = "\n".join(f"{index}. 「{text}」"
                                for index, (_, text) in enumerate(lines[:keep], start=1))
            payload = {"choices": [{"message": {"content": content}}]}
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


def serve() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def main() -> int:
    server, base = serve()
    proxy = translate.system_proxy()
    print(f"假服务器 {base}；本机系统代理 = {proxy or '无'}")
    expect("本机确实开着代理（这才算验到旁路）", bool(proxy), str(proxy))

    client = translate.OpenAICompat(f"{base}/v1", "test-key", "unit-model")
    out = client.translate(["first line", "second line", "third line"], "zh-CN")
    expect("OpenAI 兼容：条数对齐", len(out) == 3, out)
    expect("OpenAI 兼容：内容按顺序对应", out[1] == "「second line」", out)
    expect("OpenAI 兼容：编号前缀被剥掉", all(not re.match(r"^\d+\.", line) for line in out), out)
    first = SEEN[0]
    expect("打到 /v1/chat/completions", first["path"] == "/v1/chat/completions", first["path"])
    expect("带上 Bearer 密钥", first["auth"] == "Bearer test-key", str(first["auth"]))
    expect("带上模型名", first["body"].get("model") == "unit-model", str(first["body"].get("model")))
    expect("温度锁 0 保证可复现", first["body"].get("temperature") == 0.0,
          str(first["body"].get("temperature")))

    SEEN.clear()
    again = client.translate(["first line", "second line", "third line"], "zh-CN")
    expect("同一批重复请求仍会打接口（未接缓存时）", len(SEEN) == 1, len(SEEN))

    # 服务端把三行并成两行 → 必须退回逐条请求，绝不能错位
    SEEN.clear()
    STATE["mode"] = "merged"
    fallback = client.translate(["alpha", "beta", "gamma"], "zh-CN")
    STATE["mode"] = "numbered"
    expect("行数不符时逐条兜底", len(fallback) == 3, fallback)
    expect("兜底后仍按顺序对齐", fallback == ["「alpha」", "「beta」", "「gamma」"], fallback)
    expect("兜底产生了多次请求", len(SEEN) >= 4, len(SEEN))

    STATE["status"] = 503
    raised = ""
    try:
        client.translate(["boom"], "zh-CN")
    except translate.TranslationError as exc:
        raised = str(exc)
    STATE["status"] = 200
    expect("HTTP 错误码会带进异常信息", "503" in raised, raised)

    deepl = translate.DeepL("deepl-key", free=False)
    deepl.url = f"{base}/v2/translate"
    dout = deepl.translate(["one", "two"], "zh-CN")
    expect("DeepL：条数对齐", dout == ["【one】", "【two】"], dout)
    expect("DeepL：带 DeepL-Auth-Key", SEEN[-1]["auth"] == "DeepL-Auth-Key deepl-key",
           str(SEEN[-1]["auth"]))
    expect("DeepL：目标语言码大写", SEEN[-1]["body"].get("target_lang") == "ZH",
           str(SEEN[-1]["body"]))

    cache_path = cfg.CACHE_DIR / "verify-cache.jsonl"
    cache_path.unlink(missing_ok=True)
    cache = translate.Cache(path=cache_path, enabled=True)
    pipe_config = cfg.Config(cfg.SUPPORT_DIR / "verify_http.toml")
    fake = translate.OpenAICompat(f"{base}/v1", "test-key", "unit-model")
    from lumina.pipeline import SubtitlePipeline
    holder = SubtitlePipeline.__new__(SubtitlePipeline)
    holder.cache = cache
    holder._cancel = None
    round_one = SubtitlePipeline._translate_texts(holder, ["alpha", "beta"], fake, "zh-CN", None)
    calls_before = len(SEEN)
    round_two = SubtitlePipeline._translate_texts(holder, ["alpha", "beta"], fake, "zh-CN", None)
    expect("管线缓存：第二次不再打接口", len(SEEN) == calls_before, len(SEEN) - calls_before)
    expect("管线缓存：两次结果一致", round_one == round_two == ["「alpha」", "「beta」"],
           f"{round_one} {round_two}")
    cache_path.unlink(missing_ok=True)
    (cfg.SUPPORT_DIR / "verify_http.toml").unlink(missing_ok=True)

    server.shutdown()
    print("\nHTTP PASS" if not FAILURES else f"\nHTTP FAIL: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# 一条命令跑完全部验证：结构自检 + UI 交互 + 翻译线格式 + AI 字幕端到端
# + libmpv 真内嵌 + 打包 App 的 ipc 退路真实渲染截图。
#   bash scripts/verify_all.sh              约 2~3 分钟
#   bash scripts/verify_all.sh --with-asr   结构自检里也跑一遍 whisper 转写
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"

WITH_ASR=0
[ "${1:-}" = "--with-asr" ] && WITH_ASR=1

status=0

# 用户双击起来的 App（python -m lumina）绝不能被验证脚本杀掉：曾经一次 pkill
# 把人正在用的会话连带杀了。这里改成：先拒绝开跑，收尾也只收拾脚本自己的残留。
user_app="$(pgrep -f 'venv/bin/python -m lumina' || true)"
if [ -n "$user_app" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "！检测到你的 Lumina 正在运行（PID $(echo "$user_app" | tr '\n' ' ')）。"
  echo "  验证脚本会真起 mpv 与网关做实测，请先退出 App 再跑（FORCE=1 可跳过检查，"
  echo "  但脚本仍然不会去杀你的 App）。"
  exit 1
fi

cleanup() {
  pkill -f 'venv/bin/python.*scripts/(selfcheck|verify_)' >/dev/null 2>&1 || true
  pkill -x mpv >/dev/null 2>&1 || true
}
cleanup
sleep 1
for entry in "selfcheck:SELFCHK" "verify_ui_actions:UIACTION" "verify_translate_http:HTTP" \
             "verify_ai_flow:AIFLOW" "verify_embed:EMBED" "verify_render:RENDER"; do
  suite="${entry%%:*}"
  sentinel="${entry##*:}"
  args=()
  if [ "$suite" = "selfcheck" ] && [ "$WITH_ASR" = 1 ]; then
    args=(--asr)
  fi
  echo "───── $suite"
  out="$("$PY" -u "scripts/$suite.py" ${args[@]+"${args[@]}"} 2>&1)"
  echo "$out" | grep -E "^共 |PASS$|FAIL:|^RENDER |^  ✗ " | tail -8
  if ! printf '%s' "$out" | grep -q "$sentinel PASS"; then
    echo "  ✗ $suite 未通过"
    status=1
  fi
done

cleanup
if [ $status = 0 ]; then
  echo "ALL SUITES PASS"
else
  echo "有套件失败"
fi
exit $status

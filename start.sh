#!/usr/bin/env bash
# Lumina 启动器 —— 只干一件事：把装好的播放器拉起来，缺东西就先补。
#
#   ./start.sh                 打开播放器（已经在跑就把它的窗口带到前面，不再叠一个）
#   ./start.sh 影片.mkv …      直接播（可给多个文件/文件夹）
#   ./start.sh --new           强制再开一个新实例
#   ./start.sh --rebuild       把 lumina/ 下的改动重打进 App 再启动
#   ./start.sh --check         只体检，不启动
#   ./start.sh --keep-term     启动后别自动关终端窗口
#
# 双击「启动 Lumina.command」等价于 ./start.sh，成功后终端会自己关掉。
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Lumina"
VENV="$SUPPORT/venv"
PY="$VENV/bin/python"
MODEL_DIR="$SUPPORT/models"
LOG_PATH="$SUPPORT/app.log"
# 同一份代码打包出两个 bundle 时，一律优先用装进 /Applications 的那个：
# 两份都注册在 LaunchServices 里，按路径起 A 却激活了 B 是排查这类问题最费时间的坑
APP_CANDIDATES=("/Applications/Lumina.app" "$ROOT/dist/Lumina.app")

# brew 装的 mpv/ffmpeg 在这两个路径下，双击启动时 PATH 里没有它们
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

step() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m警告:\033[0m %s\n' "$*"; }
die() { printf '\033[31m出错:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

NEW_INSTANCE=0
CHECK_ONLY=0
REBUILD=0
KEEP_TERM=0
FILES=()
for arg in "$@"; do
  case "$arg" in
    --new) NEW_INSTANCE=1 ;;
    --check) CHECK_ONLY=1 ;;
    --rebuild) REBUILD=1 ;;
    --keep-term) KEEP_TERM=1 ;;
    --help | -h) sed -n '2,12p' "$0"; exit 0 ;;
    *) FILES+=("$arg") ;;
  esac
done

find_app() {
  local candidate
  for candidate in "${APP_CANDIDATES[@]}"; do
    if [ -x "$candidate/Contents/MacOS/Lumina" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------- 体检
ENGINE_MISSING=()
have mpv || ENGINE_MISSING+=(mpv)
have ffmpeg || ENGINE_MISSING+=(ffmpeg)          # ffprobe 随 ffmpeg 一起来
have whisper-cli || ENGINE_MISSING+=(whisper-cpp)

RUNTIME_OK=0
if [ -x "$PY" ] && "$PY" -c "import lumina, PySide6, httpx" >/dev/null 2>&1; then
  RUNTIME_OK=1
fi

MODEL_OK=0
compgen -G "$MODEL_DIR/ggml-*.bin" >/dev/null 2>&1 && MODEL_OK=1

APP="$(find_app || true)"

if [ "$CHECK_ONLY" = 1 ]; then
  step "环境体检"
  printf '  %-18s %s\n' "Homebrew" "$(have brew && echo 已安装 || echo '缺失（https://brew.sh）')"
  printf '  %-18s %s\n' "mpv / ffmpeg" "$(have mpv && have ffmpeg && echo 就绪 || echo "待装：${ENGINE_MISSING[*]:-无}")"
  printf '  %-18s %s\n' "whisper-cli" "$(have whisper-cli && echo 就绪 || echo 待装)"
  printf '  %-18s %s\n' "运行时 venv" "$([ "$RUNTIME_OK" = 1 ] && echo 就绪 || echo 待建)"
  printf '  %-18s %s\n' "whisper 模型" "$([ "$MODEL_OK" = 1 ] && echo 就绪 || echo '未下载（AI 字幕不可用）')"
  printf '  %-18s %s\n' "Lumina.app" "${APP:-未生成（跑 ./start.sh --rebuild）}"
  printf '  %-18s %s\n' "启动日志" "$LOG_PATH"
  exit 0
fi

# ---------------------------------------------------------------- 补依赖
if [ ${#ENGINE_MISSING[@]} -gt 0 ]; then
  have brew || die "缺 ${ENGINE_MISSING[*]}，但没装 Homebrew。先按 https://brew.sh 装 brew 再跑本脚本。"
  step "安装引擎依赖：${ENGINE_MISSING[*]}（首次可能要几分钟）"
  brew install "${ENGINE_MISSING[@]}" || die "brew 安装失败，先跑 ./start.sh --check 看卡在哪一项"
fi

if [ "$REBUILD" = 1 ] || [ "$RUNTIME_OK" != 1 ]; then
  have uv || { step "安装 uv"; brew install uv; }
  step "准备运行时（PySide6 + httpx，装在 ~/Library 下 —— 放文稿目录会被 TCC 卡在隐形授权弹窗上）"
  mkdir -p "$SUPPORT"
  [ -x "$PY" ] || uv venv "$VENV" --python 3.13 --quiet
  REINSTALL=()
  [ "$REBUILD" = 1 ] && REINSTALL+=(--reinstall-package lumina)
  (cd "$ROOT" && uv pip install --python "$PY" --quiet ${REINSTALL[@]+"${REINSTALL[@]}"} pyside6 httpx .) \
    || die "依赖安装失败"
  "$PY" -c "import lumina" || die "运行时自检失败：lumina 没能装进 $VENV"
  APP="$(find_app || true)"
fi

if [ "$MODEL_OK" != 1 ]; then
  warn "还没有 whisper 模型，AI 字幕暂时用不了（播放器本身不受影响）。"
  warn "启动后在「设置 → AI 字幕」里点下载，或跑 scripts/selfcheck.py --fixture-only 之外的模型下载。"
fi

# ---------------------------------------------------------------- 启动
FILE_ARGS=()
if [ ${#FILES[@]} -gt 0 ]; then
  for path in "${FILES[@]}"; do
    if [ -e "$path" ]; then
      FILE_ARGS+=("$(cd "$(dirname "$path")" && pwd)/$(basename "$path")")   # open 要绝对路径
    else
      warn "路径不存在，已跳过：$path"
    fi
  done
fi

if [ -n "$APP" ]; then
  OPEN_FLAGS=(-a "$APP")
  [ "$NEW_INSTANCE" = 1 ] && OPEN_FLAGS=(-n "${OPEN_FLAGS[@]}")
  if [ ${#FILE_ARGS[@]} -gt 0 ]; then
    step "启动 ${APP##*/}（${#FILE_ARGS[@]} 个文件）"
  else
    step "启动 ${APP##*/}"
  fi
  open "${OPEN_FLAGS[@]}" ${FILE_ARGS[@]+"${FILE_ARGS[@]}"} \
    || die "open 起不来，直接双击 $APP 试试；再不行看 $LOG_PATH"
else
  # 没有 .app 也要能放片：脱离终端跑，别让「关掉终端 = 播放器一起没」
  step "还没打包 App，直接跑源码（要生成 App：bash scripts/make_app.sh）"
  nohup "$PY" -m lumina ${FILE_ARGS[@]+"${FILE_ARGS[@]}"} >/dev/null 2>&1 &
  disown
fi

# 双击 .command 进来时，启动成功就把这个终端窗口收掉；失败时保留，好让错误看得见
if [ "${LUMINA_ONECLICK:-0}" = 1 ] && [ "$KEEP_TERM" != 1 ]; then
  MY_TTY="$(tty 2>/dev/null || true)"
  if [ -n "$MY_TTY" ] && [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
    osascript -e "tell application \"Terminal\"
      repeat with w in windows
        repeat with t in tabs of w
          if tty of t is \"$MY_TTY\" then close w
        end repeat
      end repeat
    end tell" >/dev/null 2>&1 &
  fi
fi
exit 0

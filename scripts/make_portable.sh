#!/usr/bin/env bash
# 生成本机跑也能发出去的 Lumina.app，并打成 zip（给 GitHub Releases 用）。
#
# 和 scripts/make_app.sh 的区别：那份是「本机自用」，启动器写死
# ~/Library/Application Support/Lumina/venv/bin/python，换台机器就跑不起来。
# 这份把 Python 运行时、依赖、云盘网关全部塞进 App 包里，不回指本机的任何路径。
#
# mpv / ffmpeg / whisper-cli 仍然由 brew 提供（首次打开时应用内的环境自检会提示装），
# 没跟着打包：libmpv 背后是一堆 /opt/homebrew 的 dylib，硬搬要改一堆 install_name，
# 不如让 Homebrew 自己装 —— 见 README 的「环境要求」。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SUPPORT="$HOME/Library/Application Support/Lumina"
DISTVENV="${DISTVENV:-$SUPPORT/distvenv2}"
GATEWAY="${OPENLIST_BIN:-$SUPPORT/build/openlist/openlist}"
VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')"
ARCH="$(uname -m)"

# 建在临时目录里：每次都是全新目录，不必删旧包（dist/ 里自用那份也不动）
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/lumina-portable-XXXXXX")"
APP="$BUILD_DIR/Lumina.app"
ZIP="$ROOT/dist/Lumina-${VERSION}-macOS-${ARCH}.zip"

command -v uv >/dev/null || { echo "✗ 缺少 uv"; exit 1; }
[ -x "$DISTVENV/bin/python" ] || { echo "✗ 缺少分发用环境 $DISTVENV"; exit 1; }

# ---------------------------------------------------------------- 运行时
# 只复制 venv 依赖的 site-packages，解释器本体单独复制 —— venv 的 pyvenv.cfg 里
# 写死了 home 的绝对路径，整份拷过去换机器必炸。
PY_HOME="$(sed -n 's/^home = //p' "$DISTVENV/pyvenv.cfg")"
PY_HOME="$(cd "$PY_HOME/.." && pwd -P)"    # -P：解掉 uv 那个 cpython-3.13 软链，拿到真实目录
echo "→ 解释器 $PY_HOME"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$PY_HOME" "$APP/Contents/Resources/python"

echo "→ 依赖 site-packages"
SITE="$(ls -d "$DISTVENV"/lib/python3.*/site-packages | head -1)"
cp -R "$SITE" "$APP/Contents/Resources/pylibs"
SP_VER="$(basename "$(dirname "$SITE")")"
echo "  $SP_VER · $(du -sh "$APP/Contents/Resources/pylibs" | cut -f1)"

# ---------------------------------------------------------------- 图标
ICON_SRC="$ROOT/assets/AppIcon-1024.png"
if [ -f "$ICON_SRC" ]; then
  echo "→ 生成 AppIcon.icns"
  ICONSET="$ROOT/build/AppIcon.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  gen() { sips -z "$2" "$2" "$ICON_SRC" --out "$ICONSET/$1" >/dev/null 2>&1; }
  gen icon_16x16.png 16;      gen icon_16x16@2x.png 32
  gen icon_32x32.png 32;      gen icon_32x32@2x.png 64
  gen icon_128x128.png 128;   gen icon_128x128@2x.png 256
  gen icon_256x256.png 256;   gen icon_256x256@2x.png 512
  gen icon_512x512.png 512;   gen icon_512x512@2x.png 1024
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  rm -rf "$ROOT/build"
fi

# ---------------------------------------------------------------- Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>Lumina</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleName</key><string>Lumina</string>
  <key>CFBundleDisplayName</key><string>Lumina Player</string>
  <key>CFBundleIdentifier</key><string>local.lumina.player</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
  <key>NSHumanReadableCopyright</key><string>GPL-3.0 · 引擎 mpv · 转写 whisper.cpp · 网关 OpenList</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><string>true</string>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>Audiovisual media</string>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <key>LSHandlerRank</key><string>Alternate</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>public.movie</string><string>public.audio</string>
        <string>public.audiovisual-content</string><string>public.avi</string>
        <string>public.mpeg-4</string><string>public.mp3</string>
        <string>com.apple.quicktime-movie</string><string>org.matroska.mkv</string>
        <string>com.xquincy.mkv</string><string>public.flac-audio</string>
        <string>com.microsoft.windows-media-wmv</string><string>public.windows-media</string>
      </array>
    </dict>
  </array>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------- 启动器
# 路径一律相对 bundle 自己算；不要出现任何 /Users/... 的绝对路径。
cat > "$APP/Contents/MacOS/Lumina" <<'SH'
#!/bin/bash
# mpv / ffmpeg / whisper-cli 由 brew 提供，GUI 环境里 PATH 不含 /opt/homebrew/bin
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# 注意别写成 $(cd $(dirname $0) ...) 这种一行嵌套 —— 系统 bash 3.2 会语法报错。
DIR="$(dirname "$0")"
DIR="$(cd "$DIR/.." && pwd)"
export LUMINA_APP_RES="$DIR/Resources"
export PYTHONNOUSERSITE=1
export PYTHONPATH="$DIR/Resources/pylibs"
PY="$DIR/Resources/python/bin/python3.13"
[ -x "$PY" ] || PY="$(ls "$DIR"/Resources/python/bin/python3.* 2>/dev/null | head -1)"
exec "$PY" -m lumina "$@"
SH
chmod +x "$APP/Contents/MacOS/Lumina"
/bin/bash -n "$APP/Contents/MacOS/Lumina" || { echo "✗ 启动器语法不过（bash 3.2）"; exit 1; }

# ---------------------------------------------------------------- 云盘网关
if [ -x "$GATEWAY" ]; then
  echo "→ 内置云盘网关"
  mkdir -p "$APP/Contents/Resources/bin"
  cp "$GATEWAY" "$APP/Contents/Resources/bin/openlist"
  chmod +x "$APP/Contents/Resources/bin/openlist"
  [ -f "$(dirname "$GATEWAY")/build-info.txt" ] && \
    cp "$(dirname "$GATEWAY")/build-info.txt" "$APP/Contents/Resources/openlist-build-info.txt"
else
  echo "  ！没有内置网关（$GATEWAY），本次跳过；网盘功能退回外部二进制"
fi

# ---------------------------------------------------------------- 自检 + 签名
echo "→ 用包内的解释器做导入自检（cd / 跑：别让仓库目录里的 lumina/ 混进 sys.path）"
( cd / && PYTHONPATH="$APP/Contents/Resources/pylibs" PYTHONNOUSERSITE=1 \
  "$APP/Contents/Resources/python/bin/python3.13" -c \
  "import lumina, PySide6, httpx
from PySide6.QtWidgets import QApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
assert '/pylibs/' in lumina.__file__, 'import 到了包外的 lumina： ' + lumina.__file__
print('  ✓ lumina', lumina.__file__)
print('  ✓ PySide6', PySide6.__version__)" ) || { echo "✗ 包内导入自检没过"; exit 1; }

echo "→ ad-hoc 签名（未公证，首次打开要右键→打开）"
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null 2>&1 \
  && echo "  ✓ 已签名" || echo "  ！codesign 失败（未签名，Gatekeeper 会拦）"

echo "→ 打包 zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo "  ✓ $ZIP $(du -h "$ZIP" | cut -f1)"
echo
echo "✓ 完成（构建目录 ${APP}）"
echo "  App 体积 $(du -sh "$APP" | cut -f1)（内含 Python 运行时 + PySide6 + 云盘网关）"
echo "  mpv / ffmpeg / whisper-cli 仍由 brew 提供，首次打开时应用内自检会提示安装"

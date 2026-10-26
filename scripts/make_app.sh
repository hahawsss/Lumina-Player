#!/usr/bin/env bash
# 生成本机可双击的 Lumina.app。
#
# 关键点：运行时 venv 不能留在 ~/Documents 里。macOS 的 TCC 会把「文稿」当受保护目录，
# 由 launchd 拉起的 GUI 进程在 Python 初始化 sys.path 读该目录下的 .pth 时会卡在
# 一个没人能看见的授权弹窗上。所以这里把 venv 建到 ~/Library/Application Support/Lumina，
# 并且用非可编辑安装（包体复制进 site-packages），运行时完全不回指 ~/Documents。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SUPPORT="$HOME/Library/Application Support/Lumina"
VENV="$SUPPORT/venv"
PY="$VENV/bin/python"

command -v uv >/dev/null || { echo "缺少 uv"; exit 1; }
mkdir -p "$SUPPORT"

echo "→ 运行时环境 $VENV"
[ -x "$PY" ] || uv venv "$VENV" --python 3.13 --quiet
echo "→ 安装依赖与 lumina 包（非可编辑）"
uv pip install --python "$PY" --quiet pyside6 httpx .
"$PY" -c "import lumina, PySide6, httpx; print('  lumina', lumina.__file__); print('  PySide6', PySide6.__version__)"

APP="$ROOT/dist/Lumina.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

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
  [ -f "$APP/Contents/Resources/AppIcon.icns" ] && echo "  ✓ AppIcon.icns" || echo "  图标生成失败（不影响运行）"
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>Lumina</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleName</key><string>Lumina</string>
  <key>CFBundleDisplayName</key><string>Lumina Player</string>
  <key>CFBundleIdentifier</key><string>local.lumina.player</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
  <key>NSHumanReadableCopyright</key><string>本地播放器 · 引擎 mpv · 转写 whisper.cpp</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationQueriesSchemes</key><array><string>https</string></array>
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

cat > "$APP/Contents/MacOS/Lumina" <<SH
#!/bin/bash
# mpv / ffmpeg / whisper-cli 由 brew 提供，GUI 环境里 PATH 不含 /opt/homebrew/bin
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
# 内置云盘网关的落点要启动器自己算：dist 那份和 /Applications 那份路径不同。
# 注意别写成 \$(cd \$(dirname \$0) ...) 这种一行嵌套 —— 系统 bash 3.2 会语法报错。
DIR="\$(dirname "\$0")"
DIR="\$(cd "\$DIR/.." && pwd)"
export LUMINA_APP_RES="\$DIR/Resources"
exec "$PY" -m lumina "\$@"
SH
chmod +x "$APP/Contents/MacOS/Lumina"
/bin/bash -n "$APP/Contents/MacOS/Lumina" || { echo "✗ 启动器语法不过（bash 3.2）"; exit 1; }

# ---------------------------------------------------------------- 云盘网关（编译进 App）
# OpenList 由 scripts/build_openlist.sh 从源码构建，落进 bundle；找不到就拒绝出包，
# 免得发一个「网盘还要另外装」的 App 出去。产物在 Application Support 的构建缓存里 ——
# 上面生成图标那步会 rm -rf 仓库里的 build/，不能往那儿放。
GATEWAY="${OPENLIST_BIN:-$SUPPORT/build/openlist/openlist}"
if [ ! -x "$GATEWAY" ]; then
  if [ "${LUMINA_ALLOW_NO_GATEWAY:-0}" = "1" ]; then
    echo "  ！没有内置网关（$GATEWAY），本次跳过；网盘功能退回外部二进制"
  else
    echo "✗ 找不到内置网关二进制 $GATEWAY"
    echo "  先跑：bash scripts/build_openlist.sh   （或 LUMINA_ALLOW_NO_GATEWAY=1 强制跳过）"
    exit 1
  fi
else
  echo "→ 内置云盘网关"
  mkdir -p "$APP/Contents/Resources/bin"
  cp "$GATEWAY" "$APP/Contents/Resources/bin/openlist"
  chmod +x "$APP/Contents/Resources/bin/openlist"
  # Apple Silicon 上没签名的二进制根本不让执行；Go 产物本来就带 linker ad-hoc 签名，
  # 但 cp 之后最好重新签一次，免得继承到别的 bundle 的签名信息。
  codesign --force --sign - --timestamp=none \
    "$APP/Contents/Resources/bin/openlist" >/dev/null 2>&1 \
    && echo "  ✓ 已 ad-hoc 签名" || echo "  ！codesign 失败（可能仍能用）"
  GATEWAY_VERSION=$("$APP/Contents/Resources/bin/openlist" version 2>/dev/null | awk '/^Version:/{print $2}')
  echo "  ✓ openlist ${GATEWAY_VERSION:-未知版本} · $(du -h "$APP/Contents/Resources/bin/openlist" | cut -f1)"
  # AGPL 组件要能随时交代是哪份源码编的：构建信息随 bundle 一起分发
  [ -f "$(dirname "$GATEWAY")/build-info.txt" ] && \
    cp "$(dirname "$GATEWAY")/build-info.txt" "$APP/Contents/Resources/openlist-build-info.txt"
fi

# 打包完整性自检：宁可现在报错，也别留下半个 App
[ -f "$APP/Contents/Info.plist" ] || { echo "✗ Info.plist 没生成"; exit 1; }
[ -x "$APP/Contents/MacOS/Lumina" ] || { echo "✗ 启动器缺失或不可执行"; exit 1; }
plutil -lint "$APP/Contents/Info.plist" >/dev/null || { echo "✗ Info.plist 不合法"; exit 1; }
if [ -f "$APP/Contents/Resources/AppIcon.icns" ]; then
  echo "  ✓ AppIcon.icns $(du -h "$APP/Contents/Resources/AppIcon.icns" | cut -f1)"
else
  echo "  （没图标，用系统默认图标）"
fi

LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP"

# 顺手装进 /Applications：那里才是双击 / Spotlight / Launchpad 的入口。
# 运行时在 ~/Library/Application Support/Lumina/venv，所以项目文件夹挪走也不影响它。
if [ -w /Applications ]; then
  rm -rf "/Applications/Lumina.app"
  cp -R "$APP" /Applications/
  [ -x "$LSREGISTER" ] && "$LSREGISTER" -f "/Applications/Lumina.app"
  echo "✓ 已安装到 /Applications/Lumina.app（双击它，或 Spotlight 搜 Lumina）"
else
  echo "（/Applications 不可写，跳过安装；dist/Lumina.app 仍可直接双击）"
fi

echo "✓ 已生成 $APP"
echo "  改了 lumina/ 下的代码后要重新执行本脚本同步进 venv"

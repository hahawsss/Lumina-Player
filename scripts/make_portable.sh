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
# 别往 bundle 里写 __pycache__：既保住 ad-hoc 签名不被自己改动破坏，也不让包内留下编译痕迹
export PYTHONDONTWRITEBYTECODE=1
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

# ---------------------------------------------------------------- 隐私清理
# 见文件末尾：清理必须放在「导入自检 + 签名」之后 —— 自检一跑，包内解释器又会把
# __pycache__ 写回来，先清等于没清。

# ---------------------------------------------------------------- 自检 + 签名
echo "→ 用包内的解释器做导入自检（cd / 跑：别让仓库目录里的 lumina/ 混进 sys.path）"
( cd / && PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$APP/Contents/Resources/pylibs" \
  "$APP/Contents/Resources/python/bin/python3.13" -c \
  "import lumina, PySide6, httpx
from PySide6.QtWidgets import QApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
assert '/pylibs/' in lumina.__file__, 'import 到了包外的 lumina： ' + lumina.__file__
print('  ✓ lumina', lumina.__file__)
print('  ✓ PySide6', PySide6.__version__)" ) || { echo "✗ 包内导入自检没过"; exit 1; }

# ---------------------------------------------------------------- 隐私清理 + 闸门
# 发出去的东西不许带「这台机器是谁、看过什么」：字节码里嵌着编译时的源码绝对路径，
# uv 的 dist-info 里记着仓库在你家目录下的位置 —— 都不是密钥，但都是个人信息。
echo "→ 清掉包里的本机痕迹"
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP" \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \
  -o -name 'direct_url.json' \) -delete 2>/dev/null || true
if [ -f "$APP/Contents/Resources/openlist-build-info.txt" ]; then
  grep -v '^built_by' "$APP/Contents/Resources/openlist-build-info.txt" > "$BUILD_DIR/bi" \
    && mv "$BUILD_DIR/bi" "$APP/Contents/Resources/openlist-build-info.txt"
fi
# uv 把解释器装在你家目录下，这份 sysconfig 快照把安装前缀原样记了下来；
# 运行时没人读它，换成中性占位即可。libpython3.13.dylib 里那份改不动（要动 install_name，
# 会破签名），留给闸门当软提示。
SYSCONFIG="$(ls "$APP"/Contents/Resources/python/lib/python3.*/_sysconfigdata__darwin_darwin.py 2>/dev/null | head -1)"
if [ -n "$SYSCONFIG" ]; then
  sed -i '' "s#$HOME/.local/share/uv#/opt/lumina-python#g" "$SYSCONFIG"
fi
echo "→ 改写后再冒烟一次（确认解释器没被改坏）"
( cd / && PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "$APP/Contents/Resources/python/bin/python3.13" -c \
  "import ssl, sqlite3, sysconfig; print('  ✓ 解释器仍可用', sysconfig.get_config_var('VERSION'))" ) \
  || { echo "✗ 清理把解释器改坏了"; exit 1; }

# ---------------------------------------------------------------- 签名
# 必须排在隐私清理之后：codesign 把「签名那一刻存在的每个文件」逐一记进资源密封，
# 之后再删 __pycache__、改 _sysconfigdata、剥 built_by 就等于事后改包 —— 密封立刻失效，
# 别人下载解压后 Gatekeeper 报「Lumina 已损坏，无法打开」，连右键→打开都救不了。
echo "→ ad-hoc 签名（未公证，首次打开要右键→打开）"
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null 2>&1 \
  || { echo "✗ codesign 失败（未签名，Gatekeeper 会拦）"; exit 1; }
codesign --verify "$APP" >/dev/null 2>&1 \
  || { echo "✗ 签完自检没过：$(codesign --verify "$APP" 2>&1 | head -1)"; exit 1; }
echo "  ✓ 已签名，资源密封完好"

echo "→ 隐私闸门（有个人数据就不出包）"
# 探针用**这台机器上真实存在的凭据值**，不是「cookie」这种字段名 —— 字段名在我们自己源码里，
# 拿它当探针会把每一次正常构建都拦死。硬失败：真凭据、用户数据路径、仓库路径、编译痕迹；
# 软提示：CPython 运行时自带的上游构建路径（改不动，也不含你的内容数据）。
python3 - "$APP" "$SUPPORT" "$ROOT" "$HOME" <<'PY' || exit 1
import json, sqlite3, subprocess, sys
from pathlib import Path

app, support, root, home = (Path(a) for a in sys.argv[1:5])
secrets = {}
db = support / "cloud/openlist/data.db"
if db.is_file():
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for (raw,) in con.execute("select addition from x_storages"):
            acc = json.loads(raw or "{}")
            for field in ("cookie", "qrcode_token"):
                value = str(acc.get(field) or "")
                if len(value) > 8:
                    secrets[f"115 {field}"] = value[:32]
    except sqlite3.Error as exc:
        print(f"  ！读不到 data.db，凭据探针降级：{exc}")
gateway = app / "Contents/Resources/bin/openlist"
if gateway.is_file() and secrets:
    try:
        out = subprocess.run([str(gateway), "admin", "token", "--data",
                              str(support / "cloud/openlist")],
                             capture_output=True, text=True, timeout=20).stdout
        token = next((line.split(":", 1)[1].strip() for line in out.splitlines()
                      if line.startswith("Admin token:")), "")
        if token:
            secrets["OpenList 管理员 token"] = token[:32]
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ！取不到管理员 token，这一项没验：{exc}")

probes = {name: value.encode() for name, value in secrets.items()}
probes.update({"用户数据目录": str(support).encode(), "仓库路径": str(root).encode()})
name_marker = home.name.encode()
hard, soft = [], 0
for path in sorted(app.rglob("*")):
    if not path.is_file():
        continue
    rel = str(path.relative_to(app))
    if path.name.endswith((".pyc", ".pyo")) or "__pycache__" in rel \
            or path.name == "direct_url.json":
        hard.append(f"{rel}: 残留的编译痕迹")
        continue
    try:
        blob = path.read_bytes()
    except OSError:
        continue
    hits = [label for label, probe in probes.items() if probe in blob]
    if name_marker in blob:
        hits.append("用户名/家目录")
    if not hits:
        continue
    if rel.startswith("Contents/Resources/python/"):
        soft += 1
    else:
        hard.append(f"{rel}: {', '.join(hits)}")
print(f"  凭据探针：{len([k for k in probes if k.startswith(('115', 'OpenList'))])} 条真值 + 2 条路径")
if soft:
    print(f"  ！{soft} 个 CPython 运行时文件带上游构建路径（不含你的数据，改不动）")
if hard:
    print(f"✗ 包里发现 {len(hard)} 处本机痕迹：")
    for line in hard[:20]:
        print(f"    {line}")
    sys.exit(1)
print("  ✓ 载荷干净：无凭据真值、无用户数据路径、无仓库路径、无编译痕迹")
PY

echo "→ 打包 zip"
rm -f "$ZIP"
ditto -c -k --keepParent --norsrc --noextattr "$APP" "$ZIP"

echo "→ 把刚打好的包解开复检（发出去的就是这一份）"
CHECK="$(mktemp -d "${TMPDIR:-/tmp}/lumina-zipcheck-XXXXXX")"
ditto -x -k "$ZIP" "$CHECK"
EXTRACTED="$CHECK/Lumina.app"
fail() { echo "✗ $1"; rm -rf "$CHECK"; exit 1; }
[ -d "$EXTRACTED" ] || fail "解压后没有 Lumina.app"
codesign --verify "$EXTRACTED" >/dev/null 2>&1 \
  || fail "解包后签名密封失效：$(codesign --verify "$EXTRACTED" 2>&1 | head -1)"
LINKS_SRC=$(find "$APP" -type l | wc -l | tr -d ' ')
LINKS_OUT=$(find "$EXTRACTED" -type l | wc -l | tr -d ' ')
[ "$LINKS_SRC" = "$LINKS_OUT" ] || fail "软链接丢了：包内 $LINKS_SRC 个，解压后只剩 $LINKS_OUT 个"
[ -x "$EXTRACTED/Contents/MacOS/Lumina" ] || fail "启动器丢了可执行位"
[ -x "$EXTRACTED/Contents/Resources/bin/openlist" ] || fail "内置网关丢了可执行位"
( cd / && PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$EXTRACTED/Contents/Resources/pylibs" \
  "$EXTRACTED/Contents/Resources/python/bin/python3.13" -c \
  "import lumina, PySide6
assert '/Lumina.app/' in lumina.__file__, '解包后 import 到了包外：' + lumina.__file__" ) \
  || fail "解包后的包内解释器跑不起来"
rm -rf "$CHECK"
echo "  ✓ 密封完好 · 软链接 $LINKS_OUT/$LINKS_SRC · 启动器与网关可执行 · 包内解释器可用"
echo "  ✓ $ZIP $(du -h "$ZIP" | cut -f1)"
echo
echo "✓ 完成（构建目录 ${APP}）"
echo "  App 体积 $(du -sh "$APP" | cut -f1)（内含 Python 运行时 + PySide6 + 云盘网关）"
echo "  mpv / ffmpeg / whisper-cli 仍由 brew 提供，首次打开时应用内自检会提示安装"

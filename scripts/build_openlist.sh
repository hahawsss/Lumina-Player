#!/usr/bin/env bash
# 把 OpenList（115 云盘网关）从源码编译进 Lumina.app。
#
# 为什么自己编：版本与构建参数要钉死（跟 Lumina 验证过的那一份同一个 tag），
# 产物直接进 bundle，用户不需要另外装。构建缓存与产物都放 Application Support，
# 不留在 ~/Documents 里（TCC 保护目录，GUI 进程读它会卡在看不见的授权弹窗上）。
#
#   bash scripts/build_openlist.sh              # 编到默认落点
#   OPENLIST_TAG=v4.3.0 bash scripts/build_openlist.sh
#   bash scripts/build_openlist.sh --clean      # 删掉源码缓存重编
set -euo pipefail

SUPPORT="$HOME/Library/Application Support/Lumina"
CACHE="$SUPPORT/build"
# 注意：APFS 默认大小写不敏感，源码目录与产物目录不能只靠大小写区分
SRC="$CACHE/openlist-src"
OUT_DIR="$CACHE/openlist"
OUT="$OUT_DIR/openlist"
GOROOT_LOCAL="$CACHE/go"
# 与现在这份已验证的二进制同一版本：v4.2.9 / commit 0497ee8 / go1.27.3
TAG="${OPENLIST_TAG:-v4.2.9}"
GO_VERSION="${GO_VERSION:-1.27.3}"
FRONTEND_REPO="${FRONTEND_REPO:-OpenListTeam/OpenList-Frontend}"
PROXY="${LUMINA_PROXY:-http://127.0.0.1:7897}"
MODULE="github.com/OpenListTeam/OpenList/v4"

say() { printf '\033[1m→\033[0m %s\n' "$1"; }
die() { printf '\033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

[ "${1:-}" = "--clean" ] && rm -rf "$SRC"

command -v git >/dev/null || die "缺少 git"
command -v curl >/dev/null || die "缺少 curl"
command -v python3 >/dev/null || die "缺少 python3（取校验和要用）"

# ---------------------------------------------------------------- Go 工具链
# brew 的 go bottle 走 ghcr 实测只有 ~20 KB/s，这里优先用系统上已有的 go，
# 没有就从国内镜像解一份到软件自己的构建目录里（不写 /usr/local、不动 brew）。
find_go() {
  if [ -n "${GO_BIN:-}" ] && [ -x "${GO_BIN:-}" ]; then printf '%s\n' "$GO_BIN"; return 0; fi
  if command -v go >/dev/null 2>&1; then printf '%s\n' "$(command -v go)"; return 0; fi
  for candidate in /opt/homebrew/bin/go /usr/local/bin/go "$GOROOT_LOCAL/bin/go"; do
    [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

bootstrap_go() {
  say "本机没有 Go：从国内镜像装 go$GO_VERSION 到 $GOROOT_LOCAL（不碰系统目录）"
  mkdir -p "$CACHE"
  local name="go$GO_VERSION.darwin-arm64.tar.gz"
  local tgz="$CACHE/$name"
  local want
  want="$(curl -fsSL --max-time 30 "https://golang.google.cn/dl/?mode=json&include=all" \
    | python3 -c "
import json, sys
for row in json.load(sys.stdin):
    if row.get('version') != 'go$GO_VERSION':
        continue
    for f in row.get('files', []):
        if f.get('filename') == '$name':
            print(f['sha256'])
" 2>/dev/null || true)"
  [ -n "$want" ] || say "  ！拿不到官方校验和，跳过校验（仍会检查解压结果）"
  if [ ! -s "$tgz" ] || { [ -n "$want" ] && [ "$(shasum -a 256 "$tgz" | cut -d' ' -f1)" != "$want" ]; }; then
    rm -f "$tgz"
    for base in https://mirrors.aliyun.com/golang \
                https://mirrors.ustc.edu.cn/golang \
                https://golang.google.cn/dl \
                https://go.dev/dl; do
      say "  试 $base/$name"
      if curl -fL --retry 2 -o "$tgz" "$base/$name"; then
        if [ -n "$want" ] && [ "$(shasum -a 256 "$tgz" | cut -d' ' -f1)" != "$want" ]; then
          say "  ！校验和不符，换下一个源"
          rm -f "$tgz"
          continue
        fi
        say "  ✓ 下载完成并校验通过（$(du -h "$tgz" | cut -f1)）"
        break
      fi
    done
  fi
  [ -s "$tgz" ] || die "Go 工具链没下到：检查网络，或自己装（brew install go）后设 GO_BIN"
  rm -rf "$GOROOT_LOCAL"
  tar -C "$CACHE" -zxf "$tgz" || die "Go 解压失败"
  [ -x "$GOROOT_LOCAL/bin/go" ] || die "解压后没有 bin/go"
  rm -f "$tgz"        # 解压完就没用了，别在构建目录里占 80 MB
}

GO="$(find_go || true)"
[ -n "$GO" ] || { bootstrap_go; GO="$GOROOT_LOCAL/bin/go"; }
[ -x "$GO" ] || die "Go 不可用：$GO"
case "$GO" in
  "$GOROOT_LOCAL"/*) export GOROOT="$GOROOT_LOCAL" ;;   # 自己引导的那份才需要指 GOROOT
esac
export PATH="$(dirname "$GO"):$PATH"
# module 缓存也留在构建目录里，一把删干净
export GOPATH="$CACHE/gopath"
export GOMODCACHE="$CACHE/gopath/pkg/mod"
mkdir -p "$GOPATH"
say "Go: $("$GO" version)"

# 代理：GitHub 与 Go module 在这台机器上只有走系统代理才可达
if [ -n "$PROXY" ] && nc -z 127.0.0.1 "${PROXY##*:}" 2>/dev/null; then
  export https_proxy="$PROXY" http_proxy="$PROXY" HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY"
  say "走本机代理 $PROXY"
fi

mkdir -p "$CACHE" "$OUT_DIR"

# ---------------------------------------------------------------- 源码
if [ -d "$SRC/.git" ] && [ "$(git -C "$SRC" describe --tags 2>/dev/null || true)" = "$TAG" ]; then
  say "源码缓存已就绪 $SRC（$TAG）"
else
  say "拉取 OpenList $TAG"
  rm -rf "$SRC"
  git clone --depth 1 --branch "$TAG" "https://github.com/OpenListTeam/OpenList.git" "$SRC" \
    || die "clone 失败：检查网络/代理，或换个 OPENLIST_TAG"
fi
COMMIT="$(git -C "$SRC" log --pretty=format:%h -1)"
say "commit $COMMIT"

# ---------------------------------------------------------------- 内嵌前端
# 官方 CI 用 GitHub API 找资产，我们这边 API 常被限流；改走 releases/latest 的
# 302 目标 + expanded_assets 页面，不碰 API。
WEB_TAG=""
WEB_TARBALL=""
latest_redirect() {
  curl -fsSL -o /dev/null -w '%{url_effective}' \
    "https://github.com/$FRONTEND_REPO/releases/latest" 2>/dev/null || true
}
LATEST_URL="$(latest_redirect)"
case "$LATEST_URL" in
  */releases/tag/*) WEB_TAG="${LATEST_URL##*/releases/tag/}" ;;
esac
if [ -n "$WEB_TAG" ]; then
  WEB_TARBALL="$(curl -fsSL "https://github.com/$FRONTEND_REPO/releases/expanded_assets/$WEB_TAG" \
    | grep -o 'openlist-frontend-dist[^"]*\.tar\.gz' | grep -v lite | head -1 || true)"
fi
if [ -z "$WEB_TARBALL" ]; then
  # 兜底：按常见命名猜一次
  WEB_TARBALL="openlist-frontend-dist-$WEB_TAG.tar.gz"
fi
if [ -n "$WEB_TAG" ] && [ -s "$CACHE/$WEB_TARBALL" ]; then
  say "前端资产已缓存 $WEB_TARBALL"
elif [ -n "$WEB_TAG" ]; then
  say "下载内嵌前端 $FRONTEND_REPO $WEB_TAG / $WEB_TARBALL"
  curl -fL --retry 3 -o "$CACHE/$WEB_TARBALL" \
    "https://github.com/$FRONTEND_REPO/releases/download/$WEB_TAG/$WEB_TARBALL" \
    || die "前端下载失败：$WEB_TAG / $WEB_TARBALL"
else
  die "拿不到 $FRONTEND_REPO 的最新 release 标签（网络/代理不通？）"
fi
rm -rf "$SRC/public/dist"
mkdir -p "$SRC/public/dist"
tar -zxf "$CACHE/$WEB_TARBALL" -C "$SRC/public/dist"
[ -n "$(ls -A "$SRC/public/dist")" ] || die "前端解压后是空的"

# ---------------------------------------------------------------- 编译
# 前端是 public/public.go 里 `//go:embed all:dist` 无条件内嵌的（这个版本没有
# with_web 标记），所以上面必须先把 dist 解压到位；构建标记只需 jsoniter。
BUILD_TAGS="jsoniter"
[ -n "$(ls -A "$SRC/public/dist" 2>/dev/null)" ] || die "public/dist 是空的，前端没内嵌进去"
# -ldflags 的值会被按空白切分，所以这些值里一律不能带空格（上一版就是栽在
# BuiltAt 的空格里，link 直接吐用法）；作者串里的空格换成 %20 由界面再解释。
BUILT_AT="$(date +'%Y-%m-%dT%H:%M:%S%z')"
say "编译（首次要下 module，几分钟）"
(
  cd "$SRC"
  export CGO_ENABLED=1
  "$GO" build -trimpath -o "$OUT" \
    -tags "$BUILD_TAGS" \
    -ldflags "-s -w -X $MODULE/internal/conf.Version=$TAG -X $MODULE/internal/conf.GitCommit=$COMMIT -X $MODULE/internal/conf.GitAuthor=OpenListTeam -X $MODULE/internal/conf.BuiltAt=$BUILT_AT -X $MODULE/internal/conf.WebVersion=$WEB_TAG" \
    .
) || die "go build 失败（上面有原因）"

# ---------------------------------------------------------------- 验收
[ -x "$OUT" ] || die "产物没生成：$OUT"
SIZE_BYTES=$(stat -f %z "$OUT")
[ "$SIZE_BYTES" -gt 30000000 ] || die "产物只有 $SIZE_BYTES 字节，不像带前端的完整构建"
file "$OUT" | grep -q "Mach-O" || die "产物不是 Mach-O：$(file "$OUT")"
VERSION_OUT="$("$OUT" version 2>&1 || true)"
printf '%s\n' "$VERSION_OUT" | grep -q "$TAG" || die "版本对不上：$VERSION_OUT"
say "产物就绪 $OUT（$(du -h "$OUT" | cut -f1)）"
echo "$VERSION_OUT" | sed 's/^/    /'
# 版本来源跟着二进制走：AGPL 组件要能随时说清是哪份源码编的
cat > "$OUT_DIR/build-info.txt" <<INFO
source        https://github.com/OpenListTeam/OpenList
tag           $TAG
commit        $COMMIT
frontend_repo $FRONTEND_REPO
frontend_tag  $WEB_TAG
build_tags    $BUILD_TAGS
go            $(go version)
built_at      $BUILT_AT
built_by      $(whoami)@$(hostname -s)
rebuild       bash scripts/build_openlist.sh
INFO
say "构建信息 $OUT_DIR/build-info.txt"
say "下一步：bash scripts/make_app.sh   # 会把它放进 Lumina.app/Contents/Resources/bin/openlist"

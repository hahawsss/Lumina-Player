"""软件数据的台账：每个产出、下载、缓存落在哪儿、占多大、能不能删。

原则只有一条：删除范围死死锁在软件自己的数据目录里，路径台账一律由代码算出来，
不手写常量 —— 目录改名、字幕根目录被用户挪走时，面板才不会显示一个不存在的路径。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from . import config as cfg, i18n


class StorageError(RuntimeError):
    pass


_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(value: float) -> str:
    size = float(max(0, value))
    for unit in _UNITS:
        if unit == _UNITS[-1] or size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def path_size(path: str | Path) -> int:
    """文件取自身大小，目录递归求和；不跟符号链接，坏掉的条目直接跳过。"""
    target = Path(path)
    try:
        if target.is_symlink():
            return 0
        if target.is_file():
            return target.stat().st_size
        if not target.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(target, followlinks=False):
        for name in files:
            entry = Path(root) / name
            try:
                if not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    return total


def display_path(path: str | Path) -> str:
    """把家目录折成 ~，长路径在侧栏里还看得清。"""
    text = str(path)
    home = str(Path.home())
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


# ---------------------------------------------------------------- 台账

@dataclass(frozen=True)
class Target:
    label: str
    path: Path
    note: str = ""
    clean: str = ""          # 对应的清理键，空 = 只给「打开」


def openlist_dir(config: cfg.Config) -> Path:
    return Path(cfg.SUPPORT_DIR) / "cloud" / "openlist"


def gateway_bundle_path() -> Path:
    """App 内置网关的落点；启动器没导出环境变量时退到仓库 dist 里那份。"""
    from . import cloud
    return cloud.bundle_binaries()[0]


def gateway_legacy_path() -> Path:
    from . import cloud
    return cloud.legacy_binary()


def gateway_legacy_present() -> bool:
    """旧副本只在真的存在时才列进台账，删掉后别留一行假路径。"""
    return gateway_legacy_path().is_file()


def build_dir() -> Path:
    """编译网关用的缓存（Go 工具链 + 源码 + module），能整目录删掉。"""
    return Path(cfg.SUPPORT_DIR) / "build"


def portable_venv_dirs() -> list[Path]:
    """make_portable.sh 用的独立运行时（可分发打包），和软件自己那份 venv 不是一回事。

    空壳目录不算：清完之后别在台账里留一行指向空气。
    """
    root = Path(cfg.SUPPORT_DIR)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and p.name.startswith("distvenv") and any(p.iterdir()))


def portable_packages() -> list[Path]:
    """仓库 dist/ 里打好的可分发 zip；装好的 App 里跑就读不到，直接跳过。"""
    repo_dist = Path(__file__).resolve().parents[1] / "dist"
    if not repo_dist.is_dir():
        return []
    return sorted(repo_dist.glob("Lumina-*.zip"))


def transcript_dir() -> Path:
    return cfg.CACHE_DIR / "transcripts"


def translation_cache_path() -> Path:
    return cfg.CACHE_DIR / "translations.jsonl"


def subtitle_root(config: cfg.Config) -> Path:
    return cfg.subtitle_root(config.get("subtitle.output_dir", ""))


def sections(config: cfg.Config) -> list[tuple[str, list[Target]]]:
    root = Path(cfg.SUPPORT_DIR)
    gateway_rows = [
        Target("OpenList 数据目录", openlist_dir(config), "115 登录状态、挂载配置"),
        Target("OpenList 日志", openlist_dir(config) / "server.out"),
        Target("网关程序（App 内置）", gateway_bundle_path(),
               "编译进 Lumina.app 的那一份，随软件一起分发"),
    ]
    if gateway_legacy_present():
        gateway_rows.append(
            Target("网关程序（旧副本）", gateway_legacy_path(),
                   "早先版本下载在数据目录里的；确认内置那份可用后可以删掉腾空间",
                   clean="gateway_legacy"))
    gateway_rows += [
        Target("应用日志", cfg.APP_LOG_PATH, clean="logs"),
        Target("播放器日志", cfg.LOG_PATH, "mpv 每次启动覆盖"),
    ]
    build_rows = [Target("网关构建缓存", build_dir(),
                         "编译网关用的 Go 工具链、源码与 module 缓存；出过包就可以删，"
                         "下次构建会重新拉", clean="build_cache")]
    for venv in portable_venv_dirs():
        build_rows.append(Target("可分发打包运行时", venv,
                                 "make_portable.sh 建在数据目录里的独立 Python 环境；"
                                 "删了下次打可分发包会重建", clean="portable_venv"))
    for package in portable_packages():
        build_rows.append(Target("可分发包", package,
                                 "打好的 zip（发给别的机器用），删掉不影响本机运行"))
    return [
        ("软件数据根", [
            Target("应用数据目录", root, "模型、缓存、日志都在这儿"),
            Target("配置文件", cfg.CONFIG_PATH),
            Target("内置运行环境", root / "venv", "软件自带的 Python 环境，别手删"),
        ]),
        ("字幕产出", [
            Target("字幕输出目录", subtitle_root(config),
                   "「字幕放在视频旁边」时这一部会落在视频目录"),
            Target("实时字幕临时文件", cfg.LIVE_DIR, "实时翻译边播边写的临时 ass，退出即清",
                   clean="live"),
            Target("转写临时音频", cfg.WORK_DIR, "whisper 用的 16k 切片，转写完就该删",
                   clean="work"),
        ]),
        ("缓存与标记", [
            Target("转写标记", transcript_dir(),
                   "记录每部片已经转到哪；删掉后一键翻译会整个重转", clean="transcripts"),
            Target("翻译缓存", translation_cache_path(),
                   "翻过的句子，删掉后同样的句子要重翻", clean="translations"),
            Target("每片识别语言", cfg.LANGUAGE_HINTS_PATH,
                   "每部片单独指定的识别语言", clean="language_hints"),
            Target("下载断点", cfg.MODEL_DIR, "没下完的 .part 文件", clean="parts"),
        ]),
        ("网关与日志", gateway_rows),
        ("构建与分发", build_rows),
    ]


def target_size(item: Target, config: cfg.Config) -> int:
    if item.clean == "parts":
        # 这一行要的是「断点占了多大」，不是整个模型目录
        return sum(path_size(part) for part in leftover_parts(config))
    return path_size(item.path)


def scan_targets(config: cfg.Config) -> list[tuple[str, Target, int]]:
    """台账 + 实测体积；面板先画出行，体积后台算完再填。"""
    rows: list[tuple[str, Target, int]] = []
    for label, items in sections(config):
        for item in items:
            if item.clean == "parts":
                parts = leftover_parts(config)
                item = replace(item, note=f"没下完的 .part 文件 · {len(parts)} 个")
            rows.append((label, item, target_size(item, config)))
    return rows


# ---------------------------------------------------------------- 模型

ASR_PATTERNS = ("ggml-*.bin",)
LLM_PATTERNS = ("*.gguf",)


def model_dir(kind: str) -> Path:
    if kind == "asr":
        return cfg.ASR_MODEL_DIR
    if kind == "llm":
        return cfg.LLM_MODEL_DIR
    raise StorageError(i18n.t("不认识模型类型：{}", kind))


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(name, pattern) for pattern in patterns)


@dataclass(frozen=True)
class ModelFile:
    kind: str
    name: str
    path: Path
    size: int
    usage: tuple[str, ...] = ()


def usage_map(config: cfg.Config) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    asr_name = str(config.get("asr.model", "") or "")
    if asr_name:
        out.setdefault(asr_name, []).append("转写在用")
    for key, label in (("translate.llama_model", "整片/一键翻译在用"),
                       ("translate.live_llama_model", "实时翻译在用")):
        name = str(config.get(key, "") or "")
        if name:
            out.setdefault(name, []).append(label)
    return out


def installed_models(config: cfg.Config) -> list[ModelFile]:
    """只认模型目录里对应类型的文件；半下载的 .part 另走清理。"""
    usages = usage_map(config)
    rows: list[ModelFile] = []
    for kind, patterns in (("asr", ASR_PATTERNS), ("llm", LLM_PATTERNS)):
        base = model_dir(kind)
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_file() or not _matches(entry.name, patterns):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            rows.append(ModelFile(kind, entry.name, entry, size,
                                  tuple(usages.get(entry.name, ()))) )
    return rows


def leftover_parts(config: cfg.Config) -> list[Path]:
    base = Path(cfg.MODEL_DIR)
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.part") if p.is_file())


def _inside(path: Path, base: Path) -> bool:
    try:
        return path.parent == base.resolve()
    except OSError:
        return False


def _within(path: Path, base: Path) -> bool:
    try:
        return base in path.resolve().parents
    except OSError:
        return False


def replacement_model(kind: str, config: cfg.Config, exclude: str = "") -> str:
    """删掉在用的模型后挑一个替补：先看默认值还在不在，再选最小的。"""
    base = model_dir(kind)
    names = [row.name for row in installed_models(config)
             if row.kind == kind and row.name != exclude]
    if not names:
        return ""
    default = str(cfg.DEFAULTS["asr"]["model"] if kind == "asr"
                  else cfg.DEFAULTS["translate"]["llama_model"])
    if default in names:
        return default
    keys = {"asr": "asr.model", "llm": "translate.llama_model"}
    current = str(config.get(keys[kind], "") or "")
    if current in names:
        return current
    smallest = min((row for row in installed_models(config)
                    if row.kind == kind and row.name != exclude),
                   key=lambda row: row.size, default=None)
    return smallest.name if smallest else names[0]


def delete_model(kind: str, name: str, config: cfg.Config) -> str:
    """删除模型文件，并把指向它的配置项改到替补模型上。"""
    base = model_dir(kind).resolve()
    path = (base / name).resolve()
    if not _inside(path, base):
        raise StorageError(i18n.t("只能删除模型目录里的文件"))
    patterns = ASR_PATTERNS if kind == "asr" else LLM_PATTERNS
    if not _matches(path.name, patterns):
        raise StorageError(i18n.t("{} 不是可删除的模型文件", path.name))
    if not path.is_file():
        raise StorageError(i18n.t("文件已经不存在：{}", path.name))
    size = path.stat().st_size
    try:
        path.unlink()
    except OSError as exc:
        raise StorageError(i18n.t("删除失败：{}", exc)) from exc
    cfg.append_log(f"删除模型：{path}（{human_size(size)}）")
    note = i18n.t("已删除 {}（{}）", path.name, human_size(size))
    keys = ({"asr": ("asr.model",),
             "llm": ("translate.llama_model", "translate.live_llama_model")}[kind])
    for key in keys:
        if str(config.get(key, "") or "") != path.name:
            continue
        fallback = replacement_model(kind, config, exclude=path.name)
        if fallback:
            config.set(key, fallback)
            note += f"，{key.split('.')[1]} 改用 {fallback}"
        else:
            note += "，这个目录下已没有同类模型，需要时重新下载"
    return note


# ---------------------------------------------------------------- 清理

def _remove_files(base: Path, patterns: tuple[str, ...], recursive: bool = False,
                  dry_run: bool = False) -> tuple[int, int]:
    if not base.is_dir():
        return (0, 0)
    base = base.resolve()
    walker = os.walk(base) if recursive else [(str(base), [],
                                              [p.name for p in base.iterdir()
                                               if not p.is_dir()])]
    removed = 0
    freed = 0
    for root, _dirs, files in walker:
        for name in files:
            entry = Path(root) / name
            try:
                if not _matches(name, patterns) or entry.is_symlink():
                    continue
                # 递归清理允许子目录，但绝不允许跑出这个目录去
                if not _within(entry, base):
                    continue
                size = entry.stat().st_size
                if not dry_run:
                    entry.unlink()
                removed += 1
                freed += size
            except OSError:
                continue
    return (removed, freed)


def _prune_empty(base: Path) -> int:
    """自底向上删掉空目录；返回删掉的个数。base 自己保留。"""
    if not base.is_dir():
        return 0
    base = base.resolve()
    removed = 0
    for root, _dirs, _files in os.walk(base, topdown=False):
        here = Path(root)
        if here == base:
            continue
        try:
            # os.walk 的 dirs 是进子目录之前扫的，子目录刚被删掉它还以为在
            if not os.listdir(here):
                here.rmdir()
                removed += 1
        except OSError:
            continue
    return removed


def _remove_children(base: Path, dry_run: bool = False,
                     skip: tuple[str, ...] = ()) -> tuple[int, int]:
    """清空一个目录里的内容（可跳过个别名字），返回 (文件数, 字节数)。"""
    if not base.is_dir():
        return (0, 0)
    base = base.resolve()
    removed = 0
    freed = 0
    for entry in sorted(base.iterdir()):
        if entry.name in skip:
            continue
        try:
            resolved = entry.resolve()
        except OSError:
            continue
        if entry.is_symlink():
            # 软链本身删掉（不顺着删它指向的东西）
            removed += 1
            if not dry_run:
                try:
                    entry.unlink()
                except OSError:
                    removed -= 1
            continue
        if not _within(resolved, base):
            continue
        if entry.is_dir():
            for root, _dirs, files in os.walk(resolved):
                for name in files:
                    try:
                        target = Path(root) / name
                        freed += target.stat().st_size
                        removed += 1
                        if not dry_run:
                            target.unlink(missing_ok=True)
                    except OSError:
                        continue
            if not dry_run:
                shutil.rmtree(resolved, ignore_errors=True)
        else:
            try:
                freed += entry.stat().st_size
                removed += 1
                if not dry_run:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    if not dry_run:
        _prune_empty(base)
    return (removed, freed)


def preview(key: str, config: cfg.Config) -> tuple[int, int]:
    """先数一遍能删多少，用来在确认框里写清楚。"""
    return clean(key, config, dry_run=True)


def clean(key: str, config: cfg.Config, dry_run: bool = False) -> tuple[int, int]:
    """按清理键删文件；一律限定在软件数据目录（或用户指定的字幕根）之内。"""
    if key == "live":
        return _remove_children(cfg.LIVE_DIR, dry_run)
    if key == "work":
        # work 整个是草稿区：临时音频、内嵌字幕抽取、老版本残留，全清；live 有自己的清理项
        return _remove_children(cfg.WORK_DIR, dry_run, skip=("live",))
    if key == "transcripts":
        return _remove_files(transcript_dir(), ("*.json",), dry_run=dry_run)
    if key == "translations":
        path = translation_cache_path()
        try:
            if not path.is_file() or not _inside(path.resolve(),
                                                 Path(cfg.CACHE_DIR).resolve()):
                return (0, 0)
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
            return (1, size)
        except OSError:
            return (0, 0)
    if key == "language_hints":
        path = cfg.LANGUAGE_HINTS_PATH
        try:
            if not path.is_file():
                return (0, 0)
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
                cfg.forget_language_hints()
            return (1, size)
        except OSError:
            return (0, 0)
    if key == "parts":
        return _remove_files(cfg.MODEL_DIR, ("*.part",), recursive=True,
                             dry_run=dry_run)
    if key == "build_cache":
        # 整个构建缓存目录清空（Go 工具链 + 源码 + module）；bundle 里那份不受影响
        return _remove_children(build_dir(), dry_run)
    if key == "portable_venv":
        # 可分发打包用的独立 venv：删了下次 make_portable.sh 会重建
        removed = 0
        freed = 0
        for venv in portable_venv_dirs():
            count, size = _remove_children(venv, dry_run)
            removed += count
            freed += size
        return (removed, freed)
    if key == "gateway_legacy":
        path = gateway_legacy_path()
        try:
            if not path.is_file() or not _inside(path.resolve(),
                                                 path.parent.resolve()):
                return (0, 0)
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
                parent = path.parent
                if parent.name == "bin" and not any(parent.iterdir()):
                    parent.rmdir()
            return (1, size)
        except OSError:
            return (0, 0)
    if key == "logs":
        removed = 0
        freed = 0
        for path, truncate in ((cfg.APP_LOG_PATH, True), (cfg.LOG_PATH, False)):
            try:
                if not path.is_file():
                    continue
                if not _inside(path.resolve(), Path(cfg.SUPPORT_DIR).resolve()):
                    continue
                freed += path.stat().st_size
                if not dry_run:
                    if truncate:
                        path.write_text("", encoding="utf-8")
                    else:
                        path.unlink()
                removed += 1
            except OSError:
                continue
        return (removed, freed)
    raise StorageError(i18n.t("不认识清理项：{}", key))


CLEAN_KEYS = ("live", "work", "transcripts", "translations", "language_hints",
              "parts", "logs")
# 旧版网关二进制、构建缓存与打包运行时都不进「一键清理」：
# 前者可能正在跑，后两者删了下次要重编（20 分钟）/重建 venv，只能单独手删
GATEWAY_LEGACY_KEY = "gateway_legacy"
BUILD_CACHE_KEY = "build_cache"
PORTABLE_VENV_KEY = "portable_venv"
ALL_CLEAN_KEYS = CLEAN_KEYS + (GATEWAY_LEGACY_KEY, BUILD_CACHE_KEY, PORTABLE_VENV_KEY)


def clean_all(config: cfg.Config, dry_run: bool = False) -> tuple[int, int]:
    removed = 0
    freed = 0
    for key in CLEAN_KEYS:
        count, size = clean(key, config, dry_run=dry_run)
        removed += count
        freed += size
    if not dry_run:
        _prune_empty(cfg.WORK_DIR)
    return (removed, freed)


# ---------------------------------------------------------------- 打开

def reveal(path: str | Path) -> None:
    """在 Finder 里打开：文件选中给它看，目录直接进；不存在就退到最近的父目录。"""
    target = Path(path)
    if not target.exists():
        parent = target.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        subprocess.run(["open", str(parent)], check=False)
        return
    args = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
    subprocess.run(args, check=False)

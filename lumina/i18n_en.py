"""英文词表：键 = 中文原文（可带 {} 占位符），值 = 英文。

漏翻的条目由 i18n.missing() 暴露，selfcheck 会把它当失败 —— 界面里出现中文
而语言是英文，就是没翻。
"""

EN: dict[str, str] = {
    # ---------------------------------------------------------------- 侧栏
    "轨道": "Tracks",
    "设置": "Settings",
    "列表": "Playlist",
    "存储": "Storage",
    "网盘": "Cloud",
    "音轨 / 字幕轨 / 显示方式": "Audio / subtitle tracks, display mode",
    "转写与翻译任务、模型选择": "Transcription & translation jobs, models",
    "解码、字幕外观、字幕文件、默认语言": "Decoding, subtitle look, files, language",
    "播放列表与打开历史": "Playlist and recent files",
    "模型删除、所有产出与缓存路径、清理": "Models, every output & cache path, cleanup",
    "115 浏览与 OpenList 管理": "115 browser and OpenList service",
    "收起侧栏": "Collapse sidebar",

    # ---------------------------------------------------------------- 控制条
    "播放/暂停 (空格)": "Play / pause (Space)",
    "停止（回到打开界面）": "Stop (back to start screen)",
    "上一个 (← 双击)": "Previous (double-click ←)",
    "下一个": "Next",
    "后退 10 秒 (←)": "Back 10s (←)",
    "前进 30 秒 (→)": "Forward 30s (→)",
    "播放速度；点击循环，长按选择具体倍速": "Playback speed — click to cycle, hold to pick",
    "静音 (M)": "Mute (M)",
    "音量 (↑↓)": "Volume (↑↓)",
    "播放列表 (L)": "Playlist (L)",
    "音轨 (A)": "Audio track (A)",
    "字幕 (S)": "Subtitles (S)",
    "AI 字幕 (T)": "AI subtitles (T)",
    "截图到工作目录": "Save screenshot to work folder",
    "设置 (⌘,)": "Settings (⌘,)",
    "全屏 (F)": "Fullscreen (F)",
    "静音": "Mute",

    # ---------------------------------------------------------------- 菜单
    "文件": "File",
    "打开视频…": "Open Video…",
    "打开文件夹…": "Open Folder…",
    "添加字幕文件…": "Add Subtitle File…",
    "最近打开": "Open Recent",
    "播放": "Playback",
    "播放/暂停": "Play / Pause",
    "后退 10 秒": "Back 10s",
    "前进 30 秒": "Forward 30s",
    "后退 60 秒": "Back 60s",
    "前进 60 秒": "Forward 60s",
    "速度": "Speed",
    "显示": "View",
    "音轨与字幕": "Tracks & Subtitles",
    "AI 字幕": "AI Subtitles",
    "存储与清理": "Storage & Cleanup",
    "网盘（115 / OpenList）": "Cloud (115 / OpenList)",
    "OpenList 管理": "OpenList Service",
    "全屏": "Full Screen",
    "帮助": "Help",
    "快捷键": "Keyboard Shortcuts",
    "关于 Lumina": "About Lumina",
    "设置…": "Settings…",
    "环境自检…": "Environment Check…",

    # ---------------------------------------------------------------- 首屏
    "拖入视频，或打开文件 · 空格播放/暂停 · F 全屏 · T 生成 AI 字幕":
        "Drop a video, or open a file · Space to play/pause · F for fullscreen · T for AI subtitles",

    # ---------------------------------------------------------------- 设置：界面语言
    "界面语言": "Interface language",
    "跟随系统 / System": "System",
    "切换后立刻生效；进度与状态文字在下一次刷新时跟着换": "Applies immediately; progress and status text follow on their next refresh",

    # ---------------------------------------------------------------- 设置面板
    "界面": "Interface",
    "解码": "Decoding",
    "自动（优先硬解）": "Auto (hardware first)",
    "强制硬解（VideoToolbox）": "Force hardware (VideoToolbox)",
    "强制硬解（拷贝帧）": "Force hardware (copy frames)",
    "强制软解": "Force software",
    "全自动": "Fully automatic",
    "渲染：gpu（自动，推荐）": "Renderer: gpu (auto, recommended)",
    "渲染：gpu + macvk（Vulkan）": "Renderer: gpu + macvk (Vulkan)",
    "渲染：gpu-next": "Renderer: gpu-next",
    "渲染：null（不出画面，排查用）": "Renderer: null (no picture, for debugging)",
    "音频直通 AC3/DTS": "Audio passthrough AC3/DTS",
    "把 AC3/DTS 原样送给功放解码。改动后要重启播放器才生效":
        "Send AC3/DTS untouched to your receiver. Takes effect after restarting the player.",
    "画面画进本窗口": "Draw video inside this window",
    "关掉退回「独立 mpv 播放窗 + 贴合视频区」那条退路：\n"
    "显卡驱动不支持 GL 内嵌、或画面出问题时用。改动后要重启播放器":
        "Turn off to fall back to the separate mpv window fitted over the video area — "
        "use it when the GPU driver can't embed GL, or when the picture misbehaves. "
        "Takes effect after restarting the player.",
    "字幕外观": "Subtitle appearance",
    "字体": "Font",
    "字号": "Size",
    "双语字幕高度": "Bilingual height",
    "原生字幕位置(mpv)": "Native subtitle position (mpv)",
    "时间延迟": "Delay",
    "生成的字幕遇到长句会自动折成两行、必要时缩小字号；原生字幕太宽可以调小上面的字号。":
        "Generated subtitles wrap long lines into two and shrink the font when needed; "
        "if native subtitles are too wide, lower the size above.",
    "字幕文件": "Subtitle files",
    "字幕放在视频旁边": "Save subtitles next to the video",
    "勾选后每部片子的字幕落在视频所在目录下的同名子文件夹；视频目录写不进去时自动回退。":
        "When checked, each film's subtitles go into a same-named subfolder beside the video; "
        "falls back automatically if that folder isn't writable.",
    "选目录…": "Choose folder…",
    "输出目录": "Output folder",
    "打开字幕文件夹": "Open subtitle folder",
    "清理旧字幕…": "Clean up old subtitles…",
    "实时翻译": "Live translation",
    "预读 {} 秒": "Look ahead {}s",
    "整片（尽可能超前）": "Whole film (as far ahead as possible)",
    "预读越多，实时翻译越可能在字幕播到之前准备好；"
    "「整片」会从当前位置一路转到片尾，最不容易漏翻但更吃 CPU。"
    "跳转后也会从新位置重新预读。"
    "115 云端实时翻译走单流代理，默认预读上限 60 秒，"
    "可在「云盘」页调整到 120 秒。":
        "The further ahead, the more likely live subtitles are ready before they appear on screen. "
        "Whole film transcribes from here to the end — fewest misses, highest CPU cost. "
        "After a seek it starts over from the new position. "
        "Cloud live translation goes through the single-stream proxy with a 60s cap by default; "
        "the Cloud tab can raise it to 120s.",
    "默认语言偏好": "Default language preference",
    "默认音轨语言": "Default audio language",
    "默认字幕语言": "Default subtitle language",
    "打开片子时按这个顺序挑音轨/字幕；越靠上越优先。":
        "Order used to pick audio/subtitle tracks when a film opens; higher is preferred.",
    "诊断与修复": "Diagnostics & repair",
    "存储与清理": "Storage & cleanup",
    "模型（可以删）、字幕、缓存、日志、OpenList 数据的具体路径和占用"
    "都列在「存储」标签页，那里每个目录都能直接打开。":
        "Paths and sizes for models (deletable), subtitles, cache, logs and OpenList data "
        "are all listed in the Storage tab, where every folder can be opened directly.",
    "打开应用数据文件夹": "Open app data folder",
    "当前实际解码：{}": "Actual decoder: {}",
    "未开始": "not started",
    "跟随视频时没有统一根目录，不提供批量清理":
        "No single root folder when following the video, so batch cleanup is unavailable",
    "这一部 → {}\n{}": "This film → {}\n{}",
    "（目录/跟随设置从下一部片子生效）": "(folder/follow settings apply from the next film)",
    "打开片子后，字幕存到视频所在目录下的同名子文件夹。":
        "Once a film is open, subtitles go into a same-named subfolder beside the video.",
    "打开片子后按「视频名 + 说明后缀」各存一份：\n{}":
        "Once a film is open, three files are saved as \"video name + descriptive suffix\":\n{}",
    "字幕放哪儿": "Where to save subtitles",
    "AI 字幕": "AI Subtitles",
    "只看原文": "Original only",
    "双语": "Bilingual",
    "只看译文": "Translation only",
    "译文": "translated",

    # ---------------------------------------------------------------- 语言名（下拉、文件名共用）
    "中文": "Chinese",
    "简体中文": "Simplified Chinese",
    "繁體中文": "Traditional Chinese",
    "英文": "English",
    "日文": "Japanese",
    "韩文": "Korean",
    "法文": "French",
    "德文": "German",
    "西文": "Spanish",
    "俄文": "Russian",
    "意文": "Italian",
    "葡文": "Portuguese",
    "泰文": "Thai",
    "越文": "Vietnamese",
    "印尼文": "Indonesian",
    "阿拉伯文": "Arabic",
    "波兰文": "Polish",
    "荷兰文": "Dutch",
    "土耳其文": "Turkish",
    "乌克兰文": "Ukrainian",
    "未标注": "Unknown",
    "无语言": "None",

    # ---------------------------------------------------------------- 语言偏好 / 环境体检
    "添加": "Add",
    "移除": "Remove",
    "越靠上越优先；写进 alang/slang 时自动展开 zh/zho/chi/cmn 等变体。":
        "Higher means preferred; variants such as zh/zho/chi/cmn are expanded when writing alang/slang.",
    "环境自检": "Environment check",
    "重新检测": "Re-check",
    "检测中…": "Checking…",
    "一键安装缺失组件": "Install missing components",
    "取消": "Cancel",
    "开始安装…（首次可能要几分钟）": "Installing… (the first run can take a few minutes)",
    "播放引擎 libmpv": "Playback engine libmpv",
    "播放引擎 mpv": "Playback engine mpv",
    "画面呈现方式": "Video presentation",
    "画进本窗口（GL 内嵌）": "Drawn inside this window (GL embedded)",
    "内嵌进本窗口": "Embedded in this window",
    " · 独立播放窗，已贴合视频区": " · separate player window, fitted to the video area",
    " · 独立播放窗，未贴合": " · separate player window, not fitted",
    "音频抽取 ffmpeg": "Audio extraction ffmpeg",
    "流探测 ffprobe": "Stream probing ffprobe",
    "未找到": "not found",
    "语音转字幕 whisper.cpp": "Speech-to-subtitles whisper.cpp",
    "转写模型": "Transcription models",
    "未下载（AI 面板里可下载）": "Not downloaded (available in the AI panel)",
    "llama-server 就绪 · {}": "llama-server ready · {}",
    "已装 llama.cpp，缺模型 {}（AI 面板里点下载）":
        "llama.cpp installed, model {} missing (download it in the AI panel)",
    "未安装：离线翻译不可用（brew install llama.cpp）":
        "Not installed: offline translation unavailable (brew install llama.cpp)",
    "本地翻译 llama.cpp": "Local translation llama.cpp",
    "翻译密钥": "Translation API key",
    "已配置": "Configured",
    "未配置：翻译不可用（设置里填，或环境变量 LUMINA_OPENAI_KEY）":
        "Not configured: translation unavailable (set it in Settings, or LUMINA_OPENAI_KEY)",
    "缺少必需组件：{}": "Missing required components: {}",
    "可以播放；这些功能待补：{}": "Playback works; these features are incomplete: {}",
    "环境完整，全部功能可用": "Environment complete, all features available",
    "没有需要安装的组件": "Nothing to install",
    "没找到 Homebrew，无法自动安装。先装 brew：https://brew.sh":
        "Homebrew not found, cannot install automatically. Install brew first: https://brew.sh",
    "启动 brew 失败：{}": "Failed to start brew: {}",
    "已取消安装": "Installation cancelled",
    "安装完成：{}": "Installed: {}",
    "brew 退出码 {}：{}": "brew exited with {}: {}",
    "AI/翻译进行中": "AI / translation running",

    # ---------------------------------------------------------------- 轨道面板
    "视频轨": "Video track",
    "音轨": "Audio track",
    "字幕轨": "Subtitle track",
    "显示方式": "Display mode",
    "加载字幕文件…": "Load subtitle file…",
    "移除外挂字幕": "Remove external subtitles",
    "重新读取轨道": "Reload tracks",
    "外挂": "external",
    "容器默认": "container default",
    "轨道 {}": "Track {}",
    "外挂 · {}": "external · {}",
    "（没有视频轨）": "(no video track)",
    "（没有音轨）": "(no audio track)",
    "关闭字幕": "Subtitles off",
    "AI/译文轨（本播放器生成）": "AI / translated track (generated by this player)",
    "「只看原文」直接用内嵌/外挂轨，保留原样式；双语与译文由播放器生成一条新轨（在 AI 栏里做）。":
        "\"Original only\" uses the embedded or external track as-is. Bilingual and translated "
        "modes generate a new track from the AI tab.",

    # ---------------------------------------------------------------- AI 面板
    "任务进度": "Task progress",
    "语音转字幕": "Speech to subtitles",
    "检查中…": "Checking…",
    "转字幕": "Transcribe",
    "字幕翻译": "Translate subtitles",
    "一键翻译": "One-click translate",
    "实时翻译": "Live translation",
    "停止": "Stop",
    "只把音频转成字幕，不翻译": "Transcribe the audio only, no translation",
    "翻译当前选中的字幕（原生或音频生成的都行）":
        "Translate the selected subtitle track (built-in, external or generated)",
    "转写 + 翻译一次完成": "Transcribe and translate in one go",
    "边播边转写并持续翻译；只用于本场播放，不保存字幕文件":
        "Transcribe and translate while playing; this session only, no subtitle files are saved",
    "转字幕只出原文 · 字幕翻译翻当前选中的轨 · 一键翻译＝转写+翻译 · 实时翻译＝边播边转边翻（不保存字幕文件）。":
        "Transcribe writes the original only · Translate subtitles translates the selected track · "
        "One-click = transcribe + translate · Live = transcribe and translate while playing "
        "(no subtitle files saved).",
    "识别语言": "Recognition language",
    "自动检测（推荐）": "Auto-detect (recommended)",
    "只对当前这部片生效；实时翻译时改完下一段就生效":
        "Applies to this film only; during live translation it takes effect on the next segment",
    "自动模式会先用语音检测校正音轨语言标记；识别不准就在上面指定本片语言。转写用 whisper.cpp 的 Metal 加速。":
        "Auto mode corrects the audio track's language tag with speech detection first; "
        "if recognition is off, set this film's language above. Transcription uses whisper.cpp's Metal acceleration.",
    "要删模型腾空间：侧栏「存储」栏里每个模型的体积都列出来了，可以直接删。":
        "To free space, the Storage tab lists every model with its size and lets you delete it.",
    "下载转写模型": "Download transcription model",
    "下载整片模型": "Download full-film model",
    "下载实时模型": "Download live model",
    "翻译引擎": "Translation engine",
    "目标语言": "Target language",
    "翻译上下文": "Translation context",
    "在线 · OpenAI 兼容接口": "Online · OpenAI-compatible API",
    "在线 · DeepL": "Online · DeepL",
    "本地 · llama.cpp + GGUF（离线）": "Local · llama.cpp + GGUF (offline)",
    "本地 · Argos（离线模型）": "Local · Argos (offline model)",
    "关闭（最快）": "Off (fastest)",
    "3 条": "3 lines",
    "6 条（推荐）": "6 lines (recommended)",
    "12 条": "12 lines",
    "{} 条": "{} lines",
    "保留前几句原文/译文给 LLM 当上下文，人称/术语更连贯但会慢一点；只对在线 OpenAI 和本地 GGUF 有效。":
        "Gives the LLM the previous source/target lines as context — more consistent names and terms, "
        "a bit slower. Only applies to online OpenAI and local GGUF.",
    "本地 GGUF 模型": "Local GGUF models",
    "一键 / 整片翻译（大模型，慢但更稳）": "One-click / full-film translation (big model, slower but steadier)",
    "实时翻译（小模型，快）": "Live translation (small model, fast)",
    "两个模型分开：实时用小模型追速度，一键/整片用大模型慢慢翻；换模型时选未下载的型号，按钮会变成下载。":
        "Two separate models: a small one for live speed, a big one for one-click / full films. "
        "Pick a model that isn't downloaded yet and the button turns into a download.",
    "接口地址": "Base URL",
    "在线模型": "Online model",
    "每批行数": "Lines per batch",
    "{} 行 / 批": "{} lines / batch",
    "密钥只进 macOS 钥匙串": "The key is stored only in the macOS Keychain",
    "保存密钥": "Save key",
    "测试翻译": "Test translation",
    "清空缓存": "Clear cache",
    "已停止": "Stopped",
    "准备中…": "Preparing…",
    "任务进行中：{}": "Running: {}",
    "上次任务：已停止": "Last job: stopped",
    "上次任务：{} · 已结束{}": "Last job: {} · finished{}",
    "上次任务：已结束": "Last job: finished",
    "下载中… {}%": "Downloading… {}%",
    "正在下载 {}…": "Downloading {}…",
    "下载失败：{}": "Download failed: {}",
    "{} 下载完成": "{} downloaded",
    "先在下拉里选一个未下载的模型": "Pick an undownloaded model in the dropdown first",
    "先选一个 GGUF 模型": "Pick a GGUF model first",
    "另一个模型正在下载，等它结束": "Another model is downloading, wait for it to finish",
    "打开视频后按本片选择；自动模式会用语音检测校正音轨标记":
        "Applies per film once a video is open; auto mode corrects the audio tag with speech detection",
    "本片指定：{}": "This film: {}",
    "（覆盖音轨的{}标记）": " (overrides the {}) tag",
    "自动：音轨标记为{}；开始转写时会用语音检测校正":
        "Auto: track tagged {}; speech detection corrects it when transcription starts",
    "自动：开始转写时用语音检测判断语言":
        "Auto: the language is detected when transcription starts",
    "本机已有：{}": "On this machine: {}",
    "本机还没有转写模型，点下面下载": "No transcription model yet — download one below",
    "检测 whisper 中…": "Checking whisper…",
    "没装 llama.cpp：体检里一键装，或 brew install llama.cpp":
        "llama.cpp not installed: install it from the environment check, or brew install llama.cpp",
    "整片 {}llama-server 已就绪 · {}": "Full-film {}llama-server ready · {}",
    "实时 {}llama-server 已就绪 · {}": "Live {}llama-server ready · {}",
    "离线可用": "offline ready",
    "缺模型，点下面下载": "model missing, download below",
    "当前转写模型已就绪": "Transcription model ready",
    "下载选中的转写模型": "Download selected model",
    "当前整片模型已就绪": "Full-film model ready",
    "当前实时模型已就绪": "Live model ready",
    "{} 已下载；换模型请在下拉里选未下载的型号":
        "{} is downloaded; to switch, pick an undownloaded model in the dropdown",
    "下载{}": "Download {}",
    "已切换引擎，点「测试翻译」验证": "Engine switched — use \"Test translation\" to verify",
    "先输入密钥": "Enter a key first",
    "密钥已存入钥匙串（{}）": "Key saved to the Keychain ({})",
    "翻译缓存已清空": "Translation cache cleared",
    "测试中…（本地模型第一次要等它加载，可能几十秒）":
        "Testing… (the first local-model run has to load it, which can take a while)",
    "先在轨道栏选一条要翻译的字幕轨": "Pick a subtitle track in the Tracks tab first",
    "这条字幕已经不是内嵌/外挂轨了": "That subtitle track is no longer available",

    # ---------------------------------------------------------------- 播放列表 / 字幕库
    "播放列表": "Playlist",
    "加文件": "Add files",
    "加文件夹": "Add folder",
    "清空": "Clear",
    "只从播放列表移除，不会删除文件": "Removes from the playlist only — files stay on disk",
    "移除这一项": "Remove this item",
    "清空播放列表": "Clear playlist",
    "最近打开": "Recent",
    "添加媒体": "Add media",
    "添加文件夹": "Add folder",
    "清理生成的字幕": "Clean up generated subtitles",
    "删除选中": "Delete selected",
    "关闭": "Close",
    "确认删除": "Confirm deletion",
    "确定删除": "Delete",
    "确定": "OK",
    "知道了": "Got it",
    "{}　　{} 个文件 · {} · {}": "{}    {} files · {} · {}",
    "删掉这 {} 个文件夹里的字幕？视频本身不动，删了可以重新转写。\n\n{}":
        "Delete subtitles in these {} folders? The videos themselves are untouched, and "
        "subtitles can be regenerated.\n\n{}",

    # ---------------------------------------------------------------- 网盘浏览 / 添加存储
    "上一级": "Up",
    "刷新": "Refresh",
    "读取目录…": "Reading folder…",
    "{} · {} 个目录 · {} 个视频": "{} · {} folders · {} videos",
    "读取目录失败：{}（可在「OpenList 服务」里启动网关）":
        "Failed to read folder: {} (start the gateway under \"OpenList service\")",
    "添加存储…": "Add storage…",
    "网页管理": "Web admin",
    "双击目录进入、双击视频播放。任意 OpenList 存储都能这样用；"
    "要加新网盘点「添加存储」，OAuth 类网盘用「网页管理」登录。":
        "Double-click a folder to enter it, a video to play it. This works for any OpenList "
        "storage; add more with \"Add storage\", and sign in OAuth drives via \"Web admin\".",
    "网关没在运行，先启动 OpenList 服务": "The gateway isn't running — start the OpenList service first",
    "添加网盘存储": "Add cloud storage",
    "字段来自网关的驱动定义；OAuth 类网盘建议用「网页管理」登录后再回来。":
        "Fields come from the gateway's driver definitions; for OAuth drives, sign in via "
        "\"Web admin\" first and come back.",
    "驱动": "Driver",
    "挂载路径": "Mount path",
    "挂载路径，例如 /ali": "Mount path, e.g. /ali",
    "备注": "Remark",
    "添加": "Add",
    "启用": "Enable",
    "添加失败": "Failed to add storage",
    "115 文件": "115 files",
    "OpenList 服务": "OpenList service",
    "网盘文件": "Files",
    "网关服务": "Service",
}

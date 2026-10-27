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

    # ---------------------------------------------------------------- 网关管理页
    "OpenList 网关": "OpenList gateway",
    "读取状态…": "Reading status…",
    "随软件启动，关窗自动停止": "Start with the app, stop on quit",
    "勾选：打开 Lumina 就把网关带起来，退出时一起收掉，网盘随时可用。\n"
    "取消：不随软件启动，只用手动的「启动/停止」；外部自己跑的网关退出时不动。":
        "Checked: opening Lumina brings the gateway up and quitting stops it — the cloud is "
        "always ready.\nUnchecked: no autostart; use Start/Stop here, and a gateway you run "
        "yourself is left alone on quit.",
    "启动": "Start",
    "重启": "Restart",
    "管理页": "Admin page",
    "数据目录": "Data folder",
    "日志": "Log",
    "OpenList 只监听本机，是软件的内置网关。停止/重启会中断正在进行的网盘播放、字幕与实时翻译；有任务时请先停止任务。":
        "OpenList listens on loopback only and ships with the app. Stopping or restarting "
        "interrupts cloud playback, subtitles and live translation; stop running jobs first.",
    "115 账户": "115 account",
    "未读取": "Not loaded",
    "扫码登录 115": "Scan QR to sign in to 115",
    "二维码来自 115 官方接口；扫码后需要重新载入 115 存储。":
        "The QR code comes from 115's official API; reload the 115 storage after scanning.",
    "115 存储": "115 storage",
    "挂载路径（建议保持 /115）": "Mount path (keep /115)",
    "本地代理（拖动最稳）": "Local proxy (steadiest seeking)",
    "Web 代理": "Web proxy",
    "302 跳转": "302 redirect",
    "WebDAV 策略": "WebDAV policy",
    "启用 Web 代理（云端实时翻译依赖）":
        "Enable web proxy (required for cloud live translation)",
    "允许 Range 代理": "Allow Range proxying",
    "直链缓存时间": "Direct-link cache",
    " 秒": " s",
    "保存存储设置": "Save storage settings",
    "云端实时翻译的单流代理需要 Web 代理；WebDAV 播放建议用本地代理。改存储会重新载入 115，正在播放时可能短暂中断。":
        "Cloud live translation needs the web proxy; WebDAV playback prefers the local proxy. "
        "Saving reloads 115 and may briefly interrupt playback.",
    "云端播放缓存": "Cloud playback cache",
    "内存缓存 {} MB": "Memory cache {} MB",
    "单流代理内存缓存": "Single-stream cache",
    "云端预读上限 {} 秒": "Cloud read-ahead cap {}s",
    "实时翻译预读上限": "Live read-ahead cap",
    "缓存只占内存，不落视频盘。扩大缓存会让跳读和预读更稳，但会占用更多内存；预读上限下一次实时任务生效。":
        "The cache lives in memory only, never on disk. A larger cache makes seeking and "
        "read-ahead steadier at the cost of RAM; the cap applies to the next live job.",
    "读取 OpenList 状态…\n网关程序：{}": "Reading OpenList status…\nGateway: {}",
    "状态读取失败：{}": "Status read failed: {}",
    "网关程序：{}": "Gateway: {}",
    "本进程启动": "started by this app",
    "外部进程 PID {}": "external process PID {}",
    "未知进程": "unknown process",
    "运行中 · {} · 端口 {} · {}": "Running · {} · port {} · {}",
    "读取 115 存储配置…": "Reading 115 storage…",
    "OpenList 未运行，先启动": "OpenList isn't running — start it first",
    "读取 115 存储失败：{}": "Failed to read 115 storage: {}",
    "已配置 115 Cookie；扫码可更新登录": "115 cookie configured; scan to refresh login",
    "未配置 115 Cookie，请扫码登录": "No 115 cookie yet — scan the QR to sign in",
    "有转写/翻译任务在跑，先在 AI 栏停止任务再操作。":
        "A transcription/translation job is running — stop it in the AI tab first.",
    "当前正在播放网盘视频，操作会中断播放/实时翻译。":
        "A cloud video is playing; this will interrupt playback and live translation.",
    "继续操作": "Continue",
    "启动 OpenList": "Start OpenList",
    "有任务在跑，先停止任务。": "A job is running — stop it first.",
    "启动 OpenList…": "Starting OpenList…",
    "OpenList 已启动": "OpenList started",
    "启动失败：{}": "Start failed: {}",
    "停止 OpenList": "Stop OpenList",
    "停止后网盘播放、字幕和实时翻译都会中断。":
        "Cloud playback, subtitles and live translation will be interrupted.",
    "确定停止": "Stop",
    "停止 OpenList…": "Stopping OpenList…",
    "OpenList 已停止": "OpenList stopped",
    "停止失败：{}": "Stop failed: {}",
    "重启 OpenList": "Restart OpenList",
    "会中断当前播放/任务，重启后需要重新载入网盘。":
        "This interrupts playback and running jobs; the cloud has to reload afterwards.",
    "确定重启": "Restart",
    "重启 OpenList…": "Restarting OpenList…",
    "OpenList 已重启": "OpenList restarted",
    "重启失败：{}": "Restart failed: {}",
    "打不开目录：{}": "Can't open the folder: {}",
    "还没有 OpenList 日志": "No OpenList log yet",
    "打不开日志：{}": "Can't open the log: {}",
    "保存 115 存储": "Save 115 storage",
    "关闭 Web 代理": "Disable web proxy",
    "云端实时翻译的单流代理依赖 Web 代理；关闭后实时翻译可能不可用。":
        "Cloud live translation's single-stream proxy needs the web proxy; disabling it may "
        "break live translation.",
    "仍要关闭": "Disable anyway",
    "改变 WebDAV 策略": "Change WebDAV policy",
    "非「本地代理」策略可能影响网盘拖动和字幕读取。":
        "A non-local policy may hurt cloud seeking and subtitle reads.",
    "仍要保存": "Save anyway",
    "保存 115 存储设置…": "Saving 115 storage…",
    "115 存储设置已保存": "115 storage settings saved",
    "保存存储失败：{}": "Failed to save storage: {}",
    "先启动 OpenList": "Start OpenList first",
    "登录 115": "Sign in to 115",
    "正在获取二维码…": "Fetching QR code…",
    "状态查询失败，重试…（{}）": "Status check failed, retrying… ({})",
    "等待扫码…": "Waiting for scan…",
    "已扫码，请在手机上确认": "Scanned — confirm on your phone",
    "115 登录失败：{}": "115 sign-in failed: {}",
    "115 登录成功，存储已刷新": "Signed in to 115; storage refreshed",
    "二维码已过期，请重新获取": "QR expired — fetch a new one",
    "已取消扫码": "Scan cancelled",
    "扫码状态 {}": "Scan status {}",
    "二维码等待超时，请重新获取": "Timed out waiting for the scan — fetch a new one",
    "二维码图片解析失败": "Couldn't decode the QR image",
    "等待扫码…（二维码几分钟内有效）": "Waiting for scan… (the QR stays valid for a few minutes)",
    "已配置 115 Cookie": "115 cookie configured",
    "网关将随软件启动、关窗一起停止": "The gateway will start and stop with the app",
    "已改为手动：启动/停止由这一页负责，退出软件不动外部进程":
        "Manual now: start/stop from this page; quitting leaves external processes alone",
    "云端缓存改为 {} MB（下一次实时翻译生效）":
        "Cloud cache set to {} MB (applies to the next live job)",
    "云端预读上限改为 {} 秒": "Cloud read-ahead cap set to {}s",

    # ---------------------------------------------------------------- 存储台账
    "占用概览": "Overview",
    "统计中…": "Measuring…",
    "删除与清理只动上面这些路径，绝不碰视频本身；有任务在跑时会拒绝清理。":
        "Delete and cleanup only touch the paths above, never your videos; they refuse while "
        "a job is running.",
    "重新统计": "Re-measure",
    "一键清理临时与缓存…": "Clean temp & cache…",
    "打开": "Open",
    "清理": "Clean",
    "删除": "Delete",
    "字幕库…": "Library…",
    "模型（可删除）": "Models (deletable)",
    "转写模型目录": "Transcription models",
    "翻译模型目录": "Translation models",
    "没下完的断点": "Unfinished downloads",
    "网关程序：{} · {}\n{}": "Gateway: {} · {}\n{}",
    "应用数据目录共 {}，其中模型 {}、缓存/临时/构建 {}":
        "App data totals {}, of which models {} and cache/temp/build {}",
    "有转写/翻译任务在跑，先在 AI 栏停止任务。":
        "A transcription/translation job is running — stop it in the AI tab first.",
    "删除模型": "Delete model",
    "\n它正在被使用：{}": "\nIn use by: {}",
    "要从磁盘删除：\n{}\n{}{}\n\n删除后要用就得重新下载。":
        "Delete from disk:\n{}\n{}{}\n\nYou'll have to download it again to use it.",
    "删除失败": "Delete failed",
    "删不掉 {}：{}": "Can't delete {}: {}",
    "已删除断点 {}（{}）": "Removed unfinished download {} ({})",
    "删除断点": "Delete partial download",
    "一键清理": "Clean everything",
    "确定清理": "Clean",
    "「{}」没有可清理的文件": "\"{}\" has nothing to clean",
    "将清理「{}」：{} 个文件 · 约 {}\n{}": "Clean \"{}\": {} files · about {}\n{}",
    "已清理「{}」{} 个文件 · 释放 {}": "Cleaned \"{}\": {} files · freed {}",
    "当前网关跑的就是数据目录里那一份旧副本；先用「网盘 → 网关服务」重启一次让它换到 App 内置的，再删。":
        "The running gateway is the old copy in the data folder; restart it from Cloud → "
        "Service so it switches to the bundled one, then delete.",
    "临时文件与缓存都是干净的": "Temp files and cache are already clean",
    "将清理临时音频、实时字幕、转写标记、翻译缓存、语言台账、下载断点与日志：\n{} 个文件 · 约 {}\n"
    "模型和已生成的字幕不会动。":
        "Clean temp audio, live subtitles, transcription markers, translation cache, language "
        "ledger, unfinished downloads and logs:\n{} files · about {}\nModels and generated "
        "subtitles are untouched.",
    "已清理 {} 个文件 · 释放 {}": "Cleaned {} files · freed {}",

    # 台账分类 / 条目（来自 storage.sections）
    "软件数据根": "App data",
    "字幕产出": "Subtitles",
    "缓存与标记": "Cache & markers",
    "网关与日志": "Gateway & logs",
    "构建与分发": "Build & distribution",
    "配置文件": "Config file",
    "模型、缓存、日志都在这儿": "models, cache and logs live here",
    "软件自带的 Python 环境，别手删": "bundled Python environment — don't delete by hand",
    "应用数据目录": "App data folder",
    "内置运行环境": "Bundled runtime",
    "字幕输出目录": "Subtitle output",
    "「字幕放在视频旁边」时这一部会落在视频目录":
        "with \"next to the video\" this film lands beside the video",
    "转写临时音频": "Transcription temp audio",
    "whisper 用的 16k 切片，转写完就该删":
        "16k slices for whisper — delete once transcription finishes",
    "实时字幕临时文件": "Live subtitle temp files",
    "实时翻译边播边写的临时 ass，退出即清":
        "temp .ass written during live translation, cleared on exit",
    "转写标记": "Transcription markers",
    "记录每部片已经转到哪；删掉后一键翻译会整个重转":
        "records how far each film got; deleting forces a full re-transcribe",
    "翻译缓存": "Translation cache",
    "翻过的句子，删掉后同样的句子要重翻":
        "translated sentences; deleting means re-translating them",
    "每片识别语言": "Per-film language",
    "每部片单独指定的识别语言": "recognition language picked per film",
    "下载断点": "Unfinished downloads",
    "没下完的 .part 文件": ".part files left by interrupted downloads",
    "应用日志": "App log",
    "播放器日志": "Player log",
    "mpv 每次启动覆盖": "overwritten on every mpv start",
    "OpenList 数据目录": "OpenList data folder",
    "115 登录状态、挂载配置": "115 login state and mount config",
    "OpenList 日志": "OpenList log",
    "网关构建缓存": "Gateway build cache",
    "编译网关用的 Go 工具链、源码与 module 缓存；出过包就可以删，下次构建会重新拉":
        "Go toolchain, source and module cache used to build the gateway; safe to delete once "
        "packaged, the next build re-fetches",
    "网关程序（App 内置）": "Gateway binary (bundled)",
    "编译进 Lumina.app 的那一份，随软件一起分发":
        "the copy compiled into Lumina.app, shipped with the app",
    "可分发包": "Distributable",
    "打好的 zip（发给别的机器用），删掉不影响本机运行":
        "the built zip for other machines; deleting it doesn't affect local use",
    "可分发打包运行时": "Portable build runtime",
    "make_portable.sh 建在数据目录里的独立 Python 环境；删了下次打可分发包会重建":
        "standalone Python env that make_portable.sh creates here; recreated on the next "
        "portable build",
    "转写在用": "in use by transcription",
    "实时翻译在用": "in use by live translation",
    "整片/一键翻译在用": "in use by full-film translation",

    # ---------------------------------------------------------------- 管线提示（进度 / 错误）
    "视频目录写不进去，这一部的字幕落到 {}":
        "The video's folder isn't writable — this film's subtitles go to {}",
    "输出目录「{}」用不了，这一部落到 {}":
        "Output folder \"{}\" isn't usable — this film falls back to {}",
    "（旧版，未确认整片完整；点「一键翻译」会重新转写）":
        " (older version, whole-film completeness unconfirmed; one-click translate will redo it)",
    "已自动载入上次生成的字幕：{} 句{}": "Loaded the subtitles generated last time: {} lines{}",
    "字幕轨挂载失败：mpv 没有接受生成的字幕文件":
        "Couldn't attach the subtitle track: mpv rejected the generated subtitle file",
    "刷新字幕轨失败：{}": "Couldn't refresh the subtitle track: {}",
    "正在取回 {} 字幕原文…": "Fetching the {} subtitle text…",
    "没有可用的字幕来源：先选一条字幕，或在 AI 面板生成字幕":
        "No subtitle source available — pick a subtitle track, or generate subtitles in the AI tab",
    "翻译字幕": "Translate subtitles",
    "翻译（{} → {}）": "Translating ({} → {})",
    "翻译完成": "Translation done",
    "翻译完成 · {} 句原样未翻": "Translation done · {} lines left untranslated",
    "有 {} 句模型原样返回、没有翻成{}；建议在 AI 栏换 3B 或更大的翻译模型":
        "{} lines came back unchanged and weren't translated into {}; try a 3B or larger "
        "translation model in the AI tab",
    "翻译完成：{} 条{} · 缓存命中 {}/{}":
        "Translation done: {} lines{} · cache hits {}/{}",
    " · {} 句原样未翻": " · {} lines untranslated",
    "还没选定要翻译的字幕轨": "No subtitle track has been chosen to translate yet",
    "整片任务正在跑，识别语言更改会在下次任务生效":
        "A whole-file job is running; the language change applies to the next job",
    "已按本片指定语言{}转写（音轨标记是{}）":
        "Transcribed as {} because this film says so (track tagged {})",
    "音轨标记是{}，语音检测为{}，已按{}转写":
        "Track tagged {}, speech detection says {} — transcribing as {}",
    "没有可用的翻译接口（{}）：本轮只转写原文":
        "No usable translation backend ({}): this run only transcribes",
    "翻译": "Translation",
    "转写完成 · 翻译有 {} 句未完成": "Transcription done · {} lines left untranslated",
    "转写 + 翻译完成": "Transcription + translation done",
    "转写完成（未翻译）": "Transcription done (not translated)",
    " + 翻译": " + translation",
    "（未翻译）": " (not translated)",
    "，其中 {} 句没有翻成{}": ", of which {} lines weren't translated into {}",
    "AI 字幕就绪{}：{} 句 · 语言 {}{}": "AI subtitles ready{}: {} lines · language {}{}",
    "未知": "unknown",
    "还没有可翻译的生成字幕，先点「转字幕」":
        "No generated subtitles to translate yet — click \"Transcribe\" first",
    "这份 AI 转写是旧版/不完整（片头可能缺字幕），请点「一键翻译」重新转写后再翻":
        "This AI transcript is old or incomplete (the opening may be missing); run one-click "
        "translate to redo it before translating",
    "已有字幕 {} 句，直接翻译（不重新转写）":
        "{} lines already available — translating directly without re-transcribing",
    "自动": "auto",
    "识别语言已切换为{}，下一段实时字幕生效":
        "Recognition language switched to {} — applies to the next live segment",
    "实时翻译：检测到回跳，从当前位置重新预读…":
        "Live translation: jumped backwards, reading ahead from here…",
    "实时翻译：检测到跳转，从当前位置重新预读…":
        "Live translation: jumped, reading ahead from here…",
    "实时字幕：已到片尾，停止转写": "Live subtitles: reached the end, stopping",
    "云端单流 · 预读 {} 秒": "Cloud single stream · look ahead {}s",
    "实时模式已开启：{}，字幕/翻译尽量做在播放头前面":
        "Live mode on: {} — subtitles and translation stay ahead of the play head",
    "实时翻译 · 预读 {}s · 已转 {} 句 · 已翻 {} 句":
        "Live translation · look ahead {}s · {} lines transcribed · {} translated",
    "实时翻译：{}，{}s 后从当前位置重试（{}/{}）…":
        "Live translation: {}, retrying from here in {}s ({}/{})…",
    "音频流被掐断": "audio stream cut off",
    "读取失败": "read failed",
    "实时翻译停了：云端单流一直在被掐断（两条读流在抢同一条上游）。"
    "把「网盘 → 网关服务」里的云端预读上限调到 30 秒、"
    "或把内存缓存调到 256 MB 再试；长片也可以直接用一键翻译。":
        "Live translation stopped: the cloud single stream keeps getting cut (two readers "
        "competing for one upstream). Lower the cloud read-ahead cap to 30s or raise the "
        "memory cache to 256 MB under Cloud → Service and retry; for long films, use "
        "one-click translation instead.",
    "实时转写": "Live transcription",

    # ---------------------------------------------------------------- 转写 / 网关 / 存储 / 翻译 / 引擎错误
    "找不到 {}，请先安装：brew install whisper-cpp ffmpeg":
        "{} not found — install it first: brew install whisper-cpp ffmpeg",
    "缺少模型 {}，在设置里下载": "Model {} missing — download it in Settings",
    "{} · {} · Metal 加速": "{} · {} · Metal accelerated",
    "抽取音频失败：{}": "Audio extraction failed: {}",
    "音频流读取被掐断：{}": "Audio stream cut off: {}",
    "这条素材里没有可转写的音轨": "This file has no transcribable audio track",
    "这条素材里没有可转写的音轨：{}": "This file has no transcribable audio track: {}",
    "没有可用的 whisper 模型": "No whisper model available",
    "抽取音频…": "Extracting audio…",
    "转写中…": "Transcribing…",
    "{} · 抽取音频…": "{} · extracting audio…",
    "{} · 转写中…": "{} · transcribing…",
    "{} · 重试…": "{} · retrying…",
    "whisper 提前退出（rc={}，{}），只生成 {} 句，字幕不完整":
        "whisper exited early (rc={}, {}), produced only {} lines — the subtitles are incomplete",
    "whisper 只处理到 {} 就退出了（{}），只生成 {} 句，字幕不完整":
        "whisper stopped at {} ({}), produced only {} lines — the subtitles are incomplete",

    "App 里没有自带的 openlist，PATH 上也没找到；跑一次 scripts/build_openlist.sh 再重装 App":
        "No bundled openlist in the app and none on PATH; run scripts/build_openlist.sh and "
        "reinstall the app",
    "找不到": "not found",
    "App 内置": "bundled with the app",
    "数据目录里的旧副本": "old copy in the data folder",
    "设置里指定的路径": "path set in Settings",
    "系统 PATH": "system PATH",
    "软件正在退出，不再带起网关": "The app is quitting — not starting the gateway",
    "OpenList 启动失败（退出码 {}）": "OpenList failed to start (exit code {})",
    "OpenList 启动超时（看 cloud/openlist/server.out）":
        "OpenList start timed out (see cloud/openlist/server.out)",
    "读取 OpenList token 失败：{}": "Failed to read the OpenList token: {}",
    "OpenList 还没有管理员 token：先完成一次初始化/登录":
        "OpenList has no admin token yet — complete its first-time setup/login",
    "读取目录失败：{}": "Failed to read folder: {}",
    "读取目录失败：HTTP {}": "Failed to read folder: HTTP {}",
    "OpenList 管理接口失败：{}": "OpenList admin API failed: {}",
    "OpenList 管理接口 HTTP {}": "OpenList admin API returned HTTP {}",
    "挂载路径不能为空": "Mount path can't be empty",
    "OpenList 里没有 115 Cloud 存储": "OpenList has no 115 Cloud storage",
    "扫码结果里没有 cookie": "The scan result contains no cookie",
    "115 存储配置不是合法 JSON：{}": "115 storage config isn't valid JSON: {}",
    "115 二维码接口失败：HTTP {} {}": "115 QR API failed: HTTP {} {}",
    "115 二维码图片失败：HTTP {}": "115 QR image failed: HTTP {}",
    "连接 115 二维码接口失败：{}": "Couldn't reach the 115 QR API: {}",
    "查询 115 扫码状态失败：{}": "Failed to poll the 115 scan status: {}",
    "完成 115 登录失败：{}": "Failed to finish the 115 sign-in: {}",
    "115 登录没有返回 cookie：{}": "115 sign-in returned no cookie: {}",
    "OpenList 没有返回下载地址（raw_url 为空）": "OpenList returned no download URL (raw_url empty)",

    "不认识模型类型：{}": "Unknown model kind: {}",
    "只能删除模型目录里的文件": "Only files inside the model folders can be deleted",
    "{} 不是可删除的模型文件": "{} isn't a deletable model file",
    "文件已经不存在：{}": "File no longer exists: {}",
    "删除失败：{}": "Delete failed: {}",
    "已删除 {}（{}）": "Deleted {} ({})",
    "不认识清理项：{}": "Unknown cleanup target: {}",

    "断点已失效，重新下载": "Partial download is stale — downloading again",
    "断点比文件还大，重新下载": "Partial download is larger than the file — downloading again",
    "下载长度不对（{} / {} MB），保留断点重试":
        "Wrong download size ({} / {} MB) — keeping the partial file and retrying",
    "下载完成": "Download complete",
    "没有 OpenAI 兼容接口的密钥（设置里填，或用环境变量 LUMINA_OPENAI_KEY）":
        "No OpenAI-compatible API key (set it in Settings, or use LUMINA_OPENAI_KEY)",
    "接口没有返回 choices: {}": "The API returned no choices: {}",
    "没有 DeepL 密钥（设置里填，或用环境变量 LUMINA_DEEPL_KEY）":
        "No DeepL key (set it in Settings, or use LUMINA_DEEPL_KEY)",
    "DeepL 返回 {} 条，期望 {} 条": "DeepL returned {} lines, expected {}",
    "Argos 没有 {}→{} 语言包": "Argos has no {}→{} language pack",
    "不认识的语言模型：{}": "Unknown language model: {}",
    "llama-server 启动失败（退出码 {}）：模型文件可能损坏":
        "llama-server failed to start (exit code {}): the model file may be corrupt",
    "llama-server 启动超时（第一次加载模型要几十秒，再试一次看看）":
        "llama-server start timed out (the first load takes a while — try once more)",
    "没有 llama.cpp：brew install llama.cpp": "llama.cpp not installed: brew install llama.cpp",
    "本地模型没下载：{}（AI 面板里点下载）":
        "Local model not downloaded: {} (download it in the AI tab)",
    "（未选择）": "(none selected)",
    "本地引擎未安装：uv sync --extra offline  (装 argostranslate)":
        "Local engine not installed: uv sync --extra offline (installs argostranslate)",
    "返回了空结果": "returned an empty result",

    "没找到 mpv 可执行文件，先装一个：brew install mpv":
        "mpv executable not found — install it: brew install mpv",
    "这个 mpv 构建没有 --input-ipc-server，无法控制；请确认 brew install mpv 完成":
        "This mpv build has no --input-ipc-server so it can't be controlled; make sure "
        "brew install mpv finished",
    "mpv 还没连上": "mpv isn't connected yet",
    "mpv 还没启动": "mpv hasn't started yet",
    "mpv 未就绪": "mpv isn't ready",
    "mpv 超时未响应：{}": "mpv timed out on {}",
    "{} 失败：{}": "{} failed: {}",
    "找不到 libmpv.2.dylib，先 brew install mpv":
        "libmpv.2.dylib not found — brew install mpv",
    "mpv 未启动": "mpv isn't running",
    "{} 失败 rc={}": "{} failed rc={}",
    "媒体文件 ({patterns});;所有文件 (*)": "Media files ({patterns});;All files (*)",
    "写入钥匙串失败：{}": "Failed to write to the Keychain: {}",

    "还没拿到文件大小": "File size not known yet",
    "上游返回 HTTP {}（探测文件大小）": "Upstream returned HTTP {} (probing the file size)",
    "上游没有给出文件大小": "Upstream didn't provide a file size",
    "探测上游失败：{}": "Probing upstream failed: {}",
    "探测上游失败：没有拿到文件大小": "Probing upstream failed: no file size",
    "上游返回 HTTP {}（预读{}）": "Upstream returned HTTP {} (reading ahead {})",
    "上游忽略了 Range（预读{}）": "Upstream ignored Range (reading ahead {})",
    "预读{}失败：{}": "Reading ahead {} failed: {}",
    "预读{}为空": "Read-ahead for {} came back empty",
    "上游返回 HTTP {}": "Upstream returned HTTP {}",
    "上游 Range 起点不符：{}": "Upstream Range start mismatch: {}",
    "上游忽略了 Range，无法从中间续读": "Upstream ignored Range — can't resume mid-file",
    "上游提前结束": "Upstream ended early",
    "片头": "opening",
    "片尾索引": "tail index",

    # ---------------------------------------------------------------- 主窗口 / OSD
    "（网盘不可用，本地播放不受影响）": " (cloud unavailable — local playback is unaffected)",
    "打开视频": "Open video",
    "打开文件夹": "Open folder",
    "{} 里没有可播放的文件": "No playable files in {}",
    "设置页底部＝环境自检与一键修复": "The environment check and one-click fixes are at the bottom of Settings",
    "无": "none",
    "关于 Lumina": "About Lumina",
    "解码引擎：mpv（VideoToolbox 硬解 / 软解可切）":
        "Decoder: mpv (VideoToolbox hardware / software switchable)",
    "语音转字幕：whisper.cpp（Metal）": "Speech to subtitles: whisper.cpp (Metal)",
    "引擎状态：{}": "Engine: {}",
    "运行中": "running",
    "未启动": "not running",
    "转写模型：{}": "Transcription models: {}",
    "模型目录：{}": "Model folder: {}",
    "配置：{}": "Config: {}",
    "日志：{}": "Log: {}",
    "添加字幕": "Add subtitle file",
    "字幕文件 (*.srt *.ass *.ssa *.vtt *.sub)": "Subtitle files (*.srt *.ass *.ssa *.vtt *.sub)",
    "已加载字幕 {}": "Loaded subtitle {}",
    "引擎暂时起不来，正在后台自动重试": "The engine isn't starting — retrying in the background",
    "：{}": ": {}",
    "播放结束": "Playback finished",
    "解：{}": "dec: {}",
    "这项要重启播放器才生效": "This takes effect after restarting the player",
    "文件不存在：{}": "File doesn't exist: {}",
    "引擎启动中，稍等…": "Starting the engine, one moment…",
    "mpv 退出了（{}），重启播放器再试": "mpv exited ({}); restart the player",
    "音量 {}": "Volume {}",
    "取消静音": "Unmute",
    "速度 {:.2f}×": "Speed {:.2f}×",
    "没有字幕轨（按 T 用 AI 生成）": "No subtitle track (press T to generate with AI)",
    "字幕：{}": "Subtitles: {}",
    "音轨：{}": "Audio: {}",
    "云端原生字幕轨翻译暂不支持；实时翻译/转字幕/一键翻译已可用":
        "Translating a cloud-native subtitle track isn't supported yet; live translation, "
        "transcribe and one-click translate are available",
    "已有任务在跑，等它结束或点停止": "A job is already running — wait or press Stop",
    "网盘转写：先释放播放流，完成/失败后自动回到原位置…":
        "Cloud transcription: releasing the playback stream first; returns to the same spot "
        "when done…",
    "网盘播放恢复失败：{}": "Couldn't resume cloud playback: {}",
    "已回到网盘播放位置": "Back at the cloud playback position",
    "云端实时翻译正在准备单流代理…": "Preparing the single-stream proxy for cloud live translation…",
    "云端实时翻译：继续使用单流代理…": "Live translation: reusing the single-stream proxy…",
    "云端实时翻译：准备单流代理（只缓存内存，不落视频盘）…":
        "Live translation: preparing the single-stream proxy (memory cache only, nothing on disk)…",
    "云端实时翻译启动失败：{}": "Cloud live translation failed to start: {}",
    "单流代理没读到片长，已回退直接播放":
        "The proxy couldn't read the duration — falling back to direct playback",
    "云端实时翻译：单流代理就绪，开始实时转写…":
        "Live translation: proxy ready, starting live transcription…",
    "已恢复直接播放": "Back to direct playback",
    "先打开一个视频": "Open a video first",
    "云端实时翻译正在准备单流代理，等它就绪": "The single-stream proxy is still starting — wait for it",
    "云端实时翻译正在准备，等它就绪": "Cloud live translation is still starting — wait for it",
    "实时翻译：转写 + 翻译…": "Live translation: transcribing + translating…",
    "转字幕：开始（不翻译）": "Transcribing (no translation)…",
    "实时翻译没启动：{}": "Live translation didn't start: {}",
    "一键翻译：翻译已转写的字幕…": "One-click: translating the existing transcript…",
    "一键翻译没启动：{}": "One-click translate didn't start: {}",
    "一键翻译：上次字幕不完整，重新转写 + 翻译…":
        "One-click: the previous transcript was incomplete — re-transcribing + translating…",
    "一键翻译：转写 + 翻译…": "One-click: transcribing + translating…",
    "识别语言改为{}：重新转写 + 翻译…":
        "Recognition language changed to {} — re-transcribing + translating…",
    "已有任务在跑，等它结束再翻译": "A job is already running — wait for it to finish",
    "截图已存：{}": "Screenshot saved: {}",
    "截图失败：画面还没出来": "Screenshot failed: no picture yet",
    "截图已存到 {}": "Screenshot saved to {}",
    "检测 whisper 中…": "Checking whisper…",
    "（{}）": " ({})",
    "最快，约 75 MB，只适合听清普通话/英语的粗转写":
        "fastest, ~75 MB, only for rough Mandarin/English",
    "快，约 130 MB": "fast, ~130 MB",
    "推荐，约 180 MB，中英质量够用": "recommended, ~180 MB, good enough for Chinese/English",
    "慢，约 600 MB，明显更准": "slow, ~600 MB, noticeably more accurate",
    "最准，约 1.5 GB，M 系列上仍可实时": "most accurate, ~1.5 GB, still real-time on M-series",

    "OpenList 启动过程出错：{}: {}": "OpenList startup error: {}: {}",
    "手动模式：不随软件启动 OpenList": "Manual mode: OpenList does not start with the app",
    "OpenList 没能随软件启动：{}": "OpenList didn't start with the app: {}",
    "OpenList 已就绪（{}）": "OpenList ready ({})",
    "读取下载地址失败：{}": "Failed to get the download URL: {}",
    "读取下载地址失败：HTTP {}": "Failed to get the download URL: HTTP {}",
    "没有正在播放的文件": "Nothing is playing",
    "这条外挂字幕我定位不到文件路径，请用面板里的「添加字幕文件」重新加载":
        "Can't locate that external subtitle file — reload it with \"Add subtitle file\"",
    "{} 解析出来是空的": "{} parsed to nothing",
    "这是图形字幕（PGS/VobSub 位图），只能 OCR 才能转文字，本播放器暂未内置 OCR":
        "This is a bitmap subtitle (PGS/VobSub); converting it needs OCR, which isn't built in yet",
    "这条字幕已经不是内嵌轨了": "That subtitle is no longer an embedded track",
    "用 ffprobe 没找到对应的字幕流": "ffprobe found no matching subtitle stream",
    "抽取内嵌字幕失败：{}": "Extracting the embedded subtitle failed: {}",
    "抽出来的字幕是空的（可能是图形字幕）":
        "The extracted subtitle is empty (it may be a bitmap subtitle)",
    "已取消下载": "Download cancelled",
    "{} 断了（{}），自动重试…": "{} dropped ({}), retrying…",
    "模型下载失败（已保留断点，再点一次会接着下）：\n{}":
        "Model download failed (the partial file is kept, click again to resume):\n{}",
    "从 {} MB 处继续下载…": "Resuming at {} MB…",
    "开始下载…": "Starting download…",
    "留空 = {}": "Leave empty = {}",
    "最快，但长句可能原样不翻": "fastest, but long sentences may come back untranslated",
    "速度与质量折中": "a balance of speed and quality",
    "质量优先，适合一键翻译": "quality first, good for one-click translation",
    "质量更高，整片慢慢翻": "higher quality, slower for whole films",
    "新一代，质量优先，整片慢慢翻": "new generation, quality first, slower for whole films",
    "最高质量，很慢，24 GB 内存可跑": "highest quality, very slow, needs 24 GB of RAM",
}

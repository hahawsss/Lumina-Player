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
}

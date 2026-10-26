# Lumina Player

> 一个自己写的 macOS 本地视频播放器 —— 打开就能看，字幕自己认，外语片自己翻。

<div align="center">

![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon-black)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![libmpv](https://img.shields.io/badge/播放内核-libmpv-orange)
![license](https://img.shields.io/badge/license-GPL--3.0-blue)

</div>

---

## 为什么写它

市面上的播放器要么功能已经够用但字幕这块始终差点意思，要么一装就是一堆看不懂的选项。我想要的东西其实很简单：

- 打开一个片子，**音轨、字幕自动按我偏好的语言选好**，不用每次手动切
- 片子没有中文字幕，**它能自己听出来、自己翻**，最好还能边播边翻
- 片子放在 115 网盘里，**点开就播**，不用先下载到本地
- 遇到问题不用回终端敲命令，**软件自己体检、自己补依赖**

Lumina 就是按这四条做出来的。它不是 mpv 的皮肤，而是把 libmpv 画进自己的窗口，外面套了一层我自己用着舒服的交互。

---

## 主要特性

### 播放

- **真·单窗口**：libmpv 以 ctypes in-process 方式嵌入自己的视频控件，不是「起一个 mpv 子进程再把它贴上去」。实测 GL 渲染路径下 VideoToolbox 硬解仍然生效（`hwdec-current=videotoolbox-copy`），不是拿性能换外观
- **硬解 / 软解可选**：`auto-safe`（优先硬解，失败自动回软解）、强制硬解、强制软解三档
- **全格式**：mkv / mp4 / m2ts / rmvb / flv / webm / mxf 等 60 多种后缀，音频也有 mp3 / flac / ape / dsf / mka 等
- **真 Finder 集成**：双击、右键「打开方式」、拖到 Dock 图标都能进来；也支持命令行传文件或整个目录
- **完整播放控制**：逐帧、±10s/±30s/±60s 跳转、0.25 倍速步进、音量/静音、章节条、播放列表、断点续播

### 字幕与语言

- **默认语言偏好**：按 BCP-47 语言标签的顺序自动匹配音轨与字幕轨（默认 中文 → 英文），一次设定，之后每部片都不用管
- **字幕样式全套可调**：字体、字号、描边、阴影、边距、延迟、粗体，双语字幕的上下位置也能换
- **原生字幕库**：srt / ass / ssa / vtt / sub / idx / lrc 等直接读入，也能手动给当前片子挂外部字幕

### AI 字幕（转写 + 翻译）

- **转写**：whisper.cpp 整片按需转写（长片自动 10 分钟分段、3 秒重叠，边跑边出句子）；也支持**实时窗口流**，边播边转
- **声道处理**：5.1 / 7.1 音轨只取中置声道再送去识别，避免对白被背景音乐盖住导致 whisper 复读
- **三态字幕**：**原文 / 双语 / 译文** 一键切换（`Z`）。双语是我们自己生成的一条 ASS —— 原文一行、译文一行，各自样式；生成轨和原生轨互斥，不会叠两层
- **翻译后端四选一**：
  - **OpenAI 兼容接口**（可填任何兼容的 base_url，默认 `gpt-4o-mini`）
  - **DeepL**（免费/付费）
  - **本地 llama.cpp + GGUF**（默认 Qwen2.5-1.5B，实时用 0.5B，完全离线）
  - **Argos Translate**（离线，安装包可选依赖）
- **懂上下文**：可选把前 N 句原文/译文喂给 LLM 当上下文，代词和称呼不会前后乱跳
- **磁盘缓存**：翻过的句子直接复用，重看同一部片不重复花钱
- **批处理 + 预读**：默认一次 24 句批量翻，实时模式小批快翻并预读播放头前方约 30 秒，译文尽量不落后于画面
- **各语言识别记忆**：每部片单独记住「识别到的语言」，下次不用重新猜

### 网盘直连（115）

- **115 网盘直接播**：本机 OpenList（内含 115driver）负责登录与直链刷新，Lumina 只认 loopback WebDAV，**播放地址永远不变**，token 由 `openlist admin token` 读取，不写进配置文件
- **单流 Range 代理**：115 CDN 对同一文件的两条并发流会 403（`115 pmt 3-2`），所以只向上游开一条顺序连接，把字节放进内存块缓存，mpv 和 ffmpeg 都向 `127.0.0.1` 发 Range 请求、从同一份缓存取数据。**视频字节不落盘**，缓存只留播放头附近（默认上限 128 MB）
- **网盘管理面板**：`D` 直接进 115 浏览 / OpenList 管理，网关可随软件自动启停，也能手动控制

### 稳定性与维护

- **环境体检自愈**：缺 mpv / ffmpeg / whisper-cli 时，在应用里直接用 Homebrew 装，流式看输出、可取消，不用回终端
- **存储台账**：每个产出、下载、缓存落在哪儿、占多大、能不能删，都在「存储与清理」面板里；删除范围死死锁在软件自己的数据目录内，路径全部由代码算出，不手写常量
- **密钥进钥匙串**：API key 一律只进 macOS 钥匙串（或环境变量 `LUMINA_OPENAI_KEY` / `LUMINA_DEEPL_KEY`），配置文件里不出现明文
- **配置可手改**：`~/Library/Application Support/Lumina/config.toml`，重启生效

---

## 快捷键

| 按键 | 功能 | 按键 | 功能 |
|---|---|---|---|
| `空格` / `K` | 播放·暂停 | `←` / `→` | -10s / +30s |
| `Shift+←/→` | ±60s | `Alt+←/→` | 上一个 / 下一个 |
| `↑` / `↓` | 音量 | `M` | 静音 |
| `[` / `]` | 速度 ±0.25 | `Backspace` | 恢复 1× |
| `,` / `.` | 逐帧 | `F` | 全屏 |
| `Esc` | 退出全屏 / 关侧栏 | `S` | 切字幕轨 |
| `A` | 切音轨 | `Z` | 原文 → 双语 → 译文 |
| `T` | AI 字幕面板 | `G` | 轨道面板 |
| `I` | 设置面板 | `L` | 播放列表 |
| `D` | 网盘（115 浏览 / OpenList 管理） | `R` | 存储与清理面板 |
| `Ctrl+O` | 打开文件 | `Ctrl+S` | 加字幕文件 |
| `⌘,` | 设置 | `Ctrl+H` | 收侧栏 |

---

## 环境要求

- **macOS**（主要在 Apple Silicon 上开发测试）
- **Python 3.11+**（推荐用 [uv](https://docs.astral.sh/uv/) 管理）
- **Homebrew** 提供的运行时依赖：
  - `mpv`（播放内核，必需）
  - `ffmpeg`（抽音轨、转码、字幕处理，必需）
  - `whisper-cpp`（AI 转写，可选）
  - `llama.cpp`（本地离线翻译，可选）

缺什么不用手动装 —— 应用内的「环境体检」会检测并直接调用 brew 补上。

---

## 安装与运行

### 下载成品（最简单）

到 [Releases](https://github.com/hahawsss/Lumina-Player/releases) 下载 `Lumina-<版本>-macOS-arm64.zip`，解压后把 `Lumina.app` 拖进「应用程序」。

> 包体没有 Apple 开发者签名，**第一次打开要右键 →「打开」**，或执行
> `xattr -dr com.apple.quarantine /Applications/Lumina.app`。
> 播放还要 brew 的 `mpv` / `ffmpeg`（没有的话 Homebrew 会在应用内提示安装）。

### 源码运行

```bash
git clone https://github.com/hahawsss/Lumina-Player.git
cd Lumina-Player
./start.sh
```

`start.sh` 会做这些事：检查并补齐依赖 → 在 `~/Library/Application Support/Lumina/venv` 建运行时环境 → 起播放器。第一次运行会稍慢。

其它用法：

```bash
./start.sh 影片.mkv          # 直接播（可给多个文件或文件夹）
./start.sh --check           # 只体检，不启动
./start.sh --new             # 强制再开一个新实例
./start.sh --rebuild         # 把 lumina/ 下的改动重打进 .app 再启动
```

也可以双击仓库根目录的 **「启动 Lumina.command」**，等价于 `./start.sh`。

开发调试：

```bash
./run.sh 影片.mkv            # 用 uv 环境直接跑 python -m lumina，参数透传
```

### 打包成可双击的 .app

```bash
./scripts/make_app.sh        # 产物：dist/Lumina.app
```

macOS 的 TCC 会把「文稿」当受保护目录，所以运行时 venv 必须建在 `~/Library/Application Support/Lumina`，且用非可编辑安装 —— 否则由 launchd 拉起的 GUI 进程会卡在一个看不见的授权弹窗上。脚本里已经处理了。

---

## 目录与数据

所有软件数据都在 `~/Library/Application Support/Lumina/`：

| 路径 | 内容 |
|---|---|
| `config.toml` | 配置，可直接编辑 |
| `venv/` | 运行时 Python 环境 |
| `models/whisper/` | whisper 模型（ggml-*.bin） |
| `models/llm/` | 本地翻译模型（*.gguf） |
| `subtitles/` | 生成的字幕 |
| `work/live/` | 实时翻译临时字幕，退出时清掉 |
| `cache/` | 翻译缓存等 |
| `app.log` | 应用启动 / 异常日志 |

卸载：删掉这个目录即可，不会在系统里留别的东西。

---

## AI 功能怎么开

1. 打开播放器，`I` 进设置 → 选翻译引擎：
   - 在线：填 OpenAI 兼容接口的 base_url + 模型名，key 写进钥匙串
   - DeepL：填 key，选免费/付费
   - 本地：选 GGUF 模型（可让软件下载），完全离线
2. 没有字幕的片子，按 `T` 开 AI 字幕面板 → 选整片转写或实时转写
3. `Z` 在 **原文 / 双语 / 译文** 之间切换，`S` 切字幕轨，`G` 里能调样式和位置

> 提示：模型文件较大（whisper small 约 500 MB），首次使用需要下载。国内网络下建议配好系统代理，在线接口会自动沿用 macOS 系统代理。

---

## 已知限制

- **开发中项目**：版本号还是 0.1.x，接口和配置项可能变动
- **未签名 / 未公证**：构建产物没有 Apple 开发者签名，首次打开要右键 →「打开」（或执行 `xattr -dr com.apple.quarantine`）
- **Apple Silicon 为主**：Intel Mac 没有实测过；Windows / Linux 没有适配计划（mpv 部分可移植，UI 层是 PySide6）
- **115 网盘需要自行登录**：首次使用要在网盘管理面板里完成 OpenList 的 115 授权
- **AI 功能依赖外部**：在线翻译要自备接口密钥（存进钥匙串）；本地翻译和转写要下载模型，第一次会花点时间

---

## 授权

代码以 **GPL-3.0** 发布，完整条文见 [LICENSE](LICENSE)。

第三方组件：

- 播放内核 **mpv**（GPL-2.0+）
- 语音转写 **whisper.cpp**（MIT）
- 网盘网关 **OpenList**（AGPL-3.0，内含 115driver）—— 单体二进制随应用分发，构建信息放在
  `Lumina.app/Contents/Resources/openlist-build-info.txt`
- UI 框架 **PySide6**（LGPL-3.0）

## 免责

本项目用于播放**自己合法拥有**的影音内容。软件不提供、不内置、不分发任何影视资源，网盘功能只是连接你自己的账号，请遵守你所在地法律与网盘服务条款。

---

<div align="center">

写给自己用的东西，顺手也放出来。要是你正好也需要这样一个播放器，欢迎试用、提问题。

</div>

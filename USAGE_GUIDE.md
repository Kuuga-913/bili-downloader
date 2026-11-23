# Bili-Pulse 音视频下载工作台 · 完整使用指南 (v2.0.0)

> 本地运行的 B 站与全网直链音视频双核下载器，配备全新 **Bili-Pulse Cyber Studio** 图形工作台：支持 **音视频合并 / 仅视频 / 仅音频**、最高 **8K 超高清 / Hi-Res 无损 / 杜比全景声**、智能链接嗅探、实时声波遥测与 Web Audio 合成器交互音效。

---

## 一、三步快速上手

### 1. 环境准备
- **Python 3.10+**（Windows 推荐官方安装包，勾选 `Add Python to PATH`）
- **FFmpeg 与 FFprobe**（音视频无损合并、单轨提取与时长校验必需）：
  - Windows 一键安装：`winget install ffmpeg`
  - macOS：`brew install ffmpeg`
  - Linux：`sudo apt install ffmpeg`

### 2. 安装 Python 依赖
解压本压缩包后，在项目根目录（`bili-downloader/`）打开终端执行：

```bash
pip install -r requirements.txt
```

> 若需使用通用爬虫 `video_spider` 的自定义站点规则，请将模板复制为正式配置：
> - Windows：`copy video_spider\config.example.yaml video_spider\config.yaml`
> - macOS / Linux：`cp video_spider/config.example.yaml video_spider/config.yaml`

### 3. 启动桌面图形工作台
- **Windows 一键启动**：直接双击根目录的 `start_bili_downloader.bat`（或 `gui\start_bili_downloader.bat`）。
- **命令行启动**：
  ```bash
  python gui/bili_downloader_launcher.pyw
  ```
- 程序会自动启动本地 HTTP 服务并唤起 Edge / Chrome `--app` 独立桌面窗口（无额外第三方 GUI 库依赖；支持 `--no-browser` 纯服务模式与 `--tk` 传统兜底模式）。

---

## 二、图形界面操作详解

### 顶栏：环境健康与个性化控制
- **环境状态胶囊**（点击弹出详情）：实时检查 `Bili-Core`、`Spider-Core`、`Python`、`FFmpeg`、`FFprobe` 是否就绪。
- **登录态胶囊 / 扫码登录**：显示本地 Cookie 是否已载入；点击后弹出二维码窗口，用手机 B 站 App 扫码即可自动将 Cookie 同步至两个下载引擎（注：需已安装 `requirements.txt` 中的 `qrcode` 依赖）。
- **权限诊断 (`--diag`)**：一键核验当前账号登录态、大会员有效期，以及目标链接是否能获取完整 DASH 流。
- **音效 / 动效 / 主题开关**：
  - **音效开关**：开启或静音 Web Audio 程序化合成器交互音效（点击、粘贴识别、模式切换、任务启动、完成和弦）。
  - **动效开关**：随时暂停或恢复背景光场漂移与二维码扫描光束动画。
  - **主题切换**：在 **Obsidian 黑曜石深色** 与 **Porcelain 白瓷浅色** 之间即时切换。

### Step 01 · 目标视频链接
- 支持粘贴：`BV` 视频、`av` 视频、番剧整季 `ss`、番剧单集 `ep`、`b23.tv` 短链、`.m3u8` 流媒体地址、`.mp4`/`.webm` 等视频直链或通用网页 URL。
- 输入后会自动点亮对应的 **链接类型徽章**，并在下方提示推荐使用的下载内核。
- **探测画质 (`--info`)**：在正式下载前点击，可预先查询该视频**实际解锁并可用**的视频画质、音频音质和分P/剧集目录（需要选用 `Bili-Core` 引擎）。

### Step 02 · 选择下载内核

| 引擎 | 对应内核 | 适用场景与核心能力 |
|---|---|---|
| **Bili-Core 专用引擎** | `bilibili-downloader/bilibili_dl.py` | **B 站首选**：支持大会员电影/番剧、充电专属视频、`--info` 画质探测、`--diag` 权限诊断、DASH 多线程分块、FFmpeg 无损混流及 FFprobe 时长校验（自动识别试看片段与 Widevine DRM）。 |
| **Spider-Core 通用引擎** | `video_spider/spider.py` | **全网通用**：支持直链断点续传、HLS `m3u8` 多线程分片抓取、B 站 WBI 签名解析，以及通过 `video_spider/config.yaml` 自由扩展其他视频站点规则。 |

### Step 03 · 轨道模式与画质 / 音质矩阵
1. **输出轨道模式**（物理弹簧分段滑块）：
   - **音视频合并 · MP4**（默认，下载分离流后自动调用 FFmpeg 无损合并为 `标题.mp4`）
   - **仅视频轨 · 无声 MP4**（输出 `标题 (仅视频).mp4`，自动禁用音质选择）
   - **仅音频轨 · M4A**（输出 `标题 (仅音频).m4a`，自动禁用画质选择）
2. **画质上限 & 音质偏好**：
   - 采用「不超过所选上限的最高可用档」策略；当点击 **探测画质 (`--info`)** 后，片源实际支持的档位会自动亮起**绿色可用圆点**。
   - 若探测出多分P/多集，下方会出现分P芯片列表，**直接点击任意分P即可自动填入分P范围**。
3. **高级参数抽屉**（点击展开）：
   - **分P / 集数范围 (`-p / --ep`)**：例如 `1,3-5`，留空则下载全部分P。
   - **并发下载线程数**：`1 ~ 32` 线程滑块调节（默认 `8` 线程）。
   - **保留分离音视频轨 (`--no-merge`)**：开启后不合并，保留独立的视频与音频文件。
   - **附加 CLI 参数**：可填入 `--keep`、`--proxy http://127.0.0.1:7890` 等原生命令行参数。
   - **B 站 Cookie**：支持显示/隐藏与保存至本地 `bilibili-downloader/cookie_string.txt`。

### Step 04 · 发射下载与实时遥测
- 点击 **「开始下载」**（或在链接框直接按 `Enter`）启动任务。
- **右侧遥测仪表盘**：实时展示当前阶段、环形总进度、`MB/s` 下载速度、已下载体积以及随速率律动的 Canvas 双波能量频谱。
- **实时执行终端**：原地刷新进度行不刷屏，按状态自动着色；支持一键复制日志与清空。
- **输出目录最近文件**：下载完成后自动刷新产物列表，悬停或聚焦任意文件条目可点击 **「定位」** 直接在资源管理器中选中该文件。

---

## 三、解锁大会员 / 充电专属高画质（Cookie 配置）

B 站按账号权限下发流地址，请通过以下任一方式配置**自己的**登录态：

1. **界面扫码登录（最推荐）**：点击顶栏「扫码登录」，使用手机 B 站 App 扫码确认即可自动写入 `cookies.json`、`cookies.txt`、`cookie_string.txt` 并同步 `video_spider/config.yaml`。
2. **手动复制 SESSDATA**：浏览器登录 B 站 → `F12` → `Application` → `Cookies` → 复制 `SESSDATA` 值 → 在高级抽屉 Cookie 栏填入 `SESSDATA=你的值` 并点击「保存本地」。
3. **复制完整请求头 Cookie**：在 `Network` 面板中复制任意 `bilibili.com` 请求的完整 `Cookie:` 请求头。
4. **浏览器扩展导出 `cookies.txt`**：导出 Netscape 格式 `cookies.txt` 放入 `bilibili-downloader/` 目录，`run.bat` 会自动识别。

> **隐私安全提示**：Cookie 等价于你的账号登录态，仅保存于本地，请勿分享或提交到公开仓库。

---

## 四、命令行进阶用法

两个下载引擎也可脱离图形界面独立运行（或点击界面底部的 **「命令行版」** 打开交互入口）：

```bash
# 查看视频实际可用画质、音质与分P列表
python bilibili-downloader/bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -c bilibili-downloader/cookies.txt --info

# 诊断账号大会员权限与流状态
python bilibili-downloader/bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -c bilibili-downloader/cookies.txt --diag

# 仅下载指定分P的 Hi-Res 音频轨
python bilibili-downloader/bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -p "1,3" --only audio --audio-quality hires

# 使用通用引擎下载 m3u8 或直链视频（16 线程）
python video_spider/spider.py "https://example.com/video.m3u8" -n 16 -o ./downloads
```

---

## 五、常见问题排查 (FAQ)

- **Q：点击「扫码登录」提示 `No module named 'qrcode'`？**  
  A：请确认已在当前 Python 环境执行 `pip install -r requirements.txt`（需包含 `qrcode` 与 `Pillow`）。
- **Q：下载时提示缺少 `ffmpeg` 或只有音视频分离文件？**  
  A：音视频合并、单轨提取与校验依赖 `ffmpeg` / `ffprobe`，请安装并确保已加入系统 `PATH`（Windows 可用 `winget install ffmpeg`）。
- **Q：下载的视频只有几分钟的试看片段？**  
  A：说明当前未携带有效登录态或账号无该大会员/充电内容权限。请重新扫码或更新 `SESSDATA` 后，用「权限诊断 (`--diag`)」确认状态再重试。
- **Q：通用引擎无法抓取某些加密 m3u8？**  
  A：遇到 AES-128 加密流时脚本会给出提示，可按日志提示改用 `ffmpeg -i "<m3u8_url>" -c copy out.mp4`。

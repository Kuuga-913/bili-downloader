# B 站音视频下载器

本地运行的 B 站视频 / 音频下载工具集合，带图形界面。可以选择 **音视频合并 / 仅视频 / 仅音频**，并分别指定画质与音质。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## 功能

- **两个下载内核**
  - `bilibili-downloader/`：B 站专用。支持 `BV` / `av` / 番剧 `ss` / 单集 `ep`、`b23.tv` 短链、Cookie / 扫码登录、DASH 多线程分块下载、ffmpeg 无损合并、ffprobe 校验、试看片段识别、DRM 检测、`--diag` 权限诊断。
  - `video_spider/`：配置驱动的通用爬虫。支持直链（断点续传）、HLS `m3u8`（多线程分片）、B 站 DASH（WBI 签名取高画质）、番剧选集，新增站点只需改 `config.yaml`。
- **图形界面**：粘贴链接 → 选下载内容 / 画质 / 音质 → 开始下载，实时日志、停止、打开输出目录、扫码登录取 Cookie、调用命令行版。
- **画质**：自动最高 / 8K(127) / 4K(120) / 1080P60(116) / 1080P+(112) / 1080P(80) / 720P(64) / 480P(32) / 360P(16)，取「不超过所选档的最高可用画质」。
- **音质**：自动最高 / Hi-Res 无损 / 杜比全景声 / 192K / 132K / 64K，按音频流 id 精确匹配，缺失时按码率取最接近的一档。
- **单轨输出**：仅视频 `标题 (仅视频).mp4`，仅音频 `标题 (仅音频).m4a`，音视频合并 `标题.mp4`。

## 目录结构

```
bili-downloader/
├── gui/
│   ├── bili_downloader_launcher.pyw      图形界面（tkinter，标准库）
│   └── start_bili_downloader.bat         Windows 一键启动
├── bilibili-downloader/
│   ├── bilibili_dl.py                    专用下载内核
│   ├── bili_login_qr.py                  扫码登录 → 导出 Cookie
│   ├── run.bat                           命令行交互入口
│   └── README.md
├── video_spider/
│   ├── spider.py                         通用下载内核
│   ├── config.example.yaml               站点规则模板（复制为 config.yaml 后填写）
│   ├── requirements.txt
│   ├── run.bat
│   └── README.md
├── requirements.txt                      全部 Python 依赖
└── .gitignore                            排除 Cookie 与下载产物
```

## 环境要求

- Python **3.10+**（脚本使用 `int | None` 类型标注）
- [ffmpeg](https://ffmpeg.org/download.html) / ffprobe，需在 `PATH` 中（音视频合并、抽轨与校验需要）
  - Windows：`winget install ffmpeg`
- tkinter（Windows / macOS 官方 Python 自带；Linux 需 `sudo apt install python3-tk`）

## 快速开始

```bash
git clone https://github.com/Kuuga-913/bili-downloader.git
cd bili-downloader
python -m venv .venv
.venv\Scripts\activate          # Windows；macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy video_spider\config.example.yaml video_spider\config.yaml   # Windows
# cp video_spider/config.example.yaml video_spider/config.yaml   # macOS/Linux
```

启动图形界面：

```bash
python gui/bili_downloader_launcher.pyw
# Windows 也可以双击 gui/start_bili_downloader.bat
```

或者直接用命令行：

```bash
python bilibili-downloader/bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --only both --info
python video_spider/spider.py "https://www.bilibili.com/video/BVxxxx" --only audio --audio-quality 192k
```

## 获取 Cookie（大会员 / 高画质）

B 站按账号权限下发流地址，登录态决定可用画质与音质。四种方式：

1. **扫码登录**：图形界面点「扫码登录 / 获取 Cookie」，或运行 `python bilibili-downloader/bili_login_qr.py`，用手机 B 站 App 扫弹窗里的二维码。成功后自动生成 `cookies.json` / `cookies.txt` / `cookie_string.txt`，并同步 `video_spider/config.yaml`。
2. **F12 复制 SESSDATA**：登录 B 站 → F12 → Application → Cookies → 复制 `SESSDATA` → 填成 `SESSDATA=<值>` 到 GUI 的 Cookie 输入框、`--cookie` 参数，或 `config.yaml`。
3. **复制整段请求头 Cookie**：F12 → Network → 任意 `bilibili.com` 请求 → Request Headers → 复制 `Cookie:` 整段值。
4. **扩展导出** `cookies.txt`（Netscape 格式，如 `Get cookies.txt LOCALLY`），放到 `bilibili-downloader/` 目录，`run.bat` 会自动识别。

验证登录态与权限：

```bash
python bilibili-downloader/bilibili_dl.py -u <链接> -c cookies.txt --diag
python bilibili-downloader/bilibili_dl.py -u <链接> -c cookies.txt --info
```

`--info` 会打印该视频实际可用的画质与音质列表。高画质、Hi-Res、杜比音质取决于账号权限和片源，无权限时自动落到可用的最高档。

## 参数速查

| 参数 | 专用版 | 通用版 | 说明 |
|---|---|---|---|
| `--only` | ✅ | ✅ | `both` 音视频合并（默认）/ `video` 仅视频 / `audio` 仅音频 |
| `-q` / `--quality` | ✅ `-q` | ✅ `--quality` | 画质上限 qn |
| `--audio-quality` | ✅ | ✅ | `auto` / `hires` / `dolby` / `192k` / `132k` / `64k` |
| `-p` / `--pages` | ✅ | — | 选择分P或集数，如 `1,3-5` |
| `--ep` | — | ✅ | 番剧选集 |
| `--info` / `--diag` | ✅ | — | 查看可用画质音质 / 诊断账号权限 |
| `--no-merge` | ✅ | — | 保留分离的音视频文件 |
| `-o` / `--output` | ✅ | ✅ | 输出目录 |

更多示例见 `bilibili-downloader/README.md` 与 `video_spider/README.md`。

## 输出位置

- 图形界面 / 通用版：项目根目录下 `downloads/`
- 专用版默认：`bilibili-downloader/downloads/<视频标题>/`
- 可用 `-o` 或 GUI 状态栏显示的路径随时改。

## 隐私

仓库不含任何账号凭证。`.gitignore` 已排除 `cookie_string.txt`、`cookies.json`、`cookies.txt`、`qrcode.png`、`config.yaml`、`*.bak-*` 与所有下载产物。Cookie 等价于账号登录态，只保存在本机，不要提交或外发；SESSDATA 过期后重新获取即可。

## License

MIT

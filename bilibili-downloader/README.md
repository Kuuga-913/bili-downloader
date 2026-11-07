# B 站视频下载器 `bilibili_dl.py`

一键下载 **B 站充电专属视频、大会员电影、番剧、普通视频**，自动选最高可用画质，多线程加速，下载后自动校验完整性。

## 功能

| 功能 | 说明 |
|---|---|
| 链接支持 | `BV` / `av` / 番剧整季 `ss` / 单集 `ep`，自动跟随 `b23.tv` 短链 |
| 权限诊断 | `--diag` 一键检查：登录状态 / 大会员有效期 / 该视频能否取到完整流 |
| 充电专属视频 | 已购买充电的账号登录后，下载**完整内容 + 高画质**（Hires/无损等） |
| 大会员电影/番剧 | 大会员账号登录后下载**完整正片**（最高 4K/HDR，视片源而定） |
| 画质选择 | `-q` 指定上限（127=8K、120=4K、112=1080P+、80=1080P、64=720P…），默认自动最高 |
| 下载内容 | `--only both` 音视频合并（默认）/ `--only video` 仅视频 / `--only audio` 仅音频 |
| 音质选择 | `--audio-quality auto`（默认自动最高）/`hires` Hi-Res /`dolby` 杜比 /`192k`/`132k`/`64k`；`--info` 会列出该视频实际可用的画质与音质 |
| 多分P / 多集 | 自动全下，或用 `-p "1,3-5"` 选择 |
| 下载加速 | 多线程 Range 分块下载 + 断点重试，CDN 不支持分块时自动降级单线程 |
| 自动合并 | ffmpeg 音视频 `-c copy` 无损合并（秒级，不转码） |
| 自动校验 | ffprobe 检查时长（与官方时长对比）与音视频流完整性，**识别试看片段** |
| DRM 检测 | 接口标记受 Widevine 保护的内容自动跳过并警告 |

## 环境要求

- Python 3.8+
- `requests`（必需）、`qrcode`（可选，`--login` 打印二维码）
- **ffmpeg / ffprobe**（合并与校验必需）

```bash
pip install requests qrcode
```

ffmpeg 安装（任选其一）：

```bash
winget install ffmpeg          # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Linux
```

## 获取大会员 Cookie（4 种方式）

### 方式 1：扫码登录（最省事，自动写入两个工具）

图形界面点「扫码登录 / 获取 Cookie」，用手机 B 站 App 扫码并确认；或命令行运行：

```bash
python bili_login_qr.py
```

成功后自动生成本目录的 `cookies.json`、`cookies.txt`、`cookie_string.txt`，并把完整 Cookie 同步进 `video_spider/config.yaml`（原文件先备份为 `.bak-20261107`）。`run.bat` 会自动识别同目录的 `cookies.txt`，图形界面启动时会自动把 `cookie_string.txt` 填进 Cookie 输入框。

### 方式 2：浏览器 F12 复制 SESSDATA

1. Chrome / Edge 打开 `https://www.bilibili.com` 并登录大会员账号
2. `F12` → **Application** → 左侧 **Cookies** → `https://www.bilibili.com`
3. 找到 `SESSDATA`，双击 Value 整串复制
4. 拼成 `SESSDATA=<复制的值>`，填到以下任意一处：
   - 图形界面的 **Cookie** 输入框
   - 命令行 `--cookie "SESSDATA=..."`
   - `video_spider/config.yaml` → `sites[name=bilibili].headers.Cookie`
5. 想更完整，可再复制 `bili_jct`、`DedeUserID`、`DedeUserID__ckMd5`，用 `; ` 拼接

### 方式 3：复制整段请求头 Cookie（字段最全）

`F12` → **Network** → 刷新页面 → 点任意 `bilibili.com` 请求 → **Headers** → **Request Headers** → 复制 `Cookie:` 后面整段值，直接粘进图形界面 Cookie 输入框或 `--cookie`。这段已包含 SESSDATA / bili_jct / DedeUserID 等全部字段。

### 方式 4：扩展导出 cookies.txt

安装浏览器扩展 `Get cookies.txt LOCALLY`（或 `Cookie-Editor` 的导出功能），在已登录的 B 站页面导出 Netscape 格式 `cookies.txt`，放到本目录即可被 `run.bat` 自动使用，或显式 `-c cookies.txt`。

> 本机 Chrome 的 Cookie 数据库使用 v20 应用绑定加密，脚本无法直接读取其中的 SESSDATA，所以走上面 4 种方式。SESSDATA 会过期，失效后重新获取即可；退出登录 / 改密码也会让它失效。

### 验证登录态与大会员

```bash
python bilibili_dl.py -u <链接> -c cookies.txt --diag   # 打印登录状态 / 大会员 / 能否取完整流
python bilibili_dl.py -u <链接> -c cookies.txt --info   # 打印实际解锁的画质与音质
```

大会员生效时 `--info` 应能看到 1080P60 / 4K / HDR 档画质，部分片源还会出现 Hi-Res、杜比全景声音质。

## 登录态（关键）

B 站的会员/充电内容按**账号权限**下发流地址。脚本不会破解任何加密，只是使用**你自己的登录态**获取你有权观看的内容——这就是"解锁"的原理。

三种方式任选：

1. **cookies 文件（推荐）**
   - 浏览器扩展（如 "Get cookies.txt LOCALLY" / "Cookie-Editor"）导出 `cookies.txt`（Netscape 格式）
   - 或 Chrome DevTools → Network → 任意 bilibili.com 请求 → Request Headers 里复制整段 `Cookie:` 值
2. **直接传 Cookie 字符串**
   ```bash
   python bilibili_dl.py -u <链接> --cookie "SESSDATA=xxxx; bili_jct=xxxx; DedeUserID=xxxx"
   ```
3. **扫码登录**（自动保存到 `downloads/cookies.json`，下次用 `-c cookies.json`）
   ```bash
   python bilibili_dl.py -u <链接> --login
   ```

> ⚠️ SESSDATA 会过期（通常几个月），失效后重新导出即可。

## 使用示例

```bash
# 查看信息与可选画质（不下载）
python bilibili_dl.py -u "https://www.bilibili.com/video/BV1W9ut6VEi9" --info

# 诊断：账号有没有权限拿完整流（登录后跑一次，一眼看清缺什么）
python bilibili_dl.py -u "https://www.bilibili.com/video/BV1W9ut6VEi9" -c cookies.txt --diag
python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ep316163" -c cookies.txt --diag

# 下载充电专属视频（需已充电的账号）
python bilibili_dl.py -u "https://www.bilibili.com/video/BV1W9ut6VEi9" -c cookies.txt

# 下载大会员电影
python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ep316163" -c cookies.txt

# 下载整部番剧
python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ss12345" -c cookies.txt

# 只下 1-3 集
python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ss12345" -c cookies.txt -p "1-3"

# 限制画质为 1080P
python bilibili_dl.py -u <链接> -c cookies.txt -q 80

# 只下载不合并（保留 .video.m4s / .audio.m4s）
python bilibili_dl.py -u <链接> -c cookies.txt --no-merge

# 选择下载内容 + 画质 / 音质
python bilibili_dl.py -u <链接> --only video -q 80                # 仅视频（无声 mp4，1080P 上限）
python bilibili_dl.py -u <链接> --only audio --audio-quality 192k  # 仅音频（m4a，192K 档）
python bilibili_dl.py -u <链接> --only both -q 64 --audio-quality 64k
python bilibili_dl.py -u <链接> --info                            # 列出实际可用画质与音质
```

### 下载内容 / 画质 / 音质说明

| 参数 | 说明 |
|---|---|
| `--only both` | 默认。下载视频流 + 音频流，用 ffmpeg `-c copy` 无损合并为一个 mp4 |
| `--only video` | 只下视频流，输出 `标题 (仅视频).mp4`，不下载音频、不需要合并 |
| `--only audio` | 只下音频流，输出 `标题 (仅音频).m4a`，最省流量 |
| `-q / --quality` | 画质上限，取「不超过该档的最高可用画质」；不填 = 账号权限内最高 |
| `--audio-quality` | `auto`(最高码率) / `hires`(Hi-Res) / `dolby`(杜比全景声) / `192k` / `132k` / `64k`；指定档位不存在时自动回退到码率最接近的一条 |

高画质（1080P+/4K/8K）、Hi-Res 与杜比音质取决于账号权限与片源；`--info` 会打印本次接口实际返回的画质与音质列表。选「仅视频」时音质选项无意义，选「仅音频」时画质选项无意义，脚本会自动忽略。

输出结构：

```
downloads/
└── 视频标题/
    ├── 视频标题.mp4                    # --only both：合并后的成品
    ├── 视频标题 (仅视频).mp4          # --only video
    ├── 视频标题 (仅音频).m4a          # --only audio
    └── 视频标题 - P02.mp4              # 多分P时
```

## 关于"破解"的实测结论（如实告知）

我对这两个示例链接做了**全平台端点实测**（web/pc、web/android、web/tv、App 签名端点 ×2 组公开 appkey）：

| 端点 | 充电视频 BV1W9ut6VEi9 | 会员电影 ep316163 |
|---|---|---|
| web / pc | durl 试看 720P（实际 300s） | durl 试看 480P |
| web / android | 同上 | 同上 |
| web / tv | 同上 | 同上 |
| App 签名端点 | 反爬拒绝 | 反爬拒绝 |

**结论**：这两个视频**不是加密内容**（接口无任何 DRM 标记），而是**服务器端访问控制**——服务端按账号权限（充电关系 / 大会员状态）决定下发哪一段字节流。对无权限账号，完整字节**在服务器端就不存在**，客户端改任何参数、伪装任何平台都拿不到，这不是加密所以没有"密钥"可破。**唯一的"解锁"就是有权限的登录态**——这也是脚本的工作方式。

若某天遇到接口返回 `drm` 标记的内容（少数杜比视界/HDR 片源），那才是真正的 Widevine 加密，需要 CDM + license 的专门工具链（如 pywidevine），脚本会明确检测并跳过，不会产出坏文件。

## 校验机制说明

每个文件下载完成后自动执行：

1. **大小/时长对比**：ffprobe 读取实际时长，与官方 `duration` 对比（容差 3%）
2. **流完整性**：确认同时包含视频流和音频流
3. **试看识别**：若实际时长明显短于完整时长（<80%），判定为试看片段，**标记失败**并提示检查登录权限

下载结果以 `[OK]` / `[FAIL]` 汇总输出，退出码 0=全部成功。

## DRM 说明（如实告知）

- B 站绝大多数内容（含充电视频、普通电影/番剧）走的是**权限控制**而非 DRM，登录后即可正常下载
- 少数标有「杜比视界/部分 HDR」的片源接口会标记 `drm`（Widevine 加密），**无法通过脚本解密**，脚本检测到会明确警告并跳过该集，不会产出坏文件
- 遇到 DRM 内容时建议选非杜比的普通画质，或换用支持 Widevine L3 的专用工具（如 BBDown）

## 常见问题

| 问题 | 处理 |
|---|---|
| `接口错误(code=-101)` | 未登录，检查 SESSDATA 是否失效 |
| `接口错误(code=-10403/-403)` | 账号无对应权限：充电视频需已购买充电，电影需大会员 |
| 下载到只有几分钟的文件 | 那就是试看片段，脚本已标 `FAIL`——登录有权限的账号重下 |
| ffmpeg 找不到 | 安装 ffmpeg 或用 `--ffmpeg C:/path/to/ffmpeg.exe` |
| 某分块下载失败 | 自动重试 3 次；仍失败可加 `--threads 4` 或减小并发 |

## 备注

脚本仅用于下载**你已购买或有权限观看**的内容并离线保存，请遵守平台条款，勿用于传播。

# video_spider · 通用视频爬虫

配置驱动的通用视频资源爬虫,支持**直链**、**HLS(m3u8)流**与 **B 站视频**,新增网站只需在 `config.yaml` 加一段规则,不需要改代码。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 直接抓取(自动匹配站点规则,未命中则用通用启发式)
python spider.py https://example.com/video/123

# 2b. 下载 B 站视频(自动调用官方 API,音视频分离下载)
python spider.py https://www.bilibili.com/video/BV1xxxxx/

# 3. 强制使用指定站点规则
python spider.py https://example.com/video/123 --site example_cdn

# 4. 常用参数
python spider.py <url> -o ./downloads     # 输出目录
python spider.py <url> -n 16              # m3u8 分片线程数(默认 8)
python spider.py <url> --proxy http://127.0.0.1:7890   # 走代理
python spider.py <url> --index 1          # 页面有多个视频时选第 2 个
python spider.py --list-sites             # 查看已配置的站点

# 5. 选择下载内容 / 画质 / 音质（B 站 DASH 流）
python spider.py <B站链接> --only video --quality 80
python spider.py <B站链接> --only audio --audio-quality 192k
python spider.py <B站链接> --only both --quality 64 --audio-quality 132k
```

## 工作流程

```
页面 URL → 匹配站点规则 → 抓取 HTML → 提取视频地址 → 直链/m3u8 下载
```

- **直链**(mp4/webm/mkv 等):流式下载,支持断点续传,中断后重跑同一命令即可续传。
- **m3u8**:自动检测 master 播放列表并选择最高清晰度,多线程拉取分片后按序合并为单个 `.ts` 文件。
- **B 站视频**:自动识别 bilibili.com,调用官方 API 获取视频/音频流,分别下载;本机装有 ffmpeg 时自动合并成 mp4,否则分别保存并提示合并命令(把 `.m4s` 后缀改成 `.mp4` 即可直接播放)。

## 站点配置说明

编辑 `config.yaml`,每个站点包含:

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 站点名,`--site` 参数用它 |
| `match` | 否 | 页面 URL 包含该关键字即自动命中 |
| `headers` | 否 | 自定义请求头(`Referer`/`Cookie` 等,登录或防盗链时用) |
| `extractors` | 否 | 视频地址提取器,按顺序尝试: `regex` / `css` / `xpath` / `json` |
| `exclude_patterns` | 否 | 排除含这些关键字的候选(广告/跟踪域名) |

不配置 `extractors` 时自动使用通用启发式(查找 `<video>`/`<source>` 标签与 m3u8/mp4 链接)。

### 提取器写法

```yaml
extractors:
  - type: regex                      # 正则提取
    pattern: 'https?://[^\s<>]+\.m3u8[^\s<>]*'
  - type: css                        # CSS 选择器,取属性
    selector: "video"
    attribute: "data-src"
  - type: xpath                      # 轻量 XPath,仅支持 //tag/@attr
    xpath: "//video/@src"
  - type: json                       # 从内嵌 JSON 提取(a.b.c 或 a[0].b)
    jsonpath: "data.video.playUrl"
```

## 下载内容 / 画质 / 音质选择

| 参数 | 说明 |
|---|---|
| `--only both` | 默认。B 站 DASH 下视频流 + 音频流，ffmpeg 无损合并为 mp4 |
| `--only video` | 只下视频流，输出 `标题 (仅视频).mp4` |
| `--only audio` | 只下音频流，输出 `标题 (仅音频).m4a` |
| `--quality QN` | 画质上限：16/32/64/80/112/116/120/127，取不超过该档的最高可用画质；不填 = 自动最高 |
| `--audio-quality` | `auto` 最高码率 / `hires` Hi-Res / `dolby` 杜比 / `192k` / `132k` / `64k`；档位不存在时按码率取最接近的一条 |

非 B 站的直链 / m3u8 混合文件若选 `--only video` 或 `--only audio`，下载完成后会用 ffmpeg 无损抽出对应轨道（`-an` / `-vn`）。画质与音质的可用上限由账号权限和片源决定，运行时会打印接口返回的可用列表。

## 常见问题

- **页面能看到视频但脚本提取不到**:视频地址由 JS 动态生成,纯 requests 拿不到。先用浏览器 DevTools → Network 过滤 `m3u8`/`mp4`,把真实地址的规律写进该站点的 `regex` 提取器;或改用 Playwright 方案。
- **需要登录才能看**:把登录后的 Cookie 填进站点规则的 `headers.Cookie`。
- **m3u8 提示已加密(AES-128)**:改用 ffmpeg 下载:
  ```bash
  ffmpeg -i "<m3u8_url>" -c copy out.mp4
  ```
- **下载的文件无法播放**:`.ts` 是 MPEG-TS 容器,大多数播放器(VLC/PotPlayer)可直接播放;也可用 ffmpeg 转成 mp4。

## 合规提示

仅爬取你有权访问的公开内容;遵守目标站的 `robots.txt` 与服务条款;不要绕过付费墙、DRM 或下载侵权内容。使用本脚本产生的任何后果由使用者自行承担。

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_spider.py - 通用视频资源爬虫(配置驱动)

功能:
  - 站点规则由 config.yaml 驱动,新增网站只需加一段配置
  - 自动识别并下载:直链(mp4/webm/mov...)、HLS 流(m3u8)
  - m3u8 支持多线程分片下载 + 自动合并;master 播放列表自动选最高清晰度
  - 直链下载支持断点续传
  - 内置通用启发式提取,不配置 extractors 也能抓取大多数页面

用法:
  python spider.py <url>                     # 自动匹配站点规则
  python spider.py <url> --site mysite       # 强制使用指定站点规则
  python spider.py <url> -o ./downloads      # 指定输出目录
  python spider.py <url> -n 16               # m3u8 分片下载线程数
  python spider.py --list-sites              # 列出已配置站点

注意:
  - 请仅爬取您有权访问的公开内容,遵守目标站 robots.txt 与服务条款
  - 遇到 AES-128 加密的 m3u8 流,请改用 ffmpeg:
      ffmpeg -i <m3u8_url> -c copy out.mp4
"""

import argparse
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, unquote

import requests
import yaml

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DIRECT_VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".flv", ".m4v", ".ts", ".mpg")
HLS_EXT = (".m3u8",)

# 通用启发式:页面中可能藏视频地址的属性
VIDEO_ATTRS = ("src", "data-src", "data-video", "data-url", "data-source", "data-play", "poster_mp4")

GENERIC_PATTERNS = [
    # m3u8 链接(含带 query 的)
    re.compile(r'https?://[^\s"\'<>\\]+?\.m3u8[^\s"\'<>\\]*', re.I),
    # 直链视频(带引号或空白边界)
    re.compile(r'https?://[^\s"\'<>\\]+?\.(?:mp4|webm|mov|mkv|flv|m4v)(?:\?[^\s"\'<>\\]*)?', re.I),
]

# 常见跟踪 / 广告域名片段,启发式结果按此过滤
EXCLUDE_HINTS = (
    "adservice", "adsystem", "doubleclick", "googletag",
    "track", "analytics", "metrics", "bugreport",
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
class SpiderError(Exception):
    """爬虫业务异常,直接向用户展示 message。"""


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """去除文件名中的非法字符,并限制长度。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._")
    return name or "video"


def http_session(site: dict | None = None, proxy: str | None = None) -> requests.Session:
    """构造带站点自定义 header 与可选代理的 Session。"""
    s = requests.Session()
    headers = dict(DEFAULT_HEADERS)
    if site and site.get("headers"):
        headers.update(site["headers"])
    s.headers.update(headers)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def fetch(url: str, sess: requests.Session, timeout: int = 30, retries: int = 3,
          referer: str | None = None) -> requests.Response:
    """带重试的 GET 请求,统一处理超时与 4xx/5xx。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            hdr = {}
            if referer:
                hdr["Referer"] = referer
            resp = sess.get(url, headers=hdr, timeout=timeout, stream=True)
            if resp.status_code >= 400:
                resp.close()
                raise SpiderError(f"HTTP {resp.status_code}: {url}")
            return resp
        except (requests.RequestException, SpiderError) as e:
            last_err = e
            if isinstance(e, SpiderError) and getattr(e, "args", (None,))[0].startswith("HTTP 4"):
                raise  # 4xx 无意义重试
            if attempt < retries:
                time.sleep(min(2 * attempt, 5))
    raise SpiderError(f"请求失败(重试 {retries} 次): {url} -> {last_err}")


def normalize_url(raw: str, base: str) -> str:
    """把相对路径/协议相对路径转为绝对 URL。"""
    raw = raw.strip()
    if raw.startswith("//"):
        return "https:" + raw
    return urljoin(base, raw)


def title_from_html(html: str, url: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title
    host = urlparse(url).netloc.replace("www.", "")
    return host or "video"


def prefer_order(url: str) -> tuple:
    """候选视频排序: m3u8 优先(通常为完整流),其次 mp4,长 URL 靠前。"""
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    kind = 0 if ext in HLS_EXT else (1 if ext in DIRECT_VIDEO_EXT else 2)
    return (kind, -len(url))


# ---------------------------------------------------------------------------
# 视频地址提取
# ---------------------------------------------------------------------------
def extract_by_regex(html: str, pattern: str) -> list[str]:
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        raise SpiderError(f"无效的正则表达式 {pattern!r}: {e}")
    return [normalize_url(m, "https://") for m in rx.findall(html)]


def extract_by_css(html: str, selector: str, attribute: str | None, base: str) -> list[str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select(selector):
        if attribute:
            val = node.get(attribute)
            if val:
                out.append(normalize_url(val, base))
        else:
            for src in (node.get("src"), node.get("data-src")):
                if src:
                    out.append(normalize_url(src, base))
            for src in node.find_all("source"):
                s = src.get("src")
                if s:
                    out.append(normalize_url(s, base))
    return out


def extract_by_xpath(html: str, xpath: str, base: str) -> list[str]:
    # 轻量 XPath:支持 //tag/@attr、//tag、以及常见相对写法
    from bs4 import BeautifulSoup
    m = re.fullmatch(r"\s*//([a-zA-Z0-9_-]+)(?:/@([a-zA-Z0-9_-]+))?\s*", xpath)
    if not m:
        raise SpiderError(f"不支持的 XPath(仅支持 //tag/@attr 形式): {xpath}")
    tag, attr = m.group(1), m.group(2)
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.find_all(tag):
        if attr:
            val = node.get(attr)
            if val:
                out.append(normalize_url(val, base))
        else:
            for a in VIDEO_ATTRS:
                val = node.get(a)
                if val:
                    out.append(normalize_url(val, base))
    return out


def extract_by_json(html: str, jsonpath: str, base: str) -> list[str]:
    """从页面内嵌 JSON 中提取(支持 a.b.c 与 a[0].b 简写路径)。"""
    import json
    m = re.search(r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>",
                  html, re.S | re.I)
    if not m:
        # 退而求其次:从整个 HTML 里找 JSON 对象块(启发式)
        m = re.search(r"(\{[^{}]*\})", html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    def dig(obj, path: str):
        for part in path.replace("[", ".").replace("]", "").split("."):
            part = part.strip()
            if not part:
                continue
            if isinstance(obj, list):
                if part.isdigit():
                    obj = obj[int(part)]
                else:
                    obj = [dig(x, part) for x in obj]
                    continue
            elif isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return None
            if obj is None:
                return None
        return obj

    val = dig(data, jsonpath)
    if isinstance(val, str):
        return [normalize_url(val, base)]
    if isinstance(val, list):
        return [normalize_url(v, base) for v in val if isinstance(v, str)]
    return []


def extract_playinfo(html: str) -> list[tuple[str, str]]:
    """解析 window.__playinfo__ 内嵌播放数据(B站等),返回 [(地址, 类型)]。

    类型: video=视频流, audio=音频流, unknown=未标注。
    """
    import json
    m = re.search(r"window\.__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    found: list[tuple[str, str]] = []

    def walk(obj, kind):
        if isinstance(obj, dict):
            for k in ("video", "audio"):
                lst = obj.get(k)
                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                    for key in ("baseUrl", "base_url", "url"):
                        v = lst[0].get(key)
                        if isinstance(v, str) and v.startswith("http"):
                            found.append((v, k))
                            break
            for k, v in obj.items():
                if k in ("baseUrl", "base_url", "url") and isinstance(v, str) and v.startswith("http"):
                    found.append((v, kind or "unknown"))
                elif k not in ("video", "audio"):
                    walk(v, kind)
        elif isinstance(obj, list):
            for x in obj:
                walk(x, kind)

    walk(data, "unknown")
    seen, out = set(), []
    for u, k in found:
        if u not in seen:
            seen.add(u)
            out.append((u, k))
    return out


def extract_generic(html: str, base: str, exclude: list[str] | None = None) -> list[str]:
    """通用启发式: <video>/<source> 标签属性 + 正则兜底。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []

    for node in soup.find_all(["video", "source", "a"]):
        for attr in ("src", "data-src", "href"):
            val = node.get(attr)
            if val and re.search(r"\.(m3u8|mp4|webm|mov|mkv|flv|m4v)(\?|$)", val, re.I):
                found.append(normalize_url(val, base))

    text = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    for rx in GENERIC_PATTERNS:
        found.extend(normalize_url(m, base) for m in rx.findall(text))

    hints = EXCLUDE_HINTS + tuple(exclude or [])
    dedup = []
    for u in found:
        if u in dedup:
            continue
        if any(h.lower() in u.lower() for h in hints):
            continue
        dedup.append(u)
    dedup.sort(key=prefer_order)
    return dedup


def wbi_sign(sess: requests.Session, params: dict) -> dict:
    """B 站 WBI 签名: 高画质请求需携带 w_rid 风控签名(算法同 yt-dlp)。"""
    import hashlib
    import time as _t
    import urllib.parse

    MIXIN_TABLE = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                   27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
                   37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
                   22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
    nav = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    if not wbi.get("img_url") or not wbi.get("sub_url"):
        raise SpiderError("获取 WBI 密钥失败")
    img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    mixin_key = "".join((img_key + sub_key)[i] for i in MIXIN_TABLE)[:32]

    signed = {**params, "wts": int(_t.time())}
    signed = {k: v for k, v in signed.items()
              if not re.search(r"[!'()*]", str(k) + str(v))}
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


QN_NAMES = {16: "360P", 32: "480P", 64: "720P", 80: "1080P", 112: "1080P+",
            116: "1080P60", 120: "4K", 125: "HDR", 126: "杜比视界", 127: "8K"}

AUDIO_IDS = {"hires": 30251, "dolby": 30250, "192k": 30280, "132k": 30232, "64k": 30216}
AUDIO_BW = {"hires": 9999999, "dolby": 9999999, "192k": 192000, "132k": 132000, "64k": 64000}
AUDIO_NAMES = {30216: "64K", 30232: "132K", 30280: "192K",
               30250: "杜比全景声", 30251: "Hi-Res 无损"}
AUDIO_CHOICES = ["auto"] + list(AUDIO_IDS)


def pick_audio_stream(auds, pref="auto"):
    """按音质档位选 DASH 音频流；指定 id 不存在时按 bandwidth 取最接近的一档。"""
    by_bw = sorted(auds, key=lambda a: (a.get("bandwidth") or 0, a.get("id") or 0),
                   reverse=True)
    if pref in (None, "", "auto"):
        return by_bw[0]
    want = AUDIO_IDS.get(pref)
    if want:
        for a in by_bw:
            if a.get("id") == want:
                return a
    target = AUDIO_BW.get(pref, 192000)
    return min(auds, key=lambda a: abs((a.get("bandwidth") or 0) - target))


def audio_label(a):
    return "%s (%s kbps)" % (AUDIO_NAMES.get(a.get("id"), "id=%s" % a.get("id")),
                             (a.get("bandwidth") or 0) // 1000)


def bilibili_playurl(bvid: str, cid: int, sess: requests.Session,
                     quality: int | None = None, audio_quality: str = "auto",
                     only: str = "both") -> list[tuple[str, str]]:
    """按 bvid+cid 调 playurl 接口获取音视频流(带 Cookie 时自动 WBI 签名)。

    quality       : 画质上限 qn(如 80=1080P)；None 为账号可用的最高画质
    audio_quality : auto / hires / dolby / 192k / 132k / 64k
    only          : both=音视频都要 / video=仅视频 / audio=仅音频
    """
    api_params = {"bvid": bvid, "cid": cid, "fnval": 4048, "fnver": 0, "fourk": 1}
    if sess.headers.get("Cookie"):
        print("  [i] 已检测到登录 Cookie,使用 WBI 签名获取最高画质...")
        try:
            api_params = wbi_sign(sess, api_params)
        except Exception as e:
            print(f"  [!] WBI 签名失败({e}),退回未签名请求(可能仅 480P)")
    j = sess.get("https://api.bilibili.com/x/player/playurl",
                 params=api_params, timeout=20).json()
    if j.get("code") != 0:
        msg = j.get("message") or ""
        hint = ""
        if "啥都木有" in msg or j.get("code") == -10403:
            hint = "(该内容可能为大会员/付费专享,普通账号无权播放)"
        raise SpiderError(f"B 站: 获取播放地址失败: {msg} {hint}".strip())
    d = j.get("data") or {}
    dash = d.get("dash") or {}
    vids = dash.get("video") or []
    auds = dash.get("audio") or []

    if len(vids) > 1:
        names = ", ".join(QN_NAMES.get(q, str(q))
                          for q in sorted({v.get("id") for v in vids}, reverse=True))
        print(f"  [i] 可用清晰度: {names}")
    if len(auds) > 1:
        print("  [i] 可用音质: " + ", ".join(
            audio_label(a) for a in sorted(auds, key=lambda x: x.get("bandwidth") or 0, reverse=True)))

    out: list[tuple[str, str]] = []
    if only in ("both", "video") and vids:
        sorted_v = sorted(vids, key=lambda v: v.get("id") or 0, reverse=True)
        chosen = next((v for v in sorted_v
                       if quality is None or (v.get("id") or 0) <= quality), sorted_v[-1])
        url = chosen.get("baseUrl") or chosen.get("base_url") or (chosen.get("backupUrl") or [None])[0]
        if url:
            print(f"  [i] 采用画质: {QN_NAMES.get(chosen.get('id'), chosen.get('id'))}")
            out.append((url, "video"))
    if only in ("both", "audio") and auds:
        chosen_a = pick_audio_stream(auds, audio_quality)
        url = (chosen_a.get("baseUrl") or chosen_a.get("base_url")
               or (chosen_a.get("backupUrl") or [None])[0])
        if url:
            print(f"  [i] 采用音质: {audio_label(chosen_a)}")
            out.append((url, "audio"))
    if not out:
        for u in d.get("durl") or []:
            if u.get("url"):
                out.append((u["url"], "unknown"))
    if not out and only == "audio":
        raise SpiderError("B 站: 该视频没有独立音频流，仅音频模式无法产出文件")
    return out


def bilibili_season(page_url: str, sess: requests.Session, ep_choice: int):
    """解析番剧链接(/bangumi/play/ssxxxx 或 epxxxx),返回 (剧集列表, 选中的一集)。

    选中集信息: {bvid, cid, ep_id, title, long_title, ...}
    """
    m_ep = re.search(r"/ep(\d+)", page_url)
    m_ss = re.search(r"/ss(\d+)", page_url)
    if not (m_ep or m_ss):
        return None
    api = ("https://api.bilibili.com/pgc/view/web/season?"
           + (f"ep_id={m_ep.group(1)}" if m_ep else f"season_id={m_ss.group(1)}"))
    j = sess.get(api, timeout=20).json()
    if j.get("code") != 0:
        raise SpiderError(f"B 站番剧: {j.get('message')}")
    res = j.get("result") or {}
    eps = res.get("episodes") or []
    if not eps:
        raise SpiderError("B 站番剧: 未获取到剧集列表")

    if m_ep:
        ep_id = int(m_ep.group(1))
        idx = next((i for i, e in enumerate(eps)
                    if str(e.get("ep_id")) == str(ep_id)), 0)
    else:
        # 优先按集数(title 字段)匹配,如第 3 集 → title=="3"
        idx = next((i for i, e in enumerate(eps)
                    if str(e.get("title")) == str(ep_choice)), None)
        if idx is None:
            idx = ep_choice - 1 if 1 <= ep_choice <= len(eps) else 0
    chosen = eps[idx]
    return res, eps, idx, chosen


def extract_bilibili(page_url: str, sess: requests.Session,
                     quality: int | None = None, audio_quality: str = "auto",
                     only: str = "both") -> list[tuple[str, str]]:
    """B 站专用: 通过官方 API 获取视频/音频流地址(dash 格式)。

    页面 HTML 中不含播放数据(未登录时 window.__playinfo__ 仅为变量引用),
    需调用 view 接口拿 cid,再调 playurl 接口获取真实流地址。
    已登录(配置 Cookie)时自动使用 WBI 签名请求,可获取账号允许的最高画质。
    """
    m = re.search(r"(BV[0-9A-Za-z]+)", page_url)
    if not m:
        raise SpiderError("B 站链接中未找到 BV 号")
    bvid = m.group(1)

    r = sess.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=20)
    data = (r.json() or {}).get("data") or {}
    cid = data.get("cid")
    if not cid:
        raise SpiderError(f"B 站: 获取 cid 失败(视频可能已失效或需登录): {data.get('message')}")

    return bilibili_playurl(bvid, cid, sess, quality, audio_quality, only)


def extract_video_urls(html: str, url: str, site: dict | None) -> list[tuple[str, str]]:
    """提取视频地址,返回 [(地址, 类型)];类型: video / audio / unknown。"""
    base = url

    # 1) 内嵌播放数据(B站等 window.__playinfo__),命中直接返回
    results = extract_playinfo(html)
    if results:
        results.sort(key=lambda x: 0 if x[1] == "video" else (1 if x[1] == "unknown" else 2))
        return results

    # 2) 站点配置提取器
    raw: list[str] = []
    if site and site.get("extractors"):
        for ex in site["extractors"]:
            kind = ex.get("type")
            try:
                if kind == "regex":
                    raw = extract_by_regex(html, ex["pattern"])
                elif kind == "css":
                    raw = extract_by_css(html, ex["selector"], ex.get("attribute"), base)
                elif kind == "xpath":
                    raw = extract_by_xpath(html, ex["xpath"], base)
                elif kind == "json":
                    raw = extract_by_json(html, ex.get("jsonpath", ""), base)
                else:
                    continue
            except SpiderError as e:
                print(f"  [!] 提取器 {kind} 出错: {e}")
                continue
            if raw:
                break

    # 3) 通用启发式兜底
    if not raw:
        raw = extract_generic(html, base, (site or {}).get("exclude_patterns"))

    # 站点级排除
    for pat in (site or {}).get("exclude_patterns", []):
        raw = [u for u in raw if pat.lower() not in u.lower()]

    seen, order = set(), []
    for u in raw:
        if u not in seen:
            seen.add(u)
            order.append((u, "unknown"))
    order.sort(key=lambda x: prefer_order(x[0]))
    return order


# ---------------------------------------------------------------------------
# 下载器
# ---------------------------------------------------------------------------
def download_direct(url: str, dest: str, sess: requests.Session, referer: str | None = None) -> str:
    """直链下载,支持 Range 断点续传;已下载完整则跳过。"""
    ref_hdr = {"Referer": referer} if referer else {}

    # 先探测远程大小,判断是否已完整下载
    try:
        head = sess.head(url, headers=ref_hdr, timeout=15, allow_redirects=True)
        remote = int(head.headers.get("Content-Length") or 0)
    except (requests.RequestException, ValueError):
        remote = 0
    if remote and os.path.exists(dest) and os.path.getsize(dest) >= remote:
        print(f"  [i] 文件已存在且完整,跳过: {dest}")
        return dest

    headers = dict(ref_hdr)
    if os.path.exists(dest):
        size = os.path.getsize(dest)
        if size > 0:
            headers["Range"] = f"bytes={size}-"
    else:
        size = 0

    resp = fetch(url, sess, referer=referer)
    total = int(resp.headers.get("Content-Length") or 0) + size
    mode = "ab" if (headers.get("Range") and resp.status_code == 206) else "wb"
    if mode == "wb":
        size = 0

    last = time.time()
    with open(dest, mode) as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if not chunk:
                continue
            f.write(chunk)
            size += len(chunk)
            now = time.time()
            if now - last >= 1.0:
                _progress("下载中", size, total)
                last = now
    _progress("完成  ", size, total)
    print()
    return dest


def parse_m3u8(text: str) -> tuple[bool, list[str]]:
    """解析 m3u8 内容,返回 (是否 master, 分段URL列表)。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    is_master = any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines)
    segments = [ln for ln in lines if not ln.startswith("#")]
    if any(ln.startswith("#EXT-X-KEY") and "METHOD=NONE" not in ln for ln in lines):
        raise SpiderError("该 m3u8 流已加密(AES-128)。请使用 ffmpeg 下载:\n"
                          f'  ffmpeg -i "<m3u8_url>" -c copy out.mp4')
    return is_master, segments


def pick_master_stream(master_text: str, base: str) -> str:
    """从 master 播放列表里挑出最清晰的子流(优先 RESOLUTION,否则按 BANDWIDTH)。"""
    best, best_url = -1, None
    for m in re.finditer(r"#EXT-X-STREAM-INF:[^\n]*\n(\S+)", master_text):
        info = m.group(0)
        res = re.search(r"RESOLUTION=(\d+)x(\d+)", info)
        bw = re.search(r"BANDWIDTH=(\d+)", info)
        if res:
            score = int(res.group(1)) * int(res.group(2))
        elif bw:
            score = int(bw.group(1))
        else:
            continue
        if score > best:
            best, best_url = score, m.group(1)
    if not best_url:
        raise SpiderError("master 播放列表解析失败,未找到子流")
    print(f"  [i] 选择最高码率子流: {best_url}")
    return normalize_url(best_url, base)


def download_m3u8(url: str, dest: str, sess: requests.Session,
                  workers: int = 8, referer: str | None = None) -> str:
    """下载 m3u8 流:解析 -> 多线程拉分片 -> 顺序合并。"""
    resp = fetch(url, sess, referer=referer)
    text = resp.text
    base = url

    is_master, _ = parse_m3u8(text)
    if is_master:
        print("  [i] 检测到 master 播放列表,自动选择最高清晰度...")
        url = pick_master_stream(text, base)
        resp = fetch(url, sess, referer=referer)
        text = resp.text
        base = url

    _, segments = parse_m3u8(text)
    if not segments:
        raise SpiderError("m3u8 中未找到任何视频分片")

    print(f"  [i] 共 {len(segments)} 个分片,{workers} 线程下载")

    tmpdir = tempfile.mkdtemp(prefix="m3u8_")
    parts: list[str] = []
    lock = threading.Lock()
    done = [0]

    def grab(i, seg_url):
        seg_abs = normalize_url(seg_url, base)
        part_path = os.path.join(tmpdir, f"{i:06d}.ts")
        r = fetch(seg_abs, sess, referer=referer)
        with open(part_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
        with lock:
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == len(segments):
                print(f"\r  [i] 分片 {done[0]}/{len(segments)}", end="", flush=True)
        return i, part_path

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(grab, i, seg): i for i, seg in enumerate(segments)}
        for fut in as_completed(futures):
            i, path = fut.result()
            parts.append((i, path))
    print()

    parts.sort(key=lambda x: x[0])
    with open(dest, "wb") as out:
        for _, part in parts:
            with open(part, "rb") as pf:
                out.write(pf.read())
    for _, part in parts:
        os.remove(part)
    os.rmdir(tmpdir)
    print(f"  [✓] 合并完成: {dest}")
    return dest


def _find_ffmpeg() -> str | None:
    """定位 ffmpeg:PATH 优先,兜底检查常见安装位置(winget 等)。"""
    import glob as _glob
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return p
    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Packages",
                     "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg*\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe", r"D:\ffmpeg*\bin\ffmpeg.exe",
    ]
    for pat in patterns:
        hits = _glob.glob(pat)
        if hits:
            return hits[0]
    return None


def _merge_av(video_path: str, audio_path: str, out_path: str) -> str:
    """合并音视频流(需要 ffmpeg);无 ffmpeg 时保留两个文件并给出提示。"""
    import subprocess
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print(f"  [!] 检测到音视频分离。未找到 ffmpeg,已分别保存:")
        print(f"       视频: {video_path}")
        print(f"       音频: {audio_path}")
        print(f"     安装 ffmpeg 后可用以下命令合并:")
        print(f'       ffmpeg -i "{os.path.basename(video_path)}" -i "{os.path.basename(audio_path)}" -c copy out.mp4')
        return video_path
    print("  [i] 使用 ffmpeg 合并音视频...")
    r = subprocess.run([ffmpeg, "-y", "-i", video_path, "-i", audio_path,
                        "-c", "copy", out_path],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(out_path):
        os.remove(video_path)
        os.remove(audio_path)
        print(f"  [✓] 音视频已合并 -> {os.path.basename(out_path)}")
        return out_path
    print(f"  [!] ffmpeg 合并失败,保留两个文件:\n       视频: {video_path}\n       音频: {audio_path}")
    return video_path


def _extract_track(src: str, only: str) -> str:
    """从音视频混合文件中无损抽出视频轨或音频轨(需要 ffmpeg)。"""
    import subprocess
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("  [!] 未找到 ffmpeg，无法抽取单轨，保留原始混合文件")
        return src
    suffix = " (仅视频).mp4" if only == "video" else " (仅音频).m4a"
    out = os.path.splitext(src)[0] + suffix
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-c", "copy"]
    cmd.append("-an" if only == "video" else "-vn")
    cmd.append(out)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(out):
        os.remove(src)
        print(f"  [✓] 已抽取{'视频' if only == 'video' else '音频'}轨 -> {os.path.basename(out)}")
        return out
    print(f"  [!] 抽轨失败({r.stderr.strip()[-200:]})，保留原始混合文件")
    return src


def _progress(label: str, current: int, total: int):
    if total:
        pct = current * 100 / total
        print(f"\r  [{label}] {current/1e6:.1f}/{total/1e6:.1f} MB ({pct:.1f}%)", end="", flush=True)
    else:
        print(f"\r  [{label}] {current/1e6:.1f} MB", end="", flush=True)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    if not os.path.exists(path):
        return {"sites": []}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"sites": []}


def find_site(url: str, sites: list[dict], forced: str | None) -> dict | None:
    if forced:
        for s in sites:
            if s.get("name") == forced:
                return s
        raise SpiderError(f"配置中不存在站点规则: {forced}")
    for s in sites:
        match = s.get("match")
        if match and match in url:
            return s
    return None


def main():
    ap = argparse.ArgumentParser(
        description="通用视频资源爬虫(配置驱动,支持直链与 m3u8 流)")
    ap.add_argument("url", nargs="?", help="目标页面 URL")
    ap.add_argument("--site", help="强制使用指定站点规则(见 config.yaml 的 name)")
    ap.add_argument("-c", "--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"),
                    help="站点配置文件路径(默认 ./config.yaml)")
    ap.add_argument("-o", "--output", default="downloads",
                    help="输出目录(默认 ./downloads)")
    ap.add_argument("-n", "--workers", type=int, default=8, help="m3u8 分片下载线程数(默认 8)")
    ap.add_argument("--proxy", help="HTTP(S) 代理,如 http://127.0.0.1:7890")
    ap.add_argument("--list-sites", action="store_true", help="列出已配置的站点规则")
    ap.add_argument("--index", type=int, default=0, help="页面存在多个视频时,选择第几个(默认 0)")
    ap.add_argument("--ep", type=int, default=1, help="番剧链接(ss/xx)时下载第几集(默认 1)")
    ap.add_argument("--only", choices=["both", "video", "audio"], default="both",
                    help="下载内容: both=音视频合并(默认) / video=仅视频 / audio=仅音频")
    ap.add_argument("--quality", type=int, default=None,
                    help="画质上限 qn(16/32/64/80/112/116/120/127)，默认自动最高")
    ap.add_argument("--audio-quality", choices=AUDIO_CHOICES, default="auto",
                    help="音质: auto=自动最高 / hires=Hi-Res / dolby=杜比 / 192k / 132k / 64k")
    args = ap.parse_args()

    if args.list_sites:
        cfg = load_config(args.config)
        if not cfg["sites"]:
            print("(未配置任何站点规则,脚本将使用通用启发式抓取)")
        for s in cfg["sites"]:
            print(f"  - {s.get('name')}: match={s.get('match')!r}")
        return

    if not args.url:
        ap.print_help()
        sys.exit(1)
    print("[模式] 下载内容: %s | 画质上限: %s | 音质: %s"
          % ({"both": "音视频合并", "video": "仅视频", "audio": "仅音频"}[args.only],
             QN_NAMES.get(args.quality, "自动最高") if args.quality else "自动最高",
             "自动最高" if args.audio_quality == "auto" else args.audio_quality))

    # 清理浏览器复制链接时残留的末尾裸问号(如 .../? 或 .../ss109700? → 去掉末尾 ?)
    if args.url.endswith("?"):
        args.url = args.url.rstrip("?")

    cfg = load_config(args.config)
    site = find_site(args.url, cfg["sites"], args.site)
    if site:
        print(f"[i] 匹配站点规则: {site.get('name')}")
    else:
        print("[i] 未匹配到站点规则,使用通用启发式")

    sess = http_session(site, args.proxy)

    # ---- 快速路径:URL 本身就是视频文件/流的直链,跳过页面解析 ----
    raw_path = urlparse(args.url).path.lower()
    if raw_path.endswith(DIRECT_VIDEO_EXT) or ".m3u8" in args.url:
        print(f"[i] URL 为视频直链,直接下载: {args.url}")
        os.makedirs(args.output, exist_ok=True)
        is_hls = ".m3u8" in args.url
        ext = os.path.splitext(raw_path)[1].lower()
        if is_hls or not ext or ext not in DIRECT_VIDEO_EXT:
            ext = ".ts" if is_hls else ".mp4"
        name = os.path.splitext(os.path.basename(unquote(raw_path)))[0] or "video"
        dest = os.path.join(args.output, sanitize_filename(name) + ext)
        try:
            if is_hls:
                download_m3u8(args.url, dest, sess, workers=args.workers,
                              referer=(site or {}).get("headers", {}).get("Referer"))
            else:
                download_direct(args.url, dest, sess,
                                referer=(site or {}).get("headers", {}).get("Referer"))
            if args.only != "both":
                dest = _extract_track(dest, args.only)
            print(f"[✓] 完成 -> {os.path.abspath(dest)}")
        except SpiderError as e:
            print(f"[✗] 失败: {e}")
            sys.exit(1)
        return

    print(f"[i] 抓取页面: {args.url}")
    resp = fetch(args.url, sess, referer=(site or {}).get("headers", {}).get("Referer"))
    html = resp.text

    title = title_from_html(html, args.url)
    print(f"[i] 页面标题: {title}")

    if "bilibili.com" in args.url:
        season_info = None
        try:
            if re.search(r"/(ep|ss)\d+", args.url):
                season_info = bilibili_season(args.url, sess, args.ep)
            if season_info:
                res, eps, idx, chosen = season_info
                # 集数在 API 的 title 字段中(列表顺序与集数无关)
                ep_no = chosen.get("title") or str(idx + 1)
                ep_title = chosen.get("long_title") or ""
                print(f"[i] 番剧《{res.get('title', '')}》共 {len(eps)} 集,选择第 {ep_no} 集: {ep_title}")
                badge = chosen.get("badge") or ""
                if badge:
                    print(f"  [!] 该集标记为「{badge}」专享,普通账号可能无法获取播放地址")
                title = f"{res.get('title', '')} 第{ep_no}集 {ep_title}".strip()
                urls = bilibili_playurl(chosen.get("bvid"), chosen.get("cid"), sess,
                                        args.quality, args.audio_quality, args.only)
            else:
                print("[i] 检测到 B 站,使用官方 API 获取播放地址...")
                urls = extract_bilibili(args.url, sess, args.quality,
                                        args.audio_quality, args.only)
        except SpiderError as e:
            print(f"[✗] {e}")
            sys.exit(1)
    else:
        urls = extract_video_urls(html, args.url, site)
    if not urls:
        print("[!] 未提取到视频地址。可能原因:")
        print("     1. 视频地址由 JS 动态生成 → 需要 --site 配置或换浏览器自动化方案")
        print("     2. 页面需要登录/Cookie → 在站点规则 headers 中补充")
        sys.exit(1)

    print(f"[i] 找到 {len(urls)} 个候选视频:")
    kind_tag = {"video": "视频流", "audio": "音频流", "unknown": ""}
    for i, (u, k) in enumerate(urls):
        tag = kind_tag.get(k, "")
        print(f"     [{i}] {u[:100]} {tag}")
    pick = args.index if 0 <= args.index < len(urls) else 0
    if args.only == "audio":
        audio_idx = [i for i, (_, k) in enumerate(urls) if k == "audio"]
        if audio_idx:
            pick = audio_idx[0]
    target, tkind = urls[pick]
    print(f"[i] 选择: [{pick}] {target}")

    os.makedirs(args.output, exist_ok=True)
    ref = (site or {}).get("headers", {}).get("Referer")

    try:
        if ".m3u8" in target:
            dest = os.path.join(args.output, sanitize_filename(title) + ".ts")
            download_m3u8(target, dest, sess, workers=args.workers, referer=ref)
            if args.only != "both":
                dest = _extract_track(dest, args.only)
        else:
            # 单轨输出直接用可播放的后缀；B 站 DASH 流本身是 fMP4，改后缀即可播放
            if tkind == "audio":
                name = sanitize_filename(title) + " (仅音频).m4a"
            elif args.only == "video":
                name = sanitize_filename(title) + " (仅视频).mp4"
            else:
                name = sanitize_filename(title) + ".m4s"
            dest_v = os.path.join(args.output, name)
            download_direct(target, dest_v, sess, referer=ref)
            dest = dest_v

            audio_urls = [u for u, k in urls if k == "audio"]
            if tkind == "unknown" and args.only != "both":
                # 站点只给混合 MP4(durl)，用 ffmpeg 无损抽轨
                dest = _extract_track(dest_v, args.only)
            elif tkind == "video" and args.only == "both" and audio_urls:
                # 音视频分离(B 站 dash 流):同时下载音频并尝试合并
                dest_a = os.path.join(args.output, sanitize_filename(title) + "_audio.m4s")
                download_direct(audio_urls[0], dest_a, sess, referer=ref)
                dest = _merge_av(dest_v, dest_a, os.path.join(args.output, sanitize_filename(title) + ".mp4"))

        print(f"[✓] 完成 -> {os.path.abspath(dest)}")
    except SpiderError as e:
        print(f"[✗] 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

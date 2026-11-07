#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bilibili 视频下载器（充电专属 / 大会员电影 / 番剧 / 普通视频）
==============================================================

特性:
  * 支持 BV/av/番剧(ss)/单集(ep) 链接，自动跟随 b23.tv 短链跳转
  * 登录方式: cookies.txt(Netscape) / cookies.json / 原始 Cookie 字符串 / 扫码登录(--login)
  * 充电专属视频: 已购买充电的账号登录后即可下载完整内容与高画质
  * 会员电影/番剧: 大会员账号登录后可下载完整内容(默认最高可用画质)
  * 下载内容可选: 音视频合并(--only both,默认) / 仅视频(--only video) / 仅音频(--only audio)
  * 画质可选 -q，音质可选 --audio-quality auto|hires|dolby|192k|132k|64k
  * 下载后自动校验: ffprobe 检查时长(与官方时长对比)、音视频流完整性
  * DRM 检测: 若接口返回受 Widevine 保护的流，明确警告并跳过

依赖:
  pip install requests          # 必需
  pip install qrcode            # 可选, --login 扫码登录时打印二维码
  ffmpeg / ffprobe              # 合并与校验必需, 见 README 安装说明

用法示例:
  # 查看视频信息(不下载)
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --info

  # 用 cookies.txt 下载(推荐: 浏览器扩展导出 Netscape 格式)
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -c cookies.txt

  # 用原始 Cookie 字符串下载
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --cookie "SESSDATA=xx; bili_jct=yy"

  # 扫码登录后下载(登录态自动保存为 cookies.json)
  python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ss12345" --login

  # 只下指定分P / 指定集数
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -c cookies.txt -p "1,3-5"
  python bilibili_dl.py -u "https://www.bilibili.com/bangumi/play/ss12345" -c cookies.txt -p "1-3"

  # 指定画质上限(127=8K 120=4K 112=1080P+ 80=1080P 64=720P ...) 默认自动最高
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" -c cookies.txt -q 80

  # 只下视频 / 只下音频 / 指定音质
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --only video -q 80
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --only audio --audio-quality 192k
  python bilibili_dl.py -u "https://www.bilibili.com/video/BVxxxx" --info   # 列出可用画质与音质
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("[错误] 缺少 requests 库，请先执行: pip install requests")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
API = "https://api.bilibili.com"
APP = "https://app.bilibili.com"
PASSPORT = "https://passport.bilibili.com"

QN_MAP = {127: "8K 超高清", 126: "杜比视界", 125: "HDR 真彩", 120: "4K 超清",
          116: "1080P 高帧率", 112: "1080P 高码率", 100: "智能修复", 80: "1080P",
          74: "720P 高帧率", 64: "720P", 32: "480P", 16: "360P", 6: "240P"}

# DASH 音频档位：优先按 B 站音频流 id 精确匹配，找不到时按 bandwidth 取最接近的一档
AUDIO_IDS = {"hires": 30251, "dolby": 30250, "192k": 30280, "132k": 30232, "64k": 30216}
AUDIO_BW = {"hires": 9999999, "dolby": 9999999, "192k": 192000, "132k": 132000, "64k": 64000}
AUDIO_NAMES = {30216: "64K", 30232: "132K", 30280: "192K",
               30250: "杜比全景声", 30251: "Hi-Res 无损"}
AUDIO_CHOICES = ["auto"] + list(AUDIO_IDS)


class BiliError(Exception):
    pass


def eprint(*a):
    print(*a, file=sys.stderr)


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip(" .")
    return s[:120] or "untitled"


# ---------------------------------------------------------------- cookies
def parse_cookie_str(s):
    jar = requests.cookies.RequestsCookieJar()
    for part in s.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        jar.set(k.strip(), v.strip())
    return jar


def load_cookies(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    jar = requests.cookies.RequestsCookieJar()
    if path.lower().endswith(".json") or text.lstrip().startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict):
            for k, v in data.items():
                jar.set(k, str(v))
        elif isinstance(data, list):  # EditThisCookie 等扩展导出的格式
            for c in data:
                jar.set(c.get("name", ""), c.get("value", ""),
                        domain=c.get("domain", ""), path=c.get("path", "/"))
        return jar
    # Netscape cookies.txt
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        p = line.split("\t")
        if len(p) >= 7:
            jar.set(p[5], p[6], domain=p[0], path=p[2])
    return jar


# ---------------------------------------------------------------- session
class Bili:
    def __init__(self, cookie_jar=None, cookie_str=None, login=False):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA,
                               "Referer": "https://www.bilibili.com/",
                               "Origin": "https://www.bilibili.com"})
        if login:
            self.qr_login()
        elif cookie_str:
            self.s.cookies.update(parse_cookie_str(cookie_str))
        elif cookie_jar:
            self.s.cookies.update(cookie_jar)
        self.logged_in = bool(self.s.cookies.get("SESSDATA"))

    def get_json(self, url, params, referer=None):
        h = {"Referer": referer} if referer else None
        r = self.s.get(url, params=params, headers=h, timeout=30)
        try:
            j = r.json()
        except Exception:
            raise BiliError("接口返回异常: HTTP %s" % r.status_code)
        if j.get("code") != 0:
            raise BiliError("接口错误(code=%s): %s" % (j.get("code"), j.get("message")))
        return j

    # ------------------------------------------------------------ 扫码登录
    def qr_login(self):
        try:
            import qrcode  # noqa
            _has_qr = True
        except ImportError:
            _has_qr = False
        j = self.s.get(PASSPORT + "/x/passport-login/web/qrcode/generate", timeout=30).json()
        if j.get("code") != 0:
            raise BiliError("获取二维码失败: %s" % j.get("message"))
        key = j["data"]["qrcode_key"]
        url = j["data"]["url"]
        print("[登录] 请用 B 站手机 App 扫码授权:")
        if _has_qr:
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        else:
            print("[登录] (未安装 qrcode 库, 请打开链接授权) %s" % url)
        deadline = time.time() + 180
        last = ""
        while time.time() < deadline:
            try:
                r = self.s.get(PASSPORT + "/x/passport-login/web/qrcode/poll",
                               params={"qrcode_key": key}, timeout=30)
                p = r.json()
            except Exception as e:
                raise BiliError("扫码轮询失败: %s" % e)
            data = p.get("data") or {}
            code = data.get("code")
            if code == 0 and self.s.cookies.get("SESSDATA"):
                self.s.cookies.update(r.cookies)
                mid = data.get("mid", "?")
                print("\r[登录] 成功! 用户ID: %s        " % mid)
                self.logged_in = True
                return
            status = {86101: "等待扫码...", 86090: "已扫码，请在手机上确认",
                      86038: "二维码已过期"}.get(code, "code=%s" % code)
            if status != last:
                print("\r[登录] %s        " % status, end="", flush=True)
                last = status
            time.sleep(2)
        raise BiliError("扫码登录超时")


# ---------------------------------------------------------------- 解析链接
def resolve_url(url):
    r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=True, timeout=20)
    final = r.url
    path = urlparse(final).path
    m = re.search(r"BV[0-9A-Za-z]{10}", final)
    if m:
        return "bvid", m.group(0), final
    m = re.search(r"/bangumi/play/ep(\d+)", path)
    if m:
        return "ep", m.group(1), final
    m = re.search(r"/bangumi/play/ss(\d+)", path)
    if m:
        return "ss", m.group(1), final
    m = re.search(r"(?:^|/)av(\d+)", path, re.I)
    if m:
        return "av", m.group(1), final
    raise BiliError("无法识别的 B 站链接: %s" % url)


# ---------------------------------------------------------------- 获取剧集
def fetch_episodes(b, kind, ident):
    """返回 (作品标题, 剧集列表, 备注)"""
    if kind in ("bvid", "av"):
        params = {"bvid": ident} if kind == "bvid" else {"aid": ident}
        j = b.get_json(API + "/x/web-interface/view", params)
        d = j["data"]
        title = d.get("title", "")
        eps = [{"bvid": d.get("bvid"), "cid": p.get("cid"), "page": p.get("page", 1),
                "part": p.get("part") or "", "ep_id": None}
               for p in d.get("pages", [])]
        note = None
        if d.get("up_season") or d.get("season_id"):
            note = "该视频属于合集/充电系列(season_id=%s)，可能包含付费内容" % d.get("season_id")
        return title, eps, note
    params = {"ep_id": ident} if kind == "ep" else {"season_id": ident}
    j = b.get_json(API + "/pgc/view/web/season", params)
    r = j.get("result") or j.get("data") or {}
    title = r.get("title") or (r.get("new_ep") or {}).get("index_show", "") or "未命名"
    eps = []
    for i, e in enumerate(r.get("episodes", []), 1):
        part = (e.get("title") or "") + ((" " + e["long_title"]) if e.get("long_title") else "")
        eps.append({"bvid": e.get("bvid"), "cid": e.get("cid"), "ep_id": e.get("id"),
                    "page": i, "part": part.strip(), "badge": e.get("badge") or ""})
    if not eps:
        raise BiliError("该剧集没有可下载的集数(可能需要登录)")
    return title, eps, None


# ---------------------------------------------------------------- 播放地址
def get_play(b, kind, ep, qn):
    if kind in ("bvid", "av"):
        url = API + "/x/player/playurl"
        params = {"bvid": ep["bvid"], "cid": ep["cid"], "fnval": "4048",
                  "fnver": "0", "fourk": "1", "platform": "pc", "qn": str(qn)}
        referer = "https://www.bilibili.com/video/" + ep["bvid"]
    else:
        url = API + "/pgc/player/web/playurl"
        params = {"ep_id": ep["ep_id"], "fnval": "4048", "fnver": "0",
                  "fourk": "1", "platform": "pc", "qn": str(qn)}
        referer = "https://www.bilibili.com/bangumi/play/ep" + str(ep["ep_id"])
    j = b.get_json(url, params, referer=referer)
    return (j.get("data") or j.get("result") or {}), referer


def is_drm(d):
    if d.get("drm_tech_type"):
        return True
    dash = d.get("dash") or {}
    for v in dash.get("video", []) or []:
        if v.get("drm"):
            return True
    return False


def pick_audio(auds, pref="auto"):
    """按音质档位选一条 DASH 音频流；指定 id 不存在时回退到码率最接近的一条。"""
    if not auds:
        return None
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
    if not a:
        return "无"
    return "%s (%s kbps)" % (AUDIO_NAMES.get(a.get("id"), "id=%s" % a.get("id")),
                             (a.get("bandwidth") or 0) // 1000)


def pick_streams(d, qn, audio_pref="auto"):
    """从 playurl 响应中选择流。返回 {kind: dash|mp4, ...}"""
    dash = d.get("dash")
    if dash:
        vids = sorted(dash.get("video", []) or [], key=lambda v: v["id"], reverse=True)
        if not vids:
            raise BiliError("DASH 无视频流")
        v = next((x for x in vids if x["id"] <= qn), vids[-1])
        a = pick_audio(dash.get("audio", []) or [], audio_pref)
        return {"kind": "dash", "video": v, "audio": a,
                "duration": dash.get("duration") or d.get("timelength", 0) / 1000,
                "quality": v["id"]}
    durl = d.get("durl")
    if durl:
        return {"kind": "mp4", "url": durl[0]["url"],
                "backup": durl[0].get("backup_url") or [],
                "duration": d.get("timelength", 0) / 1000,
                "quality": d.get("quality", 0)}
    raise BiliError("接口未返回任何可下载的流")


# ---------------------------------------------------------------- 下载
class Progress:
    def __init__(self, total, label):
        self.total = total
        self.label = label
        self.done = 0
        self.lock = Lock()
        self.t0 = time.time()
        self.last_t = self.t0
        self.last_n = 0

    def add(self, n):
        with self.lock:
            self.done += n
        now = time.time()
        if self.total and now - self.last_t >= 0.25:
            pct = self.done * 100.0 / self.total
            spd = (self.done - self.last_n) / (now - self.last_t)
            print("\r%s: %5.1f%%  %7.1f / %7.1f MB  %5.2f MB/s  "
                  % (self.label, pct, self.done / 1048576, self.total / 1048576, spd / 1048576),
                  end="", flush=True)
            self.last_t = now
            self.last_n = self.done

    def finish(self):
        if self.total:
            print("\r%s: 100.0%%  %7.1f / %7.1f MB            " % (self.label, self.total / 1048576, self.total / 1048576))
        else:
            print("\r%s: 完成              " % self.label)


def _cookie_header(b):
    cks = ["%s=%s" % (c.name, c.value) for c in b.s.cookies]
    return "; ".join(cks)


class RangeNotSupported(BiliError):
    pass


def _single_download(b, url, headers, dest, total, label):
    """单线程整流下载（不支持 Range 或分块失败时的兜底）"""
    r = b.s.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code not in (200, 206):
        raise BiliError("下载失败 HTTP %s" % r.status_code)
    if total <= 0:
        total = int(r.headers.get("Content-Length") or 0)
    prog = Progress(total, label)
    n = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            if chunk:
                f.write(chunk)
                n += len(chunk)
                prog.add(len(chunk))
    prog.finish()
    if total and n != total:
        raise BiliError("下载不完整: 期望 %d 字节, 实际 %d 字节" % (total, n))
    return n


def download_url(b, url, dest, referer, threads=8, label=""):
    """多线程分块下载，支持断点重试。返回最终字节数。"""
    headers = {"User-Agent": UA, "Referer": referer}
    cookie = _cookie_header(b)
    if cookie:
        headers["Cookie"] = cookie

    # 探测 Range 支持；注意 206 的 Content-Length 只是分片长度，
    # 总大小要从 Content-Range 解析（bytes 0-3/707009 -> 707009）
    total, supports_range = 0, False
    try:
        r = b.s.get(url, headers={**headers, "Range": "bytes=0-3"}, stream=True, timeout=20)
        if r.status_code == 206 and r.headers.get("Content-Range"):
            supports_range = True
            cr = r.headers["Content-Range"]
            try:
                total = int(cr.rsplit("/", 1)[-1])
            except ValueError:
                total = int(r.headers.get("Content-Length") or 0)
        else:
            total = int(r.headers.get("Content-Length") or 0)
        r.close()
    except Exception:
        supports_range = False
        total = 0

    if not supports_range or total <= 0:
        return _single_download(b, url, headers, dest, total, label)

    # 分块
    n = min(threads, max(1, total // (4 * 1048576) + 1))
    chunk = total // n
    parts = []
    for i in range(n):
        start = i * chunk
        end = total - 1 if i == n - 1 else (i + 1) * chunk - 1
        parts.append((start, end, "%s.part%d" % (dest, i)))

    prog = Progress(total, label)

    def work(p):
        start, end, pfile = p
        h = dict(headers)
        h["Range"] = "bytes=%d-%d" % (start, end)
        for attempt in range(3):
            try:
                r = b.s.get(url, headers=h, stream=True, timeout=120)
                if r.status_code == 200:  # 服务器忽略 Range 返回了完整文件
                    raise RangeNotSupported()
                if r.status_code not in (200, 206):
                    raise BiliError("HTTP %s" % r.status_code)
                with open(pfile, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if chunk:
                            f.write(chunk)
                            prog.add(len(chunk))
                if os.path.getsize(pfile) != (end - start + 1):
                    raise BiliError("分块大小不符")
                return
            except RangeNotSupported:
                raise
            except Exception as e:
                if attempt == 2:
                    raise BiliError("分块 %d-%d 下载失败: %s" % (start, end, e))
                time.sleep(1)

    try:
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(work, p) for p in parts]
            for f in as_completed(futs):
                f.result()  # 抛错即中断
    except RangeNotSupported:
        for _, _, pfile in parts:
            if os.path.exists(pfile):
                os.remove(pfile)
        print("  [提示] CDN 不支持分块，改用单线程下载")
        return _single_download(b, url, headers, dest, total, label)

    with open(dest, "wb") as out:
        for _, _, pfile in parts:
            with open(pfile, "rb") as pf:
                shutil.copyfileobj(pf, out)
            os.remove(pfile)
    prog.finish()
    return total


# ---------------------------------------------------------------- 合并校验
def find_ffmpeg(path=None):
    p = path or shutil.which("ffmpeg")
    if not p:
        raise BiliError(
            "未找到 ffmpeg！请安装后重试：\n"
            "  Windows: winget install ffmpeg  或  scoop install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg\n"
            "或用 --ffmpeg 指定 ffmpeg 所在路径。")
    return p


def _sanitize_m4s(path):
    """部分 CDN 的 m4s 带前导字节，去掉直到 ftyp 为止。"""
    with open(path, "rb") as f:
        head = f.read(1024)
    pos = head.find(b"ftyp")
    if 0 < pos < 512:
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[pos:])
        return True
    return False


def merge(ffmpeg, video, audio, out, vfmt="m4s"):
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-i", video, "-i", audio,
           "-c", "copy", "-movflags", "+faststart", out]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        # 尝试修正 m4s 前导字节
        fixed = _sanitize_m4s(video) | _sanitize_m4s(audio)
        if fixed:
            p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise BiliError("ffmpeg 合并失败: %s" % p.stderr.strip()[-800:])


def remux_single(ffmpeg, src, out):
    """把单条 DASH 流转封装成可直接播放的 mp4/m4a；没有 ffmpeg 时直接改后缀。"""
    if ffmpeg and os.path.exists(src):
        p = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src,
                            "-c", "copy", "-movflags", "+faststart", out],
                           capture_output=True, text=True)
        if p.returncode == 0 and os.path.exists(out):
            os.remove(src)
            return out
        print("[提示] 转封装失败，直接改后缀（fMP4 通常可直接播放）: %s"
              % p.stderr.strip()[-200:])
    os.replace(src, out)
    return out


def extract_track(ffmpeg, src, out, only):
    """从音视频混合的 MP4(durl) 中无损抽出指定轨道。"""
    if not ffmpeg:
        raise BiliError("抽取%s轨需要 ffmpeg（该视频只提供混合 MP4 流）"
                        % ("视频" if only == "video" else "音频"))
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-c", "copy"]
    cmd.append("-an" if only == "video" else "-vn")
    cmd.append(out)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out):
        raise BiliError("抽取%s轨失败: %s" % ("视频" if only == "video" else "音频",
                                            p.stderr.strip()[-300:]))
    os.remove(src)
    return out


def ffprobe(ffmpeg, path):
    exe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(exe):
        exe = shutil.which("ffprobe")
    if not exe:
        return None
    p = subprocess.run([exe, "-v", "error",
                        "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
                        "-of", "json", path], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def verify(path, expected_duration, ffmpeg, require_video=True):
    """校验输出文件。返回 (是否通过, 说明, 实际时长)"""
    size = os.path.getsize(path)
    base = "大小 %s | " % human_size(size)
    info = ffprobe(ffmpeg, path)
    if not info:
        return False, base + "ffprobe 无法解析（文件可能损坏）", None
    fmt = info.get("format", {})
    actual = float(fmt.get("duration") or 0)
    streams = [s for s in info.get("streams", []) if s.get("codec_type")]
    has_v = any(s["codec_type"] == "video" for s in streams)
    has_a = any(s["codec_type"] == "audio" for s in streams)
    tol = max(3.0, expected_duration * 0.03) if expected_duration else 5.0
    dur_ok = not expected_duration or abs(actual - expected_duration) <= tol
    if not dur_ok:
        return False, base + "时长不符: 期望 %.1fs 实际 %.1fs" % (expected_duration, actual), actual
    if require_video and not has_v:
        return False, base + "缺少视频流", actual
    if not require_video and not has_a:
        return False, base + "缺少音频流", actual
    detail = []
    for s in streams:
        if s["codec_type"] == "video":
            detail.append("视频 %sx%s" % (s.get("width", "?"), s.get("height", "?")))
        else:
            detail.append("音频 %s" % s.get("codec_name", "?"))
    return True, base + "时长 %.1fs | %s" % (actual, " | ".join(detail)), actual


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


# ---------------------------------------------------------------- 主流程
def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return sorted(set(x for x in out if 1 <= x <= total))


def main():
    ap = argparse.ArgumentParser(
        prog="bilibili_dl",
        description="B 站视频下载器：充电专属 / 大会员电影 / 番剧 / 普通视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("-u", "--url", help="B 站链接 (BV/av/番剧ss/单集ep)")
    ap.add_argument("-c", "--cookies", help="cookies 文件 (Netscape cookies.txt 或 json)")
    ap.add_argument("--cookie", help="直接传入 Cookie 字符串，如 \"SESSDATA=xx; bili_jct=yy\"")
    ap.add_argument("--login", action="store_true", help="扫码登录（登录态保存到 cookies.json）")
    ap.add_argument("-o", "--output", default="downloads", help="输出目录 (默认 downloads)")
    ap.add_argument("-q", "--quality", type=int, default=127, help="画质上限 (默认 127 自动最高)")
    ap.add_argument("--only", choices=["both", "video", "audio"], default="both",
                    help="下载内容: both=音视频合并(默认) / video=仅视频 / audio=仅音频")
    ap.add_argument("--audio-quality", choices=AUDIO_CHOICES, default="auto",
                    help="音质: auto=自动最高 / hires=Hi-Res / dolby=杜比 / 192k / 132k / 64k")
    ap.add_argument("-p", "--pages", help="选择分P/集数，如 \"1,3-5\"（默认全部）")
    ap.add_argument("--threads", type=int, default=8, help="下载线程数 (默认 8)")
    ap.add_argument("--info", action="store_true", help="只查看视频信息与可选画质，不下载")
    ap.add_argument("--diag", action="store_true", help="诊断账号登录/大会员/充电权限，判断能否获取完整流")
    ap.add_argument("--no-merge", action="store_true", help="保留分离的音视频文件，不合并")
    ap.add_argument("--keep", action="store_true", help="保留临时分片文件")
    ap.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径（自动合并校验需要）")
    args = ap.parse_args()

    if not args.url:
        ap.error("请用 -u/--url 提供 B 站链接")
    if args.quality not in QN_MAP and args.quality != 127:
        print("[提示] 未知画质码 %s，可用: %s" % (args.quality,
              ", ".join("%s=%s" % (k, v) for k, v in sorted(QN_MAP.items()))))

    # 登录
    b = None
    try:
        if args.login:
            b = Bili(login=True)
            with open(os.path.join(args.output, "cookies.json"), "w", encoding="utf-8") as f:
                json.dump(requests.utils.dict_from_cookiejar(b.s.cookies), f, indent=2)
            print("[登录] 登录态已保存: %s" % os.path.join(args.output, "cookies.json"))
        elif args.cookie:
            b = Bili(cookie_str=args.cookie)
        elif args.cookies:
            jar = load_cookies(args.cookies)
            b = Bili(cookie_jar=jar)
        else:
            b = Bili()
            print("[提示] 未提供登录信息。下载会员/充电内容需要登录（-c cookies.txt / --cookie / --login）。")
    except BiliError as e:
        sys.exit("[错误] %s" % e)

    if not b.logged_in:
        print("[提示] 当前未检测到 SESSDATA，会员/充电内容将只能获取试看或低画质流。")

    # 解析链接
    try:
        kind, ident, page_url = resolve_url(args.url)
    except BiliError as e:
        sys.exit("[错误] %s" % e)
    print("[解析] %s (%s=%s)" % (page_url, kind, ident))

    # 获取剧集
    try:
        title, eps, note = fetch_episodes(b, kind, ident)
    except BiliError as e:
        sys.exit("[错误] %s" % e)
    if note:
        print("[注意] %s" % note)
    print("[信息] %s | 共 %d 个分P/集" % (title, len(eps)))

    sel = parse_pages(args.pages, len(eps))
    eps = [e for e in eps if e["page"] in sel]
    if not eps:
        sys.exit("[错误] 选择的分P/集数无效")

    # ---- 权限诊断模式 ----
    if args.diag:
        print("\n===== 账号与权限诊断 =====")
        try:
            nav = b.get_json(API + "/x/web-interface/nav", {})
            nd = nav.get("data") or {}
            if not nd.get("isLogin"):
                print("  登录状态: 未登录（用 -c cookies.txt / --cookie / --login 登录）")
            else:
                vip = "大会员" if nd.get("vipStatus") == 1 else "普通用户"
                due = nd.get("vipDueDate") or nd.get("vip_label", {}).get("label", "")
                if due and isinstance(due, (int, float)) and due > 10000000000:
                    due = datetime.datetime.fromtimestamp(due / 1000).strftime("%Y-%m-%d")
                print("  登录用户: %s | %s%s"
                      % (nd.get("uname", "?"), vip,
                         (" (到期 %s)" % due) if due and isinstance(due, str) and due[:4].isdigit() else ""))
        except BiliError as e:
            print("  账号信息获取失败: %s" % e)
        try:
            d, _ = get_play(b, kind, eps[0], args.quality)
            if d.get("dash"):
                print("  可获取流: DASH 完整流 [OK] —— 该账号有权下载完整内容")
            elif d.get("durl"):
                print("  可获取流: 仅 durl 试看/低画质流 [FAIL] —— 该账号无权获取完整内容")
            else:
                print("  可获取流: 无")
            if d.get("quality"):
                print("  当前画质: %s(%s)" % (d.get("quality"), QN_MAP.get(d.get("quality"), "?")))
            if is_drm(d):
                print("  注意: 该内容受 DRM 保护（Widevine），无法直接解密下载")
        except BiliError as e:
            print("  流信息获取失败: %s" % e)
        print()
        return 0

    # --info 模式
    if args.info:
        try:
            d, _ = get_play(b, kind, eps[0], args.quality)
        except BiliError as e:
            print("[信息] 画质信息获取失败: %s" % e)
            d = {}
        if d.get("dash"):
            qs = sorted({v["id"] for v in d["dash"]["video"]}, reverse=True)
            print("[信息] 可选画质: %s" % ", ".join("%s(%s)" % (q, QN_MAP.get(q, "?")) for q in qs))
            auds = d["dash"].get("audio") or []
            if auds:
                print("[信息] 可选音质: %s" % ", ".join(
                    "%s (id=%s)" % (audio_label(a), a.get("id"))
                    for a in sorted(auds, key=lambda x: x.get("bandwidth") or 0, reverse=True)))
        elif d.get("durl"):
            print("[信息] 当前仅返回 MP4(durl) 流，画质 %s(%s)。登录并拥有权限后可得 DASH 高画质。"
                  % (d.get("quality"), QN_MAP.get(d.get("quality"), "?")))
        else:
            print("[信息] 该内容暂无可下载流（可能需登录/权限）。")
        if is_drm(d):
            print("[警告] 该内容受 DRM 保护，无法直接解密下载。")
        for e in eps:
            print("  - [%s] %s%s" % ("P%02d" % e["page"] if kind in ("bvid", "av") else "EP%02d" % e["page"],
                                     e["part"] or "(无标题)", (" [%s]" % e["badge"]) if e.get("badge") else ""))
        return 0

    # 下载
    os.makedirs(args.output, exist_ok=True)
    # 仅"音视频合并"强制需要 ffmpeg；仅视频/仅音频/不合并时 ffmpeg 只是可选的转封装工具
    try:
        ffmpeg = find_ffmpeg(args.ffmpeg)
    except BiliError:
        ffmpeg = None
    if args.only == "both" and not args.no_merge and not ffmpeg:
        sys.exit("[错误] 未找到 ffmpeg（音视频合并需要）\n"
                 "安装: winget install ffmpeg；或改用 --only video / --only audio / --no-merge")
    print("[模式] 下载内容: %s | 画质上限: %s(%s) | 音质: %s"
          % ({"both": "音视频合并", "video": "仅视频", "audio": "仅音频"}[args.only],
             args.quality, QN_MAP.get(args.quality, "自动最高"),
             "自动最高" if args.audio_quality == "auto" else args.audio_quality))

    results = []
    for idx, ep in enumerate(eps, 1):
        tag = "P%02d" % ep["page"] if kind in ("bvid", "av") else "EP%02d" % ep["page"]
        print("\n===== [%d/%d] %s | %s =====" % (idx, len(eps), tag, ep["part"] or title))
        try:
            d, referer = get_play(b, kind, ep, args.quality)
            if is_drm(d):
                print("[警告] 该内容受 DRM 保护（Widevine），无法直接解密，跳过。")
                results.append((tag, False, "DRM 保护"))
                continue
            st = pick_streams(d, args.quality, args.audio_quality)
            qn = st["quality"]
            print("[流] 画质 %s(%s) | 音质 %s | 时长 %.1fs | 类型 %s"
                  % (qn, QN_MAP.get(qn, "?"), audio_label(st.get("audio")),
                     st["duration"], st["kind"].upper()))
            if st["kind"] == "mp4" and qn < 80 and not b.logged_in:
                print("[提示] 未登录仅获取到 %s 流；登录后可获得更高画质。" % QN_MAP.get(qn, qn))

            base = safe_name(title)
            if len(eps) > 1:
                base = "%s - %s" % (base, tag)
            out_dir = os.path.join(args.output, safe_name(title))
            os.makedirs(out_dir, exist_ok=True)

            only = args.only
            if st["kind"] == "mp4":
                # durl 是音视频混合的 MP4，选单轨时用 ffmpeg 无损抽轨
                out = os.path.join(out_dir, base + ".mp4")
                label = "%s [mp4]" % tag
                download_url(b, st["url"], out, referer, args.threads, label)
                if only in ("video", "audio"):
                    suffix = " (仅视频).mp4" if only == "video" else " (仅音频).m4a"
                    out = extract_track(ffmpeg, out, os.path.join(out_dir, base + suffix), only)
                ok, msg, actual = (verify(out, st["duration"], ffmpeg,
                                          require_video=(only != "audio"))
                                   if ffmpeg else
                                   (True, human_size(os.path.getsize(out)), None))
            else:
                v = st["video"]
                vu = v.get("baseUrl") or (v.get("backupUrl") or [""])[0]
                vpath = os.path.join(out_dir, base + ".video.m4s")
                apath = os.path.join(out_dir, base + ".audio.m4s")
                need_v = only in ("both", "video")
                need_a = only in ("both", "audio")

                if need_v:
                    print("[下载] 视频流 %s%s" % (QN_MAP.get(v["id"], v["id"]),
                                                 " (HDR)" if v.get("codecid") == 12 else ""))
                    download_url(b, vu, vpath, referer, args.threads, "%s 视频" % tag)
                else:
                    vpath = None

                if need_a:
                    if not st["audio"]:
                        print("[失败] 该视频没有独立音频流，仅音频模式无法产出文件")
                        if vpath and os.path.exists(vpath):
                            os.remove(vpath)
                        results.append((tag, False, "无音频流"))
                        continue
                    au = st["audio"].get("baseUrl") or (st["audio"].get("backupUrl") or [""])[0]
                    print("[下载] 音频流 %s" % audio_label(st["audio"]))
                    download_url(b, au, apath, referer, args.threads, "%s 音频" % tag)
                else:
                    apath = None

                if only == "both" and (args.no_merge or not ffmpeg):
                    print("[结果] 已保存分离文件到 %s (未合并)" % out_dir)
                    results.append((tag, True, "分离文件"))
                    continue

                if only == "both":
                    out = os.path.join(out_dir, base + ".mp4")
                    print("[合并] ffmpeg ...")
                    merge(ffmpeg, vpath, apath, out)
                    ok, msg, actual = verify(out, st["duration"], ffmpeg)
                    if not args.keep:
                        for f in (vpath, apath):
                            if f and os.path.exists(f):
                                os.remove(f)
                else:
                    src = vpath if only == "video" else apath
                    suffix = " (仅视频).mp4" if only == "video" else " (仅音频).m4a"
                    out = os.path.join(out_dir, base + suffix)
                    out = remux_single(ffmpeg, src, out)
                    ok, msg, actual = (verify(out, st["duration"], ffmpeg,
                                              require_video=(only == "video"))
                                       if ffmpeg else
                                       (True, human_size(os.path.getsize(out)), None))
            results.append((tag, ok, msg))
            print("[%s] %s" % ("通过" if ok else "失败", msg))
            if not ok and st["duration"] and actual is not None and actual < st["duration"] * 0.8:
                print("  [提示] 下载到的是试看/不完整片段（%.1fs < 完整 %.1fs）。"
                      "请确认账号已登录（-c cookies.txt / --cookie / --login）且拥有充电/大会员权限后重试。"
                      % (actual, st["duration"]))
        except BiliError as e:
            print("[失败] %s" % e)
            results.append((tag, False, str(e)))
        except Exception as e:
            print("[失败] 未预期异常: %s" % e)
            results.append((tag, False, str(e)))

    # 汇总
    print("\n================ 下载汇总 ================")
    failed = 0
    for tag, ok, msg in results:
        print("  [%s] %s | %s" % ("OK " if ok else "FAIL", tag, msg[:160]))
        if not ok:
            failed += 1
    print("完成: %d 成功 / %d 失败" % (len(results) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

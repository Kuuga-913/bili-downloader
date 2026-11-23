# -*- coding: utf-8 -*-
"""B 站音视频下载器 · Bili-Pulse Cyber Studio 图形界面启动器

架构：
  - 内置轻量级本地 HTTP API 服务 (127.0.0.1 随机空闲端口，仅标准库)
  - 优先以 Edge / Chrome `--app` 无边框桌面应用模式启动独立窗口，缺省时回退默认浏览器
  - 完整包装两个下载内核：
      * bilibili-downloader: 会员 / 充电 / 番剧专用，ffmpeg 无损混流 + ffprobe 校验
      * video_spider       : 通用爬虫（直链 / m3u8 / B 站）
  - 支持 `--no-browser`（仅启动服务，用于调试/测试）及 `--tk`（极简 Tk 回退模式）
"""
from __future__ import annotations

import codecs
import http.server
import json
import mimetypes
import os
import re
import shlex
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

# 路径自动探测：本文件位于 gui/，其上一级即项目根目录
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(GUI_DIR, "web")
BASE_DIR = os.path.dirname(GUI_DIR)
BILI_DIR = os.path.join(BASE_DIR, "bilibili-downloader")
SPIDER_DIR = os.path.join(BASE_DIR, "video_spider")

_py = os.environ.get("BILI_PYTHON") or sys.executable
if os.name == "nt" and _py.lower().endswith("pythonw.exe"):
    _cand = os.path.join(os.path.dirname(_py), "python.exe")
    if os.path.exists(_cand):
        _py = _cand

PY_SPIDER = PY_BILI = _py
SPIDER = os.path.join(SPIDER_DIR, "spider.py")
BILI = os.path.join(BILI_DIR, "bilibili_dl.py")
BAT_SPIDER = os.path.join(SPIDER_DIR, "run.bat")
BAT_BILI = os.path.join(BILI_DIR, "run.bat")
OUT_SPIDER = os.path.join(BASE_DIR, "downloads")
OUT_BILI = os.path.join(BILI_DIR, "downloads")
QR_LOGIN = os.path.join(BILI_DIR, "bili_login_qr.py")
QR_IMAGE = os.path.join(BILI_DIR, "qrcode.png")
COOKIE_STRING = os.path.join(BILI_DIR, "cookie_string.txt")

TOOLS = {
    "spider": {
        "name": "Spider-Core 通用引擎",
        "label": "video_spider（通用 / 直链 / m3u8 / B 站）",
        "py": PY_SPIDER,
        "script": SPIDER,
        "bat": BAT_SPIDER,
        "out": OUT_SPIDER,
    },
    "bili": {
        "name": "Bili-Core 专用引擎",
        "label": "bilibili-downloader（会员 / 充电 / 番剧，自动合并校验）",
        "py": PY_BILI,
        "script": BILI,
        "bat": BAT_BILI,
        "out": OUT_BILI,
    },
}

MODE_OPTIONS = [
    ("音视频合并（默认，合成 mp4）", "both"),
    ("仅视频（无声 mp4）", "video"),
    ("仅音频（m4a）", "audio"),
]

QUALITY_OPTIONS = [
    ("自动最高（账号权限内最高）", None),
    ("8K 超高清 (127)", 127),
    ("4K 超清 (120)", 120),
    ("1080P60 高帧率 (116)", 116),
    ("1080P 高码率 (112)", 112),
    ("1080P (80)", 80),
    ("720P (64)", 64),
    ("480P (32)", 32),
    ("360P (16)", 16),
]

AUDIO_OPTIONS = [
    ("自动最高（取最高码率）", "auto"),
    ("Hi-Res 无损优先 (30251)", "hires"),
    ("杜比全景声优先 (30250)", "dolby"),
    ("192K 高音质 (30280)", "192k"),
    ("132K 标准 (30232)", "132k"),
    ("64K 省流量 (30216)", "64k"),
]

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 进度 & 元数据正则
RE_BILI_PROG = re.compile(
    r"^(.+?):\s+(\d+(?:\.\d+)?)%\s+([\d.]+)\s*/\s*([\d.]+)\s*MB(?:\s+([\d.]+)\s*MB/s)?"
)
RE_SPIDER_PROG = re.compile(
    r"^\s*\[([^\]]+)\]\s+([\d.]+)(?:/([\d.]+))?\s*MB(?:\s+\(([\d.]+)%\))?"
)
RE_SPIDER_SEG = re.compile(r"^\s*\[i\]\s*分片\s*(\d+)/(\d+)")
RE_INFO_TITLE_BILI = re.compile(r"^\[信息\]\s*(.+?)\s*\|\s*共\s*(\d+)\s*个分P/集")
RE_INFO_TITLE_SPIDER = re.compile(r"^\[i\]\s*页面标题:\s*(.+)")
RE_INFO_QUALS = re.compile(r"^\[信息\]\s*可选画质:\s*(.+)")
RE_INFO_AUDIOS = re.compile(r"^\[信息\]\s*可选音质:\s*(.+)")
RE_EP_ITEM = re.compile(r"^\s*-\s+\[((?:P|EP)\d+)\]\s+(.+)$")
RE_STAGE_EP = re.compile(r"^=+\s*\[(\d+)/(\d+)\]\s*([^|]+?)\s*\|\s*(.+?)\s*=+$")
RE_FILE_DONE = re.compile(r"^\[✓\]\s*完成\s*->\s*(.+)$")


def _human_size(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TB"


def _read_cookie_string() -> str:
    if os.path.exists(COOKIE_STRING):
        try:
            with open(COOKIE_STRING, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def _list_recent_outputs(limit: int = 12) -> list[dict]:
    items = []
    seen = set()
    media_exts = {".mp4", ".m4a", ".ts", ".mkv", ".webm", ".mov", ".m4s", ".flv"}
    for tool_key, info in TOOLS.items():
        root_dir = info["out"]
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _, filenames in os.walk(root_dir):
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in media_exts:
                    continue
                full = os.path.abspath(os.path.join(dirpath, fn))
                if full in seen:
                    continue
                seen.add(full)
                try:
                    st = os.stat(full)
                    items.append({
                        "name": fn,
                        "path": full,
                        "rel": os.path.relpath(full, root_dir),
                        "tool": tool_key,
                        "size": st.st_size,
                        "size_text": _human_size(st.st_size),
                        "mtime": st.st_mtime,
                        "ext": ext.lstrip(".").upper(),
                    })
                except OSError:
                    continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _kill_process_tree(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=5,
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


class StudioState:
    """集中管理下载子进程、扫码登录子进程、实时遥测与事件流。"""

    def __init__(self, server_ref: list):
        self.lock = threading.Lock()
        self.server_ref = server_ref
        self.proc: subprocess.Popen | None = None
        self.login_proc: subprocess.Popen | None = None
        self.seq = 0
        self.events: list[dict] = []
        self.qr_lines: list[str] = []
        self.client_seen = False
        self.last_heartbeat = time.time()
        self.shutdown_deadline: float | None = None
        self.telemetry = self._empty_telemetry()

    def _empty_telemetry(self) -> dict:
        return {
            "running": False,
            "action": "idle",
            "tool": "bili",
            "stage": "待命就绪",
            "indeterminate": False,
            "percent": 0.0,
            "downloaded_mb": 0.0,
            "total_mb": 0.0,
            "speed_mbs": 0.0,
            "label": "",
            "title": "",
            "ep_progress": "",
            "episodes": [],
            "available_qualities": [],
            "available_audios": [],
            "exit_code": None,
        }

    def push_event(self, ev_type: str, **payload) -> None:
        with self.lock:
            self.seq += 1
            ev = {"seq": self.seq, "type": ev_type, **payload}
            self.events.append(ev)
            if len(self.events) > 2500:
                self.events = self.events[-1500:]

    def get_env_snapshot(self) -> dict:
        tools_info = {}
        for k, t in TOOLS.items():
            tools_info[k] = {
                "name": t["name"],
                "label": t["label"],
                "out": t["out"],
                "py_ok": os.path.exists(t["py"]),
                "script_ok": os.path.exists(t["script"]),
                "bat_ok": os.path.exists(t["bat"]),
                "py": t["py"],
                "script": t["script"],
                "bat": t["bat"],
            }
        ff = shutil.which("ffmpeg")
        fp = shutil.which("ffprobe")
        ck = _read_cookie_string()
        return {
            "tools": tools_info,
            "ffmpeg": ff or "",
            "ffprobe": fp or "",
            "cookie_exists": bool(ck),
            "cookie_value": ck,
            "recent_files": _list_recent_outputs(12),
            "options": {
                "modes": [{"label": lbl, "value": val} for lbl, val in MODE_OPTIONS],
                "qualities": [{"label": lbl, "value": val} for lbl, val in QUALITY_OPTIONS],
                "audios": [{"label": lbl, "value": val} for lbl, val in AUDIO_OPTIONS],
            },
        }

    def poll(self, since: int) -> dict:
        with self.lock:
            self.client_seen = True
            self.last_heartbeat = time.time()
            self.shutdown_deadline = None
            new_events = [e for e in self.events if e["seq"] > since]
            running = bool(self.proc and self.proc.poll() is None)
            qr_running = bool(self.login_proc and self.login_proc.poll() is None)
            tel = dict(self.telemetry)
            tel["running"] = running
            tel["episodes"] = list(self.telemetry["episodes"])
            tel["available_qualities"] = list(self.telemetry["available_qualities"])
            tel["available_audios"] = list(self.telemetry["available_audios"])
            return {
                "seq": self.seq,
                "events": new_events,
                "running": running,
                "qr_running": qr_running,
                "telemetry": tel,
            }

    def build_command(self, body: dict) -> tuple[list[str], dict, str]:
        tool_key = body.get("tool", "bili")
        if tool_key not in TOOLS:
            raise ValueError(f"未知下载引擎: {tool_key}")
        action = body.get("action", "download")
        if action not in ("download", "info", "diag"):
            raise ValueError(f"未知操作类型: {action}")

        t = TOOLS[tool_key]
        url = (body.get("url") or "").strip()
        if not url:
            raise ValueError("请先输入或粘贴视频链接（BV / av / ss / ep / 直链）")

        if action in ("info", "diag") and tool_key != "bili":
            raise ValueError("「仅查看信息」与「权限诊断」仅 Bili-Core 专用引擎支持")

        mode = body.get("mode", "both")
        if mode not in ("both", "video", "audio"):
            mode = "both"

        quality = body.get("quality", None)
        audio_quality = body.get("audio_quality", "auto") or "auto"
        cookie = (body.get("cookie") or "").strip()
        pages = (body.get("pages") or "").strip()
        threads = body.get("threads", 8)
        no_merge = bool(body.get("no_merge", False))
        extra = (body.get("extra") or "").strip()

        cmd = [t["py"], t["script"]]
        use_bili = tool_key == "bili"
        if use_bili:
            cmd += ["-u", url]
            if cookie:
                cmd += ["--cookie", cookie]
        else:
            cmd.append(url)

        cmd += ["--only", mode, "-o", t["out"]]
        if mode != "audio" and quality is not None and str(quality) != "":
            cmd += ["-q" if use_bili else "--quality", str(quality)]
        if mode != "video" and audio_quality != "auto":
            cmd += ["--audio-quality", str(audio_quality)]

        if action == "info":
            cmd.append("--info")
        elif action == "diag":
            cmd.append("--diag")
        else:
            if pages:
                if use_bili:
                    cmd += ["-p", pages]
                else:
                    # video_spider 用 --ep 接收单集数字
                    ep_num = re.sub(r"\D+", "", pages.split(",")[0].split("-")[0])
                    if ep_num:
                        cmd += ["--ep", ep_num]
            if isinstance(threads, int) and threads > 0 and threads != 8:
                cmd += ["--threads" if use_bili else "--workers", str(threads)]
            if no_merge and use_bili and mode == "both":
                cmd.append("--no-merge")

        if extra:
            try:
                cmd += shlex.split(extra)
            except ValueError as exc:
                raise ValueError(f"附加参数格式错误: {exc}") from exc

        return cmd, t, action

    def start_task(self, body: dict) -> dict:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return {"ok": False, "error": "当前已有任务正在运行，请先停止后再启动"}

        try:
            cmd, tool_info, action = self.build_command(body)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(tool_info["script"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动子进程失败: {exc}"}

        with self.lock:
            self.proc = proc
            self.telemetry = self._empty_telemetry()
            self.telemetry["running"] = True
            self.telemetry["action"] = action
            self.telemetry["tool"] = body.get("tool", "bili")
            self.telemetry["stage"] = {
                "info": "探测片源画质与分P",
                "diag": "诊断账号与大会员权限",
                "download": "正在解析视频地址",
            }.get(action, "运行中")
            self.telemetry["indeterminate"] = True

        cmd_preview = " ".join((f'"{c}"' if " " in c else c) for c in cmd)
        # 掩码化日志中的 --cookie 值，避免界面日志明文暴露 SESSDATA
        masked_preview = re.sub(r'(--cookie\s+)(?:"[^"]*"|\S+)', r'\1"***"', cmd_preview)
        self.push_event("clear_log")
        self.push_event("log", text=f"$ {masked_preview}", level="cmd")

        threading.Thread(
            target=self._read_process_stream,
            args=(proc, action),
            daemon=True,
        ).start()
        return {"ok": True, "pid": proc.pid}

    def _classify_line(self, line: str) -> str:
        s = line.strip()
        if any(k in s for k in ("[通过]", "[OK]", "[✓]")):
            return "ok"
        if any(k in s for k in ("[失败]", "[错误]", "[ERR]", "[FAIL]", "[✗]")):
            return "err"
        if any(k in s for k in ("[警告]", "[提示]", "[注意]", "[!]")):
            return "warn"
        if any(k in s for k in ("[解析]", "[信息]", "[流]", "[模式]", "[下载]", "[合并]", "[i]")):
            return "info"
        if s.startswith("===") or s.startswith("---"):
            return "heading"
        return "plain"

    def _parse_telemetry_line(self, text: str, is_cr: bool) -> None:
        s = text.strip()
        if not s:
            return
        with self.lock:
            t = self.telemetry

            m = RE_BILI_PROG.match(s)
            if m:
                t["label"] = m.group(1).strip()
                t["percent"] = min(100.0, float(m.group(2)))
                t["downloaded_mb"] = float(m.group(3))
                t["total_mb"] = float(m.group(4))
                t["speed_mbs"] = float(m.group(5)) if m.group(5) else 0.0
                t["indeterminate"] = False
                if "视频" in t["label"]:
                    t["stage"] = f"下载视频流 · {t['label']}"
                elif "音频" in t["label"]:
                    t["stage"] = f"下载音频流 · {t['label']}"
                else:
                    t["stage"] = f"下载媒体 · {t['label']}"
                return

            m = RE_SPIDER_PROG.match(s)
            if m:
                t["label"] = m.group(1).strip()
                t["downloaded_mb"] = float(m.group(2))
                t["total_mb"] = float(m.group(3)) if m.group(3) else 0.0
                if m.group(4):
                    t["percent"] = min(100.0, float(m.group(4)))
                    t["indeterminate"] = False
                else:
                    t["indeterminate"] = True
                t["stage"] = f"下载中 · {t['label']}"
                return

            m = RE_SPIDER_SEG.match(s)
            if m:
                done_seg, total_seg = int(m.group(1)), max(1, int(m.group(2)))
                t["label"] = f"分片 {done_seg}/{total_seg}"
                t["percent"] = round(done_seg * 100.0 / total_seg, 1)
                t["indeterminate"] = False
                t["stage"] = f"多线程拉取 HLS 分片 ({done_seg}/{total_seg})"
                return

            if is_cr:
                return

            m = RE_INFO_TITLE_BILI.match(s)
            if m:
                t["title"] = m.group(1).strip()
                t["stage"] = f"已识别《{t['title']}》"
                return

            m = RE_INFO_TITLE_SPIDER.match(s)
            if m:
                t["title"] = m.group(1).strip()
                t["stage"] = f"已识别《{t['title']}》"
                return

            m = RE_INFO_QUALS.match(s)
            if m:
                quals = [int(x) for x in re.findall(r"(\d+)\(", m.group(1))]
                t["available_qualities"] = quals
                return

            m = RE_INFO_AUDIOS.match(s)
            if m:
                raw_aud = m.group(1)
                id_map = {
                    "30251": "hires",
                    "30250": "dolby",
                    "30280": "192k",
                    "30232": "132k",
                    "30216": "64k",
                }
                found = []
                for aid in re.findall(r"id=(\d+)", raw_aud):
                    if aid in id_map and id_map[aid] not in found:
                        found.append(id_map[aid])
                t["available_audios"] = found
                return

            m = RE_EP_ITEM.match(s)
            if m:
                tag = m.group(1)
                num = re.sub(r"\D+", "", tag)
                t["episodes"].append({"tag": tag, "num": num, "title": m.group(2).strip()})
                return

            m = RE_STAGE_EP.match(s)
            if m:
                t["ep_progress"] = f"{m.group(1)}/{m.group(2)} · {m.group(3).strip()}"
                t["stage"] = f"处理 {m.group(3).strip()} ({m.group(1)}/{m.group(2)})"
                t["percent"] = 0.0
                t["indeterminate"] = True
                return

            if "[下载] 视频流" in s:
                t["stage"] = "准备下载视频流"
                t["percent"] = 0.0
                t["indeterminate"] = False
            elif "[下载] 音频流" in s:
                t["stage"] = "准备下载音频流"
                t["percent"] = 0.0
                t["indeterminate"] = False
            elif "[合并] ffmpeg" in s or "使用 ffmpeg 合并音视频" in s:
                t["stage"] = "FFmpeg 无损混流合并中…"
                t["indeterminate"] = True
                t["speed_mbs"] = 0.0
            elif "[通过]" in s or RE_FILE_DONE.match(s):
                t["stage"] = "下载完成并通过校验"
                t["percent"] = 100.0
                t["indeterminate"] = False
                t["speed_mbs"] = 0.0
            elif s.startswith("[失败]") or s.startswith("[错误]") or s.startswith("[✗]"):
                t["stage"] = "任务执行异常"
                t["indeterminate"] = False
                t["speed_mbs"] = 0.0

    def _read_process_stream(self, proc: subprocess.Popen, action: str) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        in_cr = False

        def handle_char_stream(chunk_text: str) -> None:
            nonlocal buf, in_cr
            i = 0
            n = len(chunk_text)
            while i < n:
                ch = chunk_text[i]
                if ch == "\r":
                    if i + 1 < n and chunk_text[i + 1] == "\n":
                        self._parse_telemetry_line(buf, False)
                        self.push_event("log", text=buf, level=self._classify_line(buf))
                        buf = ""
                        in_cr = False
                        i += 2
                        continue
                    # 独立 \r：说明后面的文本是覆盖进度行
                    buf = ""
                    in_cr = True
                    i += 1
                    continue
                if ch == "\n":
                    self._parse_telemetry_line(buf, False)
                    self.push_event("log", text=buf, level=self._classify_line(buf))
                    buf = ""
                    in_cr = False
                    i += 1
                    continue
                buf += ch
                i += 1

            if buf and in_cr:
                self._parse_telemetry_line(buf, True)
                self.push_event("progress_line", text=buf)

        try:
            assert proc.stdout is not None
            while True:
                raw = proc.stdout.read(4096)
                if not raw:
                    break
                text = decoder.decode(raw, final=False)
                if text:
                    handle_char_stream(text)
            tail = decoder.decode(b"", final=True)
            if tail:
                handle_char_stream(tail)
            if buf:
                self._parse_telemetry_line(buf, False)
                self.push_event("log", text=buf, level=self._classify_line(buf))
        except Exception as exc:
            self.push_event("log", text=f"[读取输出异常] {exc}", level="err")
        finally:
            code = proc.wait()
            with self.lock:
                if self.proc is proc:
                    self.proc = None
                self.telemetry["running"] = False
                self.telemetry["indeterminate"] = False
                self.telemetry["speed_mbs"] = 0.0
                self.telemetry["exit_code"] = code
                if code == 0:
                    if action == "info":
                        self.telemetry["stage"] = "画质与分P探测完成"
                    elif action == "diag":
                        self.telemetry["stage"] = "账号权限诊断完成"
                    else:
                        self.telemetry["stage"] = "任务圆满完成"
                        self.telemetry["percent"] = 100.0
                else:
                    self.telemetry["stage"] = f"任务已结束 (退出码 {code})"
            self.push_event(
                "done",
                code=code,
                action=action,
                recent_files=_list_recent_outputs(12),
            )

    def stop_task(self) -> dict:
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            _kill_process_tree(proc)
            self.push_event("log", text="[已发送停止信号，正在清理进程]", level="warn")
            return {"ok": True}
        return {"ok": False, "error": "当前没有运行中的任务"}

    def start_qr_login(self) -> dict:
        with self.lock:
            if self.login_proc and self.login_proc.poll() is None:
                return {"ok": True, "already_running": True}
            self.qr_lines = []

        if not os.path.exists(QR_LOGIN):
            return {"ok": False, "error": f"缺少扫码脚本: {QR_LOGIN}"}

        if os.path.exists(QR_IMAGE):
            try:
                os.remove(QR_IMAGE)
            except OSError:
                pass

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            proc = subprocess.Popen(
                [PY_BILI, QR_LOGIN],
                cwd=os.path.dirname(QR_LOGIN),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动扫码进程失败: {exc}"}

        with self.lock:
            self.login_proc = proc

        threading.Thread(target=self._read_qr_stream, args=(proc,), daemon=True).start()
        return {"ok": True}

    def _read_qr_stream(self, proc: subprocess.Popen) -> None:
        ok = False
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                if line.startswith("[QRIMG]"):
                    self.push_event("qr_img", ts=int(time.time() * 1000))
                    continue
                if line.startswith("QR:"):
                    self.push_event("qr_ascii", line=line[3:])
                    continue
                if "No module named 'qrcode'" in line:
                    self.push_event(
                        "qr_status",
                        text="[提示] 当前 Python 环境缺少 qrcode 依赖，请运行: pip install -r requirements.txt",
                        level="warn",
                    )
                if "[OK] 登录成功" in line:
                    ok = True
                self.push_event("qr_status", text=line, level=self._classify_line(line))
        except Exception as exc:
            self.push_event("qr_status", text=f"[扫码] 读取异常: {exc}", level="err")
        finally:
            code = proc.wait()
            with self.lock:
                if self.login_proc is proc:
                    self.login_proc = None
            cookie_val = _read_cookie_string() if ok else ""
            self.push_event("qr_done", ok=ok, code=code, cookie_value=cookie_val)

    def stop_qr_login(self) -> dict:
        with self.lock:
            proc = self.login_proc
            self.login_proc = None
        if proc and proc.poll() is None:
            _kill_process_tree(proc)
        return {"ok": True}

    def request_shutdown(self) -> None:
        with self.lock:
            self.shutdown_deadline = time.time() + 2.5

    def cleanup_all(self) -> None:
        with self.lock:
            p1, p2 = self.proc, self.login_proc
            self.proc = None
            self.login_proc = None
        _kill_process_tree(p1)
        _kill_process_tree(p2)


class ReusableThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(state: StudioState):
    class StudioHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            # pythonw.exe 下 sys.stderr 可能为 None，静默处理 HTTP 日志
            return

        def _send_bytes(self, code: int, content_type: str, data: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send_bytes(code, "application/json; charset=utf-8", raw)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/api/env":
                self._send_json(200, state.get_env_snapshot())
                return

            if path == "/api/poll":
                qs = urllib.parse.parse_qs(parsed.query)
                since = int((qs.get("since") or ["0"])[0])
                self._send_json(200, state.poll(since))
                return

            if path == "/api/qr_image":
                if os.path.exists(QR_IMAGE):
                    with open(QR_IMAGE, "rb") as f:
                        self._send_bytes(200, "image/png", f.read())
                else:
                    self._send_json(404, {"error": "二维码尚未生成"})
                return

            # 静态前端资源
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            if ".." in rel or "\\" in rel:
                self._send_bytes(403, "text/plain; charset=utf-8", b"Forbidden")
                return
            full = os.path.abspath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(full):
                self._send_bytes(404, "text/plain; charset=utf-8", b"Not Found")
                return

            mime, _ = mimetypes.guess_type(full)
            if full.endswith(".js"):
                mime = "application/javascript; charset=utf-8"
            elif full.endswith(".css"):
                mime = "text/css; charset=utf-8"
            elif full.endswith(".html"):
                mime = "text/html; charset=utf-8"
            elif not mime:
                mime = "application/octet-stream"
            with open(full, "rb") as f:
                self._send_bytes(200, mime, f.read())

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            body = self._read_json()

            if path == "/api/start":
                res = state.start_task(body)
                self._send_json(200 if res.get("ok") else 400, res)
                return

            if path == "/api/stop":
                res = state.stop_task()
                self._send_json(200, res)
                return

            if path == "/api/qr_start":
                res = state.start_qr_login()
                self._send_json(200 if res.get("ok") else 400, res)
                return

            if path == "/api/qr_stop":
                self._send_json(200, state.stop_qr_login())
                return

            if path == "/api/save_cookie":
                ck = (body.get("cookie") or "").strip()
                try:
                    os.makedirs(os.path.dirname(COOKIE_STRING), exist_ok=True)
                    with open(COOKIE_STRING, "w", encoding="utf-8") as f:
                        f.write(ck)
                    self._send_json(200, {"ok": True})
                except OSError as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/api/open_folder":
                file_path = (body.get("file_path") or "").strip()
                if file_path and os.path.exists(file_path):
                    norm = os.path.normpath(file_path)
                    allowed = any(
                        norm.lower().startswith(os.path.abspath(t["out"]).lower())
                        for t in TOOLS.values()
                    )
                    if allowed:
                        try:
                            if os.name == "nt":
                                subprocess.Popen(["explorer", "/select,", norm])
                            else:
                                os.startfile(os.path.dirname(norm))
                            self._send_json(200, {"ok": True})
                            return
                        except OSError as exc:
                            self._send_json(500, {"ok": False, "error": str(exc)})
                            return
                tool_key = body.get("tool", "bili")
                folder = TOOLS.get(tool_key, TOOLS["bili"])["out"]
                os.makedirs(folder, exist_ok=True)
                try:
                    os.startfile(folder)
                    self._send_json(200, {"ok": True})
                except OSError as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/api/open_cli":
                tool_key = body.get("tool", "bili")
                bat = TOOLS.get(tool_key, TOOLS["bili"])["bat"]
                if not os.path.exists(bat):
                    self._send_json(404, {"ok": False, "error": f"文件不存在: {bat}"})
                    return
                try:
                    os.startfile(bat)
                    self._send_json(200, {"ok": True})
                except OSError as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/api/heartbeat":
                with state.lock:
                    state.client_seen = True
                    state.last_heartbeat = time.time()
                    state.shutdown_deadline = None
                self._send_json(200, {"ok": True})
                return

            if path == "/api/shutdown":
                state.request_shutdown()
                self._send_json(200, {"ok": True})
                return

            self._send_json(404, {"error": "Unknown endpoint"})

    return StudioHandler


def find_app_browser() -> str | None:
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def launch_desktop_window(url: str) -> None:
    browser = find_app_browser()
    if browser:
        try:
            subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    "--window-size=1280,860",
                ],
                creationflags=CREATE_NO_WINDOW,
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def run_tk_fallback(url: str) -> None:
    """当用户显式传入 --tk 时打开极简原生桌面控制卡并打开界面。"""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("B 站音视频下载器 · 服务控制台")
    root.geometry("460x180")
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Bili-Pulse Web 工作台服务正在运行", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
    ttk.Label(frame, text=url, foreground="#0a84ff").pack(anchor="w", pady=(6, 14))
    btns = ttk.Frame(frame)
    btns.pack(fill="x")
    ttk.Button(btns, text="打开工作台窗口", command=lambda: launch_desktop_window(url)).pack(side="left")
    ttk.Button(btns, text="退出服务", command=root.destroy).pack(side="right")
    launch_desktop_window(url)
    root.mainloop()


def main() -> int:
    no_browser = "--no-browser" in sys.argv
    tk_mode = "--tk" in sys.argv

    server_ref: list[ReusableThreadingHTTPServer] = []
    state = StudioState(server_ref)
    handler = make_handler(state)
    httpd = ReusableThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_ref.append(httpd)

    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    if sys.stdout is not None:
        print(f"[Bili-Pulse] Server listening on {url}", flush=True)

    # 守护线程：处理页面关闭后的优雅自动退出（仅当客户端曾连接过且非 --no-browser）
    def watchdog() -> None:
        while True:
            time.sleep(0.5)
            with state.lock:
                deadline = state.shutdown_deadline
                client_seen = state.client_seen
                idle_sec = time.time() - state.last_heartbeat
                busy = bool(state.proc and state.proc.poll() is None)
            if not no_browser and not tk_mode:
                if deadline is not None and time.time() >= deadline:
                    httpd.shutdown()
                    return
                if client_seen and not busy and idle_sec > 25.0:
                    httpd.shutdown()
                    return

    threading.Thread(target=watchdog, daemon=True).start()

    if tk_mode:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            run_tk_fallback(url)
        finally:
            state.cleanup_all()
            httpd.shutdown()
            httpd.server_close()
        return 0

    if not no_browser:
        launch_desktop_window(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.cleanup_all()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

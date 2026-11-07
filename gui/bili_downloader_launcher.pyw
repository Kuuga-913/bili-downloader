# -*- coding: utf-8 -*-
"""B 站音视频下载器 · 图形启动器（可选下载内容 / 画质 / 音质）

把两个历史工具包装成一个窗口：
  - video_spider       : 通用爬虫（直链 / m3u8 / B 站），输出 downloads/
  - bilibili-downloader: 会员/充电/番剧专用，ffmpeg 自动合并 + ffprobe 校验

下载内容支持：音视频合并 / 仅视频 / 仅音频；画质与音质可分别指定。
本文件只用标准库 tkinter；实际下载仍由各自项目自带的 venv Python 执行。
"""
import os
import sys
import shlex
import shutil
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox

# 路径自动探测：本文件位于 gui/，其上一级即项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BILI_DIR = os.path.join(BASE_DIR, "bilibili-downloader")
SPIDER_DIR = os.path.join(BASE_DIR, "video_spider")
_py = os.environ.get("BILI_PYTHON") or sys.executable
if os.name == "nt" and _py.lower().endswith("pythonw.exe"):
    _py = os.path.join(os.path.dirname(_py), "python.exe")
PY_SPIDER = PY_BILI = _py
SPIDER = os.path.join(SPIDER_DIR, "spider.py")
BILI = os.path.join(BILI_DIR, "bilibili_dl.py")
BAT_SPIDER = os.path.join(SPIDER_DIR, "run.bat")
BAT_BILI = os.path.join(BILI_DIR, "run.bat")
OUT_SPIDER = os.path.join(BASE_DIR, "downloads")
OUT_BILI = os.path.join(BILI_DIR, "downloads")
QR_LOGIN = os.path.join(BILI_DIR, "bili_login_qr.py")
COOKIE_STRING = os.path.join(BILI_DIR, "cookie_string.txt")

TOOLS = {
    "spider": {
        "label": "video_spider（通用 / 直链 / m3u8 / B 站）",
        "py": PY_SPIDER, "script": SPIDER, "bat": BAT_SPIDER, "out": OUT_SPIDER,
    },
    "bili": {
        "label": "bilibili-downloader（会员 / 充电 / 番剧，自动合并校验）",
        "py": PY_BILI, "script": BILI, "bat": BAT_BILI, "out": OUT_BILI,
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
MODE_MAP = dict((label, value) for label, value in MODE_OPTIONS)
QUALITY_MAP = dict((label, value) for label, value in QUALITY_OPTIONS)
AUDIO_MAP = dict((label, value) for label, value in AUDIO_OPTIONS)


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        self.tool = tk.StringVar(value="spider")
        self.status = tk.StringVar(value="就绪")
        self.mode_var = tk.StringVar(value=MODE_OPTIONS[0][0])
        self.quality_var = tk.StringVar(value=QUALITY_OPTIONS[0][0])
        self.audio_var = tk.StringVar(value=AUDIO_OPTIONS[0][0])
        root.title("B 站音视频下载器")
        root.geometry("980x760")
        root.minsize(820, 660)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        root.option_add("*Font", ("Microsoft YaHei UI", 10))

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="B 站音视频下载器",
                  font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(main, text="粘贴链接，选择下载内容与画质 / 音质，点「开始下载」。",
                  foreground="#555").pack(anchor="w", pady=(2, 10))

        url_row = ttk.Frame(main)
        url_row.pack(fill="x")
        ttk.Label(url_row, text="视频链接：", width=10).pack(side="left")
        self.url = ttk.Entry(url_row)
        self.url.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.url.bind("<Return>", lambda e: self.start(False))
        ttk.Button(url_row, text="粘贴", width=8, command=self.paste).pack(side="left")
        ttk.Button(url_row, text="清空", width=8,
                   command=lambda: self.url.delete(0, "end")).pack(side="left", padx=(6, 0))

        box = ttk.LabelFrame(main, text=" 选择工具 ", padding=8)
        box.pack(fill="x", pady=(10, 6))
        for key in ("spider", "bili"):
            ttk.Radiobutton(box, text=TOOLS[key]["label"], variable=self.tool,
                            value=key, command=self.on_tool_change).pack(anchor="w", pady=2)

        opts = ttk.LabelFrame(main, text=" 下载选项 ", padding=8)
        opts.pack(fill="x", pady=6)
        row = ttk.Frame(opts)
        row.pack(fill="x")
        ttk.Label(row, text="下载内容：", width=10).pack(side="left")
        self.mode_combo = ttk.Combobox(row, state="readonly", width=28,
                                       textvariable=self.mode_var,
                                       values=[x[0] for x in MODE_OPTIONS])
        self.mode_combo.pack(side="left")
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self.on_mode_change())
        ttk.Label(row, text="   画质：", width=8).pack(side="left")
        self.quality_combo = ttk.Combobox(row, state="readonly", width=26,
                                          textvariable=self.quality_var,
                                          values=[x[0] for x in QUALITY_OPTIONS])
        self.quality_combo.pack(side="left")
        ttk.Label(row, text="   音质：", width=8).pack(side="left")
        self.audio_combo = ttk.Combobox(row, state="readonly", width=26,
                                        textvariable=self.audio_var,
                                        values=[x[0] for x in AUDIO_OPTIONS])
        self.audio_combo.pack(side="left", fill="x", expand=True)
        ttk.Label(opts, text="画质 / 音质取「不超过所选上限的最高可用档」；高画质与 Hi-Res / 杜比音质需要对应账号权限，实际可用档位以接口返回为准（可用「仅查看信息」查看）。",
                  foreground="#777", wraplength=920).pack(anchor="w", pady=(8, 0))

        opt = ttk.Frame(main)
        opt.pack(fill="x", pady=(8, 0))
        ttk.Label(opt, text="附加参数：", width=10).pack(side="left")
        self.extra = ttk.Entry(opt)
        self.extra.pack(side="left", fill="x", expand=True)
        ttk.Label(main, text="可选：-p 1,3-5 选分P  |  --ep 3 选番剧集数  |  --no-merge 保留分离音视频  |  --threads 16 下载线程数（同名参数会覆盖上面的选择）",
                  foreground="#777", wraplength=920).pack(anchor="w", pady=(2, 0))

        cookie_row = ttk.Frame(main)
        cookie_row.pack(fill="x", pady=(8, 0))
        ttk.Label(cookie_row, text="Cookie：", width=10).pack(side="left")
        self.cookie = ttk.Entry(cookie_row, show="*")
        self.cookie.pack(side="left", fill="x", expand=True)
        ttk.Label(main, text="专用版可填 SESSDATA=... 解锁更高画质；通用版已把 Cookie 写在 config.yaml 里，此栏对其无效。",
                  foreground="#777", wraplength=920).pack(anchor="w", pady=(2, 0))

        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=12)
        self.btn_start = ttk.Button(btns, text="开始下载", command=lambda: self.start(False))
        self.btn_start.pack(side="left")
        self.btn_info = ttk.Button(btns, text="仅查看信息 (--info)", command=lambda: self.start(True))
        self.btn_info.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(btns, text="打开输出文件夹", command=self.open_output).pack(side="left", padx=6)
        ttk.Button(btns, text="打开命令行版", command=self.open_cli).pack(side="left", padx=6)
        ttk.Button(btns, text="扫码登录 / 获取 Cookie", command=self.qr_login).pack(side="left", padx=6)
        ttk.Button(btns, text="清空日志", command=self.clear_log).pack(side="right")

        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=14, bg="#fbfbfb", fg="#1e1e1e",
                           insertbackground="#1e1e1e", relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        bottom = ttk.Frame(root, padding=(12, 4))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status, foreground="#444").pack(side="left")

        self.on_tool_change()
        self.on_mode_change()
        self.env_check()
        self.qr_text = None
        self.qr_label = None
        self.qr_photo = None
        self.login_proc = None
        self.load_saved_cookie()
        self.root.after(80, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 基础操作 ----------
    def write(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    def clear_log(self):
        self.log.delete("1.0", "end")

    def paste(self):
        try:
            v = self.root.clipboard_get().strip()
            self.url.delete(0, "end")
            self.url.insert(0, v)
        except tk.TclError:
            messagebox.showinfo("剪贴板", "剪贴板里没有文本内容")

    def update_status(self):
        t = TOOLS[self.tool.get()]
        mode = MODE_MAP[self.mode_var.get()]
        q = QUALITY_MAP[self.quality_var.get()]
        aq = AUDIO_MAP[self.audio_var.get()]
        parts = ["输出：" + t["out"], "模式：" + dict((v, k) for k, v in MODE_OPTIONS)[mode].split("（")[0],
                 "画质：" + ("自动最高" if q is None else self.quality_var.get().split("(")[0].strip()),
                 "音质：" + ("自动最高" if aq == "auto" else self.audio_var.get().split("(")[0].strip())]
        self.status.set("   |   ".join(parts))

    def on_tool_change(self):
        self.btn_info.configure(state="normal" if self.tool.get() == "bili" else "disabled")
        self.update_status()

    def on_mode_change(self):
        mode = MODE_MAP[self.mode_var.get()]
        self.quality_combo.configure(state="disabled" if mode == "audio" else "readonly")
        self.audio_combo.configure(state="disabled" if mode == "video" else "readonly")
        self.update_status()

    def env_check(self):
        self.write("=== 环境自检 ===\n")
        for key in ("spider", "bili"):
            t = TOOLS[key]
            for name, path in (("venv python", t["py"]), ("脚本", t["script"]), ("命令行入口", t["bat"])):
                self.write("  [%s] %-12s %s\n" % ("OK" if os.path.exists(path) else "缺失", name, path))
        ff = shutil.which("ffmpeg")
        fp = shutil.which("ffprobe")
        self.write("  [%s] ffmpeg       %s\n" % ("OK" if ff else "缺失", ff or "未找到"))
        self.write("  [%s] ffprobe      %s\n" % ("OK" if fp else "缺失", fp or "未找到"))
        self.write("=== 就绪：可选音视频合并 / 仅视频 / 仅音频，并指定画质与音质 ===\n\n")

    def open_output(self):
        folder = TOOLS[self.tool.get()]["out"]
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def open_cli(self):
        bat = TOOLS[self.tool.get()]["bat"]
        if not os.path.exists(bat):
            messagebox.showerror("文件不存在", bat)
            return
        os.startfile(bat)

    # ---------- Cookie / 扫码登录 ----------
    def load_saved_cookie(self):
        """把上次扫码保存的 Cookie 串填进输入框（框内以 * 遮盖）。"""
        if os.path.exists(COOKIE_STRING):
            try:
                v = open(COOKIE_STRING, encoding="utf-8").read().strip()
            except OSError:
                return
            if v:
                self.cookie.delete(0, "end")
                self.cookie.insert(0, v)
                self.write("[Cookie] 已加载上次扫码保存的登录态（%s）\n"
                           % os.path.basename(COOKIE_STRING))

    def qr_login(self):
        """弹窗显示二维码；扫码成功后 Cookie 自动写入两个工具并填回输入框。"""
        if self.login_proc and self.login_proc.poll() is None:
            messagebox.showinfo("扫码进行中", "已有扫码窗口在等待，请在那个窗口完成扫码")
            return
        if not os.path.exists(QR_LOGIN):
            messagebox.showerror("文件不存在", QR_LOGIN)
            return
        win = tk.Toplevel(self.root)
        win.title("B 站扫码登录 · 获取 Cookie")
        win.geometry("720x760")
        win.transient(self.root)
        ttk.Label(win, text="用手机 B 站 App 扫描下方二维码并确认；成功后 Cookie 会自动保存到两个工具。",
                  wraplength=690).pack(anchor="w", padx=10, pady=(10, 6))
        self.qr_label = ttk.Label(win)
        self.qr_label.pack(pady=(0, 6))
        self.qr_photo = None
        frame = ttk.Frame(win, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)
        txt = tk.Text(frame, font=("Consolas", 8), bg="#ffffff", fg="#111111",
                      wrap="none", relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        self.qr_text = txt

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.login_proc = subprocess.Popen(
                [PY_BILI, QR_LOGIN], cwd=os.path.dirname(QR_LOGIN),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        except OSError as e:
            self.write("[扫码] 启动失败: %s\n" % e)
            self.qr_text = None
            win.destroy()
            return
        self.btn_start.configure(state="disabled")
        threading.Thread(target=self.qr_reader, args=(self.login_proc,), daemon=True).start()
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_qr(win))

    def qr_reader(self, proc):
        ok = False
        try:
            for line in proc.stdout:
                self.q.put(line)
                if "[OK] 登录成功" in line:
                    ok = True
        except Exception as e:
            self.q.put("[扫码] 读取输出出错: %s\n" % e)
        finally:
            code = proc.wait()
            self.q.put("__QR_RELOAD__" if ok else "__QR_DONE__")
            self.q.put("[扫码] 子进程结束，退出码 %s\n" % code)

    def close_qr(self, win):
        if self.login_proc and self.login_proc.poll() is None:
            self.login_proc.terminate()
        self.login_proc = None
        self.qr_text = None
        self.qr_label = None
        self.btn_start.configure(state="normal")
        win.destroy()

    # ---------- 下载 ----------
    def build_cmd(self, info_only):
        t = TOOLS[self.tool.get()]
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("缺少链接", "请先粘贴 B 站视频链接（BV / av / ss / ep）")
            return None
        mode = MODE_MAP[self.mode_var.get()]
        q = QUALITY_MAP[self.quality_var.get()]
        aq = AUDIO_MAP[self.audio_var.get()]
        cmd = [t["py"], t["script"]]
        use_bili = self.tool.get() == "bili"
        if use_bili:
            cmd += ["-u", url]
            ck = self.cookie.get().strip()
            if ck:
                cmd += ["--cookie", ck]
        else:
            cmd.append(url)

        cmd += ["--only", mode, "-o", t["out"]]
        if mode != "audio" and q is not None:
            cmd += ["-q" if use_bili else "--quality", str(q)]
        if mode != "video" and aq != "auto":
            cmd += ["--audio-quality", aq]
        if info_only and use_bili:
            cmd.append("--info")

        extra = self.extra.get().strip()
        if extra:
            try:
                cmd += shlex.split(extra)
            except ValueError as e:
                messagebox.showerror("附加参数格式错误", str(e))
                return None
        return cmd, t

    def start(self, info_only):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("正在运行", "当前下载还没结束，请先点「停止」")
            return
        built = self.build_cmd(info_only)
        if not built:
            return
        cmd, t = built
        self.clear_log()
        self.write("$ " + " ".join(('"%s"' % c if " " in c else c) for c in cmd) + "\n\n")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=os.path.dirname(t["script"]),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW, env=env,
            )
        except OSError as e:
            self.write("[启动失败] %s\n" % e)
            return
        self.btn_start.configure(state="disabled")
        self.btn_info.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status.set("运行中… 输出目录：" + t["out"])
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        try:
            for line in proc.stdout:
                self.q.put(line)
        except Exception as e:
            self.q.put("[读取输出出错] %s\n" % e)
        finally:
            code = proc.wait()
            self.q.put("\n=== 进程结束，退出码 %s ===\n" % code)
            self.q.put(None)

    def drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    self.proc = None
                    self.btn_start.configure(state="normal")
                    self.on_tool_change()
                    self.btn_stop.configure(state="disabled")
                    continue
                if item.startswith("[QRIMG]"):
                    path = item[len("[QRIMG]"):].strip()
                    try:
                        self.qr_photo = tk.PhotoImage(file=path)
                        if self.qr_label is not None and self.qr_label.winfo_exists():
                            self.qr_label.configure(image=self.qr_photo)
                            self.write("[扫码] 二维码图片已显示，请用手机 B 站 App 扫描。\n")
                    except Exception as e:
                        self.write("[扫码] 二维码图片显示失败: %s\n" % e)
                    continue
                if item.startswith("QR:"):
                    if self.qr_text is not None and self.qr_text.winfo_exists():
                        self.qr_text.insert("end", item[3:].rstrip("\r\n") + "\n")
                        self.qr_text.see("end")
                    continue
                if item in ("__QR_RELOAD__", "__QR_DONE__"):
                    if item == "__QR_RELOAD__":
                        self.load_saved_cookie()
                        self.write("[Cookie] 扫码登录完成，已同步到两个工具。\n")
                    self.login_proc = None
                    self.btn_start.configure(state="normal")
                    continue
                self.write(item)
                if self.qr_text is not None and self.qr_text.winfo_exists():
                    self.qr_text.insert("end", item)
                    self.qr_text.see("end")
        except queue.Empty:
            pass
        self.root.after(80, self.drain)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("\n[已发送停止信号]\n")
            self.btn_stop.configure(state="disabled")
        else:
            self.write("当前没有运行中的任务\n")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askokcancel("仍在下载", "下载尚未结束，确定要终止并关闭吗？"):
                return
            self.proc.terminate()
        if self.login_proc and self.login_proc.poll() is None:
            self.login_proc.terminate()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()

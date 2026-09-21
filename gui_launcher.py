# -*- coding: utf-8 -*-
"""
现代化暗黑风格多脚本集成管理控制台
具备：
- 沉浸式暗黑 Windows 标题栏（DWM 注入）
- VS Code / Linear 风格现代化配色（深空灰、霓虹绿、科技蓝、亚光边框）
- 圆角平滑科技感控制按钮与状态呼吸指示灯
- 独立虚拟终端缓冲区（原生支持 ANSI 光标重绘与 \\r 就地刷新）
- 模块化面板卡片设计，支持快捷动作胶囊与内联发送框
"""

import sys
import os
import queue
import threading
import subprocess
import ctypes
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from typing import Optional, Callable
from terminal_buffer import VirtualTerminalBuffer

# 脚本所在工作目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 现代极简暗黑主题调色板 (Tailwind Slate / Dark Mode Palette)
THEME = {
    "bg_main": "#0f172a",         # 主背景 Slate 900
    "bg_card": "#1e293b",         # 卡片/面板背景 Slate 800
    "bg_card_inner": "#090d16",   # 终端内背景 Deep Dark
    "border_card": "#334155",     # 卡片边框 Slate 700
    "border_light": "#475569",    # 悬浮边框 Slate 600
    "text_main": "#f8fafc",       # 主标题/文字 Slate 50
    "text_sub": "#94a3b8",        # 副标题/描述 Slate 400
    "text_dim": "#64748b",        # 次要提示 Slate 500
    "terminal_text": "#e2e8f0",   # 控制台字符浅亮白
    "accent_blue": "#38bdf8",     # 天蓝高亮 Sky 400
    "accent_blue_hover": "#0284c7",
    "accent_green": "#10b981",    # 运行绿 Emerald 500
    "accent_green_hover": "#059669",
    "accent_red": "#ef4444",      # 危险/停止红 Red 500
    "accent_red_hover": "#dc2626",
    "accent_amber": "#f59e0b",    # 警告琥珀色 Amber 500
    "btn_bg": "#334155",          # 普通按键底色
    "btn_hover": "#475569",       # 按键悬停
    "btn_active": "#1e293b",      # 按键激活
    "btn_pill_bg": "#1e293b",     # 快捷标签底色
    "btn_pill_border": "#3b82f6", # 快捷标签外框
}


def hex_to_colorref(hex_str: str) -> int:
    """将十六进制颜色字符串转换为 Windows COLORREF (0x00BBGGRR)"""
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_dark_titlebar(window: tk.Tk):
    """为 Windows 10/11 窗口启用精准沉浸式无缝标题栏（精确匹配内容背景与边框）"""
    if os.name != "nt":
        return
    try:
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()

        # 1. 强制启用暗黑模式 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20 或 19)
        val = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )

        # 2. 精确设置标题栏背景色（与主页面背景 #0f172a 100% 融合）
        # DWMWA_CAPTION_COLOR = 35 (Windows 11 / Windows 10 build 22000+)
        caption_color = ctypes.c_int(hex_to_colorref(THEME["bg_main"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )

        # 3. 设置标题栏文字颜色（亮白）
        # DWMWA_TEXT_COLOR = 36
        text_color = ctypes.c_int(hex_to_colorref(THEME["text_main"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
        )

        # 4. 设置窗口外边框颜色（与卡片外框 #334155 呼应）
        # DWMWA_BORDER_COLOR = 34
        border_color = ctypes.c_int(hex_to_colorref(THEME["border_card"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color)
        )
    except Exception:
        pass


class ModernButton(tk.Canvas):
    """现代矢量平滑圆角按钮，支持悬停、点击反馈与多种预设风格"""
    def __init__(
        self,
        parent,
        text: str,
        command: Optional[Callable[[], None]] = None,
        bg_color: str = THEME["btn_bg"],
        hover_color: str = THEME["btn_hover"],
        text_color: str = THEME["text_main"],
        font_style=("Segoe UI", 9, "bold"),
        width: int = 70,
        height: int = 28,
        radius: int = 6,
        border_color: str = "",
        state: str = "normal"
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent["bg"],
            highlightthickness=0
        )
        self.text = text
        self.command = command
        self.base_bg = bg_color
        self.hover_bg = hover_color
        self.text_color = text_color
        self.border_color = border_color
        self.font_style = font_style
        self.w = width
        self.h = height
        self.radius = radius
        self.state = state

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

        self._render(self.base_bg)

    def _render(self, current_bg: str):
        self.delete("all")
        r = self.radius
        w, h = self.w, self.h

        # 禁用状态变暗
        if self.state == "disabled":
            fill_c = "#1e293b"
            text_c = "#475569"
            outline_c = ""
        else:
            fill_c = current_bg
            text_c = self.text_color
            outline_c = self.border_color

        points = [
            1 + r, 1,
            w - 1 - r, 1,
            w - 1, 1,
            w - 1, 1 + r,
            w - 1, h - 1 - r,
            w - 1, h - 1,
            w - 1 - r, h - 1,
            1 + r, h - 1,
            1, h - 1,
            1, h - 1 - r,
            1, 1 + r,
            1, 1,
        ]
        self.create_polygon(points, smooth=True, fill=fill_c, outline=outline_c, width=1)
        self.create_text(w // 2, h // 2, text=self.text, fill=text_c, font=self.font_style)

    def set_state(self, new_state: str):
        self.state = new_state
        self._render(self.base_bg)

    def _on_enter(self, event):
        if self.state == "normal":
            self._render(self.hover_bg)

    def _on_leave(self, event):
        if self.state == "normal":
            self._render(self.base_bg)

    def _on_click(self, event):
        if self.state == "normal" and self.command:
            self.command()


class StatusBadge(tk.Canvas):
    """状态指示徽章（带发光感圆点和文本标签）"""
    def __init__(self, parent, text: str = "离线", color: str = "#64748b", font_style=("Segoe UI", 9)):
        super().__init__(parent, width=90, height=26, bg=parent["bg"], highlightthickness=0)
        self.text = text
        self.color = color
        self.font_style = font_style
        self._render()

    def update_status(self, text: str, color: str):
        self.text = text
        self.color = color
        self._render()

    def _render(self):
        self.delete("all")
        # 绘制柔和呼吸光晕
        self.create_oval(3, 8, 15, 20, fill=self.color, outline="")
        # 发光内圈
        self.create_oval(5, 10, 13, 18, fill="#ffffff", outline="")
        self.create_oval(6, 11, 12, 17, fill=self.color, outline="")
        # 文字
        self.create_text(22, 14, text=self.text, anchor="w", fill="#cbd5e1", font=self.font_style)


class ModernPanel(tk.Frame):
    """卡片式现代面板控件"""
    def __init__(
        self,
        parent,
        title: str,
        subtitle: str,
        script_name: str,
        cmd_list: list[str],
        allow_input: bool = False,
        quick_actions: Optional[list[tuple[str, str | Callable[[], None]]]] = None,
        max_buffer_rows: int = 35
    ):
        super().__init__(
            parent,
            bg=THEME["bg_card"],
            highlightbackground=THEME["border_card"],
            highlightthickness=1,
            padx=12,
            pady=12
        )
        self.title = title
        self.subtitle = subtitle
        self.script_name = script_name
        self.cmd_list = cmd_list
        self.allow_input = allow_input
        self.quick_actions = quick_actions or []

        self.term_buffer = VirtualTerminalBuffer(max_rows=max_buffer_rows, cols=120)
        self.process: Optional[subprocess.Popen] = None
        self.output_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._closing = False
        self._last_rendered_text = ""

        self._build_header()
        self._build_toolbar()
        self._build_terminal()
        if self.allow_input:
            self._build_input_box()

        self._check_output_loop()

    def _build_header(self):
        head = tk.Frame(self, bg=THEME["bg_card"])
        head.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))

        # 标题与副标
        title_box = tk.Frame(head, bg=THEME["bg_card"])
        title_box.pack(side=tk.LEFT, fill=tk.Y)

        lbl_title = tk.Label(
            title_box,
            text=self.title,
            font=("Segoe UI", 11, "bold"),
            fg=THEME["text_main"],
            bg=THEME["bg_card"]
        )
        lbl_title.pack(anchor=tk.W)

        lbl_sub = tk.Label(
            title_box,
            text=self.subtitle,
            font=("Consolas", 8),
            fg=THEME["text_dim"],
            bg=THEME["bg_card"]
        )
        lbl_sub.pack(anchor=tk.W)

        # 状态徽章
        self.badge = StatusBadge(head, text="待命", color="#64748b")
        self.badge.pack(side=tk.RIGHT, padx=(0, 2))

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=THEME["bg_card"])
        bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))

        # 启停操作按键
        self.btn_start = ModernButton(
            bar, "▶ 启动", command=self.start_process,
            bg_color="#059669", hover_color="#10b981", width=62, height=26
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_stop = ModernButton(
            bar, "⏹ 停止", command=self.stop_process,
            bg_color="#dc2626", hover_color="#ef4444", width=62, height=26, state="disabled"
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_restart = ModernButton(
            bar, "🔄 重启", command=self.restart_process,
            bg_color="#0284c7", hover_color="#38bdf8", width=62, height=26, state="disabled"
        )
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_clear = ModernButton(
            bar, "清屏", command=self.clear_log,
            bg_color="#334155", hover_color="#475569", width=50, height=26
        )
        self.btn_clear.pack(side=tk.RIGHT, padx=0)

        # 快捷指令胶囊
        if self.quick_actions:
            pill_bar = tk.Frame(self, bg=THEME["bg_card"])
            pill_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))

            tag_lbl = tk.Label(
                pill_bar,
                text="快捷: ",
                font=("Segoe UI", 8),
                fg=THEME["text_dim"],
                bg=THEME["bg_card"]
            )
            tag_lbl.pack(side=tk.LEFT, padx=(0, 4))

            for label, act in self.quick_actions:
                if callable(act):
                    cmd = act
                else:
                    cmd = lambda v=act: self.send_input(v)
                b = ModernButton(
                    pill_bar,
                    label,
                    command=cmd,
                    bg_color="#1e293b",
                    hover_color="#334155",
                    text_color="#38bdf8",
                    border_color="#3b82f6",
                    font_style=("Segoe UI", 8, "bold"),
                    width=max(50, len(label) * 12 + 16),
                    height=24,
                    radius=4
                )
                b.pack(side=tk.LEFT, padx=(0, 4))

    def _build_terminal(self):
        # 拟真终端容器（带有微暗背景与内衬修饰）
        term_wrap = tk.Frame(
            self,
            bg=THEME["bg_card_inner"],
            highlightbackground=THEME["border_card"],
            highlightthickness=1
        )
        term_wrap.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self.log_text = tk.Text(
            term_wrap,
            wrap=tk.CHAR,
            bg=THEME["bg_card_inner"],
            fg=THEME["terminal_text"],
            insertbackground="#38bdf8",
            selectbackground="#334155",
            selectforeground="#ffffff",
            font=("Cascadia Code", 9) if "Cascadia Code" in tkfont.families() else ("Consolas", 9),
            relief=tk.FLAT,
            padx=10,
            pady=8,
            state=tk.DISABLED
        )
        scrollbar = tk.Scrollbar(
            term_wrap,
            orient=tk.VERTICAL,
            command=self.log_text.yview,
            bg=THEME["bg_card_inner"],
            troughcolor=THEME["bg_card_inner"],
            relief=tk.FLAT,
            width=10
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_input_box(self):
        box_frame = tk.Frame(self, bg=THEME["bg_card"])
        box_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

        self.entry_var = tk.StringVar()
        entry_wrap = tk.Frame(
            box_frame,
            bg=THEME["bg_card_inner"],
            highlightbackground=THEME["border_card"],
            highlightthickness=1,
            padx=8,
            pady=3
        )
        entry_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        prompt_icon = tk.Label(
            entry_wrap,
            text=">",
            font=("Consolas", 10, "bold"),
            fg=THEME["accent_blue"],
            bg=THEME["bg_card_inner"]
        )
        prompt_icon.pack(side=tk.LEFT, padx=(0, 4))

        self.entry_input = tk.Entry(
            entry_wrap,
            textvariable=self.entry_var,
            font=("Consolas", 9),
            bg=THEME["bg_card_inner"],
            fg=THEME["text_main"],
            insertbackground=THEME["accent_blue"],
            relief=tk.FLAT,
            highlightthickness=0
        )
        self.entry_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_input.bind("<Return>", lambda e: self._on_submit())

        btn_send = ModernButton(
            box_frame,
            "发送",
            command=self._on_submit,
            bg_color=THEME["btn_bg"],
            hover_color=THEME["accent_blue_hover"],
            width=58,
            height=28
        )
        btn_send.pack(side=tk.RIGHT)

    def _render_screen(self):
        new_text = self.term_buffer.get_display_text()
        if new_text == self._last_rendered_text:
            return
        self._last_rendered_text = new_text
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert("1.0", new_text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_log(self):
        self.term_buffer.clear()
        self._last_rendered_text = ""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def start_process(self):
        if self.is_running:
            return

        target_file = os.path.join(SCRIPT_DIR, self.script_name)
        if not os.path.exists(target_file):
            self.term_buffer.feed(f"[Error] 未找到脚本: {target_file}\n")
            self._render_screen()
            self.badge.update_status("丢失", THEME["accent_red"])
            return

        self.term_buffer.feed(f"✦ 正在载入任务: {self.title}\n")
        self._render_screen()
        self.badge.update_status("运行中", THEME["accent_green"])
        self.btn_start.set_state("disabled")
        self.btn_stop.set_state("normal")
        self.btn_restart.set_state("normal")
        self.is_running = True

        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )

            self.process = subprocess.Popen(
                self.cmd_list,
                cwd=SCRIPT_DIR,
                env=child_env,
                stdin=subprocess.PIPE if self.allow_input else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                startupinfo=startupinfo,
                creationflags=creationflags,
                bufsize=0
            )
        except Exception as err:
            self.term_buffer.feed(f"[Fail] 启动异常: {err}\n")
            self._render_screen()
            self._on_process_ended(-1)
            return

        threading.Thread(target=self._reader_thread, args=(self.process,), daemon=True).start()

    def _reader_thread(self, proc: subprocess.Popen):
        decoder_buf = b""
        while True:
            try:
                chunk = proc.stdout.read(64)
                if not chunk:
                    break
                decoder_buf += chunk
                try:
                    text = decoder_buf.decode("utf-8")
                    decoder_buf = b""
                except UnicodeDecodeError:
                    try:
                        text = decoder_buf.decode("gbk")
                        decoder_buf = b""
                    except UnicodeDecodeError:
                        continue
                self.output_queue.put(text)
            except Exception:
                break

        if decoder_buf:
            text = decoder_buf.decode("utf-8", errors="replace")
            self.output_queue.put(text)

        exit_code = proc.wait()
        self.output_queue.put(("__PROCESS_EXIT__", exit_code))

    def _check_output_loop(self):
        updated = False
        try:
            while not self.output_queue.empty():
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__PROCESS_EXIT__":
                    self._on_process_ended(item[1])
                else:
                    self.term_buffer.feed(str(item))
                    updated = True
        except Exception:
            pass

        if updated:
            self._render_screen()

        if not self._closing:
            self.after(50, self._check_output_loop)

    def _on_process_ended(self, exit_code: int):
        self.is_running = False
        self.process = None
        self.badge.update_status("已停止", THEME["accent_red"])
        self.btn_start.set_state("normal")
        self.btn_stop.set_state("disabled")
        self.btn_restart.set_state("disabled")
        self.term_buffer.feed(f"\n✦ 进程安全停止 (返回码: {exit_code})\n")
        self._render_screen()

    def send_input(self, text: str):
        if not self.is_running or not self.process or not self.process.stdin:
            self.term_buffer.feed("[提示] 服务未运行\n")
            self._render_screen()
            return
        try:
            self.term_buffer.feed(f"\n>>> 指令发送: {text}\n")
            self._render_screen()
            payload = f"{text}\n".encode("utf-8")
            self.process.stdin.write(payload)
            self.process.stdin.flush()
        except Exception as exc:
            self.term_buffer.feed(f"[发送错误] {exc}\n")
            self._render_screen()

    def _on_submit(self):
        val = self.entry_var.get().strip()
        if val:
            self.send_input(val)
            self.entry_var.set("")

    def stop_process(self):
        if not self.is_running or not self.process:
            return
        self.term_buffer.feed("✦ 正在终止进程树...\n")
        self._render_screen()
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process.terminate()
        except Exception as exc:
            self.term_buffer.feed(f"[停止异常] {exc}\n")
            self._render_screen()

    def restart_process(self):
        self.stop_process()
        self.after(600, self.start_process)

    def shutdown(self):
        self._closing = True
        if self.is_running and self.process:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    self.process.terminate()
            except Exception:
                pass


def clean_lingering_script_processes():
    """在 GUI 启动前或退出后，精确扫描并清理孤立的脚本历史残留进程"""
    if os.name != "nt":
        return
    try:
        my_pid = os.getpid()
        clean_ps = f"""
Get-CimInstance Win32_Process | Where-Object {{
    ($_.CommandLine -like '*vpscodex.py*' -or
     $_.CommandLine -like '*systemproxy_watcher.py*' -or
     $_.CommandLine -like '*bluetooth.ps1*') -and
    $_.ProcessId -ne {my_pid}
}} | ForEach-Object {{
    Stop-Process -Id $_.ProcessId -Force
}}
"""
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", clean_ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5
        )
    except Exception:
        pass


class ModernConsoleApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Nexus Hub · 自动化服务集成控制中心")
        self.geometry("1460x860")
        self.minsize(1050, 600)
        self.configure(bg=THEME["bg_main"])

        # 注入 Windows 原生深色标题栏
        apply_dark_titlebar(self)

        self._build_top_navbar()
        self._build_main_grid()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # 启动后依次自动加载运行
        self.after(300, self.start_all)

    def _build_top_navbar(self):
        nav = tk.Frame(
            self,
            bg=THEME["bg_main"],
            padx=24,
            pady=16
        )
        nav.pack(fill=tk.X, side=tk.TOP)

        # 品牌 LOGO 与主标
        brand_frame = tk.Frame(nav, bg=THEME["bg_main"])
        brand_frame.pack(side=tk.LEFT)

        icon_lbl = tk.Label(
            brand_frame,
            text="◈",
            font=("Segoe UI Symbol", 16, "bold"),
            fg=THEME["accent_blue"],
            bg=THEME["bg_main"]
        )
        icon_lbl.pack(side=tk.LEFT, padx=(0, 8))

        title_lbl = tk.Label(
            brand_frame,
            text="SERVICE MATRIX",
            font=("Segoe UI", 13, "bold"),
            fg=THEME["text_main"],
            bg=THEME["bg_main"]
        )
        title_lbl.pack(side=tk.LEFT)

        badge_sub = tk.Label(
            brand_frame,
            text=" v2.5 PRO ",
            font=("Consolas", 8, "bold"),
            fg=THEME["accent_blue"],
            bg="#1e293b",
            padx=4,
            pady=2
        )
        badge_sub.pack(side=tk.LEFT, padx=(8, 0))

        # 右侧全局控制按键
        ctrl_frame = tk.Frame(nav, bg=THEME["bg_main"])
        ctrl_frame.pack(side=tk.RIGHT)

        btn_all_start = ModernButton(
            ctrl_frame, "全部拉起", command=self.start_all,
            bg_color="#059669", hover_color="#10b981", width=76, height=28, radius=6
        )
        btn_all_start.pack(side=tk.LEFT, padx=4)

        btn_all_restart = ModernButton(
            ctrl_frame, "全量重启", command=self.restart_all,
            bg_color="#0284c7", hover_color="#38bdf8", width=76, height=28, radius=6
        )
        btn_all_restart.pack(side=tk.LEFT, padx=4)

        btn_all_stop = ModernButton(
            ctrl_frame, "全量停止", command=self.stop_all,
            bg_color="#dc2626", hover_color="#ef4444", width=76, height=28, radius=6
        )
        btn_all_stop.pack(side=tk.LEFT, padx=4)

        # 顶部与下层内容区域之间的精致渐变/深色分割过渡线
        sep = tk.Frame(self, bg=THEME["border_card"], height=1)
        sep.pack(fill=tk.X, side=tk.TOP)

    def _build_main_grid(self):
        # 容器内边距
        content_frame = tk.Frame(self, bg=THEME["bg_main"], padx=16, pady=16)
        content_frame.pack(fill=tk.BOTH, expand=True)

        # 3 列均等网格布局
        content_frame.grid_columnconfigure(0, weight=1, uniform="panels")
        content_frame.grid_columnconfigure(1, weight=1, uniform="panels")
        content_frame.grid_columnconfigure(2, weight=1, uniform="panels")
        content_frame.grid_rowconfigure(0, weight=1)

        # 面板 1: VPS 控制台
        vps_actions = [
            ("⚡ 唤醒", "1"),
            ("🌙 休眠", "2"),
            ("🛑 关机", "3"),
            ("🖥️ RDP连接", "4"),
        ]
        self.panel_vps = ModernPanel(
            content_frame,
            title="VPS 云节点控制",
            subtitle="vpscodex.py · Selenium Remote Driver",
            script_name="vpscodex.py",
            cmd_list=[sys.executable, "-u", "vpscodex.py"],
            allow_input=True,
            quick_actions=vps_actions,
            max_buffer_rows=40
        )
        self.panel_vps.grid(row=0, column=0, sticky="nsew", padx=6)

        # 面板 2: 系统代理监控
        proxy_actions = [
            ("10808", "1"),
            ("9990", "2"),
            ("8890", "3"),
            ("20808", "4"),
            ("7897", "5"),
        ]
        self.panel_proxy = ModernPanel(
            content_frame,
            title="系统代理守候",
            subtitle="systemproxy_watcher.py · Registry WinINet",
            script_name="systemproxy_watcher.py",
            cmd_list=[sys.executable, "-u", "systemproxy_watcher.py"],
            allow_input=True,
            quick_actions=proxy_actions,
            max_buffer_rows=32
        )
        self.panel_proxy.grid(row=0, column=1, sticky="nsew", padx=6)

        # 面板 3: 蓝牙保活服务
        self.panel_bt = ModernPanel(
            content_frame,
            title="蓝牙连接保活",
            subtitle="bluetooth.ps1 · PnP Power Management",
            script_name="bluetooth.bat",
            cmd_list=["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "bluetooth.ps1", "-Elevated"],
            allow_input=False,
            max_buffer_rows=32
        )
        self.panel_bt.grid(row=0, column=2, sticky="nsew", padx=6)

        self.panels = [self.panel_vps, self.panel_proxy, self.panel_bt]

    def start_all(self):
        for p in self.panels:
            p.start_process()

    def stop_all(self):
        for p in self.panels:
            p.stop_process()

    def restart_all(self):
        for p in self.panels:
            p.restart_process()

    def on_close(self):
        if any(p.is_running for p in self.panels):
            if not messagebox.askokcancel("确认退出", "后台服务正在持续运行中，是否全部终止并退出控制台？"):
                return
        for p in self.panels:
            p.shutdown()
        clean_lingering_script_processes()
        self.destroy()


def check_and_elevate_admin() -> bool:
    """Windows 原生 UAC 静默检测提权"""
    if os.name != "nt":
        return True
    try:
        if ctypes.windll.shell32.IsUserAnAdmin() != 0:
            return True
    except Exception:
        pass

    try:
        params = f'"{os.path.abspath(__file__)}"'
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            params,
            SCRIPT_DIR,
            1
        )
        return False
    except Exception:
        return True


def main():
    if not check_and_elevate_admin():
        sys.exit(0)

    # 启动前清理之前异常退出的残留进程
    clean_lingering_script_processes()

    app = ModernConsoleApp()
    app.mainloop()


if __name__ == "__main__":
    main()

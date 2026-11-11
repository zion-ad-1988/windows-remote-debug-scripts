# -*- coding: utf-8 -*-
"""
NEXUS · 服务集成操控台
设计语言：与 testApp_gui 一致的浅色工业精密风
- 暖灰工作区、仪器面板、单一工业橙强调色；浅色 / 深色可切换
- 三组服务全部平铺，无选项卡、折叠面板或隐藏式二级菜单
- 相同层级的 8px 间距、统一按钮高度与清晰键盘焦点
- 运行、危险、中性操作使用不同视觉等级，日志集中反馈
- Canvas 矢量图标，离线零依赖
"""

import sys
import os
import time
import math
import queue
import threading
import subprocess
import codecs
import ctypes
import tempfile
import atexit
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from typing import Optional, Callable
from terminal_buffer import VirtualTerminalBuffer
import proxy_config

# 脚本所在工作目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 窗口 / 任务栏图标资源与独立的 AppUserModelID
ICON_PNG = os.path.join(SCRIPT_DIR, "computer_desk.png")
APP_USER_MODEL_ID = "Nexus.Field.Deck.1"

# 当前用户 Internet 设置注册表路径（清除代理时直接写入）
REG_INTERNET = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

# 浅色工业精密风双主题调色板：与 testApp_gui 共用视觉令牌
THEMES = {
    "light": {
        "bg_deepest":   "#F2F3EF",     # 暖灰工作区
        "bg_panel":     "#FBFCF9",     # 仪器面板
        "bg_raise":     "#F4F6F1",     # 控件抬升面
        "bg_inset":     "#20261F",     # 高对比运行日志
        "line_soft":    "#D8DDD4",     # 发丝分隔线
        "line_strong":  "#B8C1B4",     # 强调描边
        "text_hi":      "#20261F",     # 墨绿黑主文字
        "text_mid":     "#4E5A4C",     # 次级文字
        "text_lo":      "#606C5E",     # 说明文字
        "term_text":    "#E6EADF",     # 终端文字
        "signal":       "#B84A24",     # 工业橙强调
        "signal_hover": "#963B1C",
        "signal_pressed": "#7C3016",
        "signal_on_dark": "#853316",   # 浅底上的橙色文字
        "go":           "#276A4D",     # 运行状态绿
        "go_bg":        "#276A4D",     # 启动键底
        "go_hover":     "#1E563D",
        "danger":       "#A63B36",     # 危险状态
        "danger_bg":    "#A63B36",     # 停止键底
        "danger_hover": "#87302C",
        "info":         "#3B657A",     # 系统信息
        "warn":         "#8A5A1B",     # 注意状态
        "violet":       "#655478",     # 云服务标签
        "chip_bg":      "#F4F6F1",
        "chip_border":  "#D8DDD4",
        "chip_text":    "#4E5A4C",
    },
    "dark": {
        "bg_deepest":   "#111410",
        "bg_panel":     "#191D18",
        "bg_raise":     "#22281F",
        "bg_inset":     "#0D100C",
        "line_soft":    "#323A2F",
        "line_strong":  "#4A5645",
        "text_hi":      "#ECEFE7",
        "text_mid":     "#B2BAAC",
        "text_lo":      "#899381",
        "term_text":    "#DCE3D6",
        "signal":       "#D16A3C",
        "signal_hover": "#E07B4D",
        "signal_pressed": "#B0522A",
        "signal_on_dark": "#F0A17D",
        "go":           "#59B98B",
        "go_bg":        "#2F7D57",
        "go_hover":     "#2A7252",
        "danger":       "#E38A82",
        "danger_bg":    "#B94B45",
        "danger_hover": "#87302C",
        "info":         "#76B7D1",
        "warn":         "#D9A441",
        "violet":       "#C4B5FD",
        "chip_bg":      "#22281F",
        "chip_border":  "#323A2F",
        "chip_text":    "#B2BAAC",
    }
}

# 当前活动主题全局指针（默认浅色工业风）
THEME = THEMES["light"]


def hex_to_colorref(hex_str: str) -> int:
    """将十六进制颜色字符串转换为 Windows COLORREF (0x00BBGGRR)"""
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_titlebar_theme(window: tk.Tk, is_dark: bool):
    """为 Windows 10/11 窗口设置匹配的暗黑或明亮标题栏"""
    if os.name != "nt":
        return
    try:
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()

        val = ctypes.c_int(1 if is_dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )

        theme_data = THEMES["dark" if is_dark else "light"]
        caption_color = ctypes.c_int(hex_to_colorref(theme_data["bg_panel"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )

        text_color = ctypes.c_int(hex_to_colorref(theme_data["text_hi"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
        )

        border_color = ctypes.c_int(hex_to_colorref(theme_data["line_soft"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color)
        )
    except Exception:
        pass


def set_app_user_model_id():
    """声明进程级 AppUserModelID，避免任务栏沿用 pythonw.exe 的默认图标分组"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def apply_window_icon(window: tk.Tk):
    """为窗口与任务栏设置 computer_desk.png 图标（多尺寸以适配任务栏/标题栏）"""
    if not os.path.exists(ICON_PNG):
        return
    try:
        images = []
        try:
            from PIL import Image, ImageTk
            with Image.open(ICON_PNG) as src:
                src = src.convert("RGBA")
                for size in (16, 24, 32, 48, 64, 128, 256):
                    images.append(ImageTk.PhotoImage(src.resize((size, size), Image.LANCZOS)))
        except Exception:
            images = [tk.PhotoImage(file=ICON_PNG)]

        window.iconphoto(True, *images)
        window._icon_refs = images

        if os.name == "nt":
            from PIL import Image
            fd, ico_path = tempfile.mkstemp(suffix=".ico", prefix="field_deck_")
            os.close(fd)
            with Image.open(ICON_PNG) as src:
                src = src.convert("RGBA")
                src.save(ico_path, format="ICO",
                         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

            window.update()
            load = ctypes.windll.user32.LoadImageW
            load.restype = ctypes.c_void_p
            load.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            big = load(0, ico_path, 1, 0, 0, 0x00000010)
            small = load(0, ico_path, 1, 16, 16, 0x00000010) or big
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if hwnd:
                hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd
                if big:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, big)
                if small:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, small)
                window._icon_handle = (big, small)

            def _cleanup_ico(path=ico_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            atexit.register(_cleanup_ico)
    except Exception:
        pass


def rounded_rect_points(w: float, h: float, r: float,
                        tl: bool = True, tr: bool = True,
                        br: bool = True, bl: bool = True) -> list[float]:
    """生成可选圆角的矩形多边形点列（圆弧离散采样，y 向下角度顺时针）"""
    pts: list[float] = []
    defs = [
        (tl, r, r, 180, 270, 0, 0),
        (tr, w - r, r, 270, 360, w, 0),
        (br, w - r, h - r, 0, 90, w, h),
        (bl, r, h - r, 90, 180, 0, h),
    ]
    for on, cx, cy, a0, a1, px, py in defs:
        if on and r > 0:
            steps = 6
            for i in range(steps + 1):
                a = math.radians(a0 + (a1 - a0) * i / steps)
                pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
        else:
            pts.extend([px, py])
    return pts


def draw_icon(cv: tk.Canvas, name: str, cx: float, cy: float, size: float, color: str):
    """FIELD DECK 仪器风图标：启停类实心填充、工具类锐利线性（24 网格）"""
    s = size / 24.0
    w = max(1.0, round(size / 11.0))

    def X(v):
        return cx + (v - 12) * s

    def Y(v):
        return cy + (v - 12) * s

    def line(*pts):
        cv.create_line(*pts, fill=color, width=w, capstyle=tk.ROUND, joinstyle=tk.ROUND)

    def poly(*pts):
        cv.create_polygon(*pts, outline=color, fill="", width=w, joinstyle=tk.ROUND)

    def solid(*pts):
        cv.create_polygon(*pts, outline=color, fill=color, width=1, joinstyle=tk.ROUND)

    if name == "play":
        solid(X(8), Y(5.2), X(19), Y(12), X(8), Y(18.8))
    elif name == "play-all":
        solid(X(5.4), Y(5.6), X(12.8), Y(12), X(5.4), Y(18.4))
        solid(X(12.6), Y(5.6), X(20), Y(12), X(12.6), Y(18.4))
    elif name == "stop":
        pts = rounded_rect_points(10 * s, 10 * s, 2 * s)
        pts = [X(7) + p if i % 2 == 0 else Y(7) + p for i, p in enumerate(pts)]
        cv.create_polygon(pts, outline=color, fill=color, width=1, joinstyle=tk.ROUND)
    elif name == "stop-all":
        cv.create_oval(X(3.2), Y(3.2), X(20.8), Y(20.8), outline=color, width=w)
        pts = rounded_rect_points(7 * s, 7 * s, 1.5 * s)
        pts = [X(8.5) + p if i % 2 == 0 else Y(8.5) + p for i, p in enumerate(pts)]
        cv.create_polygon(pts, outline=color, fill=color, width=1, joinstyle=tk.ROUND)
    elif name == "restart":
        cv.create_arc(X(3.6), Y(3.6), X(20.4), Y(20.4), style="arc",
                      outline=color, width=w, start=50, extent=290)
        solid(X(20.9), Y(2.6), X(20.9), Y(10), X(14.2), Y(9.2))
    elif name == "restart-all":
        cv.create_arc(X(3.6), Y(3.6), X(20.4), Y(20.4), style="arc",
                      outline=color, width=w, start=140, extent=235)
        solid(X(3.1), Y(2.6), X(3.1), Y(10), X(9.8), Y(9.2))
        cv.create_arc(X(3.6), Y(3.6), X(20.4), Y(20.4), style="arc",
                      outline=color, width=w, start=320, extent=235)
        solid(X(20.9), Y(14), X(20.9), Y(21.4), X(14.2), Y(14.8))
    elif name == "copy":
        cv.create_rectangle(X(8.5), Y(8.5), X(20.5), Y(20.5), outline=color, width=w)
        line(X(15.5), Y(8.5), X(15.5), Y(5), X(14), Y(3.5), X(5), Y(3.5), X(3.5), Y(5),
             X(3.5), Y(15.5), X(8.5), Y(15.5))
    elif name == "clear":
        poly(X(4), Y(16.5), X(9.8), Y(10.7), X(14.5), Y(6), X(18.5), Y(6),
             X(21), Y(8.5), X(21), Y(12.5), X(13.2), Y(20.3), X(7), Y(20.3))
        line(X(11.5), Y(13.5), X(17.8), Y(7.2))
        line(X(4), Y(20.3), X(20.5), Y(20.3))
    elif name == "send":
        line(X(21), Y(3), X(11.2), Y(12.8))
        poly(X(21), Y(3), X(14.6), Y(21), X(11.2), Y(12.8), X(3), Y(9.4))
    elif name == "brand":
        line(X(3), Y(21), X(21), Y(21))
        line(X(6.2), Y(17.2), X(6.2), Y(12.6))
        line(X(12), Y(17.2), X(12), Y(8.2))
        line(X(17.8), Y(17.2), X(17.8), Y(3.8))
    elif name == "sun":
        cv.create_oval(X(8), Y(8), X(16), Y(16), outline=color, fill=color, width=1)
        line(X(12), Y(2), X(12), Y(5))
        line(X(12), Y(19), X(12), Y(22))
        line(X(2), Y(12), X(5), Y(12))
        line(X(19), Y(12), X(22), Y(12))
        line(X(4.9), Y(4.9), X(7.1), Y(7.1))
        line(X(16.9), Y(16.9), X(19.1), Y(19.1))
        line(X(4.9), Y(19.1), X(7.1), Y(16.9))
        line(X(16.9), Y(7.1), X(19.1), Y(4.9))
    elif name == "moon":
        cv.create_arc(X(3), Y(3), X(21), Y(21), start=70, extent=220, style="arc", outline=color, width=w)
        cv.create_arc(X(7.5), Y(4), X(20.5), Y(20), start=70, extent=220, style="arc", outline=color, width=w)
    elif name == "sliders":
        # 配置台图标：双轨推子
        line(X(2.6), Y(7), X(21.4), Y(7))
        line(X(2.6), Y(17), X(21.4), Y(17))
        cv.create_rectangle(X(6.6), Y(4.2), X(10.4), Y(9.8), outline=color, fill=color, width=1)
        cv.create_rectangle(X(13.6), Y(14.2), X(17.4), Y(19.8), outline=color, fill=color, width=1)


def draw_brand_mark(cv: tk.Canvas, size: int, bg_color: str, text_color: str):
    """与 testApp_gui 品牌块一致：圆角工业橙方块 + 单字母字标。"""
    cv.delete("all")
    pts = rounded_rect_points(size - 2, size - 2, 8)
    pts = [p + 1 for p in pts]
    cv.create_polygon(pts, fill=bg_color, outline="", joinstyle=tk.ROUND)
    cv.create_text(size / 2, size / 2, text="N",
                   fill=text_color, font=("Segoe UI", max(15, int(size * 0.52)), "bold"))


class ToolTip:
    """轻量 Tooltip（延迟弹出、随指针离开销毁，仪器面板样式）"""
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self._win: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(350, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._win is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        lbl = tk.Label(
            win, text=self.text, font=("Segoe UI", 9),
            fg=THEME["text_hi"], bg=THEME["bg_raise"],
            padx=8, pady=4,
            highlightbackground=THEME["line_strong"], highlightthickness=1
        )
        lbl.pack()
        win.geometry(f"+{x}+{y}")
        self._win = win

    def _hide(self, _event=None):
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


class DeckButton(tk.Canvas):
    """仪器按键：可选单侧圆角（支持分段键组）、悬停/点击反馈、实心或线性左图标"""
    def __init__(
        self,
        parent,
        text: str,
        command: Optional[Callable[[], None]] = None,
        bg_color: Optional[str] = None,
        hover_color: Optional[str] = None,
        text_color: Optional[str] = None,
        font_style=("Segoe UI", 10, "bold"),
        width: int = 78,
        height: int = 34,
        radius: int = 6,
        corner: str = "both",          # both / left / right / none
        border_color: Optional[str] = None,
        state: str = "normal",
        icon: Optional[str] = None,
        icon_size: int = 14,
        icon_gap: int = 5,
        pad_x: int = 10
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=parent["bg"],
            highlightthickness=0,
            takefocus=1 if state == "normal" else 0,
            cursor="hand2" if state == "normal" else "arrow"
        )
        self.text = text
        self.command = command
        self.base_bg = bg_color or THEME["bg_raise"]
        self.hover_bg = hover_color or THEME["line_strong"]
        self.text_color = text_color or THEME["text_hi"]
        self.border_color = border_color or ""
        self.font_style = font_style
        self.w = width
        self.h = height
        self.radius = radius
        self.corner = corner
        self.state = state
        self.icon = icon
        self.icon_size = icon_size
        self.icon_gap = icon_gap
        self.pad_x = pad_x
        self._focused = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_keyboard_activate)
        self.bind("<space>", self._on_keyboard_activate)

        self._render(self.base_bg)

    def _render(self, current_bg: str):
        self.delete("all")
        w, h, r = self.w, self.h, self.radius
        tl = tr = br = bl = True
        if self.corner == "left":
            tr = br = False
        elif self.corner == "right":
            tl = bl = False
        elif self.corner == "none":
            tl = tr = br = bl = False

        if self.state == "disabled":
            fill_c = THEME["bg_panel"]
            text_c = THEME["text_lo"]
            outline_c = THEME["line_soft"]
            self.configure(cursor="arrow")
        else:
            fill_c = current_bg
            text_c = self.text_color
            outline_c = self.border_color
            self.configure(cursor="hand2")

        outline_width = 1 if outline_c else 0
        if self._focused and self.state == "normal":
            outline_c = THEME["signal"]
            outline_width = 2

        pts = rounded_rect_points(w - 2, h - 2, r, tl, tr, br, bl)
        pts = [p + 1 for p in pts]
        self.create_polygon(pts, fill=fill_c, outline=outline_c,
                            width=outline_width, joinstyle=tk.ROUND)

        if self.icon and not self.text:
            draw_icon(self, self.icon, w / 2, h / 2, self.icon_size, text_c)
        elif self.icon:
            icon_cx = self.pad_x + self.icon_size / 2
            draw_icon(self, self.icon, icon_cx, h / 2, self.icon_size, text_c)
            text_x = self.pad_x + self.icon_size + self.icon_gap
            self.create_text(text_x, h // 2, text=self.text, fill=text_c,
                             font=self.font_style, anchor="w")
        else:
            self.create_text(w // 2, h // 2, text=self.text, fill=text_c, font=self.font_style)

    def update_style(self, bg_color: str, hover_color: str, text_color: str, border_color: str = "", icon: Optional[str] = None):
        self.base_bg = bg_color
        self.hover_bg = hover_color
        self.text_color = text_color
        self.border_color = border_color
        if icon is not None:
            self.icon = icon
        self.configure(bg=self.master["bg"])
        self._render(self.base_bg)

    def set_state(self, new_state: str):
        self.state = new_state
        self.configure(takefocus=1 if new_state == "normal" else 0)
        if new_state != "normal" and self._focused:
            self._focused = False
        self._render(self.base_bg)

    def _on_enter(self, event):
        if self.state == "normal":
            self._render(self.hover_bg)

    def _on_leave(self, event):
        if self.state == "normal":
            self._render(self.base_bg)

    def _on_click(self, event):
        if self.state == "normal":
            self.focus_set()
            if self.command:
                self.command()

    def _on_keyboard_activate(self, _event=None):
        self._on_click(None)
        return "break"

    def _on_focus_in(self, _event=None):
        if self.state == "normal":
            self._focused = True
            self._render(self.base_bg)

    def _on_focus_out(self, _event=None):
        if self._focused:
            self._focused = False
            self._render(self.base_bg)


class LedBadge(tk.Canvas):
    """LED 状态指示（发光圆点 + 等宽大写丝印文字）"""
    def __init__(self, parent, text: str = "待命", color: str = "#606C5E",
                 font_style=("Segoe UI", 8, "bold"), width: int = 108):
        super().__init__(parent, width=width, height=24, bg=parent["bg"], highlightthickness=0)
        self.text = text
        self.color = color
        self.font_style = font_style
        self._render()

    def update_status(self, text: str, color: str):
        self.text = text
        self.color = color
        self.configure(bg=self.master["bg"])
        self._render()

    def _render(self):
        self.delete("all")
        self.create_oval(1, 6, 15, 20, fill=self.color, outline="")
        self.create_oval(4, 9, 12, 17, fill="#FFFFFF", outline="")
        self.create_oval(5, 10, 11, 16, fill=self.color, outline="")
        self.create_text(21, 12, text=self.text, anchor="w",
                         fill=THEME["text_mid"], font=self.font_style)


class MiniLed(tk.Canvas):
    """导航条服务微型 LED（标签 + 灯点）"""
    def __init__(self, parent, label: str):
        super().__init__(parent, width=52, height=16, bg=parent["bg"], highlightthickness=0)
        self.label = label
        self.on = False
        self._render()

    def set_on(self, on: bool):
        if self.on != on:
            self.on = on
            self._render()

    def apply_theme(self):
        """主题切换时强制重绘，避免 Canvas 残留旧主题底色。"""
        self.configure(bg=self.master["bg"])
        self._render()

    def _render(self):
        self.delete("all")
        c = THEME["go"] if self.on else THEME["line_strong"]
        self.configure(bg=self.master["bg"])
        self.create_oval(1, 4, 9, 12, fill=c, outline="")
        self.create_text(14, 8, text=self.label, anchor="w",
                         fill=THEME["text_mid"] if self.on else THEME["text_lo"],
                         font=("Segoe UI", 8, "bold"))


def clear_system_proxy_registry() -> Optional[str]:
    """关闭并清空当前用户的系统代理（守候未运行时使用）。

    返回 None 表示成功，否则返回错误描述。守候脚本运行中不走这里，
    由脚本自身清除以便其 5s 校验持续保持关闭状态。
    """
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_INTERNET, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "")
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "")
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)
        return None
    except OSError as exc:
        return str(exc)


class ServicePanel(tk.Frame):
    """服务操控面板（丝印编号 + 模块标签 + 启停键组 + 指令芯片 + 终端缓冲区）"""
    def __init__(
        self,
        parent,
        index: str,
        title: str,
        subtitle: str,
        tag: str,
        tag_color: str,
        script_name: str,
        cmd_list: list[str],
        allow_input: bool = False,
        quick_actions: Optional[list[tuple[str, str | Callable[[], None]]]] = None,
        fixed_actions: Optional[list[tuple[str, str | Callable[[], None]]]] = None,
        config_command: Optional[Callable[[], None]] = None,
        max_buffer_rows: int = 35,
        watchdog_seconds: int = 0,
        on_status_change: Optional[Callable[[], None]] = None
    ):
        super().__init__(
            parent,
            bg=THEME["bg_panel"],
            highlightbackground=THEME["line_soft"],
            highlightthickness=1,
            padx=14,
            pady=14
        )
        self.index = index
        self.title = title
        self.subtitle = subtitle
        self.tag = tag
        self.tag_color = tag_color
        self.script_name = script_name
        self.cmd_list = cmd_list
        self.allow_input = allow_input
        self.quick_actions = quick_actions or []
        self.fixed_actions = fixed_actions or []
        self.config_command = config_command
        self.on_status_change = on_status_change

        self.term_buffer = VirtualTerminalBuffer(max_rows=max_buffer_rows, cols=120)
        self.process: Optional[subprocess.Popen] = None
        self.output_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._closing = False
        self._last_rendered_text = ""
        self.chips: list[DeckButton] = []

        # 静默看门狗：进程存活但长时间无任何输出即判定无响应，自动重建
        self.watchdog_seconds = watchdog_seconds
        self._last_output_ts = time.monotonic()
        self._restart_stamps: list[float] = []
        self._watchdog_retry = 0

        self._build_header()
        self._build_toolbar()
        self._build_terminal()
        if self.allow_input:
            self._build_input_box()

        self.term_buffer.feed(f"就绪 · {self.script_name} 尚未启动\n")
        self._render_screen()
        self._check_output_loop()

    def _build_header(self):
        self.head = tk.Frame(self, bg=THEME["bg_panel"])
        self.head.pack(fill=tk.X, side=tk.TOP, pady=(0, 10))

        self.left_box = tk.Frame(self.head, bg=THEME["bg_panel"])
        self.left_box.pack(side=tk.LEFT, fill=tk.Y)

        self.row1 = tk.Frame(self.left_box, bg=THEME["bg_panel"])
        self.row1.pack(anchor=tk.W)

        self.idx_lbl = tk.Label(
            self.row1, text=self.index, font=("Consolas", 10, "bold"),
            fg=THEME["bg_panel"], bg=THEME["signal"], padx=6, pady=2
        )
        self.idx_lbl.pack(side=tk.LEFT, padx=(0, 9), pady=1)

        self.tag_lbl = tk.Label(
            self.row1, text=f" {self.tag} ", font=("Segoe UI", 8, "bold"),
            fg=self.tag_color, bg=THEME["bg_raise"], padx=5, pady=2
        )
        self.tag_lbl.pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_title = tk.Label(
            self.row1, text=self.title, font=("Segoe UI", 13, "bold"),
            fg=THEME["text_hi"], bg=THEME["bg_panel"]
        )
        self.lbl_title.pack(side=tk.LEFT)

        self.lbl_sub = tk.Label(
            self.left_box, text=self.subtitle, font=("Segoe UI", 9),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        self.lbl_sub.pack(anchor=tk.W, pady=(4, 0))

        self.badge = LedBadge(self.head, text="待命", color=THEME["text_lo"])
        self.badge.pack(side=tk.RIGHT, padx=(0, 2))

        self.rule = tk.Frame(self, bg=THEME["line_soft"], height=1)
        self.rule.pack(fill=tk.X, side=tk.TOP, pady=(0, 10))
        self.tick = tk.Frame(self.rule, bg=THEME["signal"], width=28, height=1)
        self.tick.pack(side=tk.LEFT)
        self.line = tk.Frame(self.rule, bg=THEME["line_soft"], height=1)
        self.line.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_toolbar(self):
        self.toolbar = tk.Frame(self, bg=THEME["bg_panel"])
        self.toolbar.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))

        self.btn_start = DeckButton(
            self.toolbar, "启动", command=self.start_process,
            bg_color=THEME["go_bg"], hover_color=THEME["go_hover"], text_color="#FFFFFF",
            width=78, height=34, radius=6, icon="play", icon_size=14
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(self.btn_start, f"启动 {self.title}")

        self.btn_stop = DeckButton(
            self.toolbar, "停止", command=self.stop_process,
            bg_color=THEME["danger_bg"], hover_color=THEME["danger_hover"], text_color="#FFFFFF",
            width=78, height=34, radius=6, state="disabled", icon="stop", icon_size=14
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(self.btn_stop, f"停止 {self.title}")

        self.btn_restart = DeckButton(
            self.toolbar, "重启", command=self.restart_process,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["signal_on_dark"], border_color=THEME["line_strong"],
            width=78, height=34, radius=6, state="disabled", icon="restart", icon_size=14
        )
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(self.btn_restart, f"重启 {self.title}")

        self.btn_clear = DeckButton(
            self.toolbar, "", command=self.clear_log,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["text_mid"],
            width=28, height=28, radius=6, icon="clear", icon_size=13
        )
        self.btn_clear.pack(side=tk.RIGHT, padx=(5, 0), pady=2)
        ToolTip(self.btn_clear, "清空终端缓冲区")

        self.btn_copy = DeckButton(
            self.toolbar, "", command=self.copy_log,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["text_mid"],
            width=28, height=28, radius=6, icon="copy", icon_size=13
        )
        self.btn_copy.pack(side=tk.RIGHT, pady=2)
        ToolTip(self.btn_copy, "复制终端输出")

        if self.config_command:
            self.btn_config = DeckButton(
                self.toolbar, "", command=self.config_command,
                bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
                text_color=THEME["text_mid"], border_color=THEME["line_soft"],
                width=28, height=28, radius=6, icon="sliders", icon_size=13
            )
            self.btn_config.pack(side=tk.RIGHT, padx=(5, 0), pady=2)
            ToolTip(self.btn_config, "代理配置 · 快捷指令与脚本代理池")

        if self.quick_actions or self.fixed_actions:
            self._build_chip_bar()

    def _build_chip_bar(self, before: Optional[tk.Widget] = None):
        """构建指令芯片条；before 用于运行时重建时插回终端区之前。"""
        self.chip_bar = tk.Frame(self, bg=THEME["bg_panel"])
        pack_opts = {"fill": tk.X, "side": tk.TOP, "pady": (0, 8)}
        if before is not None:
            pack_opts["before"] = before
        self.chip_bar.pack(**pack_opts)

        self.cmd_tag = tk.Label(
            self.chip_bar, text="CMD", font=("Segoe UI", 8, "bold"),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        self.cmd_tag.pack(side=tk.LEFT, padx=(0, 7))
        self._spawn_chips()

    def _spawn_chips(self):
        """按当前 quick_actions 重建芯片按钮（保留 CMD 标签）。"""
        for child in list(self.chip_bar.winfo_children()):
            if child is not getattr(self, "cmd_tag", None):
                child.destroy()
        self.chips.clear()

        for label, act in self.quick_actions:
            if callable(act):
                cmd = act
            else:
                cmd = lambda v=act: self.send_input(v)
            b = DeckButton(
                self.chip_bar, label, command=cmd,
                bg_color=THEME["chip_bg"], hover_color=THEME["line_strong"],
                text_color=THEME["chip_text"], border_color=THEME["chip_border"],
                font_style=("Consolas", 9, "bold"),
                width=max(54, len(label) * 10 + 16), height=24, radius=4
            )
            b.pack(side=tk.LEFT, padx=(0, 5))
            ToolTip(b, "点击下发指令到服务终端")
            self.chips.append(b)

        # 固定动作芯片：与可配置指令区分，使用警示色描边
        for label, act in self.fixed_actions:
            if callable(act):
                cmd = act
            else:
                cmd = lambda v=act: self.send_input(v)
            b = DeckButton(
                self.chip_bar, label, command=cmd,
                bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
                text_color=THEME["danger"], border_color=THEME["danger"],
                font_style=("Segoe UI", 9, "bold"),
                width=max(60, len(label) * 13 + 14), height=24, radius=4
            )
            b.pack(side=tk.LEFT, padx=(10, 0))
            ToolTip(b, "清除系统代理，守候将保持关闭状态")
            self.chips.append(b)

    def set_quick_actions(self, actions: list[tuple[str, str]]):
        """运行时替换指令芯片组（代理配置变更后调用）。"""
        self.quick_actions = list(actions or [])
        alive = hasattr(self, "chip_bar") and self.chip_bar.winfo_exists()

        if not self.quick_actions and not self.fixed_actions:
            if alive:
                self.chip_bar.destroy()
            self.chips.clear()
            return

        if alive:
            self._spawn_chips()
        else:
            self._build_chip_bar(before=self.term_wrap)

    def _build_terminal(self):
        self.term_wrap = tk.Frame(
            self,
            bg=THEME["bg_inset"],
            highlightbackground=THEME["line_soft"],
            highlightthickness=1
        )
        self.term_wrap.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self.term_head = tk.Frame(self.term_wrap, bg=THEME["bg_inset"], padx=10, pady=5)
        self.term_head.pack(fill=tk.X, side=tk.TOP)
        self.term_prompt = tk.Label(
            self.term_head, text="▍", font=("Segoe UI", 9, "bold"),
            fg=THEME["signal"], bg=THEME["bg_inset"]
        )
        self.term_prompt.pack(side=tk.LEFT)
        self.term_script_lbl = tk.Label(
            self.term_head, text="LOG  " + self.script_name, font=("Consolas", 9),
            fg=THEME["text_lo"], bg=THEME["bg_inset"]
        )
        self.term_script_lbl.pack(side=tk.LEFT, padx=(1, 0))

        self.head_sep = tk.Frame(self.term_wrap, bg=THEME["line_soft"], height=1)
        self.head_sep.pack(fill=tk.X, side=tk.TOP)

        font_family = "Cascadia Code" if "Cascadia Code" in tkfont.families() else "Consolas"
        self.log_text = tk.Text(
            self.term_wrap,
            wrap=tk.CHAR,
            bg=THEME["bg_inset"],
            fg=THEME["term_text"],
            insertbackground=THEME["signal"],
            selectbackground=THEME["signal"],
            selectforeground=THEME["bg_panel"],
            font=(font_family, 10),
            relief=tk.FLAT,
            padx=12,
            pady=10,
            state=tk.DISABLED
        )
        self.scrollbar = tk.Scrollbar(
            self.term_wrap,
            orient=tk.VERTICAL,
            command=self.log_text.yview,
            bg=THEME["bg_inset"],
            troughcolor=THEME["bg_inset"],
            relief=tk.FLAT,
            width=9
        )
        self.log_text.configure(yscrollcommand=self.scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_input_box(self):
        self.box_frame = tk.Frame(self, bg=THEME["bg_panel"])
        self.box_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

        self.entry_var = tk.StringVar()
        self.entry_wrap = tk.Frame(
            self.box_frame,
            bg=THEME["bg_inset"],
            highlightbackground=THEME["line_soft"],
            highlightthickness=1,
            padx=8,
            pady=2
        )
        self.entry_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.prompt_icon = tk.Label(
            self.entry_wrap, text="▍", font=("Segoe UI", 10, "bold"),
            fg=THEME["signal"], bg=THEME["bg_inset"]
        )
        self.prompt_icon.pack(side=tk.LEFT, padx=(0, 5))

        self.entry_input = tk.Entry(
            self.entry_wrap,
            textvariable=self.entry_var,
            bg=THEME["bg_inset"],
            fg=THEME["term_text"],
            insertbackground=THEME["signal"],
            selectbackground=THEME["signal"],
            selectforeground=THEME["bg_panel"],
            relief=tk.FLAT,
            font=("Consolas", 10)
        )
        self.entry_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_input.bind("<Return>", lambda e: self._on_submit())

        self.btn_send = DeckButton(
            self.box_frame, "发送", command=self._on_submit,
            bg_color=THEME["signal"], hover_color=THEME["signal_hover"],
            text_color=THEME["bg_panel"],
            width=72, height=32, radius=6, icon="send", icon_size=13
        )
        self.btn_send.pack(side=tk.RIGHT)
        ToolTip(self.btn_send, "发送指令到服务 stdin")

    def apply_theme(self):
        """随全局主题动态更新本面板各层控件"""
        self.configure(bg=THEME["bg_panel"], highlightbackground=THEME["line_soft"])
        self.head.configure(bg=THEME["bg_panel"])
        self.left_box.configure(bg=THEME["bg_panel"])
        self.row1.configure(bg=THEME["bg_panel"])
        self.idx_lbl.configure(bg=THEME["signal"], fg=THEME["bg_panel"])
        self.tag_lbl.configure(bg=THEME["bg_raise"], fg=self.tag_color)
        self.lbl_title.configure(bg=THEME["bg_panel"], fg=THEME["text_hi"])
        self.lbl_sub.configure(bg=THEME["bg_panel"], fg=THEME["text_lo"])
        self.badge.update_status(self.badge.text, self.badge.color)
        self.rule.configure(bg=THEME["line_soft"])
        self.tick.configure(bg=THEME["signal"])
        self.line.configure(bg=THEME["line_soft"])
        self.toolbar.configure(bg=THEME["bg_panel"])

        self.btn_start.update_style(THEME["go_bg"], THEME["go_hover"], "#FFFFFF")
        self.btn_stop.update_style(THEME["danger_bg"], THEME["danger_hover"], "#FFFFFF")
        self.btn_restart.update_style(THEME["bg_raise"], THEME["line_strong"], THEME["signal_on_dark"], border_color=THEME["line_strong"])
        self.btn_clear.update_style(THEME["bg_raise"], THEME["line_strong"], THEME["text_mid"])
        self.btn_copy.update_style(THEME["bg_raise"], THEME["line_strong"], THEME["text_mid"])
        if self.config_command:
            self.btn_config.update_style(
                THEME["bg_raise"], THEME["line_strong"], THEME["text_mid"],
                border_color=THEME["line_soft"]
            )

        if getattr(self, "chip_bar", None) is not None and self.chip_bar.winfo_exists():
            self.chip_bar.configure(bg=THEME["bg_panel"])
            self.cmd_tag.configure(bg=THEME["bg_panel"], fg=THEME["text_lo"])
            for c in self.chips:
                c.update_style(THEME["chip_bg"], THEME["line_strong"], THEME["chip_text"], border_color=THEME["chip_border"])

        self.term_wrap.configure(bg=THEME["bg_inset"], highlightbackground=THEME["line_soft"])
        self.term_head.configure(bg=THEME["bg_inset"])
        self.term_prompt.configure(bg=THEME["bg_inset"], fg=THEME["signal"])
        self.term_script_lbl.configure(bg=THEME["bg_inset"], fg=THEME["text_lo"])
        self.head_sep.configure(bg=THEME["line_soft"])
        self.log_text.configure(
            bg=THEME["bg_inset"], fg=THEME["term_text"],
            insertbackground=THEME["signal"],
            selectbackground=THEME["signal"], selectforeground=THEME["bg_panel"]
        )
        self.scrollbar.configure(bg=THEME["bg_inset"], troughcolor=THEME["bg_inset"])

        if self.allow_input:
            self.box_frame.configure(bg=THEME["bg_panel"])
            self.entry_wrap.configure(bg=THEME["bg_inset"], highlightbackground=THEME["line_soft"])
            self.prompt_icon.configure(bg=THEME["bg_inset"], fg=THEME["signal"])
            self.entry_input.configure(
                bg=THEME["bg_inset"], fg=THEME["term_text"],
                insertbackground=THEME["signal"],
                selectbackground=THEME["signal"], selectforeground=THEME["bg_panel"]
            )
            self.btn_send.update_style(THEME["signal"], THEME["signal_hover"], THEME["bg_panel"])

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
        if self.is_running and self.process and self.process.stdin and self.allow_input:
            try:
                self.process.stdin.write(b"cls\n")
                self.process.stdin.flush()
            except Exception:
                pass

    def copy_log(self):
        text = self.term_buffer.get_display_text()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.term_buffer.feed("› [deck] 终端输出已复制到剪贴板\n")
            self._render_screen()

    def append_log(self, text: str):
        """由外部（操控台）向本面板终端追加一行反馈。"""
        self.term_buffer.feed(text)
        self._render_screen()

    def start_process(self):
        if self.is_running:
            return

        target_file = os.path.join(SCRIPT_DIR, self.script_name)
        if not os.path.exists(target_file):
            self.term_buffer.feed(f"[Error] 未找到脚本: {target_file}\n")
            self._render_screen()
            self.badge.update_status("缺失", THEME["danger"])
            if self.on_status_change:
                self.on_status_change()
            return

        self.term_buffer.feed(f"› 载入任务模块: {self.title}\n")
        self._render_screen()
        self.badge.update_status("运行中", THEME["go"])
        self.btn_start.set_state("disabled")
        self.btn_stop.set_state("normal")
        self.btn_restart.set_state("normal")
        self.is_running = True
        self._last_output_ts = time.monotonic()
        if self.on_status_change:
            self.on_status_change()

        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
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
        utf8_decoder = codecs.getincrementaldecoder("utf-8")()
        gbk_decoder = codecs.getincrementaldecoder("gbk")()
        use_gbk = False

        while True:
            try:
                chunk = proc.stdout.read(1024)
                if not chunk:
                    break
                self._last_output_ts = time.monotonic()
                if not use_gbk:
                    try:
                        text = utf8_decoder.decode(chunk)
                        if text:
                            self.output_queue.put(text)
                    except UnicodeDecodeError:
                        use_gbk = True
                        text = gbk_decoder.decode(chunk, final=False)
                        if text:
                            self.output_queue.put(text)
                else:
                    text = gbk_decoder.decode(chunk, final=False)
                    if text:
                        self.output_queue.put(text)
            except Exception:
                break

        try:
            rem = gbk_decoder.decode(b"", final=True) if use_gbk else utf8_decoder.decode(b"", final=True)
            if rem:
                self.output_queue.put(rem)
        except Exception:
            pass

        exit_code = proc.wait()
        self.output_queue.put(("__PROCESS_EXIT__", exit_code, proc))

    def _check_output_loop(self):
        updated = False
        try:
            while not self.output_queue.empty():
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__PROCESS_EXIT__":
                    proc_ref = item[2] if len(item) > 2 else None
                    self._on_process_ended(item[1], proc_ref)
                else:
                    self.term_buffer.feed(str(item))
                    updated = True
        except Exception:
            pass

        if updated:
            self._render_screen()

        self._check_watchdog()

        if not self._closing:
            self.after(50, self._check_output_loop)

    def _check_watchdog(self):
        """进程仍存活却长时间零输出 → 判定无响应，终止并自动重建。"""
        if not self.watchdog_seconds or not self.is_running:
            return

        silent = time.monotonic() - self._last_output_ts
        if silent <= self.watchdog_seconds:
            return

        now = time.monotonic()
        self._restart_stamps = [t for t in self._restart_stamps if now - t < 3600]
        self._last_output_ts = now

        self.term_buffer.feed(
            f"\n[看门狗] {int(silent)} 秒无任何输出，判定进程无响应，正在重建\n"
        )
        self._render_screen()

        if len(self._restart_stamps) >= 5:
            self.term_buffer.feed("[看门狗] 一小时内自愈次数已达上限，需人工排查原因\n")
            self._render_screen()
            self.stop_process()
            return

        self._restart_stamps.append(now)
        self.stop_process()
        self.after(1500, self._restart_after_watchdog)

    def _restart_after_watchdog(self):
        if self._closing:
            return
        if self.is_running:
            # 终止信号尚未生效：有限次重试，仍失败则放弃旧句柄直接重建
            self._watchdog_retry += 1
            if self._watchdog_retry <= 4:
                self.stop_process()
                self.after(800, self._restart_after_watchdog)
                return
            self.term_buffer.feed("[看门狗] 旧进程无法结束，丢弃句柄后强制重建\n")
            self._on_process_ended(-1, self.process)
        self._watchdog_retry = 0
        self.term_buffer.feed("[看门狗] 已重新拉起服务\n")
        self._render_screen()
        self.start_process()

    def _on_process_ended(self, exit_code: int, proc=None):
        if proc is not None and self.process is not None and self.process is not proc:
            # 旧进程迟到的退出通知，当前已有新会话，忽略
            return
        self.is_running = False
        self.process = None
        self.badge.update_status("已停止", THEME["danger"])
        self.btn_start.set_state("normal")
        self.btn_stop.set_state("disabled")
        self.btn_restart.set_state("disabled")
        self.term_buffer.feed(f"\n› 进程已停止 (返回码: {exit_code})\n")
        self._render_screen()
        if self.on_status_change:
            self.on_status_change()

    def send_input(self, text: str):
        if not self.is_running or not self.process or not self.process.stdin:
            self.term_buffer.feed("[提示] 服务未运行\n")
            self._render_screen()
            return

        clean_text = text.strip()
        if clean_text.lower() in ("cls", "clear"):
            self.clear_log()
            return

        try:
            payload = f"{clean_text}\n".encode("utf-8")
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
        self.term_buffer.feed("› 正在终止进程树...\n")
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
        # 除脚本自身进程外，同时清理：带本程序 profile 标记的 chrome、
        # 以及父进程已消失的孤儿 chromedriver（脚本命令行里不含它们的名字，
        # 旧版仅按 vpscodex.py 匹配，导致每次强杀都残留整棵浏览器进程树）。
        clean_ps = f"""
$pattern = 'vpscodex\\.py|vpscodex-chrome-|systemproxy_watcher\\.py|bluetooth\\.ps1'
Get-CimInstance Win32_Process | Where-Object {{
    $_.ProcessId -ne {my_pid} -and $_.CommandLine -and $_.CommandLine -match $pattern
}} | ForEach-Object {{
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}}
Get-CimInstance Win32_Process -Filter "Name='chromedriver.exe'" | ForEach-Object {{
    if (-not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)) {{
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }}
}}
"""
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", clean_ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=25
        )
    except Exception:
        pass


class ProxyConfigDialog(tk.Toplevel):
    """代理配置台：维护窗口快捷指令与守候脚本预设代理池。

    两处变更统一写入 %LOCALAPPDATA%\\NexusFieldDeck\\proxy_config.json，
    守候脚本启动时加载该文件，因此跨重启永久生效。
    """

    QUICK_AREA_H = 138   # 约 4 行可视
    POOL_AREA_H = 170    # 约 5 行可视

    def __init__(self, master: tk.Tk, on_apply: Callable[[dict], None]):
        super().__init__(master)
        self.withdraw()
        self.title("代理配置 · PROXY CONFIG")
        self.configure(bg=THEME["bg_deepest"])
        self.resizable(False, False)
        self.transient(master)

        self._on_apply = on_apply
        self._quick_rows: list[dict] = []
        self._pool_rows: list[dict] = []

        self._build()
        self._populate(proxy_config.load())

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

        self.update_idletasks()
        self._center_on(master)
        self.deiconify()
        self.grab_set()

    # ---------- 构建 ----------
    def _build(self):
        root = tk.Frame(self, bg=THEME["bg_deepest"], padx=20, pady=16)
        root.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(root, bg=THEME["bg_deepest"])
        head.pack(fill=tk.X)
        tk.Label(
            head, text="代理配置台", font=("Segoe UI", 15, "bold"),
            fg=THEME["text_hi"], bg=THEME["bg_deepest"]
        ).pack(side=tk.LEFT)
        tk.Label(
            head, text="PROXY CONFIG", font=("Consolas", 9, "bold"),
            fg=THEME["text_lo"], bg=THEME["bg_deepest"]
        ).pack(side=tk.LEFT, padx=(10, 0), pady=(4, 0))

        tk.Label(
            root,
            text="统一维护操控台指令芯片与守候脚本预设代理池 · 变更写入 proxy_config.json 后永久生效",
            font=("Segoe UI", 9), fg=THEME["text_lo"], bg=THEME["bg_deepest"]
        ).pack(anchor=tk.W, pady=(6, 10))

        rule = tk.Frame(root, bg=THEME["line_soft"], height=1)
        rule.pack(fill=tk.X, pady=(0, 12))

        self._build_section(
            root, "01", "窗口快捷指令", "QUICK ACTIONS",
            "显示为面板上的 CMD 芯片", self.QUICK_AREA_H, "quick"
        )
        self._build_section(
            root, "02", "脚本代理池", "PROXY_POOL",
            "序号 1 为默认代理 · 终端按 1~N 切换", self.POOL_AREA_H, "pool"
        )

        self.status_lbl = tk.Label(
            root, text="", font=("Segoe UI", 9, "bold"), anchor=tk.W,
            fg=THEME["text_mid"], bg=THEME["bg_deepest"]
        )
        self.status_lbl.pack(fill=tk.X, pady=(0, 12))

        foot = tk.Frame(root, bg=THEME["bg_deepest"])
        foot.pack(fill=tk.X)

        tk.Label(
            foot, text=proxy_config.config_path(), font=("Consolas", 8),
            fg=THEME["text_lo"], bg=THEME["bg_deepest"], anchor=tk.W
        ).pack(side=tk.LEFT)

        DeckButton(
            foot, "应用并生效", command=self._apply,
            bg_color=THEME["signal"], hover_color=THEME["signal_hover"],
            text_color="#FFFFFF", width=112, height=32, radius=6
        ).pack(side=tk.RIGHT, padx=(6, 0))

        DeckButton(
            foot, "恢复默认", command=self._reset_defaults,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["signal_on_dark"], border_color=THEME["line_soft"],
            width=88, height=32, radius=6
        ).pack(side=tk.RIGHT, padx=(6, 0))

        DeckButton(
            foot, "取消", command=self._close,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["text_mid"], border_color=THEME["line_soft"],
            width=76, height=32, radius=6
        ).pack(side=tk.RIGHT)

    def _build_section(self, parent, index: str, title: str, tag: str,
                       note: str, height: int, section_key: str):
        card = tk.Frame(
            parent, bg=THEME["bg_panel"],
            highlightbackground=THEME["line_soft"], highlightthickness=1,
            padx=14, pady=12
        )
        card.pack(fill=tk.X, pady=(0, 10))

        head = tk.Frame(card, bg=THEME["bg_panel"])
        head.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            head, text=index, font=("Consolas", 10, "bold"),
            fg=THEME["bg_panel"], bg=THEME["signal"], padx=6, pady=1
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(
            head, text=title, font=("Segoe UI", 11, "bold"),
            fg=THEME["text_hi"], bg=THEME["bg_panel"]
        ).pack(side=tk.LEFT)
        tk.Label(
            head, text=tag, font=("Consolas", 8, "bold"),
            fg=THEME["info"], bg=THEME["bg_raise"], padx=5, pady=2
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            head, text=note, font=("Segoe UI", 8),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        ).pack(side=tk.RIGHT, pady=(2, 0))

        wrap, canvas, inner = self._scroll_area(card, height)
        wrap.pack(fill=tk.X)

        footer = tk.Frame(card, bg=THEME["bg_panel"])
        footer.pack(fill=tk.X, pady=(8, 0))

        btn_add = DeckButton(
            footer, "新增", command=lambda: self._add_row(section_key, "", focus=True),
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["text_hi"], border_color=THEME["line_soft"],
            font_style=("Segoe UI", 9, "bold"),
            width=62, height=26, radius=5, icon="play", icon_size=9, pad_x=8
        )
        btn_add.pack(side=tk.LEFT)
        ToolTip(btn_add, "追加一行配置")

        tk.Label(
            footer, text="支持 端口 / host:port / http://host:port", font=("Segoe UI", 8),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        ).pack(side=tk.LEFT, padx=(10, 0))

        if section_key == "quick":
            self._quick_canvas, self._quick_inner = canvas, inner
            self._quick_scroll = wrap
        else:
            self._pool_canvas, self._pool_inner = canvas, inner
            self._pool_scroll = wrap

    def _scroll_area(self, parent, height: int):
        wrap = tk.Frame(
            parent, bg=THEME["bg_panel"],
            highlightbackground=THEME["line_soft"], highlightthickness=1
        )
        canvas = tk.Canvas(wrap, bg=THEME["bg_panel"], highlightthickness=0,
                           bd=0, height=height)
        bar = tk.Scrollbar(
            wrap, orient=tk.VERTICAL, command=canvas.yview, width=9,
            bg=THEME["bg_panel"], troughcolor=THEME["bg_panel"], relief=tk.FLAT
        )
        inner = tk.Frame(canvas, bg=THEME["bg_panel"])
        canvas.configure(yscrollcommand=bar.set)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window, width=max(1, canvas.winfo_width()))

        inner.bind("<Configure>", _sync)
        canvas.bind("<Configure>", _sync)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        bar.pack(side=tk.RIGHT, fill=tk.Y, pady=1, padx=(0, 1))

        tk.Frame(inner, bg=THEME["bg_panel"], height=5).pack(fill=tk.X)
        return wrap, canvas, inner

    # ---------- 行编辑 ----------
    def _rows_of(self, section: str) -> list[dict]:
        return self._quick_rows if section == "quick" else self._pool_rows

    def _canvas_of(self, section: str) -> tk.Canvas:
        return self._quick_canvas if section == "quick" else self._pool_canvas

    def _add_row(self, section: str, value: str = "", focus: bool = False):
        rows = self._rows_of(section)
        inner = self._quick_inner if section == "quick" else self._pool_inner

        if len(rows) >= proxy_config.MAX_ENTRIES:
            self._set_status(
                f"已达上限 {proxy_config.MAX_ENTRIES} 项，如需新增请先删除现有项", "error"
            )
            return None

        row = tk.Frame(inner, bg=THEME["bg_panel"])
        row.pack(fill=tk.X, padx=8, pady=(0, 6))

        seq_lbl = tk.Label(
            row, text="01", width=3, anchor=tk.W, font=("Consolas", 9, "bold"),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        seq_lbl.pack(side=tk.LEFT, padx=(0, 6))

        wrap = tk.Frame(
            row, bg=THEME["bg_inset"],
            highlightbackground=THEME["line_soft"], highlightthickness=1,
            padx=6, pady=1
        )
        wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)

        var = tk.StringVar(value=value)
        entry = tk.Entry(
            wrap, textvariable=var,
            bg=THEME["bg_inset"], fg=THEME["term_text"],
            insertbackground=THEME["signal"],
            selectbackground=THEME["signal"], selectforeground=THEME["bg_inset"],
            relief=tk.FLAT, font=("Consolas", 10)
        )
        entry.pack(fill=tk.X, expand=True)
        entry.bind("<KeyRelease>",
                   lambda _e, w=wrap: w.configure(highlightbackground=THEME["line_soft"]))
        entry.bind("<Return>", lambda _e: self._apply())

        info: dict = {"row": row, "var": var, "wrap": wrap, "entry": entry, "seq": seq_lbl}

        btn_del = DeckButton(
            row, "×", command=lambda: self._remove_row(section, info),
            bg_color=THEME["bg_panel"], hover_color=THEME["line_strong"],
            text_color=THEME["danger"], border_color=THEME["line_soft"],
            font_style=("Segoe UI", 11, "bold"),
            width=26, height=24, radius=4
        )
        btn_del.pack(side=tk.LEFT, padx=(6, 0))
        ToolTip(btn_del, "删除该项")

        rows.append(info)
        self._renumber(section)

        canvas = self._canvas_of(section)
        for widget in (row, seq_lbl, wrap, entry, btn_del):
            widget.bind("<MouseWheel>", lambda e, c=canvas: self._on_wheel(e, c))
        if section == "quick":
            self._quick_scroll.bind("<MouseWheel>", lambda e, c=canvas: self._on_wheel(e, c))
        else:
            self._pool_scroll.bind("<MouseWheel>", lambda e, c=canvas: self._on_wheel(e, c))

        if focus:
            entry.focus_set()
            canvas.yview_moveto(1.0)
        return info

    def _remove_row(self, section: str, info: dict, silent: bool = False):
        rows = self._rows_of(section)
        if info not in rows:
            return
        rows.remove(info)
        info["row"].destroy()
        self._renumber(section)
        if not silent:
            self._set_status("已移除 1 项，点击「应用并生效」写入配置", "idle")

    def _renumber(self, section: str):
        for idx, info in enumerate(self._rows_of(section), start=1):
            info["seq"].configure(text=f"{idx:02d}" if section == "quick" else str(idx))

    def _on_wheel(self, event, canvas: tk.Canvas):
        canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ---------- 数据 ----------
    def _populate(self, cfg: dict):
        for info in self._quick_rows + self._pool_rows:
            info["row"].destroy()
        self._quick_rows.clear()
        self._pool_rows.clear()

        for value in cfg.get("quick_actions", []):
            self._add_row("quick", value)
        for value in cfg.get("proxy_pool", []):
            self._add_row("pool", value)

        source = "配置文件" if cfg.get("source") == "file" else "内置默认"
        self._set_status(
            f"当前生效 · 快捷指令 {len(cfg.get('quick_actions', []))} 项 / "
            f"代理池 {len(cfg.get('proxy_pool', []))} 项（来源：{source}）",
            "idle"
        )

    def _validate(self, section: str, label: str, required: bool) -> tuple[list[str], Optional[str]]:
        rows = self._rows_of(section)
        cleaned: list[str] = []

        for idx, info in enumerate(rows):
            raw = info["var"].get().strip()
            wrap = info["wrap"]
            if not raw:
                if required:
                    wrap.configure(highlightbackground=THEME["danger"])
                    info["entry"].focus_set()
                    return [], f"{label} 第 {idx + 1} 项为空，请填写或删除该行"
                continue

            norm = proxy_config.normalize_proxy(raw)
            if not norm:
                wrap.configure(highlightbackground=THEME["danger"])
                info["entry"].focus_set()
                return [], f"{label} 第 {idx + 1} 项格式无效：{raw}"
            if norm in cleaned:
                wrap.configure(highlightbackground=THEME["danger"])
                info["entry"].focus_set()
                return [], f"{label} 第 {idx + 1} 项与已有项重复：{norm}"
            cleaned.append(norm)

        if required and not cleaned:
            return [], f"{label} 至少需要保留 1 项"
        return cleaned, None

    def _apply(self):
        # 快捷指令允许留空占位，应用前自动剪除空行
        for info in list(self._quick_rows):
            if not info["var"].get().strip():
                self._remove_row("quick", info, silent=True)

        quick, err = self._validate("quick", "窗口快捷指令", required=False)
        if err:
            self._set_status(err, "error")
            return

        pool, err = self._validate("pool", "脚本代理池", required=True)
        if err:
            self._set_status(err, "error")
            return

        try:
            payload = proxy_config.save(quick, pool)
        except (OSError, ValueError) as exc:
            self._set_status(f"写入配置失败：{exc}", "error")
            return

        # 回填规范化结果，让用户看到最终落盘形态
        for info, value in zip(self._quick_rows, quick):
            info["var"].set(value)
        for info, value in zip(self._pool_rows, pool):
            info["var"].set(value)

        try:
            self._on_apply(payload)
        except Exception as exc:
            self._set_status(f"配置已写入，但应用失败：{exc}", "error")
            return

        self.grab_release()
        self.destroy()

    def _reset_defaults(self):
        self._populate({
            "quick_actions": list(proxy_config.DEFAULT_QUICK_ACTIONS),
            "proxy_pool": list(proxy_config.DEFAULT_PROXY_POOL),
            "source": "default",
        })
        self._set_status("已载入内置默认值，点击「应用并生效」写回配置", "idle")

    def _set_status(self, text: str, kind: str = "idle"):
        color = {
            "ok": THEME["go"],
            "error": THEME["danger"],
            "idle": THEME["text_mid"],
        }.get(kind, THEME["text_mid"])
        prefix = "● " if kind != "idle" else "· "
        self.status_lbl.configure(text=prefix + text, fg=color)

    def _center_on(self, master: tk.Tk):
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
        except Exception:
            px = py = 0
            pw, ph = 1440, 900
        x = max(0, px + (pw - w) // 2)
        y = max(0, py + (ph - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class FieldDeckApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.current_theme_name = "light"
        global THEME
        THEME = THEMES[self.current_theme_name]

        self.title("Nexus · 服务集成操控台")
        self.geometry("1440x900")
        self.minsize(1280, 820)
        self.configure(bg=THEME["bg_deepest"])

        apply_window_icon(self)
        apply_titlebar_theme(self, is_dark=False)

        self._build_command_bar()
        self._build_status_bar()
        self._build_main_grid()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.update_services_summary()
        self.after(300, self.start_all)
        self._start_clock_timer()

    def _build_command_bar(self):
        self.nav = tk.Frame(self, bg=THEME["bg_panel"], padx=20, pady=10)
        self.nav.pack(fill=tk.X, side=tk.TOP)

        # 品牌区：工业橙字标 + 双行产品名
        self.brand_frame = tk.Frame(self.nav, bg=THEME["bg_panel"])
        self.brand_frame.pack(side=tk.LEFT)

        self.brand_cv = tk.Canvas(self.brand_frame, width=36, height=36,
                                  bg=THEME["bg_panel"], highlightthickness=0)
        draw_brand_mark(self.brand_cv, 36, THEME["signal"], THEME["bg_panel"])
        self.brand_cv.pack(side=tk.LEFT, padx=(0, 11))

        self.brand_text = tk.Frame(self.brand_frame, bg=THEME["bg_panel"])
        self.brand_text.pack(side=tk.LEFT)
        self.title_lbl = tk.Label(
            self.brand_text, text="Nexus",
            font=("Segoe UI", 20, "bold"), anchor=tk.W,
            fg=THEME["text_hi"], bg=THEME["bg_panel"]
        )
        self.title_lbl.pack(anchor=tk.W)
        self.brand_sub = tk.Label(
            self.brand_text, text="SERVICE CONTROL WORKSPACE",
            font=("Segoe UI", 8, "bold"), anchor=tk.W,
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        self.brand_sub.pack(anchor=tk.W)

        self.badge_sub = tk.Label(
            self.brand_frame, text=" LOCAL / SERVICE ",
            font=("Consolas", 8, "bold"),
            fg=THEME["chip_text"], bg=THEME["chip_bg"],
            padx=6, pady=3,
            highlightbackground=THEME["chip_border"], highlightthickness=1
        )
        self.badge_sub.pack(side=tk.LEFT, padx=(16, 0), pady=2)

        self.brand_sep = tk.Frame(self.nav, bg=THEME["line_soft"], width=1, height=28)
        self.brand_sep.pack(side=tk.LEFT, padx=(18, 0))

        # 中部：服务 LED 组 + 汇总
        self.mid = tk.Frame(self.nav, bg=THEME["bg_panel"])
        self.mid.pack(side=tk.LEFT, padx=(18, 0))

        self.led_vps = MiniLed(self.mid, "VPS")
        self.led_vps.pack(side=tk.LEFT, padx=(0, 4))
        self.led_proxy = MiniLed(self.mid, "PROXY")
        self.led_proxy.pack(side=tk.LEFT, padx=(0, 4))
        self.led_bt = MiniLed(self.mid, "BT")
        self.led_bt.pack(side=tk.LEFT, padx=(0, 12))

        self.summary_badge = tk.Label(
            self.mid, text="正在初始化…",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        self.summary_badge.pack(side=tk.LEFT)

        # 右侧：皮肤切换按钮 + 分段式全局控制键组
        self.ctrl = tk.Frame(self.nav, bg=THEME["bg_panel"])
        self.ctrl.pack(side=tk.RIGHT)

        # 主题切换与分段式全局控制键组
        self.btn_theme = DeckButton(
            self.ctrl, "深色模式", command=self.toggle_theme,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["text_hi"], border_color=THEME["line_soft"],
            width=100, height=34, radius=6, corner="both",
            icon="moon", icon_size=14, pad_x=10
        )
        self.btn_theme.pack(side=tk.LEFT, padx=(0, 12))
        ToolTip(self.btn_theme, "切换浅色 / 深色主题")

        self.btn_all_start = DeckButton(
            self.ctrl, "全部启动", command=self.start_all,
            bg_color=THEME["go_bg"], hover_color=THEME["go_hover"], text_color="#FFFFFF",
            width=102, height=34, radius=6, corner="left",
            icon="play-all", icon_size=15, pad_x=11
        )
        self.btn_all_start.pack(side=tk.LEFT)
        ToolTip(self.btn_all_start, "依次启动全部服务")

        self.btn_all_restart = DeckButton(
            self.ctrl, "全量重启", command=self.restart_all,
            bg_color=THEME["bg_raise"], hover_color=THEME["line_strong"],
            text_color=THEME["signal_on_dark"], border_color=THEME["line_strong"],
            width=102, height=34, radius=6, corner="none",
            icon="restart-all", icon_size=15, pad_x=11
        )
        self.btn_all_restart.pack(side=tk.LEFT, padx=(1, 0))
        ToolTip(self.btn_all_restart, "重启全部服务")

        self.btn_all_stop = DeckButton(
            self.ctrl, "全部停止", command=self.stop_all,
            bg_color=THEME["danger_bg"], hover_color=THEME["danger_hover"], text_color="#FFFFFF",
            width=102, height=34, radius=6, corner="right",
            icon="stop-all", icon_size=15, pad_x=11
        )
        self.btn_all_stop.pack(side=tk.LEFT, padx=(1, 0))
        ToolTip(self.btn_all_stop, "终止全部运行中的服务")

        self.sep = tk.Frame(self, bg=THEME["line_soft"], height=1)
        self.sep.pack(fill=tk.X, side=tk.TOP)

    def _build_main_grid(self):
        self.content_frame = tk.Frame(self, bg=THEME["bg_deepest"], padx=20, pady=16)
        self.content_frame.pack(fill=tk.BOTH, expand=True)

        self.content_frame.grid_columnconfigure(0, weight=1, uniform="panels")
        self.content_frame.grid_columnconfigure(1, weight=1, uniform="panels")
        self.content_frame.grid_columnconfigure(2, weight=1, uniform="panels")
        self.content_frame.grid_rowconfigure(0, weight=1)

        vps_actions = [
            ("唤醒 [1]", "1"),
            ("休眠 [2]", "2"),
            ("关机 [3]", "3"),
            ("远程桌面 [4]", "4"),
        ]
        self.panel_vps = ServicePanel(
            self.content_frame,
            index="01",
            title="VPS 云节点控制",
            subtitle="vpscodex.py · SELENIUM DRIVER",
            tag="CLOUD",
            tag_color=THEME["violet"],
            script_name="vpscodex.py",
            cmd_list=[sys.executable, "-u", "vpscodex.py"],
            allow_input=True,
            quick_actions=vps_actions,
            max_buffer_rows=40,
            # vpscodex 每秒都有轮询输出；静默超过 300 秒即为无响应
            watchdog_seconds=300,
            on_status_change=self.update_services_summary
        )
        self.panel_vps.grid(row=0, column=0, sticky="nsew", padx=6)

        # 代理快捷指令与脚本代理池统一由持久化配置驱动（配置台可增删改）
        proxy_cfg = proxy_config.load()
        proxy_actions = [(proxy_config.chip_label(p), p) for p in proxy_cfg["quick_actions"]]
        self.panel_proxy = ServicePanel(
            self.content_frame,
            index="02",
            title="系统代理守候",
            subtitle="systemproxy_watcher.py · WININET",
            tag="PROXY",
            tag_color=THEME["info"],
            script_name="systemproxy_watcher.py",
            cmd_list=[sys.executable, "-u", "systemproxy_watcher.py"],
            allow_input=True,
            quick_actions=proxy_actions,
            fixed_actions=[("清除代理", self.clear_proxy)],
            config_command=self.open_proxy_config,
            max_buffer_rows=32,
            on_status_change=self.update_services_summary
        )
        self.panel_proxy.grid(row=0, column=1, sticky="nsew", padx=6)

        self.panel_bt = ServicePanel(
            self.content_frame,
            index="03",
            title="蓝牙连接保活",
            subtitle="bluetooth.ps1 · PNP POWER",
            tag="SYSTEM",
            tag_color=THEME["go"],
            script_name="bluetooth.bat",
            cmd_list=["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & './bluetooth.ps1' -Elevated"],
            allow_input=False,
            max_buffer_rows=32,
            on_status_change=self.update_services_summary
        )
        self.panel_bt.grid(row=0, column=2, sticky="nsew", padx=6)

        self.panels = [self.panel_vps, self.panel_proxy, self.panel_bt]

    def _build_status_bar(self):
        self.sep_status = tk.Frame(self, bg=THEME["line_soft"], height=1)
        self.sep_status.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_bar = tk.Frame(self, bg=THEME["bg_panel"], padx=20, pady=8)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        is_admin = False
        try:
            is_admin = (ctypes.windll.shell32.IsUserAnAdmin() != 0) if os.name == "nt" else True
        except Exception:
            pass
        self._is_admin = is_admin

        self.priv_led = tk.Canvas(self.status_bar, width=10, height=10, bg=THEME["bg_panel"], highlightthickness=0)
        self.priv_led.create_oval(1, 1, 9, 9, fill=THEME["go"] if is_admin else THEME["warn"], outline="")
        self.priv_led.pack(side=tk.LEFT, padx=(0, 6), pady=2)

        self.lbl_priv = tk.Label(
            self.status_bar,
            text="管理员权限" if is_admin else "标准用户",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["go"] if is_admin else THEME["warn"],
            bg=THEME["bg_panel"]
        )
        self.lbl_priv.pack(side=tk.LEFT, padx=(0, 18))

        self.lbl_count = tk.Label(
            self.status_bar, text="服务 0/3 在线",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["text_mid"], bg=THEME["bg_panel"]
        )
        self.lbl_count.pack(side=tk.LEFT, padx=(0, 18))

        self.lbl_hint = tk.Label(
            self.status_bar,
            text="Tab 切换按钮 · Enter 激活 · 所有服务常驻显示",
            font=("Segoe UI", 9),
            fg=THEME["text_lo"], bg=THEME["bg_panel"]
        )
        self.lbl_hint.pack(side=tk.LEFT)

        self.lbl_clock = tk.Label(
            self.status_bar, text=time.strftime("%Y-%m-%d %H:%M:%S"),
            font=("Consolas", 10, "bold"),
            fg=THEME["text_mid"], bg=THEME["bg_panel"]
        )
        self.lbl_clock.pack(side=tk.RIGHT)

    def _start_clock_timer(self):
        if hasattr(self, "lbl_clock") and self.lbl_clock.winfo_exists():
            self.lbl_clock.configure(text=time.strftime("%Y-%m-%d %H:%M:%S"))
            self.after(1000, self._start_clock_timer)

    def toggle_theme(self):
        """在暗色模式与明亮模式之间平滑切换"""
        self.current_theme_name = "light" if self.current_theme_name == "dark" else "dark"
        global THEME
        THEME = THEMES[self.current_theme_name]

        # 1. 窗口及外层容器背景
        self.configure(bg=THEME["bg_deepest"])
        self.nav.configure(bg=THEME["bg_panel"])
        self.brand_frame.configure(bg=THEME["bg_panel"])
        self.brand_cv.configure(bg=THEME["bg_panel"])
        draw_brand_mark(self.brand_cv, 36, THEME["signal"], THEME["bg_panel"])

        self.brand_text.configure(bg=THEME["bg_panel"])
        self.title_lbl.configure(bg=THEME["bg_panel"], fg=THEME["text_hi"])
        self.brand_sub.configure(bg=THEME["bg_panel"], fg=THEME["text_lo"])
        self.badge_sub.configure(
            bg=THEME["chip_bg"], fg=THEME["chip_text"],
            highlightbackground=THEME["chip_border"]
        )
        self.brand_sep.configure(bg=THEME["line_soft"])
        self.mid.configure(bg=THEME["bg_panel"])
        self.led_vps.apply_theme()
        self.led_proxy.apply_theme()
        self.led_bt.apply_theme()
        self.summary_badge.configure(bg=THEME["bg_panel"])
        self.ctrl.configure(bg=THEME["bg_panel"])
        self.sep.configure(bg=THEME["line_soft"])

        # 2. 皮肤切换按键状态与文案
        if self.current_theme_name == "dark":
            self.btn_theme.text = "浅色模式"
            self.btn_theme.update_style(
                THEME["bg_raise"], THEME["line_strong"], THEME["text_hi"],
                border_color=THEME["line_soft"], icon="sun"
            )
        else:
            self.btn_theme.text = "深色模式"
            self.btn_theme.update_style(
                THEME["bg_raise"], THEME["line_strong"], THEME["text_hi"],
                border_color=THEME["line_soft"], icon="moon"
            )

        # 3. 全局启停键组
        self.btn_all_start.update_style(THEME["go_bg"], THEME["go_hover"], "#FFFFFF")
        self.btn_all_restart.update_style(THEME["bg_raise"], THEME["line_strong"], THEME["signal_on_dark"], border_color=THEME["line_strong"])
        self.btn_all_stop.update_style(THEME["danger_bg"], THEME["danger_hover"], "#FFFFFF")

        # 4. 中间网格容器与 3 个服务面板
        self.content_frame.configure(bg=THEME["bg_deepest"])
        for p in self.panels:
            p.apply_theme()

        # 5. 底部状态栏
        self.sep_status.configure(bg=THEME["line_soft"])
        self.status_bar.configure(bg=THEME["bg_panel"])
        self.priv_led.configure(bg=THEME["bg_panel"])
        self.priv_led.itemconfigure(1, fill=THEME["go"] if self._is_admin else THEME["warn"])
        self.lbl_priv.configure(
            bg=THEME["bg_panel"],
            fg=THEME["go"] if self._is_admin else THEME["warn"]
        )
        self.lbl_count.configure(bg=THEME["bg_panel"], fg=THEME["text_mid"])
        self.lbl_hint.configure(bg=THEME["bg_panel"], fg=THEME["text_lo"])
        self.lbl_clock.configure(bg=THEME["bg_panel"], fg=THEME["text_mid"])

        # 6. Windows 原生标题栏沉浸色联动
        apply_titlebar_theme(self, is_dark=(self.current_theme_name == "dark"))

        # 7. 更新服务指示
        self.update_services_summary()

    def update_services_summary(self):
        if not hasattr(self, "panels"):
            return
        running_cnt = sum(1 for p in self.panels if p.is_running)
        total_cnt = len(self.panels)

        if hasattr(self, "led_vps"):
            self.led_vps.set_on(self.panel_vps.is_running)
            self.led_proxy.set_on(self.panel_proxy.is_running)
            self.led_bt.set_on(self.panel_bt.is_running)

        if hasattr(self, "lbl_count") and self.lbl_count.winfo_exists():
            self.lbl_count.configure(text=f"服务 {running_cnt}/{total_cnt} 在线")

        if running_cnt == total_cnt:
            self.summary_badge.configure(
                text=f"全部服务正常  {running_cnt}/{total_cnt}",
                fg=THEME["go"]
            )
        elif running_cnt > 0:
            self.summary_badge.configure(
                text=f"部分服务运行  {running_cnt}/{total_cnt}",
                fg=THEME["warn"]
            )
        else:
            self.summary_badge.configure(
                text=f"待命  0/{total_cnt}",
                fg=THEME["text_lo"]
            )

    def start_all(self):
        for p in self.panels:
            p.start_process()
        self.update_services_summary()

    def stop_all(self):
        running = [p for p in self.panels if p.is_running]
        if running:
            if not messagebox.askokcancel(
                "确认操作",
                f"{len(running)} 个服务将被终止，确认全部停止？",
                icon="warning"
            ):
                return
        for p in self.panels:
            p.stop_process()
        self.update_services_summary()

    def restart_all(self):
        for p in self.panels:
            p.restart_process()
        self.update_services_summary()

    def open_proxy_config(self):
        """打开代理配置台（模态）：编辑快捷指令与脚本代理池。"""
        ProxyConfigDialog(self, on_apply=self.apply_proxy_config)

    def clear_proxy(self):
        """清除系统代理。

        守候运行中交由脚本处理——脚本会把"关闭"当作目标状态，每 5s 校验一次，
        被其它程序重新开启时再次清除；服务未运行时由操控台直接重置注册表。
        """
        if self.panel_proxy.is_running:
            self.panel_proxy.send_input("off")
            return

        err = clear_system_proxy_registry()
        if err:
            self.panel_proxy.append_log(f"› 清除系统代理失败：{err}\n")
            return

        try:
            proxy_config.set_monitoring(False)
        except Exception:
            pass

        self.panel_proxy.append_log(
            "› 系统代理已清除（服务未运行，当前无持续校验；下次启动守候将保持关闭）\n"
        )

    def apply_proxy_config(self, cfg: dict):
        """配置写入后刷新芯片组；守候脚本在运行中则按新代理池自动重启。"""
        quick = cfg.get("quick_actions") or []
        pool = cfg.get("proxy_pool") or []

        self.panel_proxy.set_quick_actions(
            [(proxy_config.chip_label(p), p) for p in quick]
        )
        self.panel_proxy.append_log(
            f"› 代理配置已更新并持久化 · 快捷指令 {len(quick)} 项 / 脚本代理池 {len(pool)} 项\n"
        )
        self.panel_proxy.append_log(
            f"› 配置文件: {cfg.get('path', proxy_config.config_path())}\n"
        )

        if self.panel_proxy.is_running:
            self.panel_proxy.append_log("› 守候脚本按新代理池重启\n")
            self.panel_proxy.restart_process()
        else:
            self.panel_proxy.append_log("› 下次启动守候脚本时按新代理池加载\n")

    def on_close(self):
        if any(p.is_running for p in self.panels):
            if not messagebox.askokcancel("确认退出", "后台服务正在持续运行中，是否全部终止并退出操控台？"):
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

    set_app_user_model_id()
    clean_lingering_script_processes()

    app = FieldDeckApp()
    app.mainloop()


if __name__ == "__main__":
    main()

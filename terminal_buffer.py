# -*- coding: utf-8 -*-
"""
终端缓冲区实现
支持 ANSI 转义序列（光标定位、单行清除、整屏清除）、\r（回车回到行首局部刷新）、\n（换行）
为 Tkinter 界面提供类似于真实控制台的局部刷新体验
"""

import re
from typing import List

# 正则匹配 ANSI 序列、控制字符、普通字符段
TOKEN_PATTERN = re.compile(
    r'(\x1B\[[0-9;?]*[a-zA-Z]|\x0c|\r\n|\r|\n|[^\x1B\x0c\r\n]+)'
)


class VirtualTerminalBuffer:
    def __init__(self, max_rows: int = 50, cols: int = 120):
        self.max_rows = max_rows
        self.cols = cols
        self.lines: List[str] = [""]
        self.cursor_row: int = 0
        self.cursor_col: int = 0

    def clear(self):
        """清空终端缓冲区"""
        self.lines = [""]
        self.cursor_row = 0
        self.cursor_col = 0

    def clear_line(self):
        """清空当前光标所在行"""
        if self.cursor_row < len(self.lines):
            self.lines[self.cursor_row] = ""
        self.cursor_col = 0

    def move_cursor(self, row: int, col: int):
        """移动光标到指定行列 (0-based)"""
        while len(self.lines) <= row:
            self.lines.append("")
        self.cursor_row = row
        self.cursor_col = max(0, col)

    def _write_text(self, text: str):
        """在当前光标位置覆盖或写入普通文本"""
        while len(self.lines) <= self.cursor_row:
            self.lines.append("")

        current_line = self.lines[self.cursor_row]
        # 如果光标超过当前行长度，用空格填补
        if self.cursor_col > len(current_line):
            current_line = current_line + " " * (self.cursor_col - len(current_line))

        # 覆盖写入
        prefix = current_line[:self.cursor_col]
        suffix = current_line[self.cursor_col + len(text):]
        self.lines[self.cursor_row] = prefix + text + suffix
        self.cursor_col += len(text)

    def _newline(self):
        """换行"""
        self.cursor_row += 1
        self.cursor_col = 0
        while len(self.lines) <= self.cursor_row:
            self.lines.append("")

        # 若超出最大行数上限，剔除顶端历史行
        if len(self.lines) > self.max_rows:
            drop_count = len(self.lines) - self.max_rows
            self.lines = self.lines[drop_count:]
            self.cursor_row = max(0, self.cursor_row - drop_count)

    def feed(self, stream: str):
        """接收子进程输出流并解析控制序列"""
        tokens = TOKEN_PATTERN.findall(stream)
        for tok in tokens:
            if tok == "\r\n":
                self._newline()
            elif tok == "\r":
                # 回车：回到当前行行首（实现单行就地刷新）
                self.cursor_col = 0
            elif tok == "\n":
                self._newline()
            elif tok == "\x0c":
                # cls 或 FF: 清屏
                self.clear()
            elif tok.startswith("\x1B["):
                # 处理 ANSI CSI 控制指令
                cmd = tok[-1]
                args = tok[2:-1]
                if cmd == "H" or cmd == "f":
                    # 光标绝对定位：\033[row;colH (1-based)
                    parts = args.split(";") if args else []
                    r = int(parts[0]) - 1 if (len(parts) > 0 and parts[0].isdigit()) else 0
                    c = int(parts[1]) - 1 if (len(parts) > 1 and parts[1].isdigit()) else 0
                    self.move_cursor(r, c)
                elif cmd == "K":
                    # 行内清除：\033[2K (清除整行) 或 \033[K (清除到行末)
                    while len(self.lines) <= self.cursor_row:
                        self.lines.append("")
                    if args == "2":
                        self.lines[self.cursor_row] = ""
                        self.cursor_col = 0
                    else:
                        self.lines[self.cursor_row] = self.lines[self.cursor_row][:self.cursor_col]
                elif cmd == "J":
                    # 屏幕清除：\033[2J
                    self.clear()
                # 其他如颜色设置 (m) 或显示光标 (?25h) 忽略或跳过
            else:
                self._write_text(tok)

    def get_display_text(self) -> str:
        """获取当前屏幕完整文本渲染"""
        # 截取末尾多余的空行
        lines_copy = list(self.lines)
        while lines_copy and not lines_copy[-1]:
            lines_copy.pop()
        return "\n".join(lines_copy)

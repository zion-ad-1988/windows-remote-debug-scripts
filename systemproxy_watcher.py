import winreg
import time
import os
import sys
import msvcrt
import ctypes
import queue
import threading
from typing import Optional

# ====== 配置区域 ======
PROXY_POOL = [
    "127.0.0.1:10808",
    "127.0.0.1:9990",
    "127.0.0.1:8890",
    "127.0.0.1:20808",
    "127.0.0.1:7897",
]
DEFAULT_PROXY_INDEX = 0
BYPASS_LIST = "*.s2api.top,s2api.top,api.100022.xyz,*.100022.xyz,api.280181.xyz;picpi.top;*.picpi.top;us.picpi.top;api.picpi.top;cn.picpi.top;*.processon.com;*.wps.cn;*.trae.cn;*.kdocs.cn;*.1kcode.com;*.snowballbi.com;copilot.tencent.com;galileotelemetry.tencent.com;*.shimo.im;*.bing.com;*.bing.cn;ai.i1988.top;cost.i1988.top;ddns.i1988.top;ddnsgo.i1988.top;*.microsoft.com;*.live.com;*.microsoftonline.com;*.windows.net;localhost;127.*;192.168.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;<local>"
CHECK_INTERVAL = 5  # 检测间隔（秒）
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
CURRENT_PROXY_ROW = 2
STATUS_ROW = 5
PROXY_POOL_START_ROW = 8
LOG_START_ROW = PROXY_POOL_START_ROW + len(PROXY_POOL) + 2
MAX_LOG_LINES = 10

# 管道输入缓冲队列（用于支持在 GUI 或重定向模式下接收标准输入）
INPUT_QUEUE: queue.Queue[str] = queue.Queue()


def _stdin_listener():
    """后台监听 sys.stdin，用于管道或重定向环境下的输入"""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            INPUT_QUEUE.put(line.strip())
        except Exception:
            break
# ====== 配置结束 ======


def read_registry() -> tuple[Optional[int], Optional[str], Optional[str]]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
            bypass = winreg.QueryValueEx(key, "ProxyOverride")[0]
            return enabled, server, bypass
    except FileNotFoundError:
        return None, None, None


def write_registry(enabled: int, server: str, bypass: str):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, enabled)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, bypass)


def notify_internet_settings():
    ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)


def apply_proxy(proxy_server: str) -> str:
    write_registry(1, proxy_server, BYPASS_LIST)
    notify_internet_settings()
    return f"[{time.strftime('%H:%M:%S')}] 代理已应用 -> {proxy_server}"


def check_and_fix(proxy_server: str) -> str | None:
    enabled, server, bypass = read_registry()
    needs_fix = False
    if enabled != 1:
        needs_fix = True
    elif server != proxy_server:
        needs_fix = True
    elif bypass != BYPASS_LIST:
        needs_fix = True
    if needs_fix:
        write_registry(1, proxy_server, BYPASS_LIST)
        notify_internet_settings()
        return f"[{time.strftime('%H:%M:%S')}] 代理已修正 -> {proxy_server}"
    return None


def append_log(logs: list[str], msg: str):
    logs.append(msg)
    if len(logs) > MAX_LOG_LINES:
        logs.pop(0)


def normalize_proxy(val: str) -> Optional[str]:
    val = val.strip()
    if not val:
        return None

    # 替换中文全角冒号
    val = val.replace("：", ":")

    # 1. 如果仅输入端口数字，如 "7890"，默认使用 127.0.0.1:端口
    if val.isdigit():
        port = int(val)
        if 1 <= port <= 65535:
            return f"127.0.0.1:{port}"
        return None

    # 2. 如果输入了完整代理地址（带协议前缀），去掉协议前缀
    for prefix in ("http://", "https://", "socks5://", "socks://", "socks="):
        if val.lower().startswith(prefix):
            val = val[len(prefix):]
            break
    val = val.rstrip("/")

    # 支持空格分隔，如 "127.0.0.1 7890"
    if " " in val:
        parts = [p for p in val.split() if p]
        if len(parts) == 2:
            val = f"{parts[0]}:{parts[1]}"

    # 校验 host:port
    if ":" in val:
        parts = val.rsplit(":", 1)
        host, port_str = parts[0].strip(), parts[1].strip()
        if host and port_str.isdigit():
            port = int(port_str)
            if 1 <= port <= 65535:
                return f"{host}:{port}"
    else:
        # 如果是完整的主机/域名地址（如 sssx.sdad）
        if "." in val or val.lower() == "localhost":
            return val

    return None


def read_proxy_choice(logs: list[str]) -> tuple[Optional[str], bool]:
    """读取用户按键输入或管道标准输入。
    返回值: (proxy_address, should_update)
    """
    # 优先检查管道/标准输入队列
    while not INPUT_QUEUE.empty():
        text = INPUT_QUEUE.get_nowait()
        if not text:
            continue

        # 检查是否为固定预设代理编号（1 ~ len(PROXY_POOL)）
        if text.isdigit():
            num = int(text)
            if 1 <= num <= len(PROXY_POOL):
                return PROXY_POOL[num - 1], True

        # 如果不是固定选项，尝试解析为端口或完整代理地址
        norm = normalize_proxy(text)
        if norm:
            append_log(logs, f"[{time.strftime('%H:%M:%S')}] 切换代理: {norm}")
            return norm, True
        else:
            append_log(logs, f"[{time.strftime('%H:%M:%S')}] 无效代理格式: {text}")
            return None, True

    if not sys.stdin.isatty():
        return None, False

    if not msvcrt.kbhit():
        return None, False

    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        if msvcrt.kbhit():
            msvcrt.getwch()
        return None, False

    # 控制台按键单键判断：1 ~ len(PROXY_POOL)
    if key.isdigit():
        num = int(key)
        if 1 <= num <= len(PROXY_POOL):
            return PROXY_POOL[num - 1], True

    return None, False


def enable_virtual_terminal():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def write_at(row: int, text: str = ""):
    sys.stdout.write(f"\033[{row};1H\033[2K{text}")


def render_static_screen():
    # 直接发送 ANSI 清屏序列，避免后台运行时创建 cmd 窗口。
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(f"系统代理监控已启动 (间隔 {CHECK_INTERVAL}s)\n")
    sys.stdout.write("\n")
    sys.stdout.write(f"按 Ctrl+C 停止\n")
    sys.stdout.write(f"{'-' * 50}\n")
    sys.stdout.write("\n")
    sys.stdout.write("预设代理池（按序号 1~5 切换，或直接输入端口/完整地址）:\n")
    # 为预设代理池保留行
    for _ in PROXY_POOL:
        sys.stdout.write("\n")
    sys.stdout.write(f"{'-' * 50}\n")
    sys.stdout.flush()


def render_dynamic_screen(current_proxy: str, remaining: int, logs: list[str]):
    write_at(CURRENT_PROXY_ROW, f"当前代理: {current_proxy}")
    write_at(STATUS_ROW, f"下次检测: {remaining}s  |  日志: {len(logs)} 条")

    for index, proxy_server in enumerate(PROXY_POOL, start=1):
        current_mark = "  <- 当前" if proxy_server == current_proxy else ""
        write_at(PROXY_POOL_START_ROW + index - 1, f"  {index}. {proxy_server}{current_mark}")

    for offset in range(MAX_LOG_LINES):
        line = logs[offset] if offset < len(logs) else ""
        write_at(LOG_START_ROW + offset, line)

    write_at(LOG_START_ROW + MAX_LOG_LINES, "")
    sys.stdout.flush()


def render_screen(current_proxy: str, remaining: int, logs: list[str]):
    render_dynamic_screen(current_proxy, remaining, logs)


def main():
    enable_virtual_terminal()
    # 启动后台 stdin 监听线程（仅在非控制台 tty 管道环境下监听，避免与 msvcrt 冲突）
    if not sys.stdin.isatty():
        t = threading.Thread(target=_stdin_listener, daemon=True)
        t.start()

    logs: list[str] = []
    current_proxy = PROXY_POOL[DEFAULT_PROXY_INDEX] if PROXY_POOL else "127.0.0.1:10808"
    render_static_screen()
    render_screen(current_proxy, CHECK_INTERVAL, logs)

    while True:
        try:
            remaining = CHECK_INTERVAL
            while remaining > 0:
                render_screen(current_proxy, remaining, logs)
                next_tick = time.monotonic() + 1
                while time.monotonic() < next_tick:
                    new_proxy, triggered = read_proxy_choice(logs)
                    if triggered:
                        if new_proxy:
                            current_proxy = new_proxy
                            append_log(logs, apply_proxy(current_proxy))
                        render_screen(current_proxy, remaining, logs)
                    time.sleep(0.05)
                remaining -= 1

            msg = check_and_fix(current_proxy)
            if msg:
                append_log(logs, msg)
                render_screen(current_proxy, remaining, logs)
            else:
                render_screen(current_proxy, remaining, logs)
        except KeyboardInterrupt:
            sys.stdout.write("\n已停止\n")
            break


if __name__ == "__main__":
    main()

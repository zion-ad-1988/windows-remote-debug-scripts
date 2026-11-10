#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS 云节点控制台 · 长稳运行版

针对“运行数小时后无响应”的成因逐项加固：

1. 指令级硬超时：每条 WebDriver 指令都在独立守护线程中执行并带超时上限。
   任一指令超时即判定当前浏览器会话失效，立刻整树回收重建，
   不会再出现“单条指令永久阻塞 → 整个进程假死”的状态。
2. 会话主动回收：按存活时长 / 累计指令数 / 连续失败次数回收 headless Chrome，
   规避长时间运行后渲染进程与 V8 堆劣化、WebSocket 僵死。
3. Job Object 兜底：进程内创建 Windows Job（KILL_ON_JOB_CLOSE），
   即使外层 GUI 用 taskkill /F 强杀本进程，chromedriver 与 chrome 子进程
   也会被系统一并回收，不再累积孤儿浏览器进程。
4. chromedriver 优先本地解析：从 ~/.wdm 缓存按 Chrome 主版本匹配，
   避免联网查询在无网络时无限期挂起；联网仅作兜底且带超时。
5. 诊断日志：%LOCALAPPDATA%\\NexusFieldDeck\\vpscodex.log（1MB 轮转），
   每次指令的慢调用、超时、回收与失败均留痕，便于回溯卡死点。

用法：
    python vpscodex.py          交互控制台（GUI 面板调用的入口）
    python vpscodex.py --once   自检：建会话 → 读状态 → 退出（用于验证环境）
"""

from __future__ import annotations

from datetime import datetime
import atexit
import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Optional

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
except Exception as exc:
    print("缺少依赖 selenium。请先执行: pip install selenium", flush=True)
    print(f"原始错误: {exc}", flush=True)
    raise SystemExit(1)

# ============================== 配置 ==============================

# 真实接入地址与密码不纳入仓库：运行时通过环境变量注入，或在本机副本中直接填写。
# 仓库版本保持脱敏，可直接公开。
URL = os.environ.get("VPSCODEX_URL", "https://home.kuniaovps.com/i/remote/1")
PASSWORD = os.environ.get("VPSCODEX_PASSWORD", "")
RDP_FILE_PATH = os.environ.get(
    "VPSCODEX_RDP_FILE", r"C:\Users\Administrator\Desktop\UK Play.rdp"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# 轮询间隔：无输入时自动刷新状态的秒数
POLL_INTERVAL_SECONDS = _env_int("VPSCODEX_POLL_INTERVAL", 60)
# 单条 WebDriver 指令的硬超时（秒）；超时即回收会话
COMMAND_TIMEOUT = _env_int("VPSCODEX_CMD_TIMEOUT", 25)
# 导航到页面的超时：页面加载 + 指令余量
NAV_TIMEOUT = _env_int("VPSCODEX_NAV_TIMEOUT", 60)
# 会话最长存活时间（秒）到点强制回收，默认 30 分钟
RECYCLE_AFTER_SECONDS = _env_int("VPSCODEX_RECYCLE_SECONDS", 1800)
# 单个会话累计指令上限，到点强制回收
RECYCLE_AFTER_COMMANDS = _env_int("VPSCODEX_RECYCLE_COMMANDS", 1200)
# 导航后等待页面就绪（登录框或状态元素出现）的最长秒数
SETTLE_TIMEOUT = _env_int("VPSCODEX_SETTLE_TIMEOUT", 90)
# 连续读取失败多少轮后回收会话
RECYCLE_AFTER_FAILURES = _env_int("VPSCODEX_RECYCLE_FAILURES", 2)

LOGIN_INPUT_SELECTOR = (
    "#password, "
    "input[type='password'][placeholder='请输入密码'], "
    "input[type='password']"
)
SUBMIT_SELECTOR = "button[type='submit']"

CHROME_PROFILE_PREFIX = "vpscodex-chrome-"

SMOKE_MODE = os.environ.get("VPSCODEX_SMOKE") == "1" or "--once" in sys.argv[1:]

# ============================== 诊断日志 ==============================

LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
    "NexusFieldDeck",
)
LOG_FILE = os.path.join(LOG_DIR, "vpscodex.log")
LOG_MAX_BYTES = 1024 * 1024
_LOG_LOCK = threading.Lock()


def _rotate_log() -> None:
    try:
        if os.path.getsize(LOG_FILE) <= LOG_MAX_BYTES:
            return
        backup = LOG_FILE + ".1"
        try:
            os.remove(backup)
        except OSError:
            pass
        os.replace(LOG_FILE, backup)
    except OSError:
        pass


def file_log(message: str, level: str = "INFO") -> None:
    """仅写文件日志（不干扰 GUI 终端画面）。"""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {level:<5} [{os.getpid()}] {message}"
    with _LOG_LOCK:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            _rotate_log()
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


# ============================== 控制台输出 ==============================


def clear_screen() -> None:
    # 不调用 os.system("cls")，避免后台运行时周期性创建可见 cmd 窗口。
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def clear_current_line() -> None:
    sys.stdout.write("\r" + (" " * 100) + "\r")
    sys.stdout.flush()


def log(message: str = "") -> None:
    """终端提示 + 文件留痕。"""
    print(message, flush=True)
    file_log(message)


def progress(text: str) -> None:
    """单行原地刷新的进度提示，只走终端不进文件。"""
    sys.stdout.write("\r" + text)
    sys.stdout.flush()


def normalize_text(text: str) -> str:
    return "".join(text.split())


# ============================== Windows 进程管控 ==============================

_JOB_HANDLE: Optional[int] = None

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def install_kill_on_close_job() -> bool:
    """把当前进程放进 KILL_ON_JOB_CLOSE 的 Job，
    本进程无论正常退出还是被强杀，其派生的 chromedriver / chrome 都会被系统回收。
    """
    global _JOB_HANDLE
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False

        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            return False

        handle = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            os.getpid(),
        )
        if not handle:
            return False

        ok = bool(kernel32.AssignProcessToJobObject(job, handle))
        kernel32.CloseHandle(handle)
        if ok:
            # 句柄必须活到进程结束；一旦关闭，Job 内所有进程立即被终止。
            _JOB_HANDLE = job
            file_log("Job Object 已启用：本进程被强杀时子进程会被系统一并回收")
        return ok
    except Exception as exc:
        file_log(f"Job Object 启用失败（不影响主流程）: {exc}", "WARN")
        return False


def kill_process_tree(pid: int) -> None:
    """按进程树强制结束，覆盖 chromedriver 及其 chrome 子进程。"""
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        else:
            os.kill(pid, 9)
    except Exception as exc:
        file_log(f"结束进程树 {pid} 失败: {exc}", "WARN")


def sweep_chrome_by_profile(marker: str = CHROME_PROFILE_PREFIX) -> int:
    """清理命令行中带有本程序 profile 标记的残留 chrome 进程。

    chromedriver 被杀后 chrome 偶尔会被重新挂到其他父进程下，
    仅靠 taskkill /T 可能漏掉，这里按 profile 路径精确兜底。
    """
    if os.name != "nt":
        return 0
    script = (
        "$marker='%s'; $n=0; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*'+$marker+'*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; "
        "$n"
    ) % marker
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
        text = (out.stdout or b"").decode("utf-8", "ignore").strip().splitlines()
        count = int(text[-1]) if text and text[-1].isdigit() else 0
        if count:
            file_log(f"清理残留 chrome 进程 {count} 个（profile 标记匹配）", "WARN")
        return count
    except Exception as exc:
        file_log(f"残留 chrome 清理失败: {exc}", "WARN")
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def profile_prefix() -> str:
    return f"{CHROME_PROFILE_PREFIX}{os.getpid()}-"


def sweep_stale_profiles() -> int:
    """删除属主进程已消失的临时 profile 目录。

    被 taskkill /F 强杀时 finally 不会执行，目录会留在 %TEMP%；
    目录名内嵌属主 pid，上游被回收即可安全删除。
    """
    base = tempfile.gettempdir()
    removed = 0
    try:
        for name in os.listdir(base):
            if not name.startswith(CHROME_PROFILE_PREFIX):
                continue
            target = os.path.join(base, name)
            match = re.match(re.escape(CHROME_PROFILE_PREFIX) + r"(\d+)-", name)
            if match:
                owner = int(match.group(1))
                if owner == os.getpid() or _pid_alive(owner):
                    continue
            else:
                # 旧格式目录（无 pid）仅在闲置超过 1 小时后清理
                try:
                    if time.time() - os.path.getmtime(target) < 3600:
                        continue
                except OSError:
                    continue
            shutil.rmtree(target, ignore_errors=True)
            if not os.path.isdir(target):
                removed += 1
    except OSError:
        pass
    if removed:
        file_log(f"清理遗留临时 profile 目录 {removed} 个")
    return removed


# ============================== chromedriver 解析 ==============================


def _version_key(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:4]) if parts else (0,)


def _installed_chrome_version() -> Optional[str]:
    """读取已安装 Chrome 版本：注册表优先，其次扫描安装目录。"""
    candidates: list[str] = []
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Google\Chrome\BLBeacon",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            candidates.append(str(winreg.QueryValueEx(key, "version")[0]))
    except Exception:
        pass

    bases = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for base in bases:
        if not base:
            continue
        app_dir = os.path.join(base, "Google", "Chrome", "Application")
        try:
            for name in os.listdir(app_dir):
                if re.fullmatch(r"\d+(?:\.\d+){3}", name):
                    candidates.append(name)
        except OSError:
            continue

    if not candidates:
        return None
    return max(candidates, key=_version_key)


def _local_driver_candidates() -> list[tuple[tuple[int, ...], str]]:
    roots = [
        os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver"),
        os.path.join(os.path.expanduser("~"), ".cache", "selenium", "chromedriver"),
    ]
    found: list[tuple[tuple[int, ...], str]] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.lower() != "chromedriver.exe":
                    continue
                match = re.search(r"\d+(?:\.\d+){2,3}", dirpath)
                version = _version_key(match.group(0)) if match else (0,)
                found.append((version, os.path.join(dirpath, name)))
    return found


def _install_driver_online(timeout: int = 90) -> str:
    """联网解析 chromedriver，带硬超时，避免无网络时无限期挂起。"""
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            from webdriver_manager.chrome import ChromeDriverManager

            box["path"] = ChromeDriverManager().install()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=worker, name="wdm-install", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"联网解析 chromedriver 超过 {timeout}s")
    if "error" in box:
        raise box["error"]
    return str(box["path"])


def resolve_chromedriver() -> str:
    """优先本地缓存（离线可用），不足时带超时联网兜底。"""
    chrome_version = _installed_chrome_version()
    chrome_major = _version_key(chrome_version)[0] if chrome_version else 0
    candidates = _local_driver_candidates()

    if candidates and chrome_major:
        matched = [(v, p) for v, p in candidates if v and v[0] == chrome_major]
        if matched:
            version, path = max(matched, key=lambda item: item[0])
            file_log(
                f"chromedriver 本地匹配：Chrome {chrome_version} -> "
                f"{'.'.join(map(str, version))}"
            )
            return path

    try:
        path = _install_driver_online()
        file_log(f"chromedriver 联网解析成功: {path}")
        return path
    except Exception as exc:
        file_log(f"chromedriver 联网解析失败: {exc}", "WARN")

    if candidates:
        version, path = max(candidates, key=lambda item: item[0])
        file_log(
            f"回退到本地最新 chromedriver {'.'.join(map(str, version))}（版本可能不匹配）",
            "WARN",
        )
        return path

    raise RuntimeError(
        "未找到可用的 chromedriver：本地缓存为空且联网解析失败。"
        "请执行 pip install webdriver-manager 后联网重试，或手动放置 chromedriver。"
    )


# ============================== 受控浏览器会话 ==============================


class SessionLost(RuntimeError):
    """当前浏览器会话不可信，需要整树回收。"""


class GuardedBrowser:
    """所有 WebDriver 指令都经过超时护栏的浏览器会话。"""

    def __init__(self) -> None:
        self._driver: Optional[webdriver.Chrome] = None
        self._service_pid: Optional[int] = None
        self._profile_dir: Optional[str] = None
        self._started_at = 0.0
        self._commands = 0
        self._poisoned = False
        self._pid_lock = threading.Lock()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        driver_path = resolve_chromedriver()
        self._profile_dir = tempfile.mkdtemp(prefix=profile_prefix())

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(f"--user-data-dir={self._profile_dir}")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-extensions")
        options.add_argument("--window-size=1440,900")
        # 长时间 headless 下避免渲染进程被判定为后台而限流/挂起
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-features=CalculateNativeWinOcclusion")
        # 限制 V8 堆上限，防止长时间运行后渲染进程堆无界增长
        options.add_argument("--js-flags=--max-old-space-size=512")

        service = Service(driver_path)
        try:
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            self._cleanup_profile()
            raise

        self._driver = driver
        self._service_pid = getattr(getattr(service, "process", None), "pid", None)
        self._started_at = time.monotonic()
        self._commands = 0
        self._poisoned = False

        try:
            driver.set_page_load_timeout(NAV_TIMEOUT)
            driver.set_script_timeout(20)
            driver.set_window_size(1440, 900)
        except Exception as exc:
            file_log(f"设置驱动超时参数失败: {exc}", "WARN")

        file_log(
            f"浏览器会话已建立 pid={self._service_pid} profile={self._profile_dir}"
        )

    def stop(self, graceful_timeout: int = 8) -> None:
        driver, self._driver = self._driver, None
        pid, self._service_pid = self._service_pid, None

        if driver is not None:
            box: dict[str, Any] = {}

            def quit_worker() -> None:
                try:
                    driver.quit()
                except Exception as exc:  # noqa: BLE001
                    box["error"] = exc

            thread = threading.Thread(target=quit_worker, name="wd-quit", daemon=True)
            thread.start()
            thread.join(graceful_timeout)
            if thread.is_alive():
                file_log("driver.quit() 超时，转为强制结束进程树", "WARN")

        kill_process_tree(pid or 0)
        sweep_chrome_by_profile()
        self._cleanup_profile()
        file_log("浏览器会话已回收")

    def _cleanup_profile(self) -> None:
        profile, self._profile_dir = self._profile_dir, None
        if not profile:
            return
        for _ in range(3):
            shutil.rmtree(profile, ignore_errors=True)
            if not os.path.isdir(profile):
                return
            time.sleep(0.4)
        file_log(f"临时 profile 目录未能删除: {profile}", "WARN")

    def restart(self) -> None:
        self.stop()
        self.start()

    @property
    def alive(self) -> bool:
        return self._driver is not None

    @property
    def service_pid(self) -> Optional[int]:
        return self._service_pid

    @property
    def command_count(self) -> int:
        return self._commands

    @property
    def age_minutes(self) -> float:
        if not self._started_at:
            return 0.0
        return (time.monotonic() - self._started_at) / 60

    def recycle_reason(self) -> Optional[str]:
        """返回需要回收的理由，None 表示会话仍健康。"""
        if self._driver is None:
            return "会话不存在"
        if self._poisoned:
            return "上一条指令超时或异常"
        age = time.monotonic() - self._started_at
        if age >= RECYCLE_AFTER_SECONDS:
            return f"已运行 {age / 60:.0f} 分钟，按计划回收"
        if self._commands >= RECYCLE_AFTER_COMMANDS:
            return f"累计 {self._commands} 条指令，按计划回收"
        return None

    # ---------- 指令护栏 ----------

    def _run_guarded(self, fn: Callable[[], Any], timeout: int, label: str) -> Any:
        if self._poisoned:
            raise SessionLost("会话已失效，等待回收重建")
        if self._driver is None:
            raise SessionLost("会话未建立")

        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        started = time.monotonic()
        thread = threading.Thread(target=runner, name=f"wd:{label}", daemon=True)
        thread.start()
        thread.join(timeout)
        elapsed = time.monotonic() - started
        self._commands += 1

        if thread.is_alive():
            # 卡住的指令无法取消，直接放弃整个会话，由上层回收重建。
            self._poisoned = True
            file_log(f"指令 {label} 超时（>{timeout}s），判定会话失效", "ERROR")
            raise SessionLost(f"指令 {label} 超时 {timeout}s")

        if elapsed > 5.0:
            file_log(f"慢指令 {label} 耗时 {elapsed:.1f}s", "WARN")

        error = box.get("error")
        if error is not None:
            if not isinstance(error, (SessionLost, StaleElementReferenceException)):
                file_log(f"指令 {label} 异常: {type(error).__name__}: {error}", "WARN")
            self._poisoned = True
            raise error
        return box.get("value")

    # ---------- 页面操作（原子、只返回纯数据） ----------

    def goto(self, url: str) -> None:
        def action() -> None:
            try:
                self._driver.get(url)  # type: ignore[union-attr]
            except TimeoutException:
                # SPA 页面常驻长连接会让 load 事件迟迟不完成，交给 probe 判定可用性
                file_log("页面加载完成事件超时，交由状态探测判定", "WARN")

        self._run_guarded(action, NAV_TIMEOUT, "goto")

    def probe(self) -> dict[str, Any]:
        """一次性读取登录态与服务器状态，返回纯数据字典。"""

        def action() -> dict[str, Any]:
            driver = self._driver
            login_visible = False
            for element in driver.find_elements(By.CSS_SELECTOR, LOGIN_INPUT_SELECTOR):
                try:
                    if element.is_displayed():
                        login_visible = True
                        break
                except StaleElementReferenceException:
                    continue
            status_text: Optional[str] = None
            for element in driver.find_elements(By.CSS_SELECTOR, "span[class*='vpsState']"):
                try:
                    text = element.text.strip()
                    if text:
                        status_text = text
                        break
                except StaleElementReferenceException:
                    continue
            if not status_text:
                for element in driver.find_elements(
                    By.XPATH, "//p[contains(., '服务器状态')]/span"
                ):
                    try:
                        text = element.text.strip()
                        if text:
                            status_text = text
                            break
                    except StaleElementReferenceException:
                        continue
            return {"login": login_visible, "status": status_text}

        return self._run_guarded(action, COMMAND_TIMEOUT, "probe")

    def login(self) -> bool:
        def action() -> bool:
            driver = self._driver
            password_input = None
            for element in driver.find_elements(By.CSS_SELECTOR, LOGIN_INPUT_SELECTOR):
                try:
                    if element.is_displayed():
                        password_input = element
                        break
                except StaleElementReferenceException:
                    continue
            if password_input is None:
                return False

            password_input.clear()
            password_input.send_keys(PASSWORD)

            submit = None
            for element in driver.find_elements(By.CSS_SELECTOR, SUBMIT_SELECTOR):
                try:
                    if element.is_displayed() and element.is_enabled():
                        submit = element
                        break
                except StaleElementReferenceException:
                    continue
            if submit is None:
                return False

            driver.execute_script("arguments[0].click();", submit)
            return True

        return bool(self._run_guarded(action, COMMAND_TIMEOUT, "login"))

    def click_text(self, target_text: str) -> bool:
        target = normalize_text(target_text)

        def action() -> bool:
            driver = self._driver
            for button in driver.find_elements(By.TAG_NAME, "button"):
                try:
                    if not button.is_displayed() or not button.is_enabled():
                        continue
                    if normalize_text(button.text) != target:
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", button
                    )
                    driver.execute_script("arguments[0].click();", button)
                    return True
                except StaleElementReferenceException:
                    continue
            return False

        return bool(self._run_guarded(action, COMMAND_TIMEOUT, f"click:{target_text}"))


# ============================== 状态与动作 ==============================


def wait_for_page_ready(browser: GuardedBrowser, timeout: int) -> dict[str, Any]:
    """等待页面出现登录框或状态元素。

    该控制台是 SPA，远端会话建立前既无密码框也无状态文本，
    因此不能按固定 5~10 秒判死，必须显式等待页面就绪。
    """
    deadline = time.monotonic() + timeout
    snapshot: dict[str, Any] = {"login": False, "status": None}
    last_remaining: Optional[int] = None
    while True:
        snapshot = browser.probe()
        if snapshot.get("login") or snapshot.get("status"):
            clear_current_line()
            return snapshot

        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != last_remaining:
            progress(f"等待页面就绪，剩余 {remaining:02d}s...")
            last_remaining = remaining
        if remaining <= 0:
            clear_current_line()
            return snapshot
        time.sleep(2.0)


def wait_for_status(browser: GuardedBrowser, seconds: int) -> dict[str, Any]:
    """在限定时间内轮询直到读到状态文本。"""
    deadline = time.monotonic() + seconds
    snapshot: dict[str, Any] = {"login": False, "status": None}
    last_remaining: Optional[int] = None
    while True:
        snapshot = browser.probe()
        if snapshot.get("status"):
            clear_current_line()
            return snapshot

        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != last_remaining:
            progress(f"等待页面状态加载，剩余 {remaining:02d}s...")
            last_remaining = remaining
        if remaining <= 0:
            clear_current_line()
            return snapshot
        time.sleep(1.0)


def collect_status(browser: GuardedBrowser, settle: int = 8) -> tuple[str, bool]:
    """返回 (状态显示文本, 是否需要回收会话)。

    settle 为等待页面就绪的秒数：常态轮询给短窗口，
    首次加载或重载后给 SETTLE_TIMEOUT 的宽窗口。
    """
    try:
        snapshot = wait_for_page_ready(browser, settle)
    except Exception as exc:
        clear_current_line()
        return f"读取失败: {exc}", True

    if snapshot.get("login"):
        clear_current_line()
        log("检测到登录状态失效，正在自动重新登录...")
        try:
            if not browser.login():
                return "登录表单存在但未能提交", True
            snapshot = wait_for_status(browser, COMMAND_TIMEOUT + 5)
            log("重新登录成功")
        except Exception as exc:
            clear_current_line()
            return f"自动登录失败: {exc}", True

    if not snapshot.get("status"):
        # 页面可能已失活：重载一次再判定
        clear_current_line()
        log("未读到状态元素，尝试重新加载页面...")
        try:
            browser.goto(URL)
            snapshot = wait_for_page_ready(browser, SETTLE_TIMEOUT)
            if snapshot.get("login"):
                if browser.login():
                    log("重新登录成功")
                    snapshot = wait_for_status(browser, COMMAND_TIMEOUT + 5)
            elif not snapshot.get("status"):
                snapshot = wait_for_status(browser, COMMAND_TIMEOUT + 5)
        except Exception as exc:
            clear_current_line()
            return f"页面重载失败: {exc}", True

    status = snapshot.get("status")
    if not status:
        return "未能读取服务器状态（页面结构或会话异常）", True
    return status, False


def open_rdp_file() -> None:
    if not os.path.exists(RDP_FILE_PATH):
        log(f"未找到远程桌面文件: {RDP_FILE_PATH}")
        return

    if os.name != "nt":
        log("当前系统不支持直接打开 .rdp 文件")
        return

    try:
        os.startfile(RDP_FILE_PATH)
        log(f"已打开远程桌面文件: {RDP_FILE_PATH}")
    except Exception as exc:
        log(f"打开远程桌面文件失败: {exc}")


def perform_action(browser: GuardedBrowser, choice: str) -> None:
    action_map = {"1": "唤醒", "2": "休眠", "3": "关机"}
    popup_confirm_map = {"2": "确认休眠", "3": "确认关机"}

    action = action_map[choice]

    try:
        snapshot = browser.probe()
        if snapshot.get("login"):
            log("检测到登录状态失效，正在自动重新登录...")
            browser.login()
            wait_for_status(browser, COMMAND_TIMEOUT + 5)
    except Exception as exc:
        clear_current_line()
        log(f"会话不可用（{exc}），正在重建浏览器后重试...")
        try:
            browser.restart()
            browser.goto(URL)
            collect_status(browser, settle=SETTLE_TIMEOUT)
        except Exception as retry_exc:
            log(f"浏览器重建失败，操作中止: {retry_exc}")
            return

    if not browser.click_text(action):
        log(f"未找到可点击按钮: {action}")
        return

    log(f"已点击【{action}】按钮")

    popup_text = popup_confirm_map.get(choice)
    if popup_text:
        if browser.click_text(popup_text):
            log(f"已点击弹窗【{popup_text}】")
        else:
            log(f"未检测到弹窗【{popup_text}】，或按钮暂不可点")


# ============================== 管道输入 ==============================

STDIN_INPUT_QUEUE: "queue.Queue[str]" = queue.Queue()
_STDIN_THREAD_STARTED = False


def _start_stdin_listener() -> None:
    global _STDIN_THREAD_STARTED
    if _STDIN_THREAD_STARTED:
        return
    _STDIN_THREAD_STARTED = True

    def reader() -> None:
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                STDIN_INPUT_QUEUE.put(line.strip())
            except Exception:
                break

    threading.Thread(target=reader, name="stdin", daemon=True).start()


def input_with_timeout(prompt_template: str, timeout: int) -> Optional[str]:
    def render_prompt(remaining_seconds: int) -> None:
        sys.stdout.write("\r" + prompt_template.format(remaining=remaining_seconds))
        sys.stdout.flush()

    deadline = time.monotonic() + timeout
    last_remaining: Optional[int] = None

    # 非交互式终端（GUI 重定向管道）：后台线程读标准输入 + 队列轮询
    if not sys.stdin.isatty():
        _start_stdin_listener()
        while True:
            remaining = max(0, int(deadline - time.monotonic() + 0.999))
            if remaining != last_remaining:
                render_prompt(remaining)
                last_remaining = remaining

            try:
                value = STDIN_INPUT_QUEUE.get_nowait()
                if value:
                    sys.stdout.write(value + "\n")
                    sys.stdout.flush()
                    return value
            except queue.Empty:
                pass

            if remaining <= 0:
                log("")
                return None

            time.sleep(0.05)

    if os.name == "nt":
        import msvcrt

        while True:
            remaining = max(0, int(deadline - time.monotonic() + 0.999))
            if remaining != last_remaining:
                render_prompt(remaining)
                last_remaining = remaining

            if msvcrt.kbhit():
                char = msvcrt.getwch()

                if char == "\x03":
                    raise KeyboardInterrupt
                if char in ("\x00", "\xe0"):
                    _ = msvcrt.getwch()
                    continue
                if char in ("\r", "\n", "\b", "\x7f"):
                    continue

                sys.stdout.write(char + "\n")
                sys.stdout.flush()
                return char.strip() or None

            if remaining <= 0:
                log("")
                return None

            time.sleep(0.05)

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)

        while True:
            remaining = max(0, int(deadline - time.monotonic() + 0.999))
            if remaining != last_remaining:
                render_prompt(remaining)
                last_remaining = remaining

            rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
            if rlist:
                char = sys.stdin.read(1)
                if char == "\x03":
                    raise KeyboardInterrupt
                if char in ("\r", "\n", "\b", "\x7f"):
                    continue

                sys.stdout.write(char + "\n")
                sys.stdout.flush()
                return char.strip() or None

            if remaining <= 0:
                log("")
                return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# ============================== 主循环 ==============================


def menu_loop(browser: GuardedBrowser) -> None:
    poll = 0
    failures = 0
    restart_stamps: list[float] = []

    while True:
        poll += 1
        status, need_recycle = collect_status(browser)
        if need_recycle:
            failures += 1
        else:
            failures = 0

        reason: Optional[str] = None
        if need_recycle and failures >= RECYCLE_AFTER_FAILURES:
            reason = f"连续 {failures} 轮读取失败"
        else:
            reason = browser.recycle_reason()

        if reason:
            now = time.monotonic()
            restart_stamps = [t for t in restart_stamps if now - t < 600]
            if len(restart_stamps) >= 4:
                log("浏览器重建过于频繁，暂停 120 秒后再试")
                time.sleep(120)
                restart_stamps.clear()

            clear_screen()
            log(f"会话回收：{reason}")
            log("正在重建浏览器会话...")
            try:
                browser.restart()
                restart_stamps.append(time.monotonic())
                browser.goto(URL)
                status, need_recycle = collect_status(browser, settle=SETTLE_TIMEOUT)
                if not need_recycle:
                    failures = 0
            except Exception as exc:
                log(f"浏览器重建失败: {exc}")
                status = f"会话不可用: {exc}"

        clear_screen()
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        age_min = browser.age_minutes

        log("VPS 控制台")
        log(f"时间: {now_text}")
        log(f"服务器状态: {status}")
        log(f"会话: 第 {poll} 次轮询 · 浏览器已用 {age_min:.0f} 分钟 · 指令 {browser.command_count} 条")
        file_log(f"轮询 #{poll} 状态={status} 会话年龄={age_min:.0f}min 指令={browser.command_count}")
        log("")
        log("操作选项:")
        log("  1. 唤醒")
        log("  2. 休眠")
        log("  3. 关机")
        log("  4. 打开远程桌面")
        log("  0. 退出")

        choice = input_with_timeout(
            "输入选项(0/1/2/3/4)，{remaining:02d}秒无输入将自动刷新: ",
            POLL_INTERVAL_SECONDS,
        )

        if choice is None or choice == "":
            continue

        if choice == "0":
            log("已退出脚本。")
            return

        if choice == "4":
            open_rdp_file()
            time.sleep(2)
            continue

        if choice not in {"1", "2", "3"}:
            log("无效选项，2 秒后刷新。")
            time.sleep(2)
            continue

        perform_action(browser, choice)
        time.sleep(2)


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    install_kill_on_close_job()
    sweep_stale_profiles()
    atexit.register(sweep_chrome_by_profile)

    clear_screen()
    file_log("进程启动 smoke=%s" % SMOKE_MODE)
    log("正在初始化 WebDriver...")

    browser = GuardedBrowser()
    exit_code = 0

    try:
        browser.start()
        log(f"浏览器已就绪（chromedriver pid={browser.service_pid}）")
        log("正在访问页面...")
        browser.goto(URL)

        # 首轮直接走完整状态采集：登录页会立即触发自动登录，无需空等满 60 秒
        initial_status, need_recycle = collect_status(browser, settle=SETTLE_TIMEOUT)
        log(f"初始状态: {initial_status}")
        if need_recycle:
            log("初始状态读取异常，进入控制台后将自动重建会话")

        if SMOKE_MODE:
            exit_code = 0 if not need_recycle else 1
        else:
            menu_loop(browser)

    except KeyboardInterrupt:
        log("\n收到 Ctrl+C，已停止。")
    except Exception as exc:
        log(f"执行失败: {exc}")
        file_log(f"致命异常: {type(exc).__name__}: {exc}", "ERROR")
        exit_code = 1
    finally:
        browser.stop()
        file_log("进程退出 code=%s" % exit_code)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
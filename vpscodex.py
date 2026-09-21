#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
import os
import sys
import time
import queue
import threading

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
except Exception as exc:
    print("缺少依赖 selenium。请先执行: pip install selenium", flush=True)
    print(f"原始错误: {exc}", flush=True)
    raise SystemExit(1)

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception as exc:
    print("缺少依赖 webdriver-manager。请先执行: pip install webdriver-manager", flush=True)
    print(f"原始错误: {exc}", flush=True)
    raise SystemExit(1)

URL = "https://home.kuniaovps.com/i/remote/1"
PASSWORD = ""
RDP_FILE_PATH = r"C:\Users\Administrator\Desktop\UK Play.rdp"
POLL_INTERVAL_SECONDS = 60
LOGIN_INPUT_SELECTOR = (
    "#password, "
    "input[type='password'][placeholder='请输入密码'], "
    "input[type='password']"
)
SUBMIT_SELECTOR = "button[type='submit']"

STATUS_SELECTOR = (
    "span[class*='vpsState'], "
    "span[class*='RUNNING'], "
    "span[class*='STOP'], "
    "span[class*='SLEEP']"
)


def clear_screen() -> None:
    # 不调用 os.system("cls")，避免后台运行时周期性创建可见 cmd 窗口。
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def log(message: str = "") -> None:
    print(message, flush=True)


def clear_current_line() -> None:
    sys.stdout.write("\r" + (" " * 100) + "\r")
    sys.stdout.flush()


def normalize_text(text: str) -> str:
    return "".join(text.split())


def find_visible_login_input(driver: webdriver.Chrome):
    for el in driver.find_elements(By.CSS_SELECTOR, LOGIN_INPUT_SELECTOR):
        try:
            if el.is_displayed():
                return el
        except Exception:
            continue
    return None


def find_status_text(driver: webdriver.Chrome) -> str | None:
    for el in driver.find_elements(By.CSS_SELECTOR, "span[class*='vpsState']"):
        try:
            text = el.text.strip()
            if text:
                return text
        except Exception:
            continue

    for el in driver.find_elements(By.XPATH, "//p[contains(., '服务器状态')]/span"):
        try:
            text = el.text.strip()
            if text:
                return text
        except Exception:
            continue

    return None


def is_login_required(driver: webdriver.Chrome, timeout: int = 10) -> bool:
    deadline = time.monotonic() + timeout
    last_remaining: int | None = None

    while True:
        if find_visible_login_input(driver) is not None:
            clear_current_line()
            return True

        if find_status_text(driver):
            clear_current_line()
            return False

        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != last_remaining:
            sys.stdout.write(f"\r等待页面加载，剩余 {remaining:02d}s...")
            sys.stdout.flush()
            last_remaining = remaining

        if remaining <= 0:
            clear_current_line()
            return False

        time.sleep(0.2)


def ensure_logged_in(driver: webdriver.Chrome) -> bool:
    if not is_login_required(driver, timeout=10):
        return False

    log("检测到登录状态失效，正在自动重新登录...")
    wait = WebDriverWait(driver, 20)

    pwd_input = wait.until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, LOGIN_INPUT_SELECTOR))
    )
    pwd_input.clear()
    pwd_input.send_keys(PASSWORD)

    submit_btn = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, SUBMIT_SELECTOR))
    )
    driver.execute_script("arguments[0].click();", submit_btn)

    _ = get_status(driver, timeout=20)
    log("重新登录成功")
    return True


def get_status(
    driver: webdriver.Chrome,
    timeout: int = 5,
    wait_message: str = "等待网页加载",
) -> str:
    deadline = time.monotonic() + timeout
    last_remaining: int | None = None

    while True:
        status = find_status_text(driver)
        if status:
            clear_current_line()
            return status

        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != last_remaining:
            sys.stdout.write(f"\r{wait_message}，剩余 {remaining:02d}s...")
            sys.stdout.flush()
            last_remaining = remaining

        if remaining <= 0:
            clear_current_line()
            raise RuntimeError("未读取到服务器状态元素")

        time.sleep(0.2)


def get_status_with_relogin(
    driver: webdriver.Chrome,
    timeout: int = 5,
    wait_message: str = "等待网页加载",
) -> str:
    try:
        ensure_logged_in(driver)
        return get_status(driver, timeout=timeout, wait_message=wait_message)
    except Exception as first_exc:
        clear_current_line()
        log("获取服务器状态失败，尝试重新登录...")

        try:
            driver.get(URL)
            ensure_logged_in(driver)
            return get_status(
                driver,
                timeout=timeout,
                wait_message="重新登录后等待状态加载",
            )
        except Exception as retry_exc:
            raise RuntimeError(f"{first_exc}；重新登录后仍失败: {retry_exc}")


def load_status_for_menu(driver: webdriver.Chrome) -> str:
    clear_screen()
    log("VPS 控制台")
    log("正在刷新页面状态...")

    try:
        return get_status_with_relogin(
            driver,
            timeout=10,
            wait_message="等待网页加载",
        )
    except Exception as exc:
        clear_current_line()
        return f"读取失败: {exc}"


def click_button_by_exact_text(
    driver: webdriver.Chrome,
    target_text: str,
    timeout: int = 5,
) -> bool:
    target = normalize_text(target_text)
    deadline = time.time() + timeout

    while time.time() < deadline:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            try:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
                current = normalize_text(btn.text)
                if current == target:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        btn,
                    )
                    driver.execute_script("arguments[0].click();", btn)
                    return True
            except Exception:
                continue
        time.sleep(0.2)

    return False


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


def perform_action(driver: webdriver.Chrome, choice: str) -> None:
    try:
        ensure_logged_in(driver)
    except Exception as exc:
        log(f"自动重新登录失败: {exc}")
        return

    action_map = {
        "1": "唤醒",
        "2": "休眠",
        "3": "关机",
    }
    popup_confirm_map = {
        "2": "确认休眠",
        "3": "确认关机",
    }

    action = action_map[choice]

    if not click_button_by_exact_text(driver, action, timeout=10):
        log(f"未找到可点击按钮: {action}")
        return

    log(f"已点击【{action}】按钮")

    popup_text = popup_confirm_map.get(choice)
    if popup_text:
        if click_button_by_exact_text(driver, popup_text, timeout=8):
            log(f"已点击弹窗【{popup_text}】")
        else:
            log(f"未检测到弹窗【{popup_text}】，或按钮暂不可点")


# 管道输入缓冲队列（用于非控制台环境/GUI下通过标准输入接收指令）
STDIN_INPUT_QUEUE: queue.Queue[str] = queue.Queue()
_STDIN_THREAD_STARTED = False


def _start_stdin_listener():
    global _STDIN_THREAD_STARTED
    if _STDIN_THREAD_STARTED:
        return
    _STDIN_THREAD_STARTED = True

    def _reader():
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                STDIN_INPUT_QUEUE.put(line.strip())
            except Exception:
                break

    threading.Thread(target=_reader, daemon=True).start()


def input_with_timeout(prompt_template: str, timeout: int) -> str | None:
    def render_prompt(remaining_seconds: int) -> None:
        sys.stdout.write("\r" + prompt_template.format(remaining=remaining_seconds))
        sys.stdout.flush()

    deadline = time.monotonic() + timeout
    last_remaining: int | None = None

    # 如果是非交互式终端（例如 GUI 重定向管道），启动后台读取线程并从队列获取输入
    if not sys.stdin.isatty():
        _start_stdin_listener()
        while True:
            remaining = max(0, int(deadline - time.monotonic() + 0.999))
            if remaining != last_remaining:
                render_prompt(remaining)
                last_remaining = remaining

            try:
                val = STDIN_INPUT_QUEUE.get_nowait()
                if val:
                    sys.stdout.write(val + "\n")
                    sys.stdout.flush()
                    return val
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
                ch = msvcrt.getwch()

                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x00", "\xe0"):
                    _ = msvcrt.getwch()
                    continue
                if ch in ("\r", "\n", "\b", "\x7f"):
                    continue

                sys.stdout.write(ch + "\n")
                sys.stdout.flush()
                return ch.strip() or None

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
                ch = sys.stdin.read(1)
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\r", "\n", "\b", "\x7f"):
                    continue

                sys.stdout.write(ch + "\n")
                sys.stdout.flush()
                return ch.strip() or None

            if remaining <= 0:
                log("")
                return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def menu_loop(driver: webdriver.Chrome) -> None:
    while True:
        status = load_status_for_menu(driver)
        clear_screen()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        log("VPS 控制台")
        log(f"时间: {now}")
        log(f"服务器状态: {status}")
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

        perform_action(driver, choice)
        time.sleep(2)


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    clear_screen()
    log("正在初始化 WebDriver（webdriver-manager）...")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    driver: webdriver.Chrome | None = None

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        log("正在访问页面...")
        driver.get(URL)

        _ = get_status_with_relogin(
            driver,
            timeout=20,
            wait_message="等待初始页面加载",
        )
        menu_loop(driver)

    except KeyboardInterrupt:
        log("\n收到 Ctrl+C，已停止。")
    except Exception as exc:
        log(f"执行失败: {exc}")
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()

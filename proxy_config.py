# -*- coding: utf-8 -*-
"""
NEXUS · 代理配置持久层
======================
全局单一数据源，供 gui_launcher.py（窗口快捷指令）与 systemproxy_watcher.py
（预设代理池）共同读写。

配置文件持久化在 %LOCALAPPDATA%\\NexusFieldDeck\\proxy_config.json，
因此所有变更在脚本重启、操控台重开乃至重新部署后依然生效：

    {
      "version": 1,
      "quick_actions": ["127.0.0.1:20808", ...],   # 操控台指令芯片
      "proxy_pool":    ["127.0.0.1:20808", ...],   # 守候脚本预设代理池
      "updated_at":    "2026-11-10 11:40:00"
    }

文件缺失或损坏时回退到本模块内置的默认值，保证脚本始终可用。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

APP_DIR_NAME = "NexusFieldDeck"
CONFIG_FILENAME = "proxy_config.json"
CONFIG_VERSION = 1

# 操控台指令芯片的出厂默认值（与历史硬编码保持一致）
DEFAULT_QUICK_ACTIONS = [
    "127.0.0.1:20808",
    "127.0.0.1:9990",
    "127.0.0.1:8890",
    "127.0.0.1:10889",
    "127.0.0.1:7897",
]

# 守候脚本预设代理池的出厂默认值（序号 1 为默认代理）
DEFAULT_PROXY_POOL = [
    "127.0.0.1:20808",
    "127.0.0.1:10808",
    "127.0.0.1:9990",
    "127.0.0.1:8890",
    "127.0.0.1:7897",
]

# 单侧配置项上限（与按键编号展示、界面高度相匹配）
MAX_ENTRIES = 12

_PROTOCOL_PREFIXES = ("http://", "https://", "socks5://", "socks://", "socks=")
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def config_dir() -> str:
    """配置目录：优先用户级 LOCALAPPDATA，缺失时回退到脚本目录。"""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    if not base:
        return os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, APP_DIR_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), CONFIG_FILENAME)


def normalize_proxy(value: Any) -> Optional[str]:
    """把用户输入规范化为 host:port（或裸域名），非法输入返回 None。

    接受形式：7890 / 127.0.0.1:7890 / 127.0.0.1 7890 /
              http://127.0.0.1:7890/ / socks5://proxy.local:1080
    """
    if value is None:
        return None
    val = str(value).strip().replace("：", ":")
    if not val:
        return None

    if val.isdigit():
        port = int(val)
        return f"127.0.0.1:{port}" if 1 <= port <= 65535 else None

    lowered = val.lower()
    for prefix in _PROTOCOL_PREFIXES:
        if lowered.startswith(prefix):
            val = val[len(prefix):]
            break
    val = val.strip().rstrip("/")

    if " " in val:
        parts = [p for p in val.split() if p]
        if len(parts) == 2:
            val = f"{parts[0]}:{parts[1]}"

    if ":" in val:
        host, _, port_str = val.rpartition(":")
        host, port_str = host.strip(), port_str.strip()
        if host and port_str.isdigit():
            port = int(port_str)
            if 1 <= port <= 65535:
                return f"{host}:{port}"
        return None

    if "." in val or val.lower() == "localhost":
        return val
    return None


def chip_label(proxy: str) -> str:
    """芯片显示名：本机回环地址只显示端口，其余保留完整地址。"""
    host, sep, port = proxy.rpartition(":")
    if sep and port.isdigit() and host.strip().lower() in _LOCAL_HOSTS:
        return port
    return proxy


def _clean_list(values: Any, fallback: list[str]) -> list[str]:
    """规范化、去重并剔除非法项；结果为空时使用 fallback。"""
    if not isinstance(values, list):
        return list(fallback)

    cleaned: list[str] = []
    for raw in values:
        norm = normalize_proxy(raw)
        if norm and norm not in cleaned:
            cleaned.append(norm)
        if len(cleaned) >= MAX_ENTRIES:
            break
    return cleaned or list(fallback)


def _read_payload() -> Optional[dict]:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load() -> dict:
    """读取生效配置。返回 quick_actions / proxy_pool / source / path。"""
    payload = _read_payload()
    if payload is None:
        return {
            "quick_actions": list(DEFAULT_QUICK_ACTIONS),
            "proxy_pool": list(DEFAULT_PROXY_POOL),
            "source": "default",
            "path": config_path(),
        }
    return {
        "quick_actions": _clean_list(payload.get("quick_actions"), DEFAULT_QUICK_ACTIONS),
        "proxy_pool": _clean_list(payload.get("proxy_pool"), DEFAULT_PROXY_POOL),
        "source": "file",
        "path": config_path(),
    }


def load_proxy_pool() -> list[str]:
    """守候脚本使用：返回生效的预设代理池（永不为空）。"""
    return load()["proxy_pool"]


def save(quick_actions: list[str], proxy_pool: list[str]) -> dict:
    """原子写入配置文件并返回落盘内容。代理池必须至少保留一项。"""
    quick = _clean_list(quick_actions, [])
    pool = _clean_list(proxy_pool, [])
    if not pool:
        raise ValueError("脚本代理池至少需要保留 1 项")

    path = config_path()
    payload = {
        "version": CONFIG_VERSION,
        "quick_actions": quick,
        "proxy_pool": pool,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)

    payload["path"] = path
    payload["source"] = "file"
    return payload


def reset() -> bool:
    """删除用户覆盖配置，回落到内置默认值。"""
    path = config_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False
# windows-remote-debug-scripts

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6.svg?logo=windows)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Style](https://img.shields.io/badge/UI-Modern%20Dark%20Console-8A2BE2.svg)](#)

Windows 远程调试与日常运维脚本集成控制中心（**Nexus Hub · Service Matrix**）。提供沉浸式暗黑现代化 GUI 控制台，支持在单窗口中以三栏划分形式并发运行并监控以下三个核心服务，配备流式虚拟终端（支持 ANSI 光标重绘与就地 `\r` 刷新）以及原生 UAC 静默提权保障。

---

## 📸 核心组件与系统架构

| 模块 | 核心脚本 | 职责与技术特性 |
| :--- | :--- | :--- |
| **GUI 总控中枢** | `gui_launcher.py`<br>`start_gui.bat` | 采用 Tkinter + Windows 原生 DWM 深色 API 打造，三栏并行管理、生命周期进程树全量回收、防残留自愈清理。 |
| **终端渲染引擎** | `terminal_buffer.py` | 虚拟终端屏幕缓冲区，原生解析 ANSI 光标跳转（`\033[row;colH`）、行清除（`\033[2K`）与回车单行就地覆写（`\r`）。 |
| **云节点运维** | `vpscodex.py` | 基于 Selenium Chrome Headless 的云服务器状态定时探测与电源管理（唤醒/休眠/关机/RDP一键拉起）。 |
| **系统代理监控** | `systemproxy_watcher.py` | 实时监听 Windows 注册表 `Internet Settings` 与 WinINet 广播，自动修正代理冲突，支持预设池快捷切换或端口/完整URL自适应解析。 |
| **蓝牙连接保活** | `bluetooth.bat`<br>`bluetooth.ps1` | 自动禁用适配器电源休眠（Selective Suspend）、实时监测系统 `bthserv` 服务状态，断开自动重启 PnP 设备重连。 |

---

## ✨ 核心特性

1. **🎨 现代极简暗黑视觉**：
   - 采用深空夜蓝与翡翠绿/科技蓝主题（Tailwind Slate 900 色系）。
   - 深度注入 Windows DWM 原生深色标题栏（`DWMWA_CAPTION_COLOR = 35`），实现窗口与内容 100% 连贯融合过渡。
   - 矢量平滑圆角按钮，带悬停发光动画与状态呼吸指示徽章。

2. **⚡ 流式终端与就地局部刷新**：
   - 彻底告别传统控制台重定向带来的全屏刷屏与乱码问题。
   - 完美承载倒计时刷新、表格覆盖刷新与原地状态更替。

3. **🛡️ 纯净进程生命周期守护**：
   - **冷启动清理**：程序拉起时自动扫描并清理以往测试遗留的孤立后台进程。
   - **退出安全回收**：关闭窗口或停止任务时，利用 `taskkill /F /T` 递归杀除进程树（含 ChromeDriver 及 PowerShell 后台）。
   - **强杀兜底**：`vpscodex.py` 将自身与 ChromeDriver 绑定进 Windows Job Object（`KILL_ON_JOB_CLOSE`），即使进程被强制结束，浏览器子进程也会由系统一并回收，不再累积孤儿进程。
   - **静默看门狗**：面板检测到服务进程长时间零输出即判定无响应，自动终止并按退避策略重建（每小时最多 5 次），无需人工干预。

4. **⌨️ 智能代理自由切换**：
   - 在系统代理面板下方输入框直接输入端口（如 `7890`），自动补全为 `127.0.0.1:7890` 并生效。
   - 输入带协议完整地址（如 `http://proxy.domain.com:8080`），自动去除协议头并正确配置注册表。

---

## 🛠️ 环境依赖

1. **操作系统**：Windows 10 / Windows 11 (x64)
2. **Python 版本**：Python 3.10+
3. **Python 依赖库**：
   ```bash
   pip install selenium webdriver-manager pywin32
   ```

---

## 🚀 快速开始

### 1. 运行方式（推荐）
直接双击根目录下的 **`start_gui.bat`**。批处理将以无控制台黑框方式唤醒 GUI，并自动调起管理员提权保护。

### 2. 命令行运行
以管理员身份打开 PowerShell 或 CMD，进入脚本所在目录执行：
```bash
python gui_launcher.py
```

### 3. 云节点探测配置

`vpscodex.py` 的接入地址与密码**不纳入仓库**，通过环境变量注入：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `VPSCODEX_URL` | `https://home.kuniaovps.com/i/remote/1` | 控制台页面地址（含分享密钥） |
| `VPSCODEX_PASSWORD` | 空 | 页面登录密码 |
| `VPSCODEX_RDP_FILE` | `%USERPROFILE%\Desktop\UK Play.rdp` | 远程桌面快捷文件路径 |

长稳参数均有默认值，通常无需改动：`VPSCODEX_POLL_INTERVAL`（轮询间隔，60s）、`VPSCODEX_CMD_TIMEOUT`（单条指令超时，25s）、`VPSCODEX_NAV_TIMEOUT`（导航超时，60s）、`VPSCODEX_SETTLE_TIMEOUT`（页面就绪等待，90s）、`VPSCODEX_RECYCLE_SECONDS`（会话回收周期，1800s）、`VPSCODEX_RECYCLE_COMMANDS`（会话指令上限，1200 条）。

环境自检：`python vpscodex.py --once`（建会话 → 读状态 → 回收，返回码 0 表示链路正常）。诊断日志位于 `%LOCALAPPDATA%\NexusFieldDeck\vpscodex.log`（1MB 轮转）。

---

## 📂 文件目录结构

```text
windows-remote-debug-scripts/
│
├── gui_launcher.py          # 现代化三栏集成 GUI 控制台主入口
├── start_gui.bat            # 一键无黑框极速启动批处理
├── terminal_buffer.py       # 虚拟终端缓冲区引擎（ANSI & \r 解析器）
│
├── vpscodex.py              # VPS 云节点控制脚本（Selenium 自动化）
├── systemproxy_watcher.py   # Windows 系统代理守候与快速切换脚本
├── bluetooth.bat            # 蓝牙保活引导脚本
├── bluetooth.ps1            # 蓝牙硬件电源与服务状态守护脚本
│
├── .gitignore               # Git 忽略规则
├── LICENSE                  # 开源协议 (MIT)
└── README.md                # 项目详细说明文档
```

---

## 📄 开源许可证

本项目采用 [MIT License](LICENSE) 开源许可协议。

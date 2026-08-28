# -*- coding: utf-8 -*-
"""专用浏览器管理：
- 独立 user-data-dir（登录态持久保存在项目 state/chrome-profile 里）
- Chrome 136+ 禁止对默认配置目录开启远程调试，因此必须用独立配置目录
- login.py 用它开一个有界面的窗口让人手动登录
- monitor.py 优先连接已在运行的实例，连不上就以无头模式自动拉起
"""
import os
import socket
import subprocess
import time
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parent
PROFILE_DIR = PROJECT_DIR / "state" / "chrome-profile"

_COMMON_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _valid_install(exe: str) -> bool:
    """完整安装的 Chromium 系浏览器，其 Application 目录下必有版本号子目录。

    用于跳过只剩一个 exe 的损坏安装残留。
    """
    d = Path(exe).parent
    try:
        return any(p.is_dir() and p.name[:1].isdigit() for p in d.iterdir())
    except OSError:
        return False


def find_chrome(explicit: str = "") -> str:
    """定位浏览器可执行文件：优先配置项，其次常见安装路径。"""
    if explicit and Path(explicit).is_file() and _valid_install(explicit):
        return explicit
    for p in _COMMON_CHROME_PATHS:
        if Path(p).is_file() and _valid_install(p):
            return p
    raise FileNotFoundError(
        "找不到可用的 Chrome/Edge，请在 config.yaml 的 browser.chrome_path 里手动指定浏览器路径"
    )


def is_port_open(port: int, timeout: float = 1.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch(chrome_path: str, port: int, headless: bool = False, start_url: str = "about:blank") -> subprocess.Popen:
    """拉起专用浏览器实例（独立配置目录 + 调试端口）。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        start_url,
    ]
    if headless:
        args.insert(1, "--headless=new")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_debug_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def ensure_browser(cfg: dict, headless_fallback: bool = True, start_url: str = "about:blank"):
    """确保有一个可连接的调试实例，返回 playwright 的 Browser 连接。

    优先复用已运行的实例（比如 login.py 打开的窗口）；否则自动拉起。
    自动拉起时若配置了 headless_fallback 则无头运行，不弹窗口。
    是否由本函数拉起的进程记录在 browser._launched_proc，供调用方决定是否回收。
    """
    from playwright.sync_api import sync_playwright

    port = cfg["browser"]["debug_port"]
    chrome_path = find_chrome(cfg["browser"].get("chrome_path", ""))

    launched_proc = None
    if not is_port_open(port):
        launched_proc = launch(chrome_path, port, headless=headless_fallback, start_url=start_url)
        if not wait_debug_port(port):
            raise RuntimeError("专用浏览器启动失败（调试端口未就绪）")

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    browser._pw = pw  # noqa: SLF001
    browser._launched_proc = launched_proc  # noqa: SLF001
    return browser


def cdp_alive(port: int) -> bool:
    try:
        requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except requests.RequestException:
        return False

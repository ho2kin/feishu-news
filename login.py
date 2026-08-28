# -*- coding: utf-8 -*-
"""一次性手动登录：打开专用浏览器窗口，由你人工完成登录（含验证码）。

登录成功后登录态会持久保存在 state/chrome-profile 里，monitor.py 直接复用，
全程不会自动执行任何登录操作。验证码过期后重跑本脚本重新登录即可。

用法：
    python login.py
"""
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser import PROJECT_DIR, ensure_browser  # noqa: E402

CONFIG_PATH = PROJECT_DIR / "config.yaml"


def load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    cfg = load_cfg()
    monitor_cfg = cfg["monitor"]
    target_url = monitor_cfg["url"]
    login_kw = monitor_cfg.get("login_url_keyword", "/login")

    print("=" * 60)
    print("正在打开专用浏览器窗口（独立配置目录，不影响日常浏览器）...")
    browser = ensure_browser(cfg, headless_fallback=False, start_url=target_url)

    # 复用第一个上下文，在其中开新标签页
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    page.goto(target_url, wait_until="domcontentloaded")

    if login_kw not in page.url:
        print("当前已是登录状态，无需重新登录。")
        page.close()
        return

    print()
    print("=" * 60)
    print("请在弹出的浏览器窗口里手动完成登录（输入验证码）。")
    print("登录成功后会自动检测并保存登录态，此窗口可保持打开也可关闭。")
    print("=" * 60)

    deadline = time.time() + 600  # 最多等 10 分钟
    while time.time() < deadline:
        time.sleep(2)
        try:
            pages = ctx.pages
            urls = [p.url for p in pages]
        except Exception:
            continue
        # 登录成功的判定：任一标签页离开了登录页且进入了目标系统
        if urls and any(login_kw not in u and "chintanneng.com" in u for u in urls):
            ok_page = next(p for p in pages if login_kw not in p.url and "chintanneng.com" in p.url)
            print(f"检测到登录成功: {ok_page.url}")
            try:
                ok_page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                ok_page.wait_for_timeout(5000)
                print(f"目标页面可访问: {ok_page.url}")
            except Exception as exc:  # noqa: BLE001
                print(f"警告：目标页面打开异常（登录态本身已保存）: {exc}")
            print("登录态已保存在 state/chrome-profile，之后 monitor.py 会自动复用。")
            return
    print("等待登录超时（10分钟）。请重新运行 python login.py 再试。")


if __name__ == "__main__":
    main()

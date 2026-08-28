# -*- coding: utf-8 -*-
"""派单中心新工单监控（由 Windows 计划任务每 15 分钟调用）。

流程：连接专用浏览器（复用已登录会话）-> 打开派单中心校验登录态 ->
在页面上下文里直连工单分页接口拉取河南省待派单工单 -> 本地过滤已审次数=0、
按自审通过时间倒序 -> 按关键字段指纹对比 -> 有新工单推飞书 -> 更新本地状态。

对网站严格只读：只打开页面与查询接口，不提交任何表单、不触发任何写操作。

用法：
    python monitor.py             # 正常检查
    python monitor.py --analyze   # 分析模式：转储页面加载的所有接口响应
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feishu  # noqa: E402
from browser import PROJECT_DIR, ensure_browser  # noqa: E402

CONFIG_PATH = PROJECT_DIR / "config.yaml"
STATE_PATH = PROJECT_DIR / "state" / "seen_items.json"
EXPIRED_TS_PATH = PROJECT_DIR / "state" / "last_expired_alert.txt"
LOCK_PATH = PROJECT_DIR / "state" / "monitor.lock"
LOG_DIR = PROJECT_DIR / "logs"


def acquire_lock() -> bool:
    """运行锁：防止手动运行与计划任务并发执行导致同一工单重复推送。"""
    try:
        if LOCK_PATH.exists():
            # 残留超过 10 分钟的锁视为上次异常退出留下的，直接接管
            if time.time() - LOCK_PATH.stat().st_mtime < 600:
                return False
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass

_log_handlers = [logging.FileHandler(LOG_DIR / "monitor.log", encoding="utf-8")]
if sys.stdout is not None:  # 计划任务用 pythonw 运行时没有控制台
    _log_handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("monitor")

# 在页面上下文里调用工单分页接口：token 取自 cookie GuShen_Token（与前端 axios 行为一致）
JS_QUERY = """
async (req) => {
  const m = document.cookie.match(/(?:^|;\\s*)GuShen_Token=([^;]+)/);
  const token = m ? decodeURIComponent(m[1]) : "";
  const resp = await fetch(req.url, {
    method: "POST",
    headers: {"Content-Type": "application/json", "authorization": token},
    credentials: "include",
    body: JSON.stringify(req.body),
  });
  return await resp.json();
}
"""


def load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_work_orders(page, m: dict) -> list:
    """分页拉取工单列表（严格只读查询）。"""
    url = m["api_url"]
    q = dict(m["query_params"])
    page_rows = int(m.get("page_rows", 100))
    max_pages = int(m.get("max_pages", 5))
    all_items: list = []
    total = None
    for idx in range(1, max_pages + 1):
        body = {"pageIndex": idx, "pageRows": page_rows, "params": q}
        j = page.evaluate(JS_QUERY, {"url": url, "body": body})
        data = (j or {}).get("data") or {}
        recs = data.get("data") or []
        total = data.get("count", total)
        all_items.extend(recs)
        log.info("接口查询第%d页: 返回%d条, 总数%s", idx, len(recs), total)
        if not recs or len(all_items) >= (total or 0):
            break
    return all_items


def apply_filter_sort(items: list, m: dict) -> list:
    fc = m.get("filter_audit_count", None)
    if fc is not None:
        items = [r for r in items if r.get("auditCount") == fc]
    field = m.get("sort_field") or ""
    reverse = (m.get("sort_order") or "desc").lower() == "desc"
    if field:
        items = sorted(items, key=lambda r: str(r.get(field) or ""), reverse=reverse)
    return items


# ---------------------------------------------------------------- 指纹与状态
def fingerprint(item: dict, key_fields: list) -> str:
    if key_fields:
        base = json.dumps({k: item.get(k, "") for k in key_fields}, ensure_ascii=False, sort_keys=True)
    else:
        base = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("状态文件损坏，视为首次运行")
    return {}


def save_state(seen: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 飞书
def notify(cfg: dict, text: str) -> bool:
    webhook = cfg["feishu"].get("webhook", "")
    secret = cfg["feishu"].get("secret", "")
    if not webhook:
        log.warning("feishu.webhook 未配置，跳过推送。消息内容:\n%s", text)
        return False
    try:
        feishu.send_text(webhook, text, secret)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("飞书推送失败: %s", exc)
        return False


def expired_alert(cfg: dict) -> None:
    """登录过期提醒（带冷却，避免每 15 分钟轰炸）。"""
    cooldown_h = float(cfg["feishu"].get("expired_alert_cooldown_hours", 12))
    now = time.time()
    if EXPIRED_TS_PATH.is_file():
        try:
            last = float(EXPIRED_TS_PATH.read_text().strip())
            if now - last < cooldown_h * 3600:
                log.info("登录已过期，提醒处于冷却期，本轮跳过推送")
                return
        except Exception:  # noqa: BLE001
            pass
    sent = notify(cfg, "【派单监控】网站登录态已过期，请运行 python login.py 重新登录一次。")
    if sent:
        EXPIRED_TS_PATH.write_text(str(now), encoding="utf-8")


# ---------------------------------------------------------------- 摘要
def _clean(v) -> str:
    s = str(v if v is not None else "").strip()
    if s.startswith("$$") and "$$" in s[2:]:
        s = s.split("$$", 2)[2]
    return s


def fmt_item(item: dict, fields: list, node_labels: dict) -> str:
    parts = []
    for f in fields:
        v = _clean(item.get(f))
        if f == "auditNode" and str(item.get(f)) in node_labels:
            v = node_labels[str(item.get(f))]
        parts.append(f"{v}" if f != "agentName" else f"{v}")
    return " | ".join(parts)


# ---------------------------------------------------------------- 主流程
def run(analyze: bool = False) -> int:
    if not acquire_lock():
        log.info("上一轮检查仍在运行，本轮跳过（避免重复推送）")
        return 0
    try:
        return _run_inner(analyze)
    finally:
        release_lock()


def _run_inner(analyze: bool = False) -> int:
    cfg = load_cfg()
    m = cfg["monitor"]
    seen = load_state()
    first_run = not seen

    browser = ensure_browser(cfg, headless_fallback=True)
    launched_proc = getattr(browser, "_launched_proc", None)
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        if analyze:
            collected = []
            page.on(
                "response",
                lambda r: collected.append((r.url, r.status))
                if "chintanneng" in r.url and "assets" not in r.url else None,
            )

        page.goto(m["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(m.get("wait_seconds", 8)) * 1000)

        # 登录过期检测
        if m.get("login_url_keyword", "/login") in page.url:
            log.warning("检测到登录过期（被重定向到 %s）", page.url)
            page.close()
            expired_alert(cfg)
            return 0

        if analyze:
            out = LOG_DIR / f"analyze-{datetime.now():%Y%m%d-%H%M%S}.log"
            with open(out, "w", encoding="utf-8") as f:
                for u, s in collected:
                    f.write(f"[{s}] {u}\n")
            log.info("分析模式：共 %d 个接口响应，已写入 %s", len(collected), out)
            page.close()
            return 0

        items = fetch_work_orders(page, m)
        page.close()

        items = apply_filter_sort(items, m)
        if not items:
            log.warning("过滤后无数据（河南·待派单·已审0次），口径或接口可能变化")
            return 1

        key_fields = m.get("item_key_fields") or []
        fps = [fingerprint(it, key_fields) for it in items]
        new_idx = [i for i, fp in enumerate(fps) if fp not in seen]
        log.info("口径内工单 %d 条，新数据 %d 条", len(items), len(new_idx))

        if first_run:
            now_iso = datetime.now().isoformat()
            for fp in fps:
                seen[fp] = now_iso
            save_state(seen)
            notify(cfg, f"【派单监控】监控已启动，当前口径内共 {len(items)} 条工单已记录为基线，之后有新增会通知你。")
            log.info("首次运行：基线 %d 条已记录，不推送明细", len(items))
            return 0

        if new_idx:
            fields = m.get("summary_fields") or ["agentName", "stationNo", "auditNode"]
            node_labels = m.get("audit_node_labels") or {}
            max_lines = int(m.get("notify_max_lines", 15))
            lines = [fmt_item(items[i], fields, node_labels) for i in new_idx[:max_lines]]
            more = f"\n...等共 {len(new_idx)} 条" if len(new_idx) > max_lines else ""
            text = (
                f"🔔 派单中心有 {len(new_idx)} 条新工单（河南省·待派单）\n"
                f"所属代理商 | 电站编号 | 审核节点\n"
                + "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))
                + more
            )
            if notify(cfg, text):
                log.info("已推送 %d 条新工单到飞书", len(new_idx))
        for fp in fps:
            seen.setdefault(fp, datetime.now().isoformat())
        save_state(seen)
        return 0
    finally:
        if launched_proc is not None:
            try:
                launched_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        browser._pw.stop()  # noqa: SLF001


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="派单中心新工单监控")
    parser.add_argument("--analyze", action="store_true", help="转储页面接口响应用于分析")
    args = parser.parse_args()
    sys.exit(run(analyze=args.analyze))

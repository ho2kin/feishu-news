# -*- coding: utf-8 -*-
"""派单中心新工单监控（纯接口模式，由 Windows 计划任务每 5 分钟调用）。

流程：读取本地 token（state/auto_login_token.json）-> 直连工单分页接口 ->
token 失效（PERMISSION_NOT_PASS）时自动 OCR 登录换新 token 重试一次 ->
本地过滤已审次数=0、按创建时间从旧到新 -> 按关键字段指纹对比 ->
有新工单推飞书 -> 更新本地状态。

对网站严格只读：只调用查询接口与登录接口，不提交任何业务表单。
全程不启动浏览器。

用法：
    python monitor.py
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

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_login  # noqa: E402
import feishu  # noqa: E402

STATE_PATH = auto_login.PROJECT_DIR / "state" / "seen_items.json"
EXPIRED_TS_PATH = auto_login.PROJECT_DIR / "state" / "last_expired_alert.txt"
LOCK_PATH = auto_login.PROJECT_DIR / "state" / "monitor.lock"
LOG_DIR = auto_login.PROJECT_DIR / "logs"


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


def fetch_work_orders(session: requests.Session, m: dict, token: str):
    """分页拉取工单列表（严格只读查询）。返回 (items, auth_failed)。

    token 失效时该接口仍返回 HTTP 200，业务体为 code=-1 / PERMISSION_NOT_PASS，
    以此判定需要重新登录。
    """
    url = m["api_url"]
    q = dict(m.get("query_params") or {})
    page_rows = int(m.get("page_rows", 100))
    max_pages = int(m.get("max_pages", 5))
    all_items: list = []
    total = None
    for idx in range(1, max_pages + 1):
        try:
            r = session.post(
                url, json={"pageIndex": idx, "pageRows": page_rows, "params": q},
                timeout=30,
                headers={"Content-Type": "application/json", "authorization": token,
                         "User-Agent": auto_login.UA, "Referer": m["url"]},
            )
        except requests.RequestException as exc:
            log.warning("接口请求异常（第%d页）: %s", idx, exc)
            return all_items, True
        if r.status_code != 200:
            log.warning("接口 HTTP %s（第%d页）", r.status_code, idx)
            return all_items, True
        try:
            j = r.json()
        except ValueError:
            log.warning("接口返回非 JSON（第%d页）: %s", idx, r.text[:80])
            return all_items, True
        if str(j.get("code")) == "800":
            data = j.get("data") or {}
            recs = data.get("data") or []
            total = data.get("count", total)
            all_items.extend(recs)
            log.info("接口查询第%d页: 返回%d条, 总数%s", idx, len(recs), total)
            if not recs or len(all_items) >= (total or 0):
                return all_items, False
        else:
            log.warning("接口业务错误 code=%s status=%s msg=%s（第%d页）",
                        j.get("code"), j.get("status"), j.get("msg"), idx)
            return all_items, True
    return all_items, False


def fetch_with_auto_login(cfg: dict, session: requests.Session):
    """拉取工单；token 失效时自动重登一次再试。失败返回 None（已发飞书提醒）。"""
    m = cfg["monitor"]
    token = auto_login.load_token()
    items, auth_failed = fetch_work_orders(session, m, token)
    if not auth_failed:
        return items

    log.info("本地 token 无效，尝试自动重新登录...")
    try:
        info = auto_login.do_login(cfg, log_fn=log.info)
    except Exception as exc:  # noqa: BLE001
        log.error("自动登录失败: %s", exc)
        expired_alert(cfg, str(exc))
        return None
    auto_login.save_token(info)
    items, auth_failed = fetch_work_orders(session, m, info["token"])
    if auth_failed:
        log.error("重新登录后接口仍不可用")
        expired_alert(cfg, "重新登录后接口仍返回无权限")
        return None
    return items


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


def expired_alert(cfg: dict, reason: str = "") -> None:
    """自动登录失败提醒（带冷却，避免每 5 分钟轰炸）。"""
    cooldown_h = float(cfg["feishu"].get("expired_alert_cooldown_hours", 12))
    now = time.time()
    if EXPIRED_TS_PATH.is_file():
        try:
            last = float(EXPIRED_TS_PATH.read_text().strip())
            if now - last < cooldown_h * 3600:
                log.info("自动登录失败提醒处于冷却期，本轮跳过推送")
                return
        except Exception:  # noqa: BLE001
            pass
    suffix = f"原因：{reason}" if reason else ""
    sent = notify(
        cfg,
        "【派单监控】自动登录失败，监控已暂停。"
        "请检查 config.yaml 里 account 的账号密码是否正确、账号是否被锁定或需要人工验证。"
        f"{suffix}",
    )
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
        parts.append(f"{v}")
    return " | ".join(parts)


# ---------------------------------------------------------------- 主流程
def run() -> int:
    if not acquire_lock():
        log.info("上一轮检查仍在运行，本轮跳过（避免重复推送）")
        return 0
    try:
        return _run_inner()
    finally:
        release_lock()


def _run_inner() -> int:
    cfg = auto_login.load_cfg()
    m = cfg["monitor"]
    seen = load_state()
    first_run = not seen

    session = requests.Session()
    items = fetch_with_auto_login(cfg, session)
    if items is None:
        return 1

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="派单中心新工单监控（纯接口模式）")
    args = parser.parse_args()
    sys.exit(run())

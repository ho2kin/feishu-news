# -*- coding: utf-8 -*-
"""纯代码自动登录（不用浏览器）：自动填账号密码 + OCR 识别图形验证码。

原理（已实测打通）：
  1. 验证码接口: GET {API}/api/base/base-pa/imageCaptcha?phone={账号}&{随机数}，直接返回 JPEG；
  2. 登录接口:   POST {API}/api/base/base-user/login，密码只做一层标准 Base64（无加密）；
     手机号走"手机登录" loginType=1，账号名走"账号登录" loginType=3，systemCode=anneng-ltc；
  3. 注意：验证码错误时服务端不单独提示，统一报"账号或密码错误"（1021_user_028），
     因此该报错按"验证码可能识别错了"处理，换一张验证码重试；
  4. 登录成功后 data.authorization 即业务接口鉴权 JWT（有效期 tokenInvalid 分钟，实测
     1440 分钟=24 小时），以 authorization 头调用业务接口。

用法：
    python auto_login.py   # 登录一次并验证 token，保存到 state/auto_login_token.json
    monitor.py 在 token 失效时会自动调用本模块的 do_login 完成重登。
"""
import base64
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.yaml"
TOKEN_PATH = PROJECT_DIR / "state" / "auto_login_token.json"
LOG_PATH = PROJECT_DIR / "logs" / "auto_login.log"

LOGIN_PAGE = "https://ltc.chintanneng.com/login"
API_BASE = "https://api-gushen.chintanneng.com/"
CAPTCHA_URL = API_BASE + "api/base/base-pa/imageCaptcha?phone={phone}&{rand}"
LOGIN_URL = API_BASE + "api/base/base-user/login"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if sys.stdout is not None:  # 计划任务用 pythonw 运行时没有控制台
        print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": LOGIN_PAGE,
        "Origin": "https://ltc.chintanneng.com",
    })
    return s


_ocr = None


def get_ocr():
    """惰性加载 ddddocr（模型加载约 1~2 秒，只在需要重登时才发生）。"""
    global _ocr
    if _ocr is None:
        try:
            from ddddocr import DdddOcr
        except ImportError:
            raise RuntimeError("缺少 OCR 库，请先执行: pip install ddddocr")
        _ocr = DdddOcr(show_ad=False)
    return _ocr


def login_once(s: requests.Session, username: str, password: str, ocr) -> dict:
    """拉一张验证码并尝试登录一次，返回登录接口的 JSON 响应。"""
    r = s.get(CAPTCHA_URL.format(phone=username, rand=random.random()), timeout=20)
    r.raise_for_status()
    captcha = ocr.classification(r.content)
    log(f"验证码识别结果: {captcha}")

    body = {"params": {"requestData": {
        "username": username,
        "password": base64.b64encode(password.encode("utf-8")).decode("ascii"),
        "captcha": captcha,
        "tfaType": "",
        "validatorCode": "",
        # 手机号走"手机登录"(1)，其他视为"账号登录"(3)，与前端模式表一致
        "loginType": 1 if re.fullmatch(r"1[3-9]\d{9}", username) else 3,
        "systemCode": "anneng-ltc",
        "loginFrom": "PC",
    }}}
    r = s.post(LOGIN_URL, json=body, timeout=20)
    r.raise_for_status()
    return r.json() or {}


def extract_token(resp: dict, session: requests.Session) -> dict:
    """从登录响应里取鉴权信息（实测发放方式：data.authorization 的 JWT）。"""
    data = resp.get("data") or {}
    token = str(data.get("authorization") or "")
    if not token:  # 兜底：部分部署把 token 种在 GuShen_Token cookie 里
        for c in session.cookies:
            if c.name == "GuShen_Token" and c.value:
                token = c.value
                break
    return {
        "token": token,
        "refresh_token": str(data.get("refresh-authorization") or ""),
        "valid_minutes": data.get("tokenInvalid"),
    }


def do_login(cfg: dict, log_fn=log) -> dict:
    """完整登录流程（含验证码重试）。成功返回含 token 的 dict，失败抛 RuntimeError。"""
    account = cfg.get("account") or {}
    username = str(account.get("username") or "").strip()
    password = str(account.get("password") or "").strip()
    if not username or not password:
        raise RuntimeError("config.yaml 的 account 段未填写 username / password")

    retries = int(account.get("captcha_retry", 5))
    ocr = get_ocr()
    s = make_session()
    try:
        s.get(LOGIN_PAGE, timeout=20)  # 预取基础 cookie
    except Exception as exc:  # noqa: BLE001
        log_fn(f"访问登录页失败: {exc}")

    last_msg = ""
    for attempt in range(1, retries + 1):
        log_fn(f"自动登录第 {attempt}/{retries} 次尝试...")
        try:
            resp = login_once(s, username, password, ocr)
        except Exception as exc:  # noqa: BLE001
            log_fn(f"网络异常: {exc}")
            time.sleep(3)
            continue
        if resp.get("code") == 800:
            log_fn("自动登录成功 ✓")
            info = extract_token(resp, s)
            if not info["token"]:
                raise RuntimeError("登录成功但响应中未取到鉴权 token")
            return info
        last_msg = f"code={resp.get('code')} msg={resp.get('msg')}"
        log_fn(f"登录失败: {last_msg}")
        if resp.get("msgCode") == "1021_user_028":
            # 验证码识别错误与密码错误都报这个码：先按验证码错了换图重试
            log_fn("可能是验证码识别错误，换一张重试...")
            time.sleep(1)
            continue
        raise RuntimeError(f"登录被拒绝（{last_msg}），请核对账号密码与账号状态")
    raise RuntimeError(f"多次尝试仍未成功（{last_msg}）")


def load_token() -> str:
    """读取本地已保存的 token；没有或文件损坏则返回空串。"""
    try:
        info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        return str(info.get("token") or "")
    except Exception:  # noqa: BLE001
        return ""


def save_token(info: dict, verified=None) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {**info, "time": datetime.now().isoformat()}
    if verified is not None:
        payload["verified"] = verified
    TOKEN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def verify_token(token: str, m: dict) -> bool:
    """用 token 实测调用工单分页接口（与 monitor.py 的调用方式一致）。"""
    body = {"pageIndex": 1, "pageRows": 10, "params": dict(m.get("query_params") or {})}
    r = requests.post(
        m["api_url"], json=body, timeout=30,
        headers={"Content-Type": "application/json", "authorization": token,
                 "User-Agent": UA, "Referer": m["url"]},
    )
    log(f"接口验证 HTTP {r.status_code}: {r.text[:120]}")
    return r.ok and str((r.json() or {}).get("code")) == "800"


def main() -> int:
    cfg = load_cfg()
    try:
        info = do_login(cfg)
    except Exception as exc:  # noqa: BLE001
        log(f"自动登录失败: {exc}")
        return 1
    save_token(info)
    ok = verify_token(info["token"], cfg["monitor"])
    save_token(info, verified=ok)
    log(f"token 已保存到 {TOKEN_PATH.name}（接口验证{'通过' if ok else '未通过'}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

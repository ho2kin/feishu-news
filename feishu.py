# -*- coding: utf-8 -*-
"""飞书群自定义机器人推送（webhook + 可选签名校验）"""
import base64
import hashlib
import hmac
import time

import requests


def _make_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_text(webhook: str, text: str, secret: str = "", timeout: int = 15) -> bool:
    """发送纯文本消息。secret 为空表示机器人未开启签名校验。"""
    if not webhook:
        raise ValueError("飞书 webhook 未配置，请在 config.yaml 的 feishu.webhook 里填写")
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _make_sign(secret, ts)
    resp = requests.post(webhook, json=payload, timeout=timeout)
    data = resp.json()
    ok = data.get("code", 0) == 0 or data.get("StatusCode") == 0
    if not ok:
        raise RuntimeError(f"飞书推送失败: {data}")
    return True

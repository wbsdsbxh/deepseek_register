#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 纯发包注册客户端（无浏览器）。

对应 chat.deepseek.com 前端 main.js 的真实调用链：

1. GET  /sign_up                                   拿 ds_session_id cookie + meta region
2. POST /api/v0/users/create_guest_challenge
        body {"target_path": "/api/v0/users/create_email_verification_code"}
        → biz_data.guest_challenge {algorithm, challenge, salt, signature, difficulty, expire_at, target_path}
3. 本地求解 PoW → 请求头 X-DS-Guest-PoW-Response = base64({"salt","answer"})
4. POST /api/v0/users/create_email_verification_code
        body {email, locale, device_id, turnstile_token, shumei_verification, hcaptcha_token, scenario}
5. 人工填验证码 → POST /api/v0/users/register
        body {locale, region, payload:{email, email_verification_code, password}, device_id, os:"web"}
"""

import json
import random
import string
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

import requests

from config import config
from pow_solver import PowSolver, get_solver

LogFn = Callable[[str], None]

CREATE_EMAIL_CODE_PATH = "/users/create_email_verification_code"
REGISTER_PATH = "/users/register"
# target_path 必须是带 /api/v0 前缀的完整路径，否则 create_guest_challenge 返回 INVALID_TARGET_PATH
CREATE_EMAIL_CODE_TARGET = "/api/v0/users/create_email_verification_code"
REGISTER_TARGET = "/api/v0/users/register"

# 注册接口 biz_code 语义（前端枚举 + 实测）
BIZ_MSG = {
    0: "成功",
    2: "验证码错误或已过期",
    3: "邮箱已被注册",
    6: "REGISTER_FROM_MAINLAND：当前出口 IP 属中国大陆，注册被地区限制拒绝",
    7: "操作过于频繁",
}


def _noop(_msg: str) -> None:
    return None


# --------------------------------------------------------------------------- #
# hCaptcha（yescaptcha）
# --------------------------------------------------------------------------- #
class YesCaptcha:
    def __init__(
        self,
        api: Optional[str] = None,
        client_key: Optional[str] = None,
        sitekey: Optional[str] = None,
        pageurl: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.api = (api or config.YESCAPTCHA_API).rstrip("/")
        self.client_key = client_key or config.YESCAPTCHA_CLIENT_KEY
        self.sitekey = sitekey or config.HCAPTCHA_SITEKEY
        self.pageurl = pageurl or config.HCAPTCHA_PAGEURL
        self.timeout = timeout or config.HCAPTCHA_TIMEOUT

    def _post(self, method: str, payload: Dict[str, Any], retries: int = 4) -> Dict[str, Any]:
        """yescaptcha 偶发 SSL EOF / 连接重置，做有限次重试。"""
        body = {"clientKey": self.client_key, **payload}
        url = f"{self.api}/{method}"
        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(url, json=body, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise
        raise last_err if last_err else RuntimeError(f"{method} 请求失败")

    def balance(self) -> Any:
        data = self._post("getBalance", {})
        return data.get("balance")

    def solve(self, log: LogFn = _noop) -> Dict[str, str]:
        created = self._post(
            "createTask",
            {
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": self.pageurl,
                    "websiteKey": self.sitekey,
                }
            },
        )
        if created.get("errorId"):
            raise RuntimeError(
                f"yescaptcha createTask 失败: {created.get('errorCode')} / {created.get('errorDescription')}"
            )
        task_id = created.get("taskId")
        if not task_id:
            raise RuntimeError(f"yescaptcha 未返回 taskId: {created}")
        log(f"[hcaptcha] 任务已创建 taskId={task_id}")

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(3)
            res = self._post("getTaskResult", {"taskId": task_id})
            if res.get("errorId"):
                raise RuntimeError(
                    f"yescaptcha getTaskResult 失败: {res.get('errorCode')} / {res.get('errorDescription')}"
                )
            if res.get("status") == "ready":
                sol = res.get("solution") or {}
                token = sol.get("gRecaptchaResponse") or sol.get("token")
                if not token:
                    raise RuntimeError(f"yescaptcha 结果里没有 token: {sol}")
                log("[hcaptcha] 已拿到 token（约 120 秒内有效）")
                return {
                    "token": token,
                    "userAgent": sol.get("userAgent") or config.USER_AGENT,
                    "respKey": str(sol.get("respKey") or ""),
                }
        raise TimeoutError(f"hcaptcha 解析超时（{self.timeout}s，taskId={task_id}）")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def random_local_part(length: Optional[int] = None) -> str:
    length = length or config.EMAIL_LOCAL_LENGTH
    alphabet = string.ascii_lowercase + string.digits
    return random.choice(string.ascii_lowercase) + "".join(
        random.choice(alphabet) for _ in range(max(0, length - 1))
    )


def generate_email(domain: Optional[str] = None) -> str:
    return f"{random_local_part()}@{(domain or config.EMAIL_DOMAIN).lstrip('@')}"


def random_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits
    seed = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
    ]
    seed += [random.choice(chars) for _ in range(max(0, length - 3))]
    random.shuffle(seed)
    return "".join(seed)


def new_device_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# 主客户端
# --------------------------------------------------------------------------- #
class DeepSeekClient:
    def __init__(
        self,
        log: LogFn = _noop,
        proxy: Optional[str] = None,
        device_id: Optional[str] = None,
        locale: str = "zh_CN",
        region: str = "CN",
        solver: Optional[PowSolver] = None,
    ):
        self.log = log
        self.device_id = device_id or new_device_id()
        self.locale = locale
        self.region = region
        self.solver = solver or get_solver()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://chat.deepseek.com",
                "Referer": config.SIGN_UP_PAGE,
                "Content-Type": "application/json",
            }
        )
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self._bootstrap_done = False
        self._lock = threading.Lock()

    # ---------------- 底层 ----------------
    def bootstrap(self) -> Dict[str, Any]:
        if self._bootstrap_done:
            return {"region": self.region}
        resp = self.session.get(
            config.SIGN_UP_PAGE,
            timeout=config.REQUEST_TIMEOUT,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        resp.raise_for_status()
        region = self.region
        marker = 'name="region" content="'
        if marker in resp.text:
            try:
                region = resp.text.split(marker, 1)[1].split('"', 1)[0] or region
            except IndexError:
                pass
        self.region = region
        self._bootstrap_done = True
        self.log(f"[http] 会话已建立 region={region} cookies={list(self.session.cookies.keys())}")
        return {"region": region}

    def _post(
        self,
        path: str,
        payload: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = f"{config.BASE_URL}{path}"
        resp = self.session.post(
            url,
            json=payload,
            headers=dict(extra_headers or {}),
            timeout=config.REQUEST_TIMEOUT,
        )
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"POST {path} 非 JSON 响应（HTTP {resp.status_code}）: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise RuntimeError(
                f"POST {path} HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)[:400]}"
            )
        return data

    # ---------------- PoW ----------------
    def build_pow_header(self, target_path: str) -> Dict[str, str]:
        data = self._post("/users/create_guest_challenge", {"target_path": target_path})
        biz = (data.get("data") or {}).get("biz_data") or {}
        challenge = biz.get("guest_challenge")
        if not challenge:
            raise RuntimeError(f"未返回 guest_challenge: {json.dumps(data, ensure_ascii=False)[:400]}")
        if not challenge.get("target_path"):
            challenge["target_path"] = target_path

        self.log(
            f"[pow] challenge difficulty={challenge.get('difficulty')} "
            f"algorithm={challenge.get('algorithm')} salt={challenge.get('salt')}"
        )
        started = time.time()
        solution = self.solver.solve(challenge)
        self.log(f"[pow] 求解完成 answer={solution['answer']} 用时 {time.time() - started:.2f}s")

        return {
            "X-DS-Guest-PoW-Response": PowSolver.guest_header_value(solution),
        }

    # ---------------- 验证码 ----------------
    def send_email_code(
        self,
        email: str,
        hcaptcha_token: Optional[str] = None,
        scenario: str = "register",
    ) -> Dict[str, Any]:
        self.bootstrap()
        # scenario 是必填项，注册场景固定 "register"（缺失会返回 HTTP 422 body.scenario）
        payload = {
            "email": email,
            "locale": self.locale,
            "device_id": self.device_id,
            "turnstile_token": None,
            "shumei_verification": None,
            "hcaptcha_token": hcaptcha_token,
            "scenario": scenario,
        }

        headers = self.build_pow_header(CREATE_EMAIL_CODE_TARGET)
        data = self._post(CREATE_EMAIL_CODE_PATH, payload, headers)
        biz = data.get("data") or {}
        biz_data = biz.get("biz_data") or {}
        self.log(
            f"[email] create_email_verification_code biz_code={biz.get('biz_code')} "
            f"msg={biz.get('biz_msg')!r} send_window={biz_data.get('send_window_secs')}"
        )
        return {
            "biz_code": biz.get("biz_code"),
            "biz_msg": biz.get("biz_msg"),
            "send_window_secs": biz_data.get("send_window_secs"),
            "raw": data,
        }

    def check_email_code(self, email: str, code: str) -> Dict[str, Any]:
        data = self._post(
            "/users/check_email_code",
            {"email": email, "email_verification_code": code},
        )
        biz = data.get("data") or {}
        return {"biz_code": biz.get("biz_code"), "biz_msg": biz.get("biz_msg"), "raw": data}

    # ---------------- 注册 ----------------
    def register(self, email: str, code: str, password: str) -> Dict[str, Any]:
        payload = {
            "locale": self.locale,
            "region": self.region,
            "payload": {
                "email": email,
                "email_verification_code": code,
                "password": password,
            },
            "device_id": self.device_id,
            "os": "web",
        }
        # /users/register 同样要 PoW，缺头会返回 code=40300 "Missing Header"
        headers = self.build_pow_header(REGISTER_TARGET)
        data = self._post(REGISTER_PATH, payload, headers)
        # 顶层 code 是协议层状态，0 才代表请求被受理
        top_code = data.get("code")
        biz = data.get("data") or {}
        if biz is None or not isinstance(biz, dict):
            return {
                "biz_code": top_code,
                "biz_msg": data.get("msg"),
                "user": None,
                "raw": data,
            }
        biz_data = biz.get("biz_data") or {}
        return {
            "biz_code": biz.get("biz_code"),
            "biz_msg": biz.get("biz_msg"),
            "user": biz_data.get("user"),
            "raw": data,
        }

    # ---------------- 登录校验 ----------------
    def login(self, email: str, password: str) -> Dict[str, Any]:
        payload = {
            "email": email,
            "password": password,
            "device_id": self.device_id,
            "os": "web",
        }
        try:
            data = self._post("/users/login", payload)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        biz = data.get("data") or {}
        return {
            "ok": biz.get("biz_code") == 0,
            "biz_code": biz.get("biz_code"),
            "biz_msg": biz.get("biz_msg"),
            "raw": data,
        }

    # ---------------- 组合动作 ----------------
    def send_code_with_captcha(self, email: str, captcha: Optional[YesCaptcha] = None) -> Dict[str, Any]:
        """解 hCaptcha + 算 PoW + 发验证码，全自动。"""
        solver = captcha or YesCaptcha()
        self.bootstrap()
        self.log(f"[流程] 开始解析 hCaptcha: {email}")
        solved = solver.solve(self.log)
        if solved.get("userAgent"):
            self.session.headers["User-Agent"] = solved["userAgent"]
        sent = self.send_email_code(email, hcaptcha_token=solved["token"])
        return {"email": email, "sent": sent, "device_id": self.device_id}


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    def _print(msg: str) -> None:
        print(msg, flush=True)

    client = DeepSeekClient(log=_print)
    hdr = client.build_pow_header(CREATE_EMAIL_CODE_TARGET)
    print("header:", hdr)
    import base64
    print("decoded:", base64.b64decode(hdr["X-DS-Guest-PoW-Response"]).decode())
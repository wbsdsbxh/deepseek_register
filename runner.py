#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
注册任务调度器（进程内单例）。

两个模式：
1. run_batch()      —— 一键开启：连续自动注册，每条跑到「发出验证码」就挂起等人工填码
2. register_one()   —— 手动指定邮箱密码注册单个账号

停止：stop() 设置停止标志，工作线程在下一次检查点退出。
"""

import threading
import time
import traceback
from collections import deque
from typing import Any, Dict, Optional

import store
from config import config
from deepseek_api import BIZ_MSG, DeepSeekClient, YesCaptcha, generate_email, random_password


class Task:
    """一个等待人工输入验证码的挂起任务。"""

    def __init__(self, account_id: str, email: str, password: str, client: DeepSeekClient):
        self.account_id = account_id
        self.email = email
        self.password = password
        self.client = client
        self.created_at = time.time()
        self.status = "pending_code"  # pending_code / submitting / done / failed


class Runner:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logs: deque = deque(maxlen=500)
        self._tasks: Dict[str, Task] = {}  # account_id -> Task
        self._counter = {"success": 0, "failed": 0, "sent": 0}
        self._started_at: Optional[float] = None
        self._last_error = ""

    # ---------------- 日志 ----------------
    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        with self._lock:
            self._logs.append(line)
        try:
            with open(config.log_path(), "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError:
            pass

    def logs(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            items = list(self._logs)
        return {"total": len(items), "items": items[since:]}

    # ---------------- 状态 ----------------
    def is_running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive() and not self._stop_flag.is_set())

    def status(self) -> Dict[str, Any]:
        with self._lock:
            pending = [
                {
                    "account_id": t.account_id,
                    "email": t.email,
                    "password": t.password,
                    "status": t.status,
                    "waited": int(time.time() - t.created_at),
                }
                for t in self._tasks.values()
                if t.status == "pending_code"
            ]
            counters = dict(self._counter)
        return {
            "running": self.is_running(),
            "started_at": self._started_at,
            "counters": counters,
            "pending": pending,
            "last_error": self._last_error,
            "email_domain": config.EMAIL_DOMAIN,
            "concurrency": config.CONCURRENCY,
            "stats": store.stats(),
        }

    # ---------------- 控制 ----------------
    def start(self, count: int = 0, interval: int = 5) -> Dict[str, Any]:
        if self.is_running():
            return {"ok": False, "error": "已经在运行中"}
        if not config.YESCAPTCHA_CLIENT_KEY:
            return {"ok": False, "error": "未配置 YESCAPTCHA_CLIENT_KEY"}

        self._stop_flag.clear()
        self._started_at = time.time()
        self._last_error = ""
        with self._lock:
            self._counter = {"success": 0, "failed": 0, "sent": 0}
        self._thread = threading.Thread(
            target=self._run_batch,
            args=(count, interval),
            daemon=True,
            name="ds-batch",
        )
        self._thread.start()
        self.log(f"▶ 批量注册已启动 count={'无限' if count <= 0 else count} interval={interval}s")
        return {"ok": True}

    def stop(self) -> Dict[str, Any]:
        if not self._thread or not self._thread.is_alive():
            return {"ok": True, "already": True}
        self._stop_flag.set()
        self.log("⏹ 已发出停止指令，当前这一条收尾后退出")
        return {"ok": True}

    # ---------------- 批量主循环 ----------------
    def _run_batch(self, count: int, interval: int) -> None:
        done = 0
        try:
            while not self._stop_flag.is_set():
                if count > 0 and done >= count:
                    self.log(f"✅ 已完成 {done} 条，退出")
                    break
                done += 1
                self._one_round(done)
                if self._stop_flag.is_set():
                    break
                # 可中断等待
                self._stop_flag.wait(timeout=max(1, interval))
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{exc}"
            self.log(f"💥 批量任务异常: {exc}")
            self.log(traceback.format_exc(limit=5))
        finally:
            self.log("🛑 批量注册已停止")

    def _one_round(self, round_index: int) -> None:
        email = generate_email()
        password = random_password(12)
        if store.email_exists(email):
            self.log(f"⚠ 邮箱重复，跳过: {email}")
            return

        record = store.create_account(email, password, note=f"批量 #{round_index}")
        account_id = record["id"]
        self.log(f"📧 第 {round_index} 条: {email}")

        client = DeepSeekClient(log=self.log)
        try:
            store.update_account(account_id, step="解 hCaptcha")
            self.log("🧩 正在解 hCaptcha...")
            captcha = YesCaptcha()
            solved = captcha.solve(self.log)
            if solved.get("userAgent"):
                client.session.headers["User-Agent"] = solved["userAgent"]

            store.update_account(account_id, step="算 PoW 并发送验证码")
            self.log("🔐 计算 PoW 并请求发送验证码...")
            sent = client.send_email_code(email, hcaptcha_token=solved["token"])

            if sent.get("biz_code") != 0:
                raise RuntimeError(f"发码失败 biz_code={sent.get('biz_code')} msg={sent.get('biz_msg')!r}")

            with self._lock:
                self._counter["sent"] += 1
            store.update_account(
                account_id,
                status="pending_code",
                step="等人工填验证码",
                message="验证码已发送，等待填入",
            )
            task = Task(account_id, email, password, client)
            with self._lock:
                self._tasks[account_id] = task
            self.log(f"⏳ 验证码已发送到 {email}，等待人工填入（间隔 {sent.get('send_window_secs')}s）")

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._counter["failed"] += 1
            store.update_account(account_id, status="failed", step="失败", message=str(exc))
            self._last_error = str(exc)
            self.log(f"❌ 发码失败 {email}: {exc}")

    # ---------------- 手动单个 ----------------
    def register_one(self, email: str, password: str) -> Dict[str, Any]:
        email = (email or "").strip()
        password = (password or "").strip()
        if not email or "@" not in email:
            return {"ok": False, "error": "邮箱格式不对"}
        if len(password) < 6:
            return {"ok": False, "error": "密码至少 6 位"}
        if store.email_exists(email):
            return {"ok": False, "error": "这个邮箱已在列表里"}

        record = store.create_account(email, password, note="手动")
        account_id = record["id"]
        self.log(f"📧 手动注册: {email}")

        client = DeepSeekClient(log=self.log)
        try:
            store.update_account(account_id, step="解 hCaptcha")
            self.log("🧩 正在解 hCaptcha...")
            captcha = YesCaptcha()
            solved = captcha.solve(self.log)
            if solved.get("userAgent"):
                client.session.headers["User-Agent"] = solved["userAgent"]

            store.update_account(account_id, step="算 PoW 并发送验证码")
            self.log("🔐 计算 PoW 并请求发送验证码...")
            sent = client.send_email_code(email, hcaptcha_token=solved["token"])
            if sent.get("biz_code") != 0:
                raise RuntimeError(f"发码失败 biz_code={sent.get('biz_code')} msg={sent.get('biz_msg')!r}")

            with self._lock:
                self._counter["sent"] += 1
                self._tasks[account_id] = Task(account_id, email, password, client)
            store.update_account(
                account_id,
                status="pending_code",
                step="等人工填验证码",
                message="验证码已发送，等待填入",
            )
            self.log(f"⏳ 验证码已发送到 {email}，去邮箱取码")
            return {"ok": True, "account_id": account_id, "email": email}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._counter["failed"] += 1
            store.update_account(account_id, status="failed", step="失败", message=str(exc))
            self._last_error = str(exc)
            self.log(f"❌ 失败 {email}: {exc}")
            return {"ok": False, "error": str(exc), "account_id": account_id}

    # ---------------- 提交验证码 ----------------
    def submit_code(self, account_id: str, code: str) -> Dict[str, Any]:
        code = (code or "").strip()
        if not code:
            return {"ok": False, "error": "验证码是空的"}

        with self._lock:
            task = self._tasks.get(account_id)
        if not task:
            return {"ok": False, "error": "没有这个待验证任务（可能已提交或服务已重启）"}
        if task.status != "pending_code":
            return {"ok": False, "error": f"当前状态 {task.status}，不能再提交"}

        task.status = "submitting"
        store.update_account(account_id, step="提交验证码")
        self.log(f"🔑 提交验证码 {code} → {task.email}（含 PoW，难度会显著高于发码阶段）")

        try:
            result = task.client.register(task.email, code, task.password)
            biz_code = result.get("biz_code")
            if biz_code != 0:
                msg = BIZ_MSG.get(biz_code) or result.get("biz_msg") or f"biz_code={biz_code}"
                # 地区限制 / 邮箱已注册这类不可重试的错误直接判死
                fatal = biz_code in (3, 6)
                store.update_account(
                    account_id,
                    status="failed" if fatal else "pending_code",
                    step="注册被拒",
                    message=msg,
                )
                if fatal:
                    task.status = "failed"
                else:
                    task.status = "pending_code"
                self.log(f"❌ {task.email} 被拒: {msg}")
                return {"ok": False, "error": msg, "biz_code": biz_code}

            user = result.get("user") or {}
            task.status = "done"
            with self._lock:
                self._counter["success"] += 1
            store.update_account(
                account_id,
                status="success",
                step="注册成功",
                message="",
                user_id=user.get("id"),
                user_email=user.get("email"),
            )
            self.log(f"🎉 注册成功: {task.email}")
            return {"ok": True, "email": task.email, "user": user}
        except Exception as exc:  # noqa: BLE001
            task.status = "pending_code"
            self.log(f"❌ 提交验证码出错: {exc}")
            store.update_account(account_id, status="pending_code", step="提交出错", message=str(exc))
            return {"ok": False, "error": str(exc)}

    def resend_code(self, account_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(account_id)
        if not task:
            return {"ok": False, "error": "没有这个任务"}
        try:
            self.log(f"🔁 重新发送验证码 → {task.email}")
            captcha = YesCaptcha()
            solved = captcha.solve(self.log)
            sent = task.client.send_email_code(task.email, hcaptcha_token=solved["token"])
            if sent.get("biz_code") != 0:
                return {"ok": False, "error": f"biz_code={sent.get('biz_code')} {sent.get('biz_msg')}"}
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}


runner = Runner()
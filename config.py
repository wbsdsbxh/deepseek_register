#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置模块：纯环境变量 + 代码默认值，不依赖 dotenv。
所有可调项都能在 Web 控制台里改，改完即时生效。
"""

import os

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Config:
    PROJECT_ROOT = PROJECT_ROOT

    # ---------- Web ----------
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = _env_int("PORT", 8080)

    # ---------- 注册行为 ----------
    CONCURRENCY = _env_int("CONCURRENCY", 1)
    MAX_RETRIES = _env_int("MAX_RETRIES", 3)
    RETRY_DELAY = _env_int("RETRY_DELAY", 3)
    # 一轮注册最多等待人工填验证码的秒数
    CODE_TIMEOUT = _env_int("CODE_TIMEOUT", 600)

    # ---------- 邮箱 ----------
    EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "wbsdsb.lol").strip().lstrip("@")
    EMAIL_LOCAL_LENGTH = _env_int("EMAIL_LOCAL_LENGTH", 10)

    # ---------- yescaptcha ----------
    YESCAPTCHA_API = os.getenv("YESCAPTCHA_API", "https://api.yescaptcha.com").rstrip("/")
    YESCAPTCHA_CLIENT_KEY = os.getenv(
        "YESCAPTCHA_CLIENT_KEY",
        "a464840760e6a51667b0bec43812b4eefa3cf82629102",
    )
    HCAPTCHA_SITEKEY = os.getenv("HCAPTCHA_SITEKEY", "352e5376-f2cc-43fe-a744-e51640449610")
    HCAPTCHA_PAGEURL = os.getenv("HCAPTCHA_PAGEURL", "https://chat.deepseek.com/sign_up")
    HCAPTCHA_TIMEOUT = _env_int("HCAPTCHA_TIMEOUT", 180)

    # ---------- DeepSeek 端点 ----------
    BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://chat.deepseek.com/api/v0").rstrip("/")
    SIGN_UP_PAGE = os.getenv("DEEPSEEK_SIGNUP_PAGE", "https://chat.deepseek.com/sign_up")
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 30)

    # ---------- 文件 ----------
    ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.json")
    LOG_FILE = os.getenv("LOG_FILE", "run.log")

    @classmethod
    def accounts_path(cls) -> str:
        return os.path.join(PROJECT_ROOT, cls.ACCOUNTS_FILE)

    @classmethod
    def log_path(cls) -> str:
        return os.path.join(PROJECT_ROOT, cls.LOG_FILE)


config = Config()
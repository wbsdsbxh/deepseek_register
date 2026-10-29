#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账号存储：accounts.json，线程安全，支持分页查询、导出、状态更新。
账号状态：pending_code（等验证码）/ success / failed
"""

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from config import config

_LOCK = threading.RLock()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load() -> List[Dict[str, Any]]:
    path = config.accounts_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        return []
    return []


def _save(items: List[Dict[str, Any]]) -> None:
    path = config.accounts_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(items, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_accounts(page: int = 1, page_size: int = 8, keyword: str = "") -> Dict[str, Any]:
    """分页返回账号，默认每页 8 条。"""
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    with _LOCK:
        items = _load()

    if keyword:
        kw = keyword.strip().lower()
        items = [
            it for it in items
            if kw in str(it.get("email", "")).lower() or kw in str(it.get("status", "")).lower()
        ]

    items.sort(key=lambda it: it.get("updated_at", ""), reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": page_items,
    }


def all_accounts() -> List[Dict[str, Any]]:
    with _LOCK:
        return _load()


def find(account_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for it in _load():
            if it.get("id") == account_id:
                return it
    return None


def find_by_email(email: str) -> Optional[Dict[str, Any]]:
    target = (email or "").strip().lower()
    with _LOCK:
        for it in _load():
            if str(it.get("email", "")).lower() == target:
                return it
    return None


def email_exists(email: str) -> bool:
    return find_by_email(email) is not None


def create_account(email: str, password: str, note: str = "") -> Dict[str, Any]:
    """新建一条账号记录，状态 pending_code。"""
    with _LOCK:
        items = _load()
        record = {
            "id": uuid.uuid4().hex[:12],
            "email": email.strip(),
            "password": password,
            "status": "pending_code",
            "step": "等待验证码",
            "note": note,
            "message": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        items.append(record)
        _save(items)
        return record


def update_account(account_id: str, **fields) -> Optional[Dict[str, Any]]:
    with _LOCK:
        items = _load()
        for it in items:
            if it.get("id") == account_id:
                it.update(fields)
                it["updated_at"] = _now()
                _save(items)
                return it
    return None


def delete_account(account_id: str) -> bool:
    with _LOCK:
        items = _load()
        left = [it for it in items if it.get("id") != account_id]
        if len(left) == len(items):
            return False
        _save(left)
        return True


def clear_accounts() -> int:
    with _LOCK:
        items = _load()
        _save([])
        return len(items)


def export_text() -> str:
    """导出为 email:password 行格式。"""
    with _LOCK:
        items = _load()
    lines = []
    for it in items:
        if it.get("status") == "success":
            lines.append(f"{it.get('email')}:{it.get('password')}")
    return "\n".join(lines) + ("\n" if lines else "")


def export_csv() -> str:
    with _LOCK:
        items = _load()
    rows = ["email,password,status,created_at,updated_at"]
    for it in items:
        rows.append(
            ",".join([
                str(it.get("email", "")),
                str(it.get("password", "")),
                str(it.get("status", "")),
                str(it.get("created_at", "")),
                str(it.get("updated_at", "")),
            ])
        )
    return "\n".join(rows) + "\n"


def stats() -> Dict[str, int]:
    with _LOCK:
        items = _load()
    total = len(items)
    success = sum(1 for it in items if it.get("status") == "success")
    failed = sum(1 for it in items if it.get("status") == "failed")
    pending = sum(1 for it in items if it.get("status") == "pending_code")
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "pending_code": pending,
    }
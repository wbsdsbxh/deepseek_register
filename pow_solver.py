#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeekHashV1 PoW 求解器。

封装 https://github.com/AmanCode22/deepseek_pow_solver 的 wasm 导出：
    solve_pow(challenge_hex, salt, expire_at, difficulty) -> nonce
返回 0xFFFFFFFFFFFFFFFF 表示在 difficulty 次迭代内无解。

wasm 文件位置：assets/deepseek_pow_solver.wasm
"""

import base64
import json
import os
import threading
from typing import Any, Dict, Optional

WASM_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "assets",
    "deepseek_pow_solver.wasm",
)

MAX_U64 = 0xFFFFFFFFFFFFFFFF


class PowSolver:
    """线程安全的 wasm 求解器实例池。"""

    def __init__(self, wasm_path: Optional[str] = None):
        self.wasm_path = wasm_path or WASM_PATH
        self._lock = threading.Lock()
        self._local = threading.local()
        self._engine = None
        self._module = None
        self._load()

    # ---------------- 加载 ----------------
    def _load(self) -> None:
        import wasmtime

        if not os.path.exists(self.wasm_path):
            raise FileNotFoundError(f"PoW wasm 不存在: {self.wasm_path}")
        with open(self.wasm_path, "rb") as fp:
            raw = fp.read()
        self._wasmtime = wasmtime
        self._engine = wasmtime.Engine()
        self._module = wasmtime.Module(self._engine, raw)

    def _instance(self):
        """每个线程一个 wasm 实例，避免复用状态。"""
        inst = getattr(self._local, "instance", None)
        if inst is not None:
            return inst
        store = self._wasmtime.Store(self._engine)
        instance = self._wasmtime.Instance(store, self._module, [])
        exports = instance.exports(store)
        bundle = {
            "store": store,
            "memory": exports["memory"],
            "alloc": exports["alloc"],
            "solve": exports["solve_pow"],
        }
        self._local.instance = bundle
        return bundle

    # ---------------- 求解 ----------------
    def solve(self, challenge: Dict[str, Any]) -> Dict[str, Any]:
        """
        challenge 是服务端 guest_challenge 原样对象：
        {algorithm, challenge, salt, signature, difficulty, expire_at, expire_after, target_path}
        """
        algorithm = str(challenge.get("algorithm") or "")
        if algorithm != "DeepSeekHashV1":
            raise ValueError(f"不支持的 PoW 算法: {algorithm}")

        challenge_hex = str(challenge.get("challenge") or "")
        salt = str(challenge.get("salt") or "")
        expire_at = int(challenge.get("expire_at") or 0)
        difficulty = int(challenge.get("difficulty") or 0)
        if not challenge_hex or not salt or not expire_at:
            raise ValueError(f"challenge 字段不完整: {json.dumps(challenge, ensure_ascii=False)[:300]}")

        bundle = self._instance()
        store = bundle["store"]
        memory = bundle["memory"]
        alloc = bundle["alloc"]
        solve_fn = bundle["solve"]

        def write_str(text: str):
            data = text.encode("utf-8")
            ptr = alloc(store, len(data))
            memory.write(store, data, ptr)
            return ptr, len(data)

        ch_ptr, ch_len = write_str(challenge_hex)
        salt_ptr, salt_len = write_str(salt)

        with self._lock:
            result = solve_fn(store, ch_ptr, ch_len, salt_ptr, salt_len, expire_at, difficulty)
        if result < 0:
            result += 1 << 64

        if result == MAX_U64:
            # difficulty 是迭代上限，配额不够时放大重试一次
            boosted = max(difficulty * 50, 5_000_000)
            with self._lock:
                result = solve_fn(store, ch_ptr, ch_len, salt_ptr, salt_len, expire_at, boosted)
            if result < 0:
                result += 1 << 64
            if result == MAX_U64:
                raise RuntimeError(f"PoW 求解失败，试了 {boosted} 次仍无解")

        return {
            "answer": int(result),
            "algorithm": algorithm,
            "challenge": challenge_hex,
            "salt": salt,
            "signature": challenge.get("signature"),
            "target_path": challenge.get("target_path"),
            "expire_at": expire_at,
            "difficulty": difficulty,
        }

    # ---------------- 请求头 ----------------
    @staticmethod
    def header_value(solution: Dict[str, Any]) -> str:
        """
        guest 场景请求头 X-DS-Guest-PoW-Response:
            base64(json({"salt": ..., "answer": ...}))
        正式接口场景 X-DS-PoW-Response:
            base64(json({algorithm, challenge, salt, answer, signature, target_path}))
        """
        payload = {
            "algorithm": solution["algorithm"],
            "challenge": solution["challenge"],
            "salt": solution["salt"],
            "answer": solution["answer"],
            "signature": solution["signature"],
            "target_path": solution["target_path"],
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    @staticmethod
    def guest_header_value(solution: Dict[str, Any]) -> str:
        payload = {"salt": solution["salt"], "answer": solution["answer"]}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")


_solver: Optional[PowSolver] = None
_solver_lock = threading.Lock()


def get_solver() -> PowSolver:
    global _solver
    if _solver is None:
        with _solver_lock:
            if _solver is None:
                _solver = PowSolver()
    return _solver
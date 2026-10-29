#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 注册控制台 —— Web 端入口。

启动：
    python main.py                # 0.0.0.0:8080
    PORT=9000 python main.py

页面：http://127.0.0.1:8080/
"""

import os

from flask import Flask, Response, jsonify, render_template, request

import store
from config import config
from deepseek_api import YesCaptcha
from runner import runner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.json.ensure_ascii = False


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return render_template("index.html", domain=config.EMAIL_DOMAIN)


@app.get("/api/status")
def api_status():
    return jsonify(runner.status())


@app.get("/api/logs")
def api_logs():
    since = _int_arg("since", 0)
    return jsonify(runner.logs(since=since))


@app.get("/api/balance")
def api_balance():
    try:
        return jsonify({"ok": True, "balance": YesCaptcha().balance()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)})


# --------------------------------------------------------------------------- #
@app.post("/api/start")
def api_start():
    data = request.get_json(silent=True) or {}
    count = int(data.get("count") or 0)
    interval = int(data.get("interval") or 5)
    return jsonify(runner.start(count=count, interval=interval))


@app.post("/api/stop")
def api_stop():
    return jsonify(runner.stop())


@app.post("/api/register_one")
def api_register_one():
    data = request.get_json(silent=True) or {}
    return jsonify(runner.register_one(data.get("email", ""), data.get("password", "")))


@app.post("/api/submit_code")
def api_submit_code():
    data = request.get_json(silent=True) or {}
    return jsonify(runner.submit_code(data.get("account_id", ""), data.get("code", "")))


@app.post("/api/resend_code")
def api_resend_code():
    data = request.get_json(silent=True) or {}
    return jsonify(runner.resend_code(data.get("account_id", "")))


# --------------------------------------------------------------------------- #
@app.get("/api/accounts")
def api_accounts():
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", 8)
    keyword = request.args.get("keyword", "")
    return jsonify(store.list_accounts(page=page, page_size=page_size, keyword=keyword))


@app.delete("/api/accounts/<account_id>")
def api_delete_account(account_id: str):
    return jsonify({"ok": store.delete_account(account_id)})


@app.post("/api/accounts/clear")
def api_clear_accounts():
    removed = store.clear_accounts()
    return jsonify({"ok": True, "removed": removed})


@app.get("/api/export")
def api_export():
    fmt = request.args.get("format", "txt").lower()
    if fmt == "csv":
        return Response(
            store.export_csv(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=accounts.csv"},
        )
    return Response(
        store.export_text(),
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=accounts.txt"},
    )


@app.post("/api/verify/<account_id>")
def api_verify(account_id: str):
    from deepseek_api import DeepSeekClient

    record = store.find(account_id)
    if not record:
        return jsonify({"ok": False, "error": "账号不存在"})
    client = DeepSeekClient(log=runner.log)
    result = client.login(record["email"], record["password"])
    store.update_account(
        account_id,
        verified=bool(result.get("ok")),
        verify_message=result.get("biz_msg") or result.get("error") or "",
    )
    return jsonify(result)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
    print(f"Web 控制台: http://{config.HOST}:{config.PORT}/")
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True, use_reloader=False)
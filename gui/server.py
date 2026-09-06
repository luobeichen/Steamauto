"""Steamauto GUI 的 Flask 服务（本地 Web 界面）。"""
import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

from . import buff, config_editor, config_schema, login, runner, uu

# 降级 Flask/werkzeug 的 HTTP 访问日志，避免刷屏（仅保留 WARNING 及以上）
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    log_file = runner.latest_log_file()
    return jsonify(
        {
            "running": runner.is_running(),
            "pid": runner.get_pid(),
            "log_file": os.path.basename(log_file) if log_file else None,
        }
    )


@app.route("/api/start", methods=["POST"])
def api_start():
    ok, msg = runner.start()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok, msg = runner.stop()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """完全退出 GUI：先停止 Steamauto 子进程，再退出 GUI 进程本身。"""

    def _do_shutdown():
        time.sleep(0.5)  # 让响应先返回给前端
        try:
            runner.stop()
        except Exception:
            pass
        os._exit(0)  # 立即终止整个 GUI 进程

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"ok": True, "msg": "GUI 正在退出..."})


@app.route("/api/logs")
def api_logs():
    tail = request.args.get("tail", type=int)
    flush = request.args.get("flush") == "1"
    lines, name = runner.read_logs(tail=tail, flush=flush)
    return jsonify({"lines": lines, "file": name})


@app.route("/api/log_level", methods=["GET", "POST"])
def api_log_level():
    if request.method == "GET":
        config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
        return jsonify({"level": config.get("log_level", "info")})
    data = request.get_json(silent=True) or {}
    level = data.get("level", "info")
    if level not in ("debug", "info", "warning", "error"):
        return jsonify({"ok": False, "msg": "无效日志等级"})
    config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
    ok, result = config_schema.save_from_table(config, {"log_level": level})
    if not ok:
        return jsonify({"ok": False, "msg": result})
    config_editor.save_json5(config_editor.CONFIG_FILE_PATH, result)
    return jsonify({"ok": True, "msg": "日志等级已设为 " + level, "level": level})


@app.route("/api/config")
def api_config():
    config_text = config_editor.read_text(config_editor.CONFIG_FILE_PATH)
    account = config_editor.load_json5(config_editor.ACCOUNT_FILE_PATH)
    account_text = config_editor.read_text(config_editor.ACCOUNT_FILE_PATH)
    return jsonify(
        {
            "config_text": config_text or "",
            "account": account if isinstance(account, dict) else {},
            "account_text": account_text or "",
        }
    )


@app.route("/api/config/save", methods=["POST"])
def api_config_save():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    ok, msg = config_editor.save_text(config_editor.CONFIG_FILE_PATH, content)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/account/save", methods=["POST"])
def api_account_save():
    data = request.get_json(silent=True) or {}
    account = {
        "shared_secret": data.get("shared_secret", ""),
        "identity_secret": data.get("identity_secret", ""),
        "steam_username": data.get("steam_username", ""),
        "steam_password": data.get("steam_password", ""),
    }
    config_editor.save_json5(config_editor.ACCOUNT_FILE_PATH, account)
    return jsonify({"ok": True, "msg": "保存成功"})


@app.route("/api/account/reset", methods=["POST"])
def api_account_reset():
    config_editor.save_json5(config_editor.ACCOUNT_FILE_PATH, config_editor.ACCOUNT_DEFAULT)
    return jsonify({"ok": True, "msg": "已恢复默认值", "account": dict(config_editor.ACCOUNT_DEFAULT)})


@app.route("/api/account/export")
def api_account_export():
    content = config_editor.read_text(config_editor.ACCOUNT_FILE_PATH) or ""
    return jsonify({"ok": True, "content": content, "filename": "steam_account_info.json5"})


@app.route("/api/account/import", methods=["POST"])
def api_account_import():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    ok, value = config_editor.validate_json5(content)
    if not ok:
        return jsonify({"ok": False, "msg": "JSON5 语法错误：" + str(value)})
    if not isinstance(value, dict):
        return jsonify({"ok": False, "msg": "内容应为 JSON 对象"})
    config_editor.save_text(config_editor.ACCOUNT_FILE_PATH, content)
    return jsonify({"ok": True, "msg": "导入成功", "account": value})


@app.route("/api/login/status")
def api_login_status():
    return jsonify(login.get_state())


@app.route("/api/login/refresh", methods=["POST"])
def api_login_refresh():
    login.refresh_login_status()
    return jsonify({"ok": True, "status": login.get_state()})


@app.route("/api/buff/inventory")
def api_buff_inventory():
    client = buff.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "BUFF 未登录，请先在「平台登录」页登录 BUFF"})
    items = client.get_inventory_all()
    rows = client.enrich_inventory(items)
    balance = client.get_balance()
    return jsonify({"ok": True, "items": rows, "balance": balance})


@app.route("/api/buff/remark", methods=["POST"])
def api_buff_remark():
    data = request.get_json(silent=True) or {}
    assetid = data.get("assetid", "")
    remark = data.get("remark", "")
    if not assetid:
        return jsonify({"ok": False, "msg": "缺少 assetid"})
    client = buff.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "BUFF 未登录"})
    result = client.set_remark(assetid, remark)
    if result.get("code") == "OK":
        return jsonify({"ok": True, "msg": "备注已保存"})
    return jsonify({"ok": False, "msg": result.get("error") or "保存失败"})


@app.route("/api/buff/search")
def api_buff_search():
    key = request.args.get("key", "")
    source = request.args.get("source", "market")
    if not key:
        return jsonify({"ok": False, "msg": "缺少关键词"})
    client = buff.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "BUFF 未登录"})
    if source == "inventory":
        items = client.get_inventory_all()
        rows = client.enrich_inventory(items)
        matched = [r for r in rows if key.lower() in (r["name"] + " " + r["market_hash_name"]).lower()]
        return jsonify({"ok": True, "items": matched})
    data = client.search_market_all(key)
    if not data:
        return jsonify({"ok": False, "msg": "搜索失败"})
    items = [{
        "goods_id": it.get("id"),
        "name": it.get("name") or "",
        "market_hash_name": it.get("market_hash_name") or "",
    } for it in data]
    # 只对前 50 个补充行情（避免 API 调用过多），其余行情列为空
    enrich_items = client.enrich_search_items(items[:50])
    items = enrich_items + items[50:]
    return jsonify({"ok": True, "items": items})


@app.route("/api/buff/trade/config", methods=["GET", "POST"])
def api_buff_trade_config():
    if request.method == "GET":
        config = buff.load_trade_config()
        client = buff.get_client()
        if client is not None and config:
            config = client.enrich_search_items(config)
        return jsonify({"ok": True, "config": config})
    data = request.get_json(silent=True) or {}
    config = data.get("config", [])
    buff.save_trade_config(config)
    return jsonify({"ok": True, "msg": "配置已保存"})


@app.route("/api/buff/trade/scan", methods=["POST"])
def api_buff_trade_scan():
    data = request.get_json(silent=True) or {}
    dry_run = data.get("dry_run", True)
    client = buff.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "BUFF 未登录"})
    config = buff.load_trade_config()
    results = buff.scan_and_trade(client, config, dry_run=dry_run)
    return jsonify({"ok": True, "results": results})


@app.route("/api/buff/trade/interval", methods=["GET", "POST"])
def api_buff_trade_interval():
    if request.method == "GET":
        return jsonify({"ok": True, "interval": buff.get_scan_interval() or buff.load_scan_interval()})
    data = request.get_json(silent=True) or {}
    interval = int(data.get("interval", 0))
    dry_run = data.get("dry_run", True)
    ok, msg = buff.start_auto_scan(interval, dry_run=dry_run)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/buff/deal_price")
def api_buff_deal_price():
    goods_id = request.args.get("goods_id", "")
    if not goods_id:
        return jsonify({"ok": False, "msg": "缺少 goods_id"})
    client = buff.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "BUFF 未登录"})
    price = buff.get_latest_deal_price(client, goods_id)
    return jsonify({"ok": True, "price": price})


@app.route("/api/login/start", methods=["POST"])
def api_login_start():
    data = request.get_json(silent=True) or {}
    platform = data.get("platform", "")
    if platform not in ("steam", "buff", "uu"):
        return jsonify({"ok": False, "msg": "未知平台"})
    try:
        login.start_login(platform)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": True, "msg": "已开始登录"})


@app.route("/api/login/interact")
def api_login_interact():
    req = login.bridge.peek_request()
    return jsonify({"request": req})


@app.route("/api/login/respond", methods=["POST"])
def api_login_respond():
    data = request.get_json(silent=True) or {}
    value = data.get("value", "")
    login.bridge.respond(value)
    return jsonify({"ok": True})


@app.route("/api/login/qrcode")
def api_login_qrcode():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False})
    import base64
    import io

    import qrcode

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return jsonify({"ok": True, "image": "data:image/png;base64," + b64})


@app.route("/api/config/table")
def api_config_table():
    config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
    return jsonify({"groups": config_schema.get_table_data(config)})


@app.route("/api/config/table/save", methods=["POST"])
def api_config_table_save():
    data = request.get_json(silent=True) or {}
    values = data.get("values", {})
    config = config_editor.load_json5(config_editor.CONFIG_FILE_PATH) or {}
    ok, result = config_schema.save_from_table(config, values)
    if not ok:
        return jsonify({"ok": False, "msg": result})
    config_editor.save_json5(config_editor.CONFIG_FILE_PATH, result)
    return jsonify({"ok": True, "msg": "保存成功"})


@app.route("/api/uu/inventory")
def api_uu_inventory():
    client = uu.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "UU 未登录，请先在「平台登录」页登录悠悠有品"})
    items = client.get_inventory()
    rows = client.enrich_inventory(items)
    return jsonify({"ok": True, "items": rows})


@app.route("/api/uu/search")
def api_uu_search():
    key = request.args.get("key", "")
    if not key:
        return jsonify({"ok": False, "msg": "缺少关键词"})
    client = uu.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "UU 未登录"})
    data = client.search_market(key)
    if not data:
        return jsonify({"ok": False, "msg": "搜索失败"})
    # 返回结构待实测，做防御性解析
    d = data.get("data") if isinstance(data.get("data"), (dict, list)) else data.get("Data")
    items_raw = None
    if isinstance(d, dict):
        items_raw = (d.get("commodityInfoList") or d.get("templateList")
                     or d.get("list") or d.get("commodityList") or d.get("items"))
    elif isinstance(d, list):
        items_raw = d
    items = []
    for it in (items_raw or []):
        items.append({
            "template_id": it.get("templateId") or it.get("id") or it.get("template_id"),
            "name": it.get("commodityName") or it.get("name") or "",
            "market_hash_name": it.get("marketHashName") or it.get("hashName") or it.get("market_hash_name") or "",
        })
    # 只对前 50 个补充行情（避免 API 调用过多）
    enrich_items = client.enrich_search_items(items[:50])
    items = enrich_items + items[50:]
    return jsonify({"ok": True, "items": items})


@app.route("/api/uu/trade/config", methods=["GET", "POST"])
def api_uu_trade_config():
    if request.method == "GET":
        config = uu.load_trade_config()
        client = uu.get_client()
        if client is not None and config:
            config = client.enrich_search_items(config)
        return jsonify({"ok": True, "config": config})
    data = request.get_json(silent=True) or {}
    config = data.get("config", [])
    uu.save_trade_config(config)
    return jsonify({"ok": True, "msg": "配置已保存"})


@app.route("/api/uu/trade/scan", methods=["POST"])
def api_uu_trade_scan():
    data = request.get_json(silent=True) or {}
    dry_run = data.get("dry_run", True)
    client = uu.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "UU 未登录"})
    config = uu.load_trade_config()
    results = uu.scan_and_trade(client, config, dry_run=dry_run)
    return jsonify({"ok": True, "results": results})


@app.route("/api/uu/trade/interval", methods=["GET", "POST"])
def api_uu_trade_interval():
    if request.method == "GET":
        return jsonify({"ok": True, "interval": uu.get_scan_interval() or uu.load_scan_interval()})
    data = request.get_json(silent=True) or {}
    interval = int(data.get("interval", 0))
    dry_run = data.get("dry_run", True)
    ok, msg = uu.start_auto_scan(interval, dry_run=dry_run)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/uu/deal_price")
def api_uu_deal_price():
    template_id = request.args.get("template_id", "")
    if not template_id:
        return jsonify({"ok": False, "msg": "缺少 template_id"})
    client = uu.get_client()
    if client is None:
        return jsonify({"ok": False, "msg": "UU 未登录"})
    price = client.get_latest_deal_price(template_id)
    return jsonify({"ok": True, "price": price})

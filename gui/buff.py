"""BUFF 客户端封装（GUI 用）：库存查询、成交、搜索、在售/求购行情。

session cookie 复用 Steamauto 的登录缓存 config/buff_cookies_{username}.txt。
"""
import json
import logging
import os
import threading

import json5

from . import config_editor
from api.BuffApi import BuffAccount, models

logger = logging.getLogger("buff")

# 允许实盘（真实下单）的 goods_id 白名单。仅此集合内的饰品在 dry-run 关闭时会真实下单，
# 其他饰品一律只记录不下单（需用户明确允许才加入）。
LIVE_ALLOW = {"773534"}


def _get_username():
    try:
        with open(config_editor.ACCOUNT_FILE_PATH, encoding="utf-8") as f:
            return json5.loads(f.read()).get("steam_username", "") or ""
    except Exception:
        return ""


def get_client():
    """从配置读取 BUFF session cookie，创建客户端；未登录返回 None。"""
    username = _get_username()
    cookie_path = os.path.join(config_editor.CONFIG_FOLDER, "buff_cookies_" + username + ".txt")
    if not os.path.exists(cookie_path):
        return None
    with open(cookie_path, encoding="utf-8") as f:
        cookie = f.read().strip()
    if not cookie or cookie == "session=":
        return None
    return BuffClient(cookie)


class BuffClient:
    """包装 api/BuffApi.BuffAccount（core SDK），供 GUI 复用，不再 requests 直调。"""

    def __init__(self, cookie):
        self.account = BuffAccount(cookie)

    # ---- 库存 ----
    def get_inventory(self, game="csgo", page_num=1, page_size=100):
        return self.account.get_inventory(game, page_num, page_size)

    def get_inventory_all(self, game="csgo"):
        """拉取全部库存饰品，返回 items 列表。"""
        return self.account.get_inventory_all(game)

    # ---- 成交 ----
    def get_bill_order(self, goods_id, game="csgo", page_num=1, page_size=20):
        return self.account.get_bill_order(goods_id, game, page_num, page_size)

    # ---- 搜索 ----
    def search_goods(self, key, game="csgo"):
        suggestions = self.account.search_goods(key, game)
        return {"code": "OK", "data": {"suggestions": suggestions}} if suggestions else {"code": "ERROR"}

    def search_market(self, keyword, game="csgo", page_num=1, page_size=100):
        """搜索市场商品（完整结果，支持分页）。"""
        return self.account.search_market(keyword, game, page_num, page_size)

    def search_market_all(self, keyword, game="csgo", max_items=500):
        """分页拉取全部搜索结果，返回 items 列表。"""
        return self.account.search_market_all(keyword, game, max_items)

    # ---- 行情 ----
    def get_sell_order(self, goods_id, game="csgo", page_num=1, page_size=10):
        data = self.account.get_sell_order(goods_id, page_num=page_num, game_name=game)
        return {"code": "OK", "data": data} if data else {"code": "ERROR", "data": {}}

    def get_buy_order(self, goods_id, game="csgo", page_num=1, page_size=10):
        return self.account.get_buy_order(goods_id, game, page_num, page_size)

    def get_buy_order_max(self, goods_id, game="csgo"):
        """获取指定饰品的最高求购价（buy_order 第一个 item）。"""
        return self.account.get_buy_order_max(goods_id, game)

    def set_remark(self, assetid, remark, game="csgo"):
        """修改库存饰品备注（按 assetid，备注最长 40 字）。"""
        return self.account.set_remark(assetid, remark, game)

    def enrich_inventory(self, items):
        """补充求购价和最新成交价（同一 goods_id 只查一次）。"""
        rows = summarize_inventory(items)
        goods_ids = list(dict.fromkeys(r["goods_id"] for r in rows if r["goods_id"]))
        for gid in goods_ids:
            buy_max = self.get_buy_order_max(gid)
            deal = get_latest_deal_price(self, gid)
            for r in rows:
                if r["goods_id"] == gid:
                    r["buy_max_price"] = buy_max
                    r["deal_price"] = deal
        return rows

    def get_sell_min(self, goods_id, game="csgo"):
        """获取在售最低价（sell_order 第一个 item 的 price）。"""
        return self.account.get_sell_min(goods_id, game)

    def enrich_search_items(self, items):
        """对搜索结果的每个 goods_id，补充求购价、在售价、自己售价、最新成交价。"""
        # 库存 → goods_id 的自己售价映射（只取已上架的饰品）
        own_price_map = {}
        try:
            for it in self.get_inventory_all():
                gid = it.get("goods_id")
                if gid and it.get("sell_order_id") and gid not in own_price_map:
                    own_price_map[gid] = it.get("sell_order_price")
        except Exception:
            pass
        for item in items:
            gid = item.get("goods_id")
            if not gid:
                continue
            item["buy_max_price"] = self.get_buy_order_max(gid)
            item["sell_min_price"] = self.get_sell_min(gid)
            item["sell_order_price"] = own_price_map.get(gid)  # 库存无/未上架则 None
            item["deal_price"] = get_latest_deal_price(self, gid)
        return items

    def get_balance(self):
        """查询 BUFF 余额。"""
        try:
            return self.account.get_user_brief_assest()
        except Exception:
            return None

    def get_steamid(self, game="csgo"):
        """获取当前 Steam ID（从在售列表）。"""
        return self.account.get_steamid(game)

    def find_assetid(self, goods_id):
        """在库存里找该 goods_id 的饰品 assetid（未上架的优先）。"""
        try:
            items = self.get_inventory_all()
            for it in items:
                if str(it.get("goods_id")) == str(goods_id) and not it.get("sell_order_id"):
                    return it.get("assetid")
            for it in items:
                if str(it.get("goods_id")) == str(goods_id):
                    return it.get("assetid")
        except Exception:
            pass
        return None

    def create_sell_order(self, assetid, price, steamid, game="csgo", mode="manual"):
        """上架饰品（指定价格）。steamid/mode 保留兼容，实际由 BuffAccount.on_sale 处理。"""
        it = None
        for item in self.account.get_inventory_all(game):
            if str(item.get("assetid")) == str(assetid):
                it = item
                break
        if it is None:
            return {"code": "ERROR", "error": "库存中未找到 assetid=%s" % assetid}
        asset = models.BuffOnSaleAsset(
            assetid=str(assetid),
            classid=int(it["classid"]),
            instanceid=int(it["instanceid"]),
            market_hash_name=it.get("market_hash_name") or "",
            price=price,
        )
        success, problems = self.account.on_sale([asset])
        if isinstance(problems, dict) and problems:
            return {"code": "ERROR", "error": problems.get("error") or str(problems)}
        return {"code": "OK", "data": success}

    def buy(self, goods_id, sell_order_id, price, pay_method="buff-bankcard", game="csgo"):
        """购买在售单（复用 BuffAccount.buy_goods，内部已处理 csrf）。"""
        try:
            return self.account.buy_goods(
                sell_order_id=sell_order_id,
                goods_id=goods_id,
                price=price,
                pay_method=pay_method,
                ask_seller_send_offer=False,
                game_name=game,
            )
        except ValueError as e:
            return {"code": "ERROR", "error": str(e)}


def summarize_inventory(items):
    """把库存 items 转成表格行（含备注、求购价、在售价、自己售价）。"""
    rows = []
    for it in items:
        extra = it.get("asset_extra") or {}
        rows.append({
            "assetid": it.get("assetid"),
            "goods_id": it.get("goods_id"),
            "name": it.get("name") or "",
            "market_hash_name": it.get("market_hash_name") or "",
            "remark": extra.get("remark") or "",          # 备注（即购入价）
            "sell_min_price": it.get("sell_min_price"),   # 市场最低在售价
            "buy_max_price": it.get("buy_max_price"),     # 市场最高求购价（可能为 0，enrich 后补充）
            "sell_order_price": it.get("sell_order_price"),  # 自己售价（若已上架）
            "on_sale": bool(it.get("sell_order_id")),
        })
    return rows


def get_latest_deal_price(client, goods_id, game="csgo"):
    """查询指定饰品的最新成交价，无成交返回 None。"""
    data = client.get_bill_order(goods_id, game, 1, 10)
    if data.get("code") != "OK":
        return None
    items = data["data"].get("items", [])
    if not items:
        return None
    # bill_order 的 items 是成交记录，含 price 字段
    prices = [it.get("price") for it in items if it.get("price") is not None]
    return prices[0] if prices else None


# ==================== 自动交易（功能 2） ====================

def _trade_config_path():
    return os.path.join(config_editor.CONFIG_FOLDER, "buff_trade.json")


def load_trade_config():
    """加载交易配置（勾选的饰品 + 最高购入价/最低售价）。"""
    path = _trade_config_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_trade_config(config):
    path = _trade_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ==================== 后台定时扫描 ====================

_scan_stop = threading.Event()
_scan_thread = None
_scan_interval = 0


def _scan_interval_path():
    return os.path.join(config_editor.CONFIG_FOLDER, "buff_scan_interval.json")


def load_scan_interval():
    """加载扫描周期（秒），默认 0（不自动扫描）。"""
    path = _scan_interval_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("interval", 0))
    except Exception:
        return 0


def save_scan_interval(interval_seconds):
    path = _scan_interval_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"interval": int(interval_seconds)}, f)


def get_scan_interval():
    return _scan_interval


def start_auto_scan(interval_seconds, dry_run=True):
    """启动后台定时扫描（interval_seconds 秒一次）。"""
    global _scan_thread, _scan_interval, _scan_stop
    stop_auto_scan()
    interval_seconds = int(interval_seconds)
    if interval_seconds <= 0:
        save_scan_interval(0)
        return False, "已停止自动扫描"
    _scan_interval = interval_seconds
    save_scan_interval(interval_seconds)
    _scan_stop = threading.Event()

    def _loop():
        while not _scan_stop.is_set():
            _scan_stop.wait(interval_seconds)
            if _scan_stop.is_set():
                break
            try:
                client = get_client()
                if client:
                    config = load_trade_config()
                    if config:
                        logger.info("[自动扫描] 开始扫描 %d 个饰品", len(config))
                        scan_and_trade(client, config, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                logger.error("[自动扫描] 异常: %s", e)

    _scan_thread = threading.Thread(target=_loop, daemon=True)
    _scan_thread.start()
    return True, "已启动自动扫描（每 %d 秒）" % interval_seconds


def stop_auto_scan():
    global _scan_thread, _scan_interval
    _scan_interval = 0
    if _scan_thread is not None:
        _scan_stop.set()
        _scan_thread = None


def scan_and_trade(client, config, dry_run=True):
    """扫描配置的饰品，返回决策结果列表。

    决策逻辑（低买高卖）：
    - 市场在售最低价 < 最高购入价 → 买入（buy）
    - 市场最高求购价 > 最低售价 → 卖给求购者（sell_to_bidder，价格略低于求购价）
    - 市场最高求购价 < 最低售价 → 上架（list，价格=在售最低价-0.01，但不低于最低售价）
    """
    results = []
    for item in config:
        gid = item.get("goods_id")
        name = item.get("name") or item.get("market_hash_name") or str(gid)
        try:
            max_buy = float(item.get("max_buy_price") or 0)
            min_sell = float(item.get("min_sell_price") or 0)
            buy_count = int(float(item.get("buy_count") or 0))
            sell_count = int(float(item.get("sell_count") or 0))
        except (TypeError, ValueError):
            max_buy = 0
            min_sell = 0
            buy_count = 0
            sell_count = 0

        # 行情
        sell_min = None
        sell_order_id = None
        try:
            so = client.get_sell_order(gid, page_num=1, page_size=1)
            if so.get("code") == "OK":
                its = so["data"].get("items", [])
                if its:
                    sell_min = float(its[0].get("price") or 0)
                    sell_order_id = its[0].get("id")
        except Exception:
            pass

        buy_max = client.get_buy_order_max(gid)
        try:
            buy_max = float(buy_max) if buy_max is not None else None
        except (TypeError, ValueError):
            buy_max = None

        # 决策
        decision = None
        action_price = None
        reason = ""
        if sell_min is not None and max_buy > 0 and sell_min <= max_buy and buy_count > 0:
            decision = "buy"
            action_price = sell_min
            reason = "在售最低价 %.2f <= 最高购入价 %.2f（剩余购入 %d）" % (sell_min, max_buy, buy_count)
        elif buy_max is not None and min_sell > 0 and buy_max > min_sell and sell_count > 0:
            decision = "sell_to_bidder"
            action_price = round(buy_max - 0.01, 2)
            reason = "最高求购价 %.2f > 最低售价 %.2f（剩余售出 %d）" % (buy_max, min_sell, sell_count)
        elif buy_max is not None and min_sell > 0 and buy_max < min_sell and sell_count > 0:
            decision = "list"
            if sell_min is not None:
                action_price = max(round(sell_min - 0.01, 2), min_sell)
            else:
                action_price = min_sell
            reason = "最高求购价 %.2f < 最低售价 %.2f（剩余售出 %d）" % (buy_max, min_sell, sell_count)
        else:
            if sell_min is not None and sell_min <= max_buy and buy_count <= 0:
                reason = "满足购入条件但购入数量已用完"
            elif buy_max is not None and min_sell > 0 and buy_max > min_sell and sell_count <= 0:
                reason = "满足售出条件但售出数量已用完"
            else:
                reason = "无满足条件的操作"

        # 执行（非 dry-run 时真实下单，但仅在 LIVE_ALLOW 白名单内）
        executed = False
        exec_msg = ""
        allowed = str(gid) in LIVE_ALLOW
        if not dry_run and not allowed and decision:
            exec_msg = "未授权实盘（仅限白名单饰品）"
        elif not dry_run and decision == "buy" and sell_order_id:
            logger.info("[实盘] 购买 %s(goods_id=%s) @ %s", name, gid, action_price)
            r = client.buy(gid, sell_order_id, action_price)
            if r.get("code") == "OK":
                executed = True
                item["buy_count"] = str(max(0, buy_count - 1))
                exec_msg = "已购买"
                logger.info("[实盘] 购买成功 %s", name)
            else:
                exec_msg = "购买失败: " + str(r.get("error") or r.get("msg") or "")
                logger.error("[实盘] 购买失败 %s: %s", name, exec_msg)
        elif not dry_run and decision in ("sell_to_bidder", "list"):
            assetid = client.find_assetid(gid)
            steamid = client.get_steamid()
            if assetid and steamid:
                logger.info("[实盘] 上架 %s(assetid=%s) @ %s", name, assetid, action_price)
                r = client.create_sell_order(assetid, action_price, steamid)
                if r.get("code") == "OK":
                    executed = True
                    item["sell_count"] = str(max(0, sell_count - 1))
                    exec_msg = "已上架"
                    logger.info("[实盘] 上架成功 %s", name)
                else:
                    exec_msg = "上架失败: " + str(r.get("error") or r.get("msg") or "")
                    logger.error("[实盘] 上架失败 %s: %s", name, exec_msg)
            else:
                exec_msg = "无可用库存或无法获取 steamid"
                logger.warning("[实盘] 无法上架 %s: %s", name, exec_msg)

        results.append({
            "goods_id": gid,
            "name": name,
            "sell_min_price": sell_min,
            "buy_max_price": buy_max,
            "max_buy_price": max_buy,
            "min_sell_price": min_sell,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "decision": decision,
            "action_price": action_price,
            "sell_order_id": sell_order_id,
            "reason": reason,
            "dry_run": dry_run,
            "executed": executed,
            "exec_msg": exec_msg,
        })

    # 非 dry-run 且有执行时，保存更新后的配置（执行数量已减一）
    if not dry_run:
        save_trade_config(config)
    return results

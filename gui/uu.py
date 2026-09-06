"""UU（悠悠有品）客户端封装（GUI 用）：库存查询、搜索、行情、自动交易。

token 复用 Steamauto 的登录缓存 config/uu_token_{username}.txt，
底层 API 复用 uuyoupinapi.UUAccount（token 鉴权 + uk 校验）。

与 BUFF 的关键差异（写代码时特别注意）：
- 标识：template_id（对应 BUFF 的 goods_id）、SteamAssetId（对应 BUFF 的 assetid）
- 购买：UU 是「求购单」模式，publish_purchase_order 发求购单（价格合适即时撮合成交）
- 上架：sell_items → /api/commodity/Inventory/SellInventoryWithLeaseV2
- 搜索：/api/homepage/pc/goods/market/querySaleTemplate（按关键词，参数待实测）
- 行情：在售最低价 get_least_market_price；求购价 get_template_purchase_order_pc
"""
import json
import logging
import os
import threading

import json5
import uuyoupinapi

from . import config_editor

logger = logging.getLogger("uu")

# 允许实盘（真实下单）的 template_id 白名单。仅此集合内的饰品在 dry-run 关闭时会真实下单，
# 其他饰品一律只记录不下单（需用户明确允许才加入）。
LIVE_ALLOW = set()


def _get_username():
    try:
        with open(config_editor.ACCOUNT_FILE_PATH, encoding="utf-8") as f:
            return json5.loads(f.read()).get("steam_username", "") or ""
    except Exception:
        return ""


def get_client():
    """从配置读取 UU token，创建客户端；未登录/token 无效返回 None。"""
    username = _get_username()
    token_path = os.path.join(config_editor.PROJECT_ROOT, "config", "uu_token_" + username + ".txt")
    if not os.path.exists(token_path):
        return None
    with open(token_path, encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        return None
    try:
        return UUClient(token)
    except Exception as e:  # noqa: BLE001
        logger.warning("UU 登录失败（token 无效）: %s", e)
        return None


class UUClient:
    def __init__(self, token):
        # 构造时会调 getUserInfo 验证 token，无效会抛异常
        self.api = uuyoupinapi.UUAccount(token)
        self.deviceToken = self.api.deviceToken

    def _call(self, method, path, data=None, uk_verify=False, pc_platform=False):
        """通用调用，返回解析后的 dict（统一大小写兼容）。"""
        try:
            resp = self.api.call_api(method, path, data=data, uk_verify=uk_verify, pc_platform=pc_platform)
            return resp.json()
        except Exception:
            return None

    # ---- 库存 ----
    def get_inventory(self):
        """返回库存 items 列表（Data.ItemsInfos）。"""
        return self.api.get_inventory()

    def get_inventory_all(self):
        """UU 库存接口一次返回全部（pageSize=1000），直接返回列表。"""
        return self.api.get_inventory()

    # ---- 我的在售 ----
    def get_my_sell_list(self):
        """返回我在售的商品列表（含 id=CommodityId、templateId、name）。"""
        try:
            return self.api.get_sell_list()
        except Exception:
            return []

    # ---- 搜索 ----
    def search_market(self, keyword, page_index=1, page_size=100):
        """搜索市场商品模板（querySaleTemplate）。参数结构待实测。"""
        data = {
            "gameId": "730",
            "commodityName": keyword,  # 待实测：可能是 searchName/keyWord
            "pageIndex": page_index,
            "pageSize": page_size,
        }
        return self._call("POST", "/api/homepage/pc/goods/market/querySaleTemplate", data=data, uk_verify=True, pc_platform=True)

    # ---- 行情 ----
    def get_sell_min(self, template_id):
        """在售最低价（get_least_market_price → Data.CommodityList[0].Price）。"""
        try:
            price = self.api.get_least_market_price(template_id)
            return float(price) if price else None
        except Exception:
            return None

    def get_buy_max(self, template_id):
        """市场最高求购价（求购单列表第一个/最高价）。返回结构待实测，做防御性提取。"""
        try:
            data = self._call(
                "POST",
                "/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC",
                data={"templateId": template_id, "pageIndex": 1, "pageSize": 1, "minAbrade": 0, "maxAbrade": 1, "typeId": -1},
                uk_verify=True,
                pc_platform=True,
            )
            if not data or data.get("code") != 0:
                return None
            d = data.get("data")
            items = None
            if isinstance(d, dict):
                items = d.get("purchaseOrderList") or d.get("orderList") or d.get("commodityInfoList") or d.get("list")
            elif isinstance(d, list):
                items = d
            if not items:
                return None
            first = items[0]
            price = first.get("price") or first.get("purchasePrice") or first.get("unitPrice") or first.get("Price")
            return float(price) if price is not None else None
        except Exception:
            return None

    def get_latest_deal_price(self, template_id):
        """参考价：UU 无「最新成交价」接口，退回用最低在售价作为参考。"""
        return self.get_sell_min(template_id)

    # ---- 备注 / 购入价 ----
    def set_buy_price(self, steam_asset_id, market_hash_name, buy_price, abrade="0"):
        """保存购入价（对应 BUFF 的备注，语义是记录购入成本）。"""
        try:
            self.api.save_buy_price([{
                "steamAssetId": str(steam_asset_id),
                "marketHashName": market_hash_name,
                "buyPrice": str(buy_price),
                "abrade": str(abrade),
            }])
            return {"code": 0}
        except Exception as e:  # noqa: BLE001
            return {"code": "ERROR", "error": str(e)}

    # ---- 购买（发求购单）----
    def buy(self, template_id, hash_name, name, price, num=1):
        """发求购单购买（价格合适即时撮合成交）。"""
        try:
            resp = self.api.publish_purchase_order(template_id, hash_name, name, price, num)
            return resp.json()
        except Exception as e:  # noqa: BLE001
            return {"code": "ERROR", "error": str(e)}

    # ---- 上架 / 改价 / 下架 ----
    def create_sell_order(self, assetid, price, steamid=None, game="csgo", mode="manual"):
        """上架饰品（sell_items，参数是 {assetid: price}）。"""
        try:
            count = self.api.sell_items({str(assetid): price})
            return {"code": 0 if count > 0 else "FAIL", "count": count}
        except Exception as e:  # noqa: BLE001
            return {"code": "ERROR", "error": str(e)}

    def change_price(self, commodity_id, price):
        """改价（CommodityId → 新价格）。"""
        try:
            self.api.change_price({str(commodity_id): price})
            return {"code": 0}
        except Exception as e:  # noqa: BLE001
            return {"code": "ERROR", "error": str(e)}

    def off_shelf(self, commodity_ids):
        """下架。"""
        try:
            self.api.off_shelf(list(commodity_ids))
            return {"code": 0}
        except Exception as e:  # noqa: BLE001
            return {"code": "ERROR", "error": str(e)}

    def find_assetid(self, template_id):
        """在库存里找该 template_id 的饰品 SteamAssetId（未上架的优先）。"""
        try:
            items = self.get_inventory()
            for it in items:
                ti = it.get("TemplateInfo") or {}
                if str(ti.get("Id")) == str(template_id) and it.get("AssetStatus") == 0:
                    return it.get("SteamAssetId")
            for it in items:
                ti = it.get("TemplateInfo") or {}
                if str(ti.get("Id")) == str(template_id):
                    return it.get("SteamAssetId")
        except Exception:
            pass
        return None

    # ---- 补充行情 ----
    def enrich_inventory(self, items):
        """把库存 items 转成表格行，并补充求购价/在售价。"""
        rows = summarize_inventory(items)
        template_ids = list(dict.fromkeys(r["template_id"] for r in rows if r["template_id"]))
        for tid in template_ids:
            buy_max = self.get_buy_max(tid)
            sell_min = self.get_sell_min(tid)
            for r in rows:
                if r["template_id"] == tid:
                    r["buy_max_price"] = buy_max
                    r["sell_min_price"] = sell_min
        return rows

    def enrich_search_items(self, items):
        """对搜索结果的每个 template_id，补充求购价、在售价。"""
        for item in items:
            tid = item.get("template_id")
            if not tid:
                continue
            item["buy_max_price"] = self.get_buy_max(tid)
            item["sell_min_price"] = self.get_sell_min(tid)
        return items


def summarize_inventory(items):
    """把 UU 库存 items 转成表格行。"""
    rows = []
    for it in items:
        ti = it.get("TemplateInfo") or {}
        buy_price_raw = it.get("AssetBuyPrice") or ""
        if isinstance(buy_price_raw, str):
            buy_price_raw = buy_price_raw.replace("购￥", "").replace("￥", "")
        try:
            buy_price = float(buy_price_raw) if buy_price_raw else None
        except (TypeError, ValueError):
            buy_price = None
        rows.append({
            "assetid": it.get("SteamAssetId"),
            "template_id": ti.get("Id"),
            "name": ti.get("CommodityName") or "",
            "mark_price": ti.get("MarkPrice"),  # 参考价
            "buy_price": buy_price,              # 购入价
            "sell_min_price": None,              # enrich 后补充
            "buy_max_price": None,               # enrich 后补充
            "on_sale": it.get("AssetStatus", 0) != 0,  # 非 0 视为已上架/租赁等
            "tradable": it.get("Tradable", True),
        })
    return rows


# ==================== 自动交易 ====================

def _trade_config_path():
    return os.path.join(config_editor.PROJECT_ROOT, "config", "uu_trade.json")


def load_trade_config():
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
    return os.path.join(config_editor.PROJECT_ROOT, "config", "uu_scan_interval.json")


def load_scan_interval():
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
                        logger.info("[UU自动扫描] 开始扫描 %d 个饰品", len(config))
                        scan_and_trade(client, config, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                logger.error("[UU自动扫描] 异常: %s", e)

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

    决策逻辑（低买高卖，UU 语义）：
    - 市场在售最低价 <= 最高购入价 → 发求购单（buy）
    - 市场最高求购价 > 最低售价 → 上架（list_to_bidder，价格略低于求购价）
    - 市场最高求购价 < 最低售价 → 上架（list，价格=在售最低价-0.01，但不低于最低售价）
    """
    results = []
    for item in config:
        tid = item.get("template_id")
        name = item.get("name") or item.get("market_hash_name") or str(tid)
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
        sell_min = client.get_sell_min(tid)
        buy_max = client.get_buy_max(tid)

        # 决策
        decision = None
        action_price = None
        reason = ""
        if sell_min is not None and max_buy > 0 and sell_min <= max_buy and buy_count > 0:
            decision = "buy"
            action_price = sell_min
            reason = "在售最低价 %.2f <= 最高购入价 %.2f（剩余购入 %d）" % (sell_min, max_buy, buy_count)
        elif buy_max is not None and min_sell > 0 and buy_max > min_sell and sell_count > 0:
            decision = "list_to_bidder"
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
        allowed = str(tid) in LIVE_ALLOW
        if not dry_run and not allowed and decision:
            exec_msg = "未授权实盘（仅限白名单饰品）"
        elif not dry_run and decision == "buy":
            hash_name = item.get("market_hash_name") or name
            logger.info("[UU实盘] 发求购单 %s(template_id=%s) @ %s", name, tid, action_price)
            r = client.buy(tid, hash_name, name, action_price, 1)
            if r and r.get("code") == 0:
                executed = True
                item["buy_count"] = str(max(0, buy_count - 1))
                exec_msg = "已发求购单"
                logger.info("[UU实盘] 发求购单成功 %s", name)
            else:
                exec_msg = "发求购单失败: " + str((r or {}).get("msg") or (r or {}).get("error") or "")
                logger.error("[UU实盘] 发求购单失败 %s: %s", name, exec_msg)
        elif not dry_run and decision in ("list_to_bidder", "list"):
            assetid = client.find_assetid(tid)
            if assetid:
                logger.info("[UU实盘] 上架 %s(assetid=%s) @ %s", name, assetid, action_price)
                r = client.create_sell_order(assetid, action_price)
                if r.get("code") == 0:
                    executed = True
                    item["sell_count"] = str(max(0, sell_count - 1))
                    exec_msg = "已上架"
                    logger.info("[UU实盘] 上架成功 %s", name)
                else:
                    exec_msg = "上架失败: " + str(r.get("error") or r.get("msg") or "")
                    logger.error("[UU实盘] 上架失败 %s: %s", name, exec_msg)
            else:
                exec_msg = "无可用库存"
                logger.warning("[UU实盘] 无法上架 %s: %s", name, exec_msg)

        results.append({
            "template_id": tid,
            "name": name,
            "sell_min_price": sell_min,
            "buy_max_price": buy_max,
            "max_buy_price": max_buy,
            "min_sell_price": min_sell,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "decision": decision,
            "action_price": action_price,
            "reason": reason,
            "dry_run": dry_run,
            "executed": executed,
            "exec_msg": exec_msg,
        })
    return results

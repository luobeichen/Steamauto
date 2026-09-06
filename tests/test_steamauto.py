"""Steamauto 免鉴权改动的持久回归测试（标准库 unittest）。

用法：在项目根目录运行
    python -m unittest tests.test_steamauto -v

覆盖：OfflineSteamClient、发货人工确认、login_to_steam 解除 secret 强制、
manual_confirm_delivery 配置、日志改动（werkzeug 降级/默认 info/子进程日志走文件 tail）。
"""
import json5
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _isolated_cwd():
    """临时目录隔离 config/logs/session，返回临时目录路径。"""
    d = tempfile.mkdtemp(prefix="steamauto-test-")
    os.chdir(d)
    return d


class TestOfflineSteamClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
        from utils.steam_client import OfflineSteamClient
        cls.OfflineSteamClient = OfflineSteamClient

    def test_identity(self):
        oc = self.OfflineSteamClient("linux_user")
        self.assertEqual(oc.username, "linux_user")
        self.assertIsNone(oc.get_steam64id_from_cookies())
        self.assertFalse(oc.is_session_alive())

    def test_unknown_method_raises(self):
        import steampy.exceptions
        oc = self.OfflineSteamClient("u")
        with self.assertRaises(steampy.exceptions.LoginRequired):
            oc.accept_trade_offer("x")


class TestManualConfirmDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
        from utils import steam_client as sc
        from utils.steam_client import OfflineSteamClient
        cls.sc = sc
        cls.OfflineSteamClient = OfflineSteamClient

    def test_short_circuit(self):
        notifications = []
        orig = self.sc.send_notification
        self.sc.send_notification = lambda client, msg, title=None: notifications.append((msg, title))
        try:
            result = self.sc.accept_trade_offer(self.OfflineSteamClient("u"), threading.Lock(), "10001", desc="物品A")
            self.assertTrue(result)
            self.assertEqual(len(notifications), 1)
            self.assertIn("10001", notifications[0][0])
            self.assertEqual(notifications[0][1], "待人工确认发货")
        finally:
            self.sc.send_notification = orig


class TestSecretOptional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
        from utils import steam_client as sc
        cls.sc = sc
        # 构造「只有账号密码、secret 空」的账号文件
        cls._orig_account = sc.STEAM_ACCOUNT_INFO_FILE_PATH
        cls.account_path = os.path.join(cls._tmp, "steam_account_info.json5")
        sc.STEAM_ACCOUNT_INFO_FILE_PATH = cls.account_path
        with open(cls.account_path, "w", encoding="utf-8") as f:
            f.write('{"steam_username": "user", "steam_password": "pass", "shared_secret": "", "identity_secret": ""}')

    def test_empty_secret_not_rejected(self):
        sc = self.sc
        sc._check_proxy_availability = lambda config: False
        sc.pause = lambda: None
        errors = []
        orig_error = sc.logger.error
        sc.logger.error = lambda msg, *a, **k: errors.append(str(msg))
        try:
            result = sc.login_to_steam({})
        finally:
            sc.logger.error = orig_error
        # 返回 None 是因为代理检查失败（而非 secret 字段为空）
        self.assertIsNone(result)
        self.assertFalse(any("为空" in e for e in errors))


class TestLoggingChanges(unittest.TestCase):
    def test_werkzeug_warning(self):
        with open(os.path.join(SRC, "gui", "server.py"), encoding="utf-8") as f:
            self.assertIn('logging.getLogger("werkzeug").setLevel(logging.WARNING)', f.read())

    def test_default_log_level_info(self):
        from utils import static
        self.assertEqual(json5.loads(static.DEFAULT_CONFIG_JSON).get("log_level"), "info")

    def test_runner_devnull_no_window(self):
        from gui import runner
        orig = subprocess.Popen
        captured = {}
        def fake_popen(cmd, cwd=None, creationflags=0, stdout=None, stderr=None, env=None):
            captured["flags"] = creationflags
            captured["stdout"] = stdout
            captured["stderr"] = stderr

            class P:
                pid = 1

                @staticmethod
                def poll():
                    return None

            return P

        subprocess.Popen = fake_popen
        try:
            ok, _ = runner.start()
        finally:
            subprocess.Popen = orig
            runner._proc = None
        self.assertTrue(ok)
        self.assertEqual(captured.get("stdout"), subprocess.DEVNULL)
        self.assertEqual(captured.get("stderr"), subprocess.DEVNULL)
        self.assertEqual(captured.get("flags"), subprocess.CREATE_NO_WINDOW)

    def test_runner_env_no_pause(self):
        from gui import runner
        orig = subprocess.Popen
        captured = {}

        def fake_popen(cmd, cwd=None, creationflags=0, stdout=None, stderr=None, env=None):
            captured["env"] = env

            class P:
                pid = 1

                @staticmethod
                def poll():
                    return None

            return P

        subprocess.Popen = fake_popen
        try:
            ok, _ = runner.start()
        finally:
            subprocess.Popen = orig
            runner._proc = None
        self.assertTrue(ok)
        self.assertEqual(captured.get("env", {}).get("STEAMAUTO_NO_PAUSE"), "1")

    def test_log_level_api(self):
        import shutil
        from gui import config_editor, server

        tmp = tempfile.mkdtemp(prefix="steamauto-test-")
        orig_cfg = config_editor.CONFIG_FILE_PATH
        config_editor.CONFIG_FILE_PATH = os.path.join(tmp, "config", "config.json5")
        try:
            c = server.app.test_client()
            self.assertEqual(c.get("/api/log_level").get_json().get("level"), "info")
            r = c.post("/api/log_level", json={"level": "debug"})
            self.assertTrue(r.get_json().get("ok"))
            with open(config_editor.CONFIG_FILE_PATH, encoding="utf-8") as f:
                saved = json5.load(f)
            self.assertEqual(saved.get("log_level"), "debug")
            self.assertFalse(c.post("/api/log_level", json={"level": "hack"}).get_json().get("ok"))
        finally:
            config_editor.CONFIG_FILE_PATH = orig_cfg
            shutil.rmtree(tmp, ignore_errors=True)


class TestPluginCheckSkipsFailed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _isolated_cwd()
        import Steamauto
        cls.Steamauto = Steamauto

    def test_skips_failed_plugins(self):
        class OkPlugin:
            def init(self):
                return False

        class FailPlugin:
            def init(self):
                return True

        class CrashPlugin:
            def init(self):
                raise RuntimeError("boom")

        plugins = self.Steamauto.plugins_check([OkPlugin(), FailPlugin(), CrashPlugin()])
        self.assertEqual(len(plugins), 1)
        self.assertIsInstance(plugins[0], OkPlugin)

    def test_empty_returns_empty_list(self):
        self.assertEqual(self.Steamauto.plugins_check([]), [])


class TestGuiLoginOfflineAndShutdown(unittest.TestCase):
    def test_buff_skip_qrcode_gui_mode(self):
        with open(os.path.join(SRC, "utils", "buff_helper.py"), encoding="utf-8") as f:
            self.assertIn("STEAMAUTO_NO_PAUSE", f.read())

    def test_uu_skip_input_gui_mode(self):
        with open(os.path.join(SRC, "utils", "uu_helper.py"), encoding="utf-8") as f:
            self.assertIn("STEAMAUTO_NO_PAUSE", f.read())

    def test_shutdown_api(self):
        from gui import server
        routes = {str(r) for r in server.app.url_map.iter_rules()}
        self.assertIn("/api/shutdown", routes)
        with open(os.path.join(SRC, "gui", "static", "app.js"), encoding="utf-8") as f:
            self.assertIn("quitGui", f.read())
        with open(os.path.join(SRC, "gui", "templates", "index.html"), encoding="utf-8") as f:
            self.assertIn("btn-quit-gui", f.read())

    def test_buff_skip_binding_check_in_manual_confirm(self):
        """人工确认模式下，BUFF 插件跳过 Steam 账号与 BUFF 的绑定校验。"""
        with open(os.path.join(SRC, "plugins", "BuffAutoAcceptOffer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("static.manual_confirm_delivery", src)
        mc_idx = src.index("static.manual_confirm_delivery")
        bind_idx = src.index('steam_info["max_bind_count"]')
        self.assertLess(mc_idx, bind_idx)

    def test_uu_sms_prompt_bridged(self):
        """UU 短信发送提示应作为 input prompt 桥接到 GUI。"""
        with open(os.path.join(SRC, "utils", "uu_helper.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('input("请编辑发送短信 "', src)

    def test_config_platform_groups(self):
        """配置表格按平台分组，出租/出售分独立板块，Steam 独立 tab。"""
        from gui import config_schema
        groups = [g["group"] for g in config_schema.get_table_data({})]
        self.assertEqual(groups, [
            "Steam", "通用配置", "BUFF", "悠悠有品", "悠悠有品 · 出租", "悠悠有品 · 出售",
            "ECOSteam", "ECOSteam · 出售同步", "ECOSteam · 出租同步", "C5",
        ])


class TestLoginRefresh(unittest.TestCase):
    def test_refresh_detects_cache(self):
        import shutil
        from gui import config_editor, login

        tmp = tempfile.mkdtemp(prefix="steamauto-test-")
        orig_root = config_editor.PROJECT_ROOT
        orig_account = config_editor.ACCOUNT_FILE_PATH
        config_editor.PROJECT_ROOT = tmp
        config_editor.ACCOUNT_FILE_PATH = os.path.join(tmp, "config", "steam_account_info.json5")
        os.makedirs(os.path.join(tmp, "config"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "session"), exist_ok=True)
        try:
            with open(config_editor.ACCOUNT_FILE_PATH, "w", encoding="utf-8") as f:
                f.write('{"steam_username": "testuser"}')
            with open(os.path.join(tmp, "session", "steam_account_testuser.json"), "w", encoding="utf-8") as f:
                f.write('{"access_token": "tok"}')
            with open(os.path.join(tmp, "config", "buff_cookies_testuser.txt"), "w", encoding="utf-8") as f:
                f.write("session=abc")
            with open(os.path.join(tmp, "config", "uu_token_testuser.txt"), "w", encoding="utf-8") as f:
                f.write("token123")
            login.refresh_login_status()
            state = login.get_state()
            self.assertEqual(state["steam"]["status"], "success")
            self.assertEqual(state["buff"]["status"], "success")
            self.assertEqual(state["uu"]["status"], "success")
        finally:
            config_editor.PROJECT_ROOT = orig_root
            config_editor.ACCOUNT_FILE_PATH = orig_account
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refresh_api_route(self):
        from gui import server
        routes = {str(r) for r in server.app.url_map.iter_rules()}
        self.assertIn("/api/login/refresh", routes)

    def test_config_tabs_frontend(self):
        with open(os.path.join(SRC, "gui", "templates", "index.html"), encoding="utf-8") as f:
            self.assertIn("config-group-tabs", f.read())
        with open(os.path.join(SRC, "gui", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("switchConfigTab", js)
        self.assertIn("split(' · ')", js)


class TestRealOrder(unittest.TestCase):
    def _mock_client(self, sell_min, buy_max):
        class MC:
            def __init__(self):
                self.buy_calls = []
                self.sell_calls = []

            def get_sell_order(self, gid, page_num=1, page_size=10):
                return {"code": "OK", "data": {"items": [{"price": str(sell_min), "id": "sell1"}]}}

            def get_buy_order_max(self, gid):
                return buy_max

            def buy(self, gid, sell_order_id, price, pay_method="buff-bankcard", game="csgo"):
                self.buy_calls.append((gid, price))
                return {"code": "OK"}

            def create_sell_order(self, assetid, price, steamid, game="csgo", mode="manual"):
                self.sell_calls.append((assetid, price))
                return {"code": "OK"}

            def find_assetid(self, gid):
                return "asset_123"

            def get_steamid(self):
                return "76561198327946298"

        return MC()

    def _scan(self, client, config, dry_run):
        from gui import buff
        with mock.patch.object(buff, "save_trade_config"), \
                mock.patch.object(buff, "LIVE_ALLOW", {"1"}):
            return buff.scan_and_trade(client, config, dry_run=dry_run)

    def test_scan_executes_buy_and_decrements(self):
        c = self._mock_client(5.0, 4.0)
        cfg = [{"goods_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, False)[0]
        self.assertEqual(r["decision"], "buy")
        self.assertTrue(r["executed"])
        self.assertEqual(len(c.buy_calls), 1)
        self.assertEqual(cfg[0]["buy_count"], "1")  # 2 -> 1

    def test_scan_executes_sell_and_decrements(self):
        c = self._mock_client(10.0, 8.0)  # 求购 8 > 最低售价 7 → sell_to_bidder
        cfg = [{"goods_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, False)[0]
        self.assertEqual(r["decision"], "sell_to_bidder")
        self.assertTrue(r["executed"])
        self.assertEqual(len(c.sell_calls), 1)
        self.assertEqual(cfg[0]["sell_count"], "0")  # 1 -> 0

    def test_dry_run_does_not_execute(self):
        c = self._mock_client(5.0, 4.0)
        cfg = [{"goods_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, True)[0]
        self.assertEqual(r["decision"], "buy")
        self.assertFalse(r["executed"])
        self.assertEqual(len(c.buy_calls), 0)
        self.assertEqual(cfg[0]["buy_count"], "2")  # 不减

    def test_buy_request_construction(self):
        from gui import buff
        c = mock.MagicMock()
        c.session.cookies.get.return_value = "csrf123"
        buff.BuffClient.buy(c, "34250", "sell_order_1", "5.0")
        args, kwargs = c.session.post.call_args
        self.assertIn("/api/market/goods/buy", args[0])
        self.assertEqual(kwargs["json"]["goods_id"], "34250")
        self.assertEqual(kwargs["json"]["sell_order_id"], "sell_order_1")
        self.assertEqual(kwargs["json"]["pay_method"], 1)  # buff-bankcard

    def test_create_sell_order_request(self):
        from gui import buff
        c = mock.MagicMock()
        buff.BuffClient.create_sell_order(c, "asset_1", "9.9", "steamid_1")
        args = c._post.call_args[0]
        self.assertEqual(args[0], "/api/market/sell_order/create/manual")
        self.assertEqual(args[1]["assets"], [{"assetid": "asset_1", "price": "9.9"}])
        self.assertEqual(args[1]["steamid"], "steamid_1")


class TestUURealOrder(unittest.TestCase):
    def _mock_client(self, sell_min, buy_max):
        class MC:
            def __init__(self):
                self.buy_calls = []
                self.sell_calls = []

            def get_sell_min(self, tid):
                return sell_min

            def get_buy_max(self, tid):
                return buy_max

            def buy(self, tid, hash_name, name, price, num=1):
                self.buy_calls.append((tid, price))
                return {"code": 0}

            def create_sell_order(self, assetid, price, steamid=None, game="csgo", mode="manual"):
                self.sell_calls.append((assetid, price))
                return {"code": 0}

            def find_assetid(self, tid):
                return "asset_123"

        return MC()

    def _scan(self, client, config, dry_run):
        from gui import uu
        with mock.patch.object(uu, "save_trade_config"), \
                mock.patch.object(uu, "LIVE_ALLOW", {"1"}):
            return uu.scan_and_trade(client, config, dry_run=dry_run)

    def test_scan_executes_buy_and_decrements(self):
        c = self._mock_client(5.0, 4.0)
        cfg = [{"template_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, False)[0]
        self.assertEqual(r["decision"], "buy")
        self.assertTrue(r["executed"])
        self.assertEqual(len(c.buy_calls), 1)
        self.assertEqual(cfg[0]["buy_count"], "1")  # 2 -> 1

    def test_scan_executes_sell_and_decrements(self):
        c = self._mock_client(10.0, 8.0)  # 求购 8 > 最低售价 7 → list_to_bidder
        cfg = [{"template_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, False)[0]
        self.assertEqual(r["decision"], "list_to_bidder")
        self.assertTrue(r["executed"])
        self.assertEqual(len(c.sell_calls), 1)
        self.assertEqual(cfg[0]["sell_count"], "0")  # 1 -> 0

    def test_dry_run_does_not_execute(self):
        c = self._mock_client(5.0, 4.0)
        cfg = [{"template_id": 1, "name": "x", "max_buy_price": "6", "min_sell_price": "7", "buy_count": "2", "sell_count": "1"}]
        r = self._scan(c, cfg, True)[0]
        self.assertEqual(r["decision"], "buy")
        self.assertFalse(r["executed"])
        self.assertEqual(len(c.buy_calls), 0)
        self.assertEqual(cfg[0]["buy_count"], "2")  # 不减

    def test_summarize_inventory(self):
        from gui import uu
        items = [{
            "SteamAssetId": 123,
            "TemplateInfo": {"Id": 109666, "CommodityName": "AK-47 | 红线", "MarkPrice": 3.5},
            "AssetBuyPrice": "购￥3.50",
            "Tradable": True,
            "AssetStatus": 0,
        }]
        rows = uu.summarize_inventory(items)
        self.assertEqual(rows[0]["template_id"], 109666)
        self.assertEqual(rows[0]["name"], "AK-47 | 红线")
        self.assertEqual(rows[0]["buy_price"], 3.5)
        self.assertFalse(rows[0]["on_sale"])

    def test_uu_routes(self):
        from gui import server
        routes = {str(r) for r in server.app.url_map.iter_rules()}
        for route in ("/api/uu/inventory", "/api/uu/search", "/api/uu/trade/config",
                      "/api/uu/trade/scan", "/api/uu/trade/interval", "/api/uu/deal_price"):
            self.assertIn(route, routes)


if __name__ == "__main__":
    unittest.main(verbosity=2)

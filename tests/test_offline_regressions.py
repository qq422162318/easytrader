# -*- coding: utf-8 -*-
"""Regression tests that never connect to a broker, desktop UI, or network."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from easytrader import api, clienttrader, exceptions, grid_data_get_strategy
from easytrader import remoteclient, server
from easytrader.xq_follower import XueQiuFollower
from easytrader.xqtrader import XueQiuTrader


class FakeEdit:
    def __init__(self, value):
        self.value = value
        self.keys = []

    def click_input(self):
        pass

    def type_keys(self, keys, **kwargs):
        self.keys.append(keys)
        if keys == "^a{BACKSPACE}":
            self.value = ""
        else:
            self.value += keys


class TestTraderRegressions(unittest.TestCase):
    def test_debug_false_still_creates_each_supported_broker(self):
        with patch.object(api.log, "setLevel"):
            for broker in ("xq", "ths", "yh_client", "ht_client", "gj_client"):
                with self.subTest(broker=broker):
                    self.assertIsNotNone(api.use(broker, debug=False))

    def test_trade_inputs_replace_previous_values(self):
        trader = clienttrader.ClientTrader()
        edit = FakeEdit("100")
        trader._main = SimpleNamespace(window=Mock(return_value=edit))
        trader.wait = Mock()
        trader._type_keys(1034, "200")
        self.assertEqual("200", edit.value)
        trader._type_keys(1034, "50")
        self.assertEqual("50", edit.value)
        self.assertEqual(["^a{BACKSPACE}", "200", "^a{BACKSPACE}", "50"], edit.keys)

    def test_buy_and_sell_submit_the_requested_limit_price(self):
        trader = clienttrader.ClientTrader()
        controls = {1032: FakeEdit("600000"), 1033: FakeEdit("0.65"),
                    1034: FakeEdit("100")}
        trader._main = SimpleNamespace(
            window=Mock(side_effect=lambda control_id, **kwargs: controls[control_id])
        )
        trader.wait = Mock()
        trader._switch_left_menus = Mock()
        trader._submit_trade = Mock()
        trader._handle_pop_dialogs = Mock(return_value={"entrust_no": "mock"})
        for action, price, amount in (("buy", 0.55, 200), ("sell", 0.45, 100)):
            with self.subTest(action=action):
                result = getattr(trader, action)("sh162411", price, amount)
                self.assertEqual({"entrust_no": "mock"}, result)
                self.assertEqual("162411", controls[1032].value)
                self.assertEqual(str(price), controls[1033].value)
                self.assertEqual(str(amount), controls[1034].value)
        self.assertEqual(2, trader._submit_trade.call_count)

    def test_market_type_uses_combo_item_indices(self):
        trader = clienttrader.ClientTrader()
        combo = Mock()
        combo.texts.return_value = ["当前选项", "对手方最优价格", "本方最优价格"]
        trader._main = SimpleNamespace(window=Mock(return_value=combo))
        trader._set_market_trade_type("本方最优价格")
        combo.select.assert_called_once_with(1)
        trader._main.window.assert_called_once_with(
            control_id=trader.config.TRADE_MARKET_TYPE_CONTROL_ID,
            class_name="ComboBox",
        )

    def test_unknown_market_type_is_not_submitted(self):
        trader = clienttrader.ClientTrader()
        combo = Mock()
        combo.texts.return_value = ["当前选项", "对手方最优价格"]
        trader._main = SimpleNamespace(window=Mock(return_value=combo))
        with self.assertRaisesRegex(TypeError, "不支持对应的市价类型"):
            trader._set_market_trade_type("不存在的类型")
        trader._main.window.assert_called_once_with(
            control_id=trader.config.TRADE_MARKET_TYPE_CONTROL_ID,
            class_name="ComboBox",
        )
        combo.select.assert_not_called()


class TestGridRegressions(unittest.TestCase):
    def setUp(self):
        self.grid = Mock()
        self.trader = SimpleNamespace(
            main=Mock(), app=SimpleNamespace(process=123),
            config=SimpleNamespace(GRID_DTYPE={"证券代码": str}),
        )
        self.strategy = grid_data_get_strategy.CopyStrategy(self.trader)
        self.strategy._get_grid = Mock(return_value=self.grid)

    def test_blocked_copy_does_not_return_previous_table(self):
        clipboard = {"text": "证券代码\t数量\n162411\t100\n"}

        def clear():
            clipboard["text"] = ""

        with patch.object(grid_data_get_strategy.pywinauto.clipboard,
                          "EmptyClipboard", side_effect=clear) as empty, \
                patch.object(grid_data_get_strategy.pywinauto.clipboard,
                             "GetData", side_effect=lambda: clipboard["text"]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs", return_value=[{}]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "auto_fill_captcha", return_value=False), \
                patch.object(grid_data_get_strategy.time, "sleep"):
            with self.assertRaises(exceptions.GridDataError):
                self.strategy.get(1047)
        empty.assert_called_once_with()
        self.grid.type_keys.assert_called_once_with("^A^C")

    def test_new_copy_replaces_previous_clipboard_table(self):
        clipboard = {"text": "证券代码\t数量\n600000\t999\n"}
        events = []

        def clear():
            events.append("clear")
            clipboard["text"] = ""

        def copy(keys):
            events.append("copy")
            clipboard["text"] = "证券代码\t数量\n162411\t100\n"

        self.grid.type_keys.side_effect = copy
        with patch.object(grid_data_get_strategy.pywinauto.clipboard,
                          "EmptyClipboard", side_effect=clear), \
                patch.object(grid_data_get_strategy.pywinauto.clipboard,
                             "GetData", side_effect=lambda: clipboard["text"]), \
                patch.object(grid_data_get_strategy.time, "sleep"):
            records = self.strategy.get(1047)
        self.assertEqual(["clear", "copy"], events)
        self.assertEqual([{"证券代码": "162411", "数量": 100}], records)

    def test_captcha_confirm_resumes_original_copy_without_a_second_copy(self):
        clipboard = {"text": "old table", "captcha": True}
        new_table = "证券代码\t数量\n162411\t100\n"

        def clear():
            clipboard["text"] = ""

        def accept(app):
            clipboard["captcha"] = False
            clipboard["text"] = new_table
            return True

        with patch.object(grid_data_get_strategy.pywinauto.clipboard,
                          "EmptyClipboard", side_effect=clear) as empty, \
                patch.object(grid_data_get_strategy.pywinauto.clipboard,
                             "GetData", side_effect=lambda: clipboard["text"]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs",
                             side_effect=lambda app: [{}] if clipboard["captcha"] else []), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "auto_fill_captcha", side_effect=accept), \
                patch.object(grid_data_get_strategy.time, "sleep"):
            records = self.strategy.get(1047)
        self.assertEqual([{"证券代码": "162411", "数量": 100}], records)
        empty.assert_called_once_with()
        self.grid.type_keys.assert_called_once_with("^A^C")

    def test_no_recopy_when_captcha_attempt_returns_true_but_dialog_remains(self):
        with patch.object(self.strategy, "_copy_grid", return_value="") as copy, \
                patch.object(self.strategy, "_get_clipboard_data",
                             side_effect=exceptions.GridDataError("still blocked")), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs", return_value=[{}]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "auto_fill_captcha", return_value=True), \
                patch.object(grid_data_get_strategy.time, "sleep"):
            with self.assertRaises(exceptions.GridDataError):
                self.strategy.get(1047)
        copy.assert_called_once_with(self.grid)

    def test_recopy_once_if_confirmed_client_did_not_resume_clipboard_copy(self):
        content = "证券代码\t数量\n162411\t100\n"
        with patch.object(self.strategy, "_copy_grid", side_effect=["", content]) as copy, \
                patch.object(self.strategy, "_get_clipboard_data",
                             side_effect=exceptions.GridDataError("no resumed copy")), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs", return_value=[]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "auto_fill_captcha", return_value=True), \
                patch.object(grid_data_get_strategy.time, "sleep"):
            records = self.strategy.get(1047)
        self.assertEqual([{"证券代码": "162411", "数量": 100}], records)
        self.assertEqual(2, copy.call_count)

    def test_clipboard_errors_have_a_bounded_delayed_retry(self):
        with patch.object(grid_data_get_strategy.pywinauto.clipboard, "GetData",
                          side_effect=RuntimeError("clipboard locked")) as get, \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs", return_value=[]), \
                patch.object(grid_data_get_strategy.time, "sleep") as sleep:
            with self.assertRaises(exceptions.GridDataError):
                self.strategy._get_clipboard_data()
        self.assertEqual(10, get.call_count)
        self.assertEqual(9, sleep.call_count)

    def test_delayed_copy_waits_for_nonempty_text(self):
        content = "证券代码\t数量\n162411\t100\n"
        with patch.object(grid_data_get_strategy.pywinauto.clipboard,
                          "GetData", side_effect=["", content]), \
                patch.object(grid_data_get_strategy.captcha_handler,
                             "discover_captcha_dialogs", return_value=[]), \
                patch.object(grid_data_get_strategy.time, "sleep") as sleep:
            self.assertEqual(content, self.strategy._get_clipboard_data())
        sleep.assert_called_once_with(0.1)


class TestRemoteRegressions(unittest.TestCase):
    def setUp(self):
        self.client = remoteclient.RemoteClient("ht_client", "unused.invalid")
        self.client._s = Mock()
        self.client._s.post.return_value = SimpleNamespace(
            status_code=201, json=Mock(return_value={"msg": "mock"})
        )

    def test_prepare_sends_all_local_account_fields_without_local_path(self):
        account = {"user": "mock-user", "password": "mock-password",
                   "comm_password": "mock-comm", "exe_path": "mock.exe"}
        with patch.object(remoteclient.helpers, "file2dict", return_value=account):
            self.client.prepare("local-only.json", timeout=5)
        payload = self.client._s.post.call_args.kwargs["json"]
        self.assertEqual(dict(account, broker="ht_client", timeout=5), payload)
        self.assertNotIn("kwargs", payload)
        self.assertNotIn("config_path", payload)

    def test_prepare_supports_cookie_accounts_without_user_password_fields(self):
        self.client._broker = "xq"
        account = {"cookies": "mock-cookie=1", "portfolio_code": "ZHMOCK"}
        with patch.object(remoteclient.helpers, "file2dict", return_value=account):
            self.client.prepare("local-xq.json")
        payload = self.client._s.post.call_args.kwargs["json"]
        self.assertEqual("mock-cookie=1", payload["cookies"])
        self.assertEqual("ZHMOCK", payload["portfolio_code"])
        self.assertNotIn("config_path", payload)

    def test_buy_and_sell_flatten_optional_arguments(self):
        for action in ("buy", "sell"):
            with self.subTest(action=action):
                getattr(self.client, action)("162411", 0.55, 100,
                                             entrust_prop="market", volume=55)
                self.client._s.post.assert_called_with(
                    self.client._api + "/" + action,
                    json={"security": "162411", "price": 0.55, "amount": 100,
                          "entrust_prop": "market", "volume": 55},
                )

    def test_server_prepare_uses_login_for_login_capable_brokers(self):
        for broker in ("ht_client", "yh_client", "xq"):
            with self.subTest(broker=broker):
                trader = SimpleNamespace(prepare=Mock(), connect=Mock())
                with patch.object(server.api, "use", return_value=trader), \
                        patch.dict(server.global_store, {}, clear=True):
                    response = server.app.test_client().post(
                        "/prepare", json={"broker": broker, "user": "mock-user"}
                    )
                    self.assertEqual(201, response.status_code)
                    trader.prepare.assert_called_once_with(user="mock-user")
                    trader.connect.assert_not_called()
                    self.assertIs(trader, server.global_store["user"])

    def test_server_prepare_connects_generic_ths_without_login(self):
        trader = SimpleNamespace(connect=Mock())
        with patch.object(server.api, "use", return_value=trader), \
                patch.dict(server.global_store, {}, clear=True):
            response = server.app.test_client().post(
                "/prepare", json={"broker": "ths", "exe_path": "mock.exe"}
            )
            self.assertEqual(201, response.status_code)
            trader.connect.assert_called_once_with(exe_path="mock.exe")


class TestFollowerRegressions(unittest.TestCase):
    def test_adjust_sell_does_not_adjust_buy_transactions(self):
        follower = XueQiuFollower()
        follower._adjust_sell = True
        follower._adjust_sell_amount = Mock(return_value=200)
        transactions = [
            {"weight": 10, "prev_weight": 0, "price": 10,
             "created_at": 0, "stock_symbol": "SZ000001"},
            {"weight": 0, "prev_weight": 10, "price": 10,
             "created_at": 0, "stock_symbol": "SZ000002"},
        ]
        follower.project_transactions(transactions, assets=100000)
        self.assertEqual(1000, transactions[0]["amount"])
        self.assertEqual(200, transactions[1]["amount"])
        follower._adjust_sell_amount.assert_called_once_with("sz000002", 1000)

    def test_follow_starts_all_strategies_with_optional_scalar_assets(self):
        for total_assets in ([10000, 20000], 10000):
            with self.subTest(total_assets=total_assets):
                follower = XueQiuFollower()
                follower.start_trader_thread = Mock()
                follower.extract_strategy_name = Mock(side_effect=lambda code: code)
                with patch("easytrader.xq_follower.Thread") as thread:
                    follower.follow(Mock(), ["ZH1", "ZH2"],
                                    total_assets=total_assets, cmd_cache=False)
                self.assertEqual(2, thread.call_count)
                self.assertEqual(["ZH1", "ZH2"],
                                 [call.kwargs["args"][0] for call in thread.call_args_list])
                expected = total_assets if isinstance(total_assets, list) else [10000, 10000]
                self.assertEqual(expected,
                                 [call.kwargs["kwargs"]["assets"]
                                  for call in thread.call_args_list])

    def test_asset_list_length_mismatch_fails_before_threads_start(self):
        follower = XueQiuFollower()
        follower.start_trader_thread = Mock()
        with patch("easytrader.xq_follower.Thread") as thread:
            with self.assertRaises(ValueError):
                follower.follow(Mock(), ["ZH1", "ZH2"],
                                total_assets=[10000], cmd_cache=False)
        follower.start_trader_thread.assert_not_called()
        thread.assert_not_called()


class TestXueQiuTradeRegressions(unittest.TestCase):
    def test_invalid_values_are_rejected_before_any_account_or_network_access(self):
        trader = XueQiuTrader()
        trader._search_stock_info = Mock()
        trader.get_balance = Mock()
        trader.s = Mock()
        cases = [("price", -1), ("amount", -100), ("volume", -1000),
                 ("price", None), ("price", "bad"),
                 ("price", float("nan")), ("amount", float("inf")),
                 ("volume", float("nan")), ("volume", float("inf"))]
        for action in ("buy", "sell"):
            for key, value in cases:
                with self.subTest(action=action, key=key, value=value):
                    kwargs = {"price": 10, "amount": 100, "volume": 0}
                    kwargs[key] = value
                    with self.assertRaises(exceptions.TradeError):
                        getattr(trader, action)("000001", **kwargs)
        with self.assertRaises(exceptions.TradeError):
            trader.buy("000001", price=1e308, amount=1e308)
        trader._search_stock_info.assert_not_called()
        trader.get_balance.assert_not_called()
        trader.s.post.assert_not_called()

    def test_valid_buy_and_sell_keep_their_direction(self):
        trader = XueQiuTrader()
        trader.account_config = {"portfolio_code": "ZHMOCK"}
        trader._search_stock_info = Mock(return_value={
            "stock_id": 1, "flag": 1, "name": "Mock stock",
        })
        trader.get_balance = Mock(return_value=[{
            "asset_balance": 10000, "current_balance": 5000,
        }])
        trader._get_position = Mock(side_effect=lambda: [{"stock_id": 1, "weight": 20}])
        trader.s = Mock()
        trader.s.post.return_value = SimpleNamespace(
            status_code=200,
            text=json.dumps({"id": "mock", "created_at": 0, "updated_at": 0}),
        )
        for action, weight, cash in (("buy", 30, 40), ("sell", 10, 60)):
            with self.subTest(action=action):
                result = getattr(trader, action)("000001", price="10", amount=100)
                self.assertEqual("mock", result[0]["entrust_no"])
                data = trader.s.post.call_args.kwargs["data"]
                self.assertEqual(weight, json.loads(data["holdings"])[0]["weight"])
                self.assertEqual(cash, data["cash"])


if __name__ == "__main__":
    unittest.main()

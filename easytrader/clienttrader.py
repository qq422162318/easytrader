# -*- coding: utf-8 -*-
import abc
import functools
import os
import sys
import time

from . import captcha_handler
from . import grid_data_get_strategy
from . import helpers
from . import pop_dialog_handler
from .config import client

if not sys.platform.startswith("darwin"):
    import pywinauto
    import pywinauto.clipboard


class IClientTrader(abc.ABC):
    @property
    @abc.abstractmethod
    def app(self):
        """Return current app instance"""
        pass

    @property
    @abc.abstractmethod
    def main(self):
        """Return current main window instance"""
        pass

    @property
    @abc.abstractmethod
    def config(self):
        """Return current config instance"""
        pass

    @abc.abstractmethod
    def wait(self, seconds):
        """Wait for operation return"""
        pass

    @property
    @abc.abstractmethod
    def grid_data_get_strategy(self):
        """
        :return: Implement class of IGridDataGetStrategy
        :rtype: grid_data.get_strategy.IGridDataGetStrategy
        """
        pass

    @grid_data_get_strategy.setter
    @abc.abstractmethod
    def grid_data_get_strategy(self, strategy_cls):
        """
        :param strategy_cls: Grid data get strategy
        :type strategy_cls: grid_data.get_strategy.IGridDataGetStrategy
        :return: formatted grid data
        :rtype: list[dict]
        """
        pass


class ClientTrader(IClientTrader):
    def __init__(self):
        self._config = client.create(self.broker_type)
        self._app = None
        self._main = None
        self.grid_data_get_strategy = grid_data_get_strategy.CopyStrategy

    @property
    def app(self):
        return self._app

    @property
    def main(self):
        return self._main

    @property
    def config(self):
        return self._config

    @property
    def grid_data_get_strategy(self):
        return self._grid_data_get_strategy

    @grid_data_get_strategy.setter
    def grid_data_get_strategy(self, strategy_cls):
        if not issubclass(
            strategy_cls, grid_data_get_strategy.IGridDataGetStrategy
        ):
            raise TypeError(
                "Strategy must be implement class of IGridDataGetStrategy"
            )
        self._grid_data_get_strategy = strategy_cls(self)

    def connect(self, exe_path=None, **kwargs):
        """
        直接连接登陆后的客户端
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :return:
        """
        connect_path = exe_path or self._config.DEFAULT_EXE_PATH
        if connect_path is None:
            raise ValueError(
                "参数 exe_path 未设置，请设置客户端对应的 exe 地址,类似 C:\\客户端安装目录\\xiadan.exe"
            )

        self._app = pywinauto.Application().connect(
            path=connect_path, timeout=10
        )
        self._close_prompt_windows()
        try:
            self._main = self._app.window(title=self._config.TITLE)
            self._main.wait("exists", timeout=5)
        except Exception:
            # 兼容标题不一致的客户端：回退到最顶层窗口
            self._main = self._app.top_window()

    @property
    def broker_type(self):
        return "ths"

    @property
    def balance(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_balance_from_statics()

    def _get_balance_from_statics(self):
        result = {}
        for key, control_id in self._config.BALANCE_CONTROL_ID_GROUP.items():
            result[key] = float(
                self._main.window(
                    control_id=control_id, class_name="Static"
                ).window_text()
            )
        return result

    @property
    def position(self):
        self._switch_left_menus(["查询[F4]", "资金股票"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_entrusts(self):
        self._switch_left_menus(["查询[F4]", "当日委托"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def today_trades(self):
        self._switch_left_menus(["查询[F4]", "当日成交"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    @property
    def cancel_entrusts(self):
        self._refresh()
        self._switch_left_menus(["撤单[F3]"])

        return self._get_grid_data(self._config.COMMON_GRID_CONTROL_ID)

    def cancel_entrust(self, entrust_no):
        self._refresh()
        for i, entrust in enumerate(self.cancel_entrusts):
            if (
                entrust[self._config.CANCEL_ENTRUST_ENTRUST_FIELD]
                == entrust_no
            ):
                self._cancel_entrust_by_double_click(i)
                return self._handle_pop_dialogs()
        else:
            return {"message": "委托单状态错误不能撤单, 该委托单可能已经成交或者已撤"}

    def buy(self, security, price, amount, **kwargs):
        self._switch_left_menus(["买入[F1]"])

        return self.trade(security, price, amount)

    def sell(self, security, price, amount, **kwargs):
        self._switch_left_menus(["卖出[F2]"])

        return self.trade(security, price, amount)

    def market_buy(self, security, amount, ttype=None, **kwargs):
        """
        市价买入
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']

        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "买入"])

        return self.market_trade(security, amount, ttype)

    def market_sell(self, security, amount, ttype=None, **kwargs):
        """
        市价卖出
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']

        :return: {'entrust_no': '委托单号'}
        """
        self._switch_left_menus(["市价委托", "卖出"])

        return self.market_trade(security, amount, ttype)

    def market_trade(self, security, amount, ttype=None, **kwargs):
        """
        市价交易
        :param security: 六位证券代码
        :param amount: 交易数量
        :param ttype: 市价委托类型，默认客户端默认选择，
                     深市可选 ['对手方最优价格', '本方最优价格', '即时成交剩余撤销', '最优五档即时成交剩余 '全额成交或撤销']
                     沪市可选 ['最优五档成交剩余撤销', '最优五档成交剩余转限价']

        :return: {'entrust_no': '委托单号'}
        """
        self._set_market_trade_params(security, amount)
        if ttype is not None:
            self._set_market_trade_type(ttype)
        self._submit_trade()

        return self._handle_pop_dialogs(
            handler_class=pop_dialog_handler.TradePopDialogHandler
        )

    def _set_market_trade_type(self, ttype):
        """根据选择的市价交易类型选择对应的下拉选项"""
        selects = self._main.window(
            control_id=self._config.TRADE_MARKET_TYPE_CONTROL_ID,
            class_name="ComboBox",
        )
        for i, text in enumerate(selects.texts()):
            # skip 0 index, because 0 index is current select index
            if i == 0:
                continue
            if ttype in text:
                selects.select(i - 1)
                break
        else:
            raise TypeError("不支持对应的市价类型: {}".format(ttype))

    def _is_exist_pop_dialog(self):
        self.wait(0.2)  # wait dialog display
        return (
            self._main.wrapper_object()
            != self._app.top_window().wrapper_object()
        )

    def _run_exe_path(self, exe_path):
        return os.path.join(os.path.dirname(exe_path), "xiadan.exe")

    def wait(self, seconds):
        time.sleep(seconds)

    def exit(self):
        self._app.kill()

    def _close_prompt_windows(self):
        self.wait(1)
        for w in self._app.windows(class_name="#32770"):
            if w.window_text() != self._config.TITLE:
                w.close()
        self.wait(1)

    def trade(self, security, price, amount):
        self._set_trade_params(security, price, amount)

        self._submit_trade()

        return self._handle_pop_dialogs(
            handler_class=pop_dialog_handler.TradePopDialogHandler
        )

    def _click(self, control_id):
        self._app.top_window().window(
            control_id=control_id, class_name="Button"
        ).click()

    def _submit_trade(self):
        time.sleep(0.05)
        self._main.window(
            control_id=self._config.TRADE_SUBMIT_CONTROL_ID,
            class_name="Button",
        ).click()

    def _get_pop_dialog_title(self):
        return (
            self._app.top_window()
            .window(control_id=self._config.POP_DIALOD_TITLE_CONTROL_ID)
            .window_text()
        )

    def _set_trade_params(self, security, price, amount):
        code = security[-6:]

        self._type_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)

        # 等待客户端带出行情后，再写入调用者指定的限价。
        self.wait(0.3)
        self._type_keys(self._config.TRADE_PRICE_CONTROL_ID, str(price))
        self._type_keys(self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount)))

    def _set_market_trade_params(self, security, amount):
        code = security[-6:]

        self._type_keys(self._config.TRADE_SECURITY_CONTROL_ID, code)

        # wait security input finish
        self.wait(0.1)

        self._type_keys(self._config.TRADE_AMOUNT_CONTROL_ID, str(int(amount)))

    def _get_grid_data(self, control_id):
        # 所有 grid 拷贝读取(持仓/委托/成交/撤单)前后都检测验证码弹窗:
        # - 前置: 清掉上次拷贝残留的弹窗, 避免本次拷贝被挡;
        # - 后置: 本次拷贝可能又触发新弹窗(反拷贝保护), 立即填掉不留残留。
        #   try/finally 保证读取成功或抛异常都会执行后置检测。
        captcha_handler.auto_fill_captcha(self.app)
        try:
            return self._grid_data_get_strategy.get(control_id)
        finally:
            captcha_handler.auto_fill_captcha(self.app)

    def _type_keys(self, control_id, text):
        # 交易客户端的输入框是 MFC 自绘控件，set_edit_text/WM_SETTEXT 不会
        # 触发它的 EN_CHANGE 校验（数量、可买数量都不会刷新），必须模拟真实
        # 键击：先点击聚焦，等待客户端响应，再逐字输入。
        handle = self._main.window(control_id=control_id, class_name="Edit")
        handle.click_input()
        self.wait(0.3)
        handle.type_keys("^a{BACKSPACE}")
        handle.type_keys(str(text), with_spaces=False)

    def _switch_left_menus(self, path, sleep=0.2):
        self._get_left_menus_handle().get_item(path).click()
        self.wait(sleep)

    def _switch_left_menus_by_shortcut(self, shortcut, sleep=0.5):
        self._app.top_window().type_keys(shortcut)
        self.wait(sleep)

    @functools.lru_cache()
    def _get_left_menus_handle(self):
        while True:
            try:
                handle = self._main.window(
                    control_id=129, class_name="SysTreeView32"
                )
                # sometime can't find handle ready, must retry
                handle.wait("ready", 2)
                return handle
            except:
                pass

    def _cancel_entrust_by_double_click(self, row):
        x = self._config.CANCEL_ENTRUST_GRID_LEFT_MARGIN
        y = (
            self._config.CANCEL_ENTRUST_GRID_FIRST_ROW_HEIGHT
            + self._config.CANCEL_ENTRUST_GRID_ROW_HEIGHT * row
        )
        self._app.top_window().window(
            control_id=self._config.COMMON_GRID_CONTROL_ID,
            class_name="CVirtualGridCtrl",
        ).double_click(coords=(x, y))

    def _refresh(self):
        self._switch_left_menus(["买入[F1]"], sleep=0.05)

    def _handle_pop_dialogs(
        self, handler_class=pop_dialog_handler.PopDialogHandler
    ):
        handler = handler_class(self._app)

        while self._is_exist_pop_dialog():
            title = self._get_pop_dialog_title()

            result = handler.handle(title)
            if result:
                return result
        return {"message": "success"}


class BaseLoginClientTrader(ClientTrader):
    @abc.abstractmethod
    def login(self, user, password, exe_path, comm_password=None, **kwargs):
        """Login Client Trader"""
        pass

    def prepare(
        self,
        config_path=None,
        user=None,
        password=None,
        exe_path=None,
        comm_password=None,
        **kwargs
    ):
        """
        登陆客户端
        :param config_path: 登陆配置文件，跟参数登陆方式二选一
        :param user: 账号
        :param password: 明文密码
        :param exe_path: 客户端路径类似 r'C:\\htzqzyb2\\xiadan.exe', 默认 r'C:\\htzqzyb2\\xiadan.exe'
        :param comm_password: 通讯密码
        :return:
        """
        if config_path is not None:
            account = helpers.file2dict(config_path)
            user = account["user"]
            password = account["password"]
            comm_password = account.get("comm_password")
            exe_path = account.get("exe_path")
        self.login(
            user,
            password,
            exe_path or self._config.DEFAULT_EXE_PATH,
            comm_password,
            **kwargs
        )

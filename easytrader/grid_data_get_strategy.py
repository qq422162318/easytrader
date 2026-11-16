# -*- coding: utf-8 -*-
import abc
import io
import tempfile
import time

import pandas as pd
import pywinauto.clipboard

from . import captcha_handler
from . import exceptions
from .log import log


class IGridDataGetStrategy(abc.ABC):
    @abc.abstractmethod
    def get(self, control_id: int):
        """
        :param control_id: grid 的 control id
        :return: grid 数据
        :rtype: List[Dict]
        """
        pass


class BaseStrategy(IGridDataGetStrategy):
    def __init__(self, trader):
        self._trader = trader

    @abc.abstractmethod
    def get(self, control_id: int):
        """
        :param control_id: grid 的 control id
        :return: grid 数据
        :rtype: list[dict]
        """
        pass

    def _get_grid(self, control_id):
        grid = self._trader.main.window(
            control_id=control_id, class_name="CVirtualGridCtrl"
        )
        return grid


class CopyStrategy(BaseStrategy):
    """
    通过复制 grid 内容到剪切板z再读取来获取 grid 内容
    """

    def get(self, control_id: int):
        grid = self._get_grid(control_id)
        content = self._read_grid_content(grid)

        if not content.strip():
            raise exceptions.GridDataError(
                "复制表格数据失败, 剪贴板为空。"
                "客户端可能正在提示输入验证码, 或当前面板没有数据"
            )

        return self._format_grid_data(content)

    def _read_grid_content(self, grid):
        """拷贝 grid 内容; 若拷贝中弹出验证码则自动填写后重试一次。

        前置检测(已有残留弹窗)由 ClientTrader._get_grid_data 统一做,
        这里只处理"本次拷贝恰好触发弹窗、剪贴板被挡成空串"的情况。
        """
        content = self._copy_grid(grid)

        if not content.strip():
            # 拷贝可能刚触发弹窗、但弹窗还在弹出中(竞态), 稍等再检测。
            time.sleep(0.5)
            if captcha_handler.auto_fill_captcha(self._trader.app):
                # 验证通过后，客户端可能继续原来的复制并写入剪贴板。
                # 先读取这次复制的结果，不能清空后立刻再复制触发新验证码。
                log.info("检测到验证码弹窗, 已自动填写验证码, 等待复制结果")
                time.sleep(1)
                try:
                    content = self._get_clipboard_data()
                except exceptions.GridDataError:
                    # 不接受返回 True 作为验证码已通过的证明；弹窗仍在时不重试。
                    if not captcha_handler.discover_captcha_dialogs(self._trader.app):
                        content = self._copy_grid(grid)

        return content

    def _copy_grid(self, grid):
        """把焦点稳定到 grid 上再 ^A^C, 避免拷贝到别的窗口内容。"""
        try:
            self._trader.main.set_focus()
        except Exception:
            pass
        try:
            grid.set_focus()
        except Exception:
            pass
        time.sleep(0.2)
        # 复制可能被验证码拦截；先清空，避免把上一次表格当成本次结果。
        self._clear_clipboard()
        grid.type_keys("^A^C")
        try:
            return self._get_clipboard_data()
        except exceptions.GridDataError:
            return ""

    def _format_grid_data(self, data):
        df = pd.read_csv(
            io.StringIO(data),
            delimiter="\t",
            dtype=self._trader.config.GRID_DTYPE,
            na_filter=False,
        )
        return df.to_dict("records")

    def _clear_clipboard(self):
        for attempt in range(10):
            try:
                pywinauto.clipboard.EmptyClipboard()
                return
            except Exception as exc:
                if attempt == 9:
                    raise exceptions.GridDataError("无法清空剪贴板") from exc
                time.sleep(0.1)

    def _get_clipboard_data(self):
        last_error = None
        for attempt in range(10):
            try:
                content = pywinauto.clipboard.GetData()
                if isinstance(content, str) and content.strip():
                    return content
            except Exception as exc:
                last_error = exc
            # 只读探测，自动填写统一由 _read_grid_content() 触发。
            if captcha_handler.discover_captcha_dialogs(self._trader.app):
                raise exceptions.GridDataError(
                    "客户端弹出验证码提示框, 无法复制表格数据, "
                    "请先人工输入验证码"
                )
            if attempt < 9:
                time.sleep(0.1)
        raise exceptions.GridDataError("读取剪贴板超时, 未获得表格文本") from last_error


class XlsStrategy(BaseStrategy):
    """
    通过将 Grid 另存为 xls 文件再读取的方式获取 grid 内容，
    用于绕过一些客户端不允许复制的限制
    """

    def get(self, control_id: int):
        grid = self._get_grid(control_id)

        # ctrl+s 保存 grid 内容为 xls 文件
        grid.type_keys("^s")
        self._trader.wait(1)

        temp_path = tempfile.mktemp(suffix=".csv")
        self._trader.app.top_window().type_keys(temp_path)

        # alt+s保存，alt+y替换已存在的文件
        self._trader.app.top_window().type_keys("%{s}%{y}")
        return self._format_grid_data(temp_path)

    def _format_grid_data(self, data):
        df = pd.read_csv(
            data,
            encoding="gbk",
            delimiter="\t",
            dtype=self._trader.config.GRID_DTYPE,
            na_filter=False,
        )
        return df.to_dict("records")

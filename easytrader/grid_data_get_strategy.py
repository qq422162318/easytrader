# -*- coding: utf-8 -*-
import abc
import io
import tempfile
import time

import pandas as pd

from . import _clipboard
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

    _CLIPBOARD_TIMEOUT = 3.0
    _CLIPBOARD_CALL_TIMEOUT = 1.0
    _CLIPBOARD_ATTEMPTS = 10

    def get(self, control_id: int):
        # 上一次复制的弹窗仍在时，不能先清空/再次复制。先确认并等待关闭；
        # 旧复制可能来自别的查询页，不能把它的剪贴板当成本次查询结果。
        if captcha_handler.discover_captcha_dialogs(self._trader.app):
            try:
                captcha_handler.guard(self._trader.app, "复制表格数据")
            except exceptions.CaptchaError as exc:
                raise exceptions.GridDataError(str(exc)) from exc
        grid = self._get_grid(control_id)
        content = self._read_grid_content(grid)

        if not content.strip():
            raise exceptions.GridDataError(
                "复制表格数据失败, 剪贴板为空。"
                "客户端可能正在提示输入验证码, 或当前面板没有数据"
            )

        return self._format_grid_data(content)

    def _submit_captcha_and_wait(self):
        if not captcha_handler.auto_fill_captcha(self._trader.app):
            raise exceptions.GridDataError(
                "验证码未能安全自动填写，请在客户端人工填写并点击确定后重试"
            )
        # 返回 True 只表示发送了一次输入/确认，绝不表示客户端已接受。
        log.info("检测到验证码弹窗, 已提交一次验证码, 等待弹窗关闭（最多 5 秒）")
        if not captcha_handler.wait_for_captcha_dismissal(self._trader.app, timeout=5.0):
            log.warning("验证码确认超时：弹窗仍在，已停止读取，不重复填码或复制")
            raise exceptions.GridDataError(
                "验证码提交后 5 秒内弹窗仍未关闭，可能识别错误或确认未生效；"
                "已停止自动重填，请在客户端检查验证码并点击确定后重试"
            )
        log.info("验证码弹窗已关闭, 等待原复制结果（最多 3 秒）")

    def _read_grid_content(self, grid):
        """Handle this copy's captcha once and prefer its resumed result."""
        content = self._copy_grid(grid)
        if content.strip():
            return content
        # 拷贝可能刚触发弹窗、但弹窗还在创建中(竞态), 稍等再检测。
        time.sleep(0.5)
        if not captcha_handler.discover_captcha_dialogs(self._trader.app):
            return content
        self._submit_captcha_and_wait()
        try:
            # 验证后客户端可能继续原复制；不可先清空这份结果。
            return self._get_clipboard_data()
        except exceptions.GridDataError:
            if captcha_handler.discover_captcha_dialogs(self._trader.app):
                raise
        # 弹窗已关闭但原复制没继续，最多重新复制一次；不再自动提交验证码。
        log.warning("验证码弹窗已关闭但原复制未返回文本, 仅重新复制一次")
        content = self._copy_grid(grid)
        if not content.strip():
            raise exceptions.GridDataError(
                "验证码处理后仍未获得表格文本，已停止复制；请检查客户端查询页后重试"
            )
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
        deadline = time.monotonic() + self._CLIPBOARD_TIMEOUT
        last_error = None
        for attempt in range(self._CLIPBOARD_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                _clipboard.empty(timeout=min(self._CLIPBOARD_CALL_TIMEOUT, remaining))
                return
            except exceptions.GridDataError as exc:
                last_error = exc
            if attempt < self._CLIPBOARD_ATTEMPTS - 1:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise exceptions.GridDataError("清空剪贴板超时（最多 3 秒）") from last_error

    def _get_clipboard_data(self):
        deadline = time.monotonic() + self._CLIPBOARD_TIMEOUT
        last_error = None
        for attempt in range(self._CLIPBOARD_ATTEMPTS):
            # 弹窗仍在时不要调用 GetClipboardData，避免触发客户端延迟渲染。
            if captcha_handler.discover_captcha_dialogs(self._trader.app):
                raise exceptions.GridDataError(
                    "客户端弹出验证码提示框, 无法复制表格数据, 请先人工输入验证码"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                content = _clipboard.read_text(
                    timeout=min(self._CLIPBOARD_CALL_TIMEOUT, remaining)
                )
                if content.strip():
                    return content
            except exceptions.GridDataError as exc:
                last_error = exc
            if attempt < self._CLIPBOARD_ATTEMPTS - 1:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        log.warning("读取剪贴板超时（最多 3 秒），已停止本次读取")
        raise exceptions.GridDataError(
            "读取剪贴板超时（最多 3 秒）, 未获得表格文本"
        ) from last_error


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

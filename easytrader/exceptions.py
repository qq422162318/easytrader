# -*- coding: utf-8 -*-
class TradeError(IOError):
    pass


class CaptchaError(Exception):
    """
    客户端弹出了验证码提示框(同花顺在检测到频繁拷贝表格数据时触发)，
    此时剪贴板拷贝会失效，表格数据无法读取，需要人工在客户端输入验证码。
    """

    pass


class GridDataError(Exception):
    """表格数据读取失败(剪贴板为空、复制被拦截等)"""

    pass


class NotLoginError(Exception):
    def __init__(self, result=None):
        super(NotLoginError, self).__init__()
        self.result = result

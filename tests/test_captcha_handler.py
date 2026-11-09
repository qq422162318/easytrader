# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from easytrader import captcha_handler


class TestCaptchaDiscovery(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(process=4321)
        self.children = {
            200: [
                (2404, "Edit", ""),
                (2405, "Static", "验证码图片"),
                (1, "Button", "确定"),
                (2, "Button", "取消"),
                (2393, "Static", "检测到您正在拷贝数据"),
            ],
            201: [(2404, "Edit", "")],
            202: [(2404, "Edit", "验证码")],
        }
        self.class_names = {dialog: "#32770" for dialog in self.children}
        self.window_text = {
            200: "提示",
            201: "提示",
            202: "提示",
            2404: "",
            2405: "验证码图片",
            1: "确定",
            2: "取消",
            2393: "检测到您正在拷贝数据",
        }
        self.pids = {200: 4321, 201: 9999, 202: 4321}
        self.visible = {200: True, 201: True, 202: False}

    def _install_win32_mocks(self):
        def enum_windows(callback, _):
            for hwnd in (200, 201, 202):
                callback(hwnd, None)

        def enum_children(parent, callback, _):
            for cid, cls, text in self.children.get(parent, []):
                child = {
                    2404: 3004,
                    2405: 3005,
                    1: 3001,
                    2: 3002,
                    2393: 3093,
                }[cid]
                self.class_names[child] = cls
                self.window_text[child] = text
                callback(child, None)

        def get_dlg_ctrl_id(hwnd):
            return {3004: 2404, 3005: 2405, 3001: 1, 3002: 2, 3093: 2393}[hwnd]

        self.patches = [
            patch.object(captcha_handler.win32gui, "EnumWindows", enum_windows),
            patch.object(captcha_handler.win32gui, "EnumChildWindows", enum_children),
            patch.object(
                captcha_handler.win32gui,
                "GetClassName",
                lambda hwnd: self.class_names[hwnd],
            ),
            patch.object(
                captcha_handler.win32gui,
                "GetWindowText",
                lambda hwnd: self.window_text.get(hwnd, ""),
            ),
            patch.object(
                captcha_handler.win32gui,
                "GetDlgCtrlID",
                get_dlg_ctrl_id,
            ),
            patch.object(
                captcha_handler.win32gui,
                "GetWindowRect",
                lambda hwnd: (10, 20, 110, 50),
            ),
            patch.object(
                captcha_handler.win32gui,
                "IsWindow",
                lambda hwnd: True,
            ),
            patch.object(
                captcha_handler.win32gui,
                "IsWindowVisible",
                lambda hwnd: self.visible[hwnd],
            ),
            patch.object(
                captcha_handler.win32process,
                "GetWindowThreadProcessId",
                lambda hwnd: (0, self.pids[hwnd]),
            ),
            # discover_captcha_dialog() 会在检测到弹窗后调用 ocr_pos() 做真实
            # 截图 + 百度 OCR，并用 pywinauto（Application -> child_window ->
            # set_focus/type_keys/click）写入输入框、点击确定。发现类测试只
            # 关心窗口/控件发现，与屏幕、网络、真实 UI 交互无关，这里固定
            # 返回假验证码并 mock 掉整条 pywinauto 调用链，避免依赖外部资源。
            patch.object(captcha_handler, "ocr_pos", return_value="1234"),
            patch.object(captcha_handler, "Application", MagicMock()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in getattr(self, "patches", []):
            p.stop()

    def test_discovery_filters_pid_visibility_and_returns_ids(self):
        self._install_win32_mocks()

        dialogs = captcha_handler.discover_captcha_dialogs(self.app)

        self.assertEqual([200], [item["dialog_hwnd"] for item in dialogs])
        self.assertEqual(4321, dialogs[0]["pid"])
        controls = dialogs[0]["controls"]
        self.assertEqual(
            {2404, 2405, 1, 2, 2393},
            {control["control_id"] for control in controls},
        )
        self.assertEqual(3004, captcha_handler.get_captcha_edit_handle(self.app))
        self.assertEqual(3005, captcha_handler.get_captcha_image_handle(self.app))
        self.assertEqual(3005, captcha_handler.get_captcha_control_handle(self.app, 2405))
        self.assertEqual(200, captcha_handler.find_captcha_dialog(self.app))

    def test_absent_dialog_returns_empty_values(self):
        self.children[200][-1] = (2393, "Static", "普通提示")
        self.children[200][1] = (2405, "Static", "图像")
        self._install_win32_mocks()

        self.assertEqual([], captcha_handler.discover_captcha_dialogs(self.app))
        self.assertIsNone(captcha_handler.discover_captcha_dialog(self.app))
        self.assertFalse(captcha_handler.is_captcha_dialog_displayed(self.app))
        self.assertIsNone(captcha_handler.get_captcha_edit_handle(self.app))

    def test_discovery_does_not_close_or_interact_with_dialog(self):
        self._install_win32_mocks()
        post_message = Mock()
        with patch.object(captcha_handler.win32gui, "PostMessage", post_message):
            dialogs = captcha_handler.discover_captcha_dialogs(self.app)
            self.assertEqual(1, len(dialogs))
            self.assertFalse(post_message.called)
        self.assertFalse(captcha_handler.dismiss_captcha_dialog(self.app))
        self.assertFalse(post_message.called)

    def test_enumeration_error_is_tolerated(self):
        with patch.object(
            captcha_handler.win32gui,
            "EnumWindows",
            side_effect=RuntimeError("desktop changed"),
        ):
            self.assertEqual([], captcha_handler.discover_captcha_dialogs(self.app))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import builtins
import importlib.util
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
            # 发现类 API 必须只读；同时拦截 OCR 和 UI 并断言没有调用，
            # 即使意外回归为自动填写也不会访问真实屏幕或网络。
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
        captcha_handler.discover_captcha_dialog(self.app)
        captcha_handler.is_captcha_dialog_displayed(self.app)
        captcha_handler.get_captcha_edit_handle(self.app)
        captcha_handler.ocr_pos.assert_not_called()
        captcha_handler.Application.assert_not_called()
        self.assertFalse(captcha_handler.dismiss_captcha_dialog(self.app))
        self.assertFalse(post_message.called)

    def test_enumeration_error_is_tolerated(self):
        with patch.object(
            captcha_handler.win32gui,
            "EnumWindows",
            side_effect=RuntimeError("desktop changed"),
        ):
            self.assertEqual([], captcha_handler.discover_captcha_dialogs(self.app))


class TestCaptchaAutoFill(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(process=4321)
        self.snapshot = {
            "dialog_hwnd": 200,
            "controls": [
                {"control_id": 2405, "class_name": "Static", "hwnd": 3005,
                 "rect": (10, 20, 110, 50)},
                {"control_id": 2404, "class_name": "Edit", "hwnd": 3004,
                 "rect": (10, 60, 110, 90)},
                {"control_id": 1, "class_name": "Button", "hwnd": 3001,
                 "rect": (10, 100, 110, 130)},
            ],
        }

    def test_auto_fill_preserves_input_when_ocr_returns_no_text(self):
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos", return_value=" ") as ocr, \
                patch.object(captcha_handler, "Application") as application:
            self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
        ocr.assert_called_once_with(20, 10, 100, 30)
        application.assert_not_called()

    def test_auto_fill_does_not_submit_incomplete_or_nonnumeric_ocr(self):
        for text in ("8", "123", "12345", "12a4"):
            with self.subTest(text=text), \
                    patch.object(captcha_handler, "discover_captcha_dialog",
                                 return_value=self.snapshot), \
                    patch.object(captcha_handler, "ocr_pos", return_value=text), \
                    patch.object(captcha_handler, "Application") as application, \
                    patch.object(captcha_handler.win32gui, "SendMessageTimeout") as send:
                self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
                application.assert_not_called()
                send.assert_not_called()

    def test_auto_fill_waits_for_image_and_does_not_submit_without_it(self):
        self.snapshot["controls"] = self.snapshot["controls"][1:]
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot) as discover, \
                patch.object(captcha_handler, "ocr_pos") as ocr, \
                patch.object(captcha_handler, "Application") as application, \
                patch.object(captcha_handler.time, "sleep") as sleep:
            self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
        self.assertEqual(2, discover.call_count)
        sleep.assert_called_once_with(0.5)
        ocr.assert_not_called()
        application.assert_not_called()

    def test_auto_fill_rejects_invalid_image_geometry(self):
        self.snapshot["controls"][0]["rect"] = (10, 20, 10, 50)
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos") as ocr, \
                patch.object(captcha_handler, "Application") as application:
            self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
        ocr.assert_not_called()
        application.assert_not_called()

    def test_auto_fill_types_nonempty_text_and_clicks_ok(self):
        dialog = MagicMock()
        edit = MagicMock()
        dialog.child_window.return_value = edit
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos", return_value=" 1234 "), \
                patch.object(captcha_handler.win32gui, "SendMessageTimeout") as send, \
                patch.object(captcha_handler, "Application") as application:
            application.return_value.connect.return_value.window.return_value = dialog
            self.assertTrue(captcha_handler.auto_fill_captcha(self.app))
        application.return_value.connect.assert_called_once_with(handle=200)
        dialog.set_focus.assert_called_once_with()
        edit.set_keyboard_focus.assert_called_once_with()
        edit.set_focus.assert_not_called()
        self.assertEqual(
            [(("{HOME}+{END}{BACKSPACE}",),
              {"set_foreground": False, "vk_packet": False, "pause": 0.1}),
             (("1234",), {"with_spaces": True, "set_foreground": False,
                          "vk_packet": False, "pause": 0.1})],
            [(call.args, call.kwargs) for call in edit.type_keys.call_args_list],
        )
        edit.text_block.assert_not_called()
        edit.window_text.assert_not_called()
        send.assert_called_once_with(
            3001, captcha_handler.win32con.BM_CLICK, 0, 0,
            captcha_handler.win32con.SMTO_ABORTIFHUNG | captcha_handler.win32con.SMTO_BLOCK,
            1000,
        )

    def test_auto_fill_replaces_existing_code_without_edit_text_readback(self):
        dialog = MagicMock()
        edit = MagicMock()
        dialog.child_window.return_value = edit
        contents = ["12341234"]
        edit.text_block.side_effect = AssertionError("protected Edit readback is unreliable")
        edit.window_text.side_effect = AssertionError("protected Edit readback is unreliable")

        def type_keys(keys, **kwargs):
            if keys == "{HOME}+{END}{BACKSPACE}":
                contents[0] = ""
            else:
                contents[0] += keys

        edit.type_keys.side_effect = type_keys
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos", return_value="1234"), \
                patch.object(captcha_handler.win32gui, "SendMessageTimeout") as send, \
                patch.object(captcha_handler, "Application") as application:
            application.return_value.connect.return_value.window.return_value = dialog
            self.assertTrue(captcha_handler.auto_fill_captcha(self.app))
        self.assertEqual("1234", contents[0])
        self.assertEqual(2, edit.type_keys.call_count)
        send.assert_called_once_with(
            3001, captcha_handler.win32con.BM_CLICK, 0, 0,
            captcha_handler.win32con.SMTO_ABORTIFHUNG | captcha_handler.win32con.SMTO_BLOCK,
            1000,
        )

    def test_auto_fill_reports_ui_failure_without_confirming_or_leaking(self):
        with patch.object(captcha_handler, "discover_captcha_dialog",
                          return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos", return_value="1234"), \
                patch.object(captcha_handler, "Application",
                             side_effect=RuntimeError("private OAuth credential")), \
                patch.object(captcha_handler.win32gui, "SendMessageTimeout") as send, \
                patch.object(captcha_handler.log, "warning") as warning:
            self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
        send.assert_not_called()
        warning.assert_called_once_with("自动填写验证码失败 (%s)", "RuntimeError")


    def test_confirmation_timeout_is_not_retried_or_reported_as_success(self):
        dialog = MagicMock()
        with patch.object(captcha_handler, "discover_captcha_dialog", return_value=self.snapshot), \
                patch.object(captcha_handler, "ocr_pos", return_value="8632"), \
                patch.object(captcha_handler.win32gui, "SendMessageTimeout",
                             side_effect=RuntimeError("native timeout")) as send, \
                patch.object(captcha_handler, "Application") as application:
            application.return_value.connect.return_value.window.return_value = dialog
            self.assertFalse(captcha_handler.auto_fill_captcha(self.app))
        self.assertEqual(1, send.call_count)
        self.assertEqual(2, dialog.child_window.return_value.type_keys.call_count)


class TestCaptchaDismissalWait(unittest.TestCase):
    def test_already_closed_dialog_returns_without_waiting(self):
        with patch.object(captcha_handler, "discover_captcha_dialogs", return_value=[]), \
                patch.object(captcha_handler.time, "sleep") as sleep:
            self.assertTrue(captcha_handler.wait_for_captcha_dismissal(SimpleNamespace(process=4321)))
        sleep.assert_not_called()

    def test_waits_for_delayed_close_without_reading_input_or_reconfirming(self):
        app = SimpleNamespace(process=4321)
        with patch.object(captcha_handler, "discover_captcha_dialogs",
                          side_effect=[[{}], [], []]) as discover, \
                patch.object(captcha_handler, "auto_fill_captcha") as fill, \
                patch.object(captcha_handler.time, "sleep") as sleep:
            self.assertTrue(captcha_handler.wait_for_captcha_dismissal(app))
        self.assertEqual(2, discover.call_count)
        sleep.assert_called_once_with(0.1)
        fill.assert_not_called()

    def test_remaining_dialog_times_out_at_deadline_without_repeat_actions(self):
        clock = [0.0]
        with patch.object(captcha_handler, "discover_captcha_dialogs", return_value=[{}]), \
                patch.object(captcha_handler.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(captcha_handler.time, "sleep",
                             side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
                patch.object(captcha_handler, "auto_fill_captcha") as fill:
            self.assertFalse(captcha_handler.wait_for_captcha_dismissal(
                SimpleNamespace(process=4321), timeout=0.25,
            ))
        self.assertAlmostEqual(0.25, clock[0])
        fill.assert_not_called()


class TestOptionalOcrDependencies(unittest.TestCase):
    @staticmethod
    def _block_optional(name, *args, **kwargs):
        if name.split(".")[0] in {"aip", "mss", "numpy", "cv2"}:
            raise ModuleNotFoundError("blocked optional OCR dependency")
        return TestOptionalOcrDependencies._original_import(name, *args, **kwargs)

    _original_import = staticmethod(builtins.__import__)

    def test_captcha_module_import_does_not_require_optional_ocr_packages(self):
        spec = importlib.util.spec_from_file_location(
            "easytrader._captcha_import_test", captcha_handler.__file__
        )
        module = importlib.util.module_from_spec(spec)
        with patch.object(builtins, "__import__", side_effect=self._block_optional):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.discover_captcha_dialog))

    def test_missing_screenshot_dependency_has_clear_captcha_error(self):
        with patch.object(builtins, "__import__", side_effect=self._block_optional):
            with self.assertRaisesRegex(captcha_handler.exceptions.CaptchaError, "mss"):
                captcha_handler.ocr_pos(0, 0, 10, 10)

    def test_missing_baidu_dependency_has_clear_captcha_error(self):
        with patch.object(builtins, "__import__", side_effect=self._block_optional):
            with self.assertRaisesRegex(captcha_handler.exceptions.CaptchaError,
                                        "baidu-aip"):
                captcha_handler.baidu_ocr("unused.png")

    def test_empty_ocr_response_returns_empty_text(self):
        with patch.dict("sys.modules", {"aip": SimpleNamespace(AipOcr=Mock())}):
            import aip
            aip.AipOcr.return_value.basicGeneral.return_value = {"words_result": []}
            with patch.object(builtins, "open", unittest.mock.mock_open(read_data=b"mock")):
                self.assertEqual("", captcha_handler.baidu_ocr("mock.png"))

    def test_baidu_joins_all_captcha_fragments_and_removes_spacing(self):
        with patch.dict("sys.modules", {"aip": SimpleNamespace(AipOcr=Mock())}):
            import aip
            aip.AipOcr.return_value.basicGeneral.return_value = {
                "words_result": [{"words": " 8 "}, {"words": "6 3 2"}],
            }
            with patch.object(builtins, "open", unittest.mock.mock_open(read_data=b"mock")):
                self.assertEqual("8632", captcha_handler.baidu_ocr("mock.png"))

    def test_baidu_discards_symbols_but_does_not_guess_letters(self):
        for words, expected in (("9463◆", "9463"), ("O463◆", "O463")):
            with self.subTest(words=words), \
                    patch.dict("sys.modules", {"aip": SimpleNamespace(AipOcr=Mock())}):
                import aip
                aip.AipOcr.return_value.basicGeneral.return_value = {
                    "words_result": [{"words": words}],
                }
                with patch.object(builtins, "open", unittest.mock.mock_open(read_data=b"mock")):
                    self.assertEqual(expected, captcha_handler.baidu_ocr("mock.png"))

    def test_opencv_receives_bgra_and_sends_grayscale_before_binary(self):
        from types import ModuleType
        from PIL import Image
        mss = ModuleType("mss")
        screenshot = SimpleNamespace(
            size=(2, 1), bgra=bytes((255, 0, 0, 0, 0, 0, 255, 0)),
            rgb=bytes((0, 0, 255, 255, 0, 0)),
        )
        capture = MagicMock()
        capture.__enter__.return_value.grab.return_value = screenshot
        mss.mss = Mock(return_value=capture)
        gray = object()
        enlarged = object()
        cv2 = SimpleNamespace(
            COLOR_BGRA2GRAY=10, INTER_CUBIC=20, THRESH_BINARY=30,
            cvtColor=Mock(return_value=gray), resize=Mock(return_value=enlarged),
            threshold=Mock(return_value=(150, object())),
        )
        pixels = Mock()
        array = object()
        pixels.reshape.return_value = array
        np = SimpleNamespace(uint8=object(), frombuffer=Mock(return_value=pixels))

        def ocr(path):
            with Image.open(path) as image:
                self.assertEqual((22, 19), image.size)
                self.assertEqual({120, 255}, set(image.tobytes()))
            return "8632"

        binary = Image.new("L", (6, 3), 0)
        gray_image = Image.new("L", (6, 3), 120)
        with patch.dict("sys.modules", {"mss": mss, "cv2": cv2, "numpy": np}), \
                patch.object(Image, "fromarray", side_effect=[binary, gray_image]), \
                patch.object(captcha_handler, "baidu_ocr", side_effect=ocr) as recognize:
            self.assertEqual("8632", captcha_handler.ocr_pos(20, 10, 2, 1))
        recognize.assert_called_once()
        np.frombuffer.assert_called_once_with(screenshot.bgra, dtype=np.uint8)
        pixels.reshape.assert_called_once_with(1, 2, 4)
        cv2.cvtColor.assert_called_once_with(array, cv2.COLOR_BGRA2GRAY)
        cv2.resize.assert_called_once_with(gray, (6, 3), interpolation=cv2.INTER_CUBIC)
        cv2.threshold.assert_called_once_with(enlarged, 150, 255, cv2.THRESH_BINARY)

    def test_incomplete_gray_ocr_retries_same_captcha_as_binary_once(self):
        from types import ModuleType
        from PIL import Image
        mss = ModuleType("mss")
        screenshot = SimpleNamespace(rgb=bytes((120, 120, 120)) * 4, size=(2, 2))
        capture = MagicMock()
        capture.__enter__.return_value.grab.return_value = screenshot
        mss.mss = Mock(return_value=capture)
        seen_pixels = []
        paths = []

        def ocr(path):
            paths.append(path)
            with Image.open(path) as image:
                seen_pixels.append(set(image.tobytes()))
            return "5" if len(paths) == 1 else "8557"

        with patch.dict("sys.modules", {"mss": mss, "cv2": None, "numpy": None}), \
                patch.object(captcha_handler, "baidu_ocr", side_effect=ocr):
            self.assertEqual("8557", captcha_handler.ocr_pos(0, 0, 2, 2))
        self.assertEqual([{120, 255}, {0, 255}], seen_pixels)
        self.assertEqual(2, len(paths))
        self.assertEqual(paths[0], paths[1])
        capture.__enter__.return_value.grab.assert_called_once_with(
            {"top": 0, "left": 0, "width": 2, "height": 2}
        )

    def test_both_incomplete_ocr_variants_stop_after_two_calls(self):
        from types import ModuleType
        mss = ModuleType("mss")
        screenshot = SimpleNamespace(rgb=bytes((120, 120, 120)) * 4, size=(2, 2))
        capture = MagicMock()
        capture.__enter__.return_value.grab.return_value = screenshot
        mss.mss = Mock(return_value=capture)
        with patch.dict("sys.modules", {"mss": mss, "cv2": None, "numpy": None}), \
                patch.object(captcha_handler, "baidu_ocr", return_value="5") as ocr:
            self.assertEqual("", captcha_handler.ocr_pos(0, 0, 2, 2))
        self.assertEqual(2, ocr.call_count)

    def test_screenshot_uses_a_private_file_and_removes_it(self):
        import os
        from types import ModuleType
        from PIL import Image
        mss = ModuleType("mss")
        captured_paths = []
        screenshot = SimpleNamespace(
            rgb=bytes((0, 0, 0, 149, 149, 149, 151, 151, 151, 255, 255, 255)),
            size=(2, 2),
        )
        capture = MagicMock()
        capture.__enter__.return_value.grab.return_value = screenshot

        def ocr(path):
            captured_paths.append(path)
            self.assertTrue(os.path.isfile(path))
            with Image.open(path) as image:
                self.assertEqual("PNG", image.format)
                self.assertEqual("L", image.mode)
                self.assertEqual((22, 22), image.size)
                self.assertEqual(255, image.getpixel((0, 0)))
                self.assertTrue(set(image.tobytes()) - {0, 255})
            return "8632"

        mss.mss = Mock(return_value=capture)
        with patch.dict("sys.modules", {"mss": mss, "cv2": None, "numpy": None}), \
                patch.object(captcha_handler, "baidu_ocr", side_effect=ocr):
            self.assertEqual("8632", captcha_handler.ocr_pos(0, 0, 2, 2))
            self.assertEqual("8632", captcha_handler.ocr_pos(0, 0, 2, 2))
        self.assertEqual(2, len(set(captured_paths)))
        self.assertTrue(all(not os.path.exists(path) for path in captured_paths))
        self.assertTrue(all(not os.path.isdir(os.path.dirname(path))
                            for path in captured_paths))


class TestCaptchaOneFourAmbiguity(unittest.TestCase):
    def setUp(self):
        from types import ModuleType
        self.mss = ModuleType("mss")
        self.capture = MagicMock()
        self.capture.__enter__.return_value.grab.return_value = SimpleNamespace(
            rgb=bytes((120, 50, 250)) * 4, size=(2, 2),
        )
        self.mss.mss = Mock(return_value=self.capture)

    def _recognize(self, replies):
        from PIL import Image
        replies = iter(replies)
        modes = []

        def ocr(path):
            with Image.open(path) as image:
                modes.append(image.mode)
                self.assertEqual((22, 22), image.size)
            return next(replies)

        with patch.dict("sys.modules", {"mss": self.mss, "cv2": None, "numpy": None}), \
                patch.object(captcha_handler, "baidu_ocr", side_effect=ocr) as request:
            text = captcha_handler.ocr_pos(0, 0, 2, 2)
        self.capture.__enter__.return_value.grab.assert_called_once_with(
            {"top": 0, "left": 0, "width": 2, "height": 2}
        )
        self.assertEqual(len(modes), request.call_count)
        return text, modes

    def test_gray_and_color_agreement_preserves_four(self):
        text, modes = self._recognize(["4964", "4964"])
        self.assertEqual("4964", text)
        self.assertEqual(["L", "RGB"], modes)

    def test_gray_and_color_agreement_preserves_real_one(self):
        text, modes = self._recognize(["1961", "1961"])
        self.assertEqual("1961", text)
        self.assertEqual(["L", "RGB"], modes)

    def test_four_digit_one_four_conflict_does_not_guess(self):
        with patch.object(captcha_handler.log, "warning") as warning:
            text, modes = self._recognize(["1964", "4964"])
        self.assertEqual("", text)
        self.assertEqual(["L", "RGB"], modes)
        warning.assert_called_once_with("验证码 1/4 识别核验不一致，已停止自动填写")

    def test_incomplete_color_check_does_not_accept_gray_candidate(self):
        text, modes = self._recognize(["4964", "49"])
        self.assertEqual("", text)
        self.assertEqual(["L", "RGB"], modes)

    def test_binary_fallback_also_cross_checks_one_four_and_stops_at_three(self):
        text, modes = self._recognize(["5", "1964", "4964"])
        self.assertEqual("", text)
        self.assertEqual(["L", "L", "RGB"], modes)

    def test_agreed_binary_candidate_is_accepted_after_color_check(self):
        text, modes = self._recognize(["5", "1961", "1961"])
        self.assertEqual("1961", text)
        self.assertEqual(["L", "L", "RGB"], modes)

    def test_unambiguous_gray_code_uses_only_one_ocr_request(self):
        text, modes = self._recognize(["8557"])
        self.assertEqual("8557", text)
        self.assertEqual(["L"], modes)

    def test_conflicting_one_four_result_never_clears_input_or_clicks(self):
        snapshot = {
            "dialog_hwnd": 200,
            "controls": [
                {"control_id": 2405, "class_name": "Static", "hwnd": 3005,
                 "rect": (0, 0, 2, 2)},
                {"control_id": 2404, "class_name": "Edit", "hwnd": 3004},
                {"control_id": 1, "class_name": "Button", "hwnd": 3001},
            ],
        }
        with patch.dict("sys.modules", {"mss": self.mss, "cv2": None, "numpy": None}), \
                patch.object(captcha_handler, "baidu_ocr", side_effect=["1964", "4964"]) as ocr, \
                patch.object(captcha_handler, "discover_captcha_dialog", return_value=snapshot), \
                patch.object(captcha_handler, "Application") as application, \
                patch.object(captcha_handler.win32gui, "SendMessageTimeout") as send:
            self.assertFalse(captcha_handler.auto_fill_captcha(SimpleNamespace(process=4321)))
        self.assertEqual(2, ocr.call_count)
        application.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

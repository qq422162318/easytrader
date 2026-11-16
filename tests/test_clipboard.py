# -*- coding: utf-8 -*-
"""Clipboard worker tests; never open or read the real Windows clipboard."""
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from easytrader import _clipboard, exceptions


class TestBoundedClipboard(unittest.TestCase):
    def test_unicode_text_stays_in_an_in_memory_pipe(self):
        text = "证券代码\t数量\n000001\t100\n"
        reply = json.dumps({"ok": True, "text": text}, ensure_ascii=False).encode("utf-8")
        with patch.object(_clipboard.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout=reply)) as run:
            self.assertEqual(text, _clipboard.read_text(timeout=0.75))
        command = run.call_args.args[0]
        self.assertEqual([sys.executable, "-I", "-B", "-c"], command[:4])
        self.assertEqual("read", command[-1])
        self.assertNotIn(text, " ".join(command))
        self.assertEqual(subprocess.PIPE, run.call_args.kwargs["stdout"])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stderr"])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stdin"])
        self.assertEqual(0.75, run.call_args.kwargs["timeout"])

    def test_clear_uses_the_same_bounded_worker(self):
        with patch.object(_clipboard.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout=b'{"ok":true,"text":""}')) as run:
            self.assertIsNone(_clipboard.empty(timeout=0.5))
        self.assertEqual("clear", run.call_args.args[0][-1])
        self.assertEqual(0.5, run.call_args.kwargs["timeout"])

    def test_native_timeout_becomes_sanitized_grid_error_without_retry(self):
        with patch.object(_clipboard.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            "clipboard", 0.1, output=b"private clipboard text",
        )) as run:
            with self.assertRaisesRegex(exceptions.GridDataError, "Windows 剪贴板调用超时") as caught:
                _clipboard.read_text(timeout=0.1)
        run.assert_called_once()
        self.assertNotIn("private", str(caught.exception))

    def test_a_really_blocked_helper_is_killed_and_reaped(self):
        # Exercise an actual blocked subprocess, but never touch the desktop
        # or clipboard. This proves that the timeout is not just a loop count.
        processes = []
        original_popen = subprocess.Popen

        def start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(_clipboard, "_WORKER", "import time; time.sleep(60)"), \
                patch.object(_clipboard.subprocess, "Popen", side_effect=start):
            with self.assertRaisesRegex(exceptions.GridDataError, "Windows 剪贴板调用超时"):
                _clipboard.read_text(timeout=0.25)
        self.assertEqual(1, len(processes))
        self.assertIsNotNone(processes[0].poll())

    def test_failed_worker_or_invalid_reply_never_returns_partial_text(self):
        for reply in (
            SimpleNamespace(returncode=1, stdout=b'{"ok":true,"text":"partial"}'),
            SimpleNamespace(returncode=0, stdout=b'{"ok":false}'),
            SimpleNamespace(returncode=0, stdout=b"invalid"),
            SimpleNamespace(returncode=0, stdout=b'{"ok":true,"text":null}'),
        ):
            with self.subTest(reply=reply), \
                    patch.object(_clipboard.subprocess, "run", return_value=reply):
                with self.assertRaises(exceptions.GridDataError):
                    _clipboard.read_text()

    def test_worker_closes_clipboard_even_when_native_call_raises(self):
        fake_clipboard = SimpleNamespace(
            OpenClipboard=Mock(), CloseClipboard=Mock(),
            IsClipboardFormatAvailable=Mock(return_value=True),
            CF_UNICODETEXT=13,
            GetClipboardData=Mock(side_effect=RuntimeError("private clipboard text")),
            EmptyClipboard=Mock(side_effect=RuntimeError("private clipboard text")),
        )
        for operation in ("read", "clear"):
            with self.subTest(operation=operation), \
                    patch.dict("sys.modules", {"win32clipboard": fake_clipboard}), \
                    patch.object(sys, "argv", ["clipboard-worker", operation]), \
                    patch.object(sys, "stdout", SimpleNamespace(buffer=Mock())) as output:
                fake_clipboard.OpenClipboard.reset_mock()
                fake_clipboard.CloseClipboard.reset_mock()
                exec(compile(_clipboard._WORKER, "clipboard-worker", "exec"), {})
            fake_clipboard.OpenClipboard.assert_called_once_with()
            fake_clipboard.CloseClipboard.assert_called_once_with()
            self.assertEqual(b'{"ok": false}', output.buffer.write.call_args.args[0])

    def test_open_failure_does_not_close_someone_elses_clipboard(self):
        fake_clipboard = SimpleNamespace(
            OpenClipboard=Mock(side_effect=RuntimeError("locked")), CloseClipboard=Mock(),
        )
        with patch.dict("sys.modules", {"win32clipboard": fake_clipboard}), \
                patch.object(sys, "argv", ["clipboard-worker", "read"]), \
                patch.object(sys, "stdout", SimpleNamespace(buffer=Mock())):
            exec(compile(_clipboard._WORKER, "clipboard-worker", "exec"), {})
        fake_clipboard.CloseClipboard.assert_not_called()


if __name__ == "__main__":
    unittest.main()

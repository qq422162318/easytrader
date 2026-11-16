# -*- coding: utf-8 -*-
"""Bound native clipboard calls without leaving blocked threads in the caller.

GetClipboardData can wait for a delayed-rendering clipboard owner. A retry
count in the caller does not bound that single Windows call, so perform each
operation in a short-lived, killable interpreter. Clipboard contents stay in
an in-memory pipe; they are never placed in arguments, logs or temporary files.
"""
import json
import subprocess
import sys

from . import exceptions


_WORKER = r"""
import json
import sys
import win32clipboard

try:
    win32clipboard.OpenClipboard()
    try:
        if sys.argv[1] == "clear":
            win32clipboard.EmptyClipboard()
            text = ""
        elif sys.argv[1] == "read":
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            else:
                text = ""
        else:
            raise ValueError("Unknown clipboard operation")
    finally:
        win32clipboard.CloseClipboard()
    result = {"ok": True, "text": text if isinstance(text, str) else ""}
except Exception:
    # Do not expose clipboard content or full native exceptions to logs.
    result = {"ok": False}
sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
"""


def _run(operation, timeout):
    if operation not in ("read", "clear"):
        raise ValueError("Unknown clipboard operation")
    if timeout <= 0:
        raise exceptions.GridDataError("Windows 剪贴板调用超时")
    try:
        result = subprocess.run(
            # -c avoids multiprocessing's spawn import of the user's main
            # script, which could repeat unguarded broker operations.
            [sys.executable, "-I", "-B", "-c", _WORKER, operation],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps our helper on timeout. A blocked
        # native read therefore cannot keep the caller or clipboard locked.
        raise exceptions.GridDataError(
            "Windows 剪贴板调用超时，已终止本次剪贴板读取进程"
        ) from None
    except OSError:
        raise exceptions.GridDataError("无法启动剪贴板读取进程") from None
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise exceptions.GridDataError("剪贴板读取进程未返回有效结果") from None
    if (result.returncode != 0 or not isinstance(payload, dict)
            or payload.get("ok") is not True):
        raise exceptions.GridDataError("Windows 剪贴板操作失败或被占用")
    text = payload.get("text")
    if not isinstance(text, str):
        raise exceptions.GridDataError("剪贴板读取进程未返回文本")
    return text


def read_text(timeout=1.0):
    """Read Unicode text, or terminate the helper if Windows does not return."""
    return _run("read", timeout)


def empty(timeout=1.0):
    """Clear the clipboard with the same native-call time limit."""
    _run("clear", timeout)

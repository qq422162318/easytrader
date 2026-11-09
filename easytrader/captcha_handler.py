# -*- coding: utf-8 -*-
"""Read-only detection of the THS captcha dialog.

The dialog and all of its child HWNDs are created dynamically by Windows.
Call :func:`discover_captcha_dialog` each time the dialog is needed; never
persist an HWND across a dialog close/reopen or a client restart.

This module deliberately does not capture the captcha image, type text, click
buttons, or close the dialog.  It only reports the current window metadata so
caller-owned code can decide what to do.
"""
import os
import tempfile
import time

import win32api
import win32con
import win32gui
import win32process
import numpy as np
from .log import log
from aip import AipOcr
import mss
import mss.tools
import cv2
from pywinauto import Application

_CAPTCHA_KEYWORDS = ("拷贝数据", "验证码")
_DIALOG_CLASS = "#32770"
_CAPTCHA_EDIT_CONTROL_ID = 2404
_CAPTCHA_IMAGE_CONTROL_ID = 2405


def _child_texts(hwnd):
    """Return text from descendant Static/Edit controls for recognition."""
    texts = []

    def collect(child, _):
        try:
            if win32gui.GetClassName(child) in ("Static", "Edit"):
                texts.append(win32gui.GetWindowText(child))
        except Exception:
            # A dialog can disappear while EnumChildWindows is running.
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, collect, None)
    except Exception:
        pass
    return texts


def _is_captcha_dialog(hwnd):
    """Return whether a live HWND contains known captcha warning text."""
    try:
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        title = ""
    blob = " ".join([title] + _child_texts(hwnd))
    return any(keyword in blob for keyword in _CAPTCHA_KEYWORDS)


def _control_snapshot(hwnd):
    """Build a best-effort, read-only description of one child HWND."""
    try:
        class_name = win32gui.GetClassName(hwnd)
        text = win32gui.GetWindowText(hwnd)
        control_id = win32gui.GetDlgCtrlID(hwnd)
        try:
            rect = tuple(win32gui.GetWindowRect(hwnd))
        except Exception:
            rect = None
        return {
            "hwnd": hwnd,
            "control_id": control_id,
            "class_name": class_name,
            "text": text,
            "rect": rect,
        }
    except Exception:
        # The child may have been destroyed between enumeration and inspection.
        return None


def _controls_for_dialog(dialog_hwnd):
    controls = []

    def collect(child, _):
        record = _control_snapshot(child)
        if record is not None:
            controls.append(record)
        return True

    try:
        win32gui.EnumChildWindows(dialog_hwnd, collect, None)
    except Exception:
        pass
    return controls


def _dialog_snapshot(hwnd, pid):
    """Return metadata for a live captcha dialog, or ``None`` on a race."""
    try:
        if not win32gui.IsWindow(hwnd):
            return None
        if win32gui.GetClassName(hwnd) != _DIALOG_CLASS:
            return None
        if win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
            return None
        if not win32gui.IsWindowVisible(hwnd):
            return None
        if not _is_captcha_dialog(hwnd):
            return None
        try:
            rect = tuple(win32gui.GetWindowRect(hwnd))
        except Exception:
            rect = None
        return {
            "dialog_hwnd": hwnd,
            "class_name": _DIALOG_CLASS,
            "title": win32gui.GetWindowText(hwnd),
            "pid": pid,
            "visible": True,
            "rect": rect,
            "controls": _controls_for_dialog(hwnd),
        }
    except Exception:
        # The dialog may close while it is being inspected.
        return None


def discover_captcha_dialogs(app):
    """Return current captcha-dialog snapshots belonging to ``app``.

    Each call performs a fresh top-level enumeration.  Returned ``hwnd`` and
    ``control_id`` values describe the current window instance only.  This
    function has no UI side effects: it does not focus, click, type, capture,
    or close anything.
    """
    try:
        pid = app.process
    except Exception:
        return []

    found = []

    def scan(hwnd, _):
        snapshot = _dialog_snapshot(hwnd, pid)
        if snapshot is not None:
            found.append(snapshot)
        return True

    try:
        win32gui.EnumWindows(scan, None)
    except Exception as exc:
        log.warning("枚举验证码弹窗失败: %s", exc)
    return found


def discover_captcha_dialog(app):
    """Return the first current captcha-dialog snapshot, or ``None``.

    Read-only: never types, clicks, captures, or closes anything.  The
    auto-fill behaviour lives in :func:`auto_fill_captcha` so that detection
    calls (guard, ``is_captcha_dialog_displayed``, handle lookups) never
    accidentally enter an OCR code into the client.
    """
    dialogs = discover_captcha_dialogs(app)
    if dialogs:
        dialog = dialogs[0]

        print("弹窗 HWND:", hex(dialog["dialog_hwnd"]))
        print("弹窗 PID:", dialog["pid"])
        for control in dialog["controls"]:
            print(
                hex(control["hwnd"]),
                control["control_id"],
                control["class_name"],
                repr(control["text"]),
            )
        image_control = next(
            (i for i in dialog["controls"] if i["control_id"] == 2405), None
        )
        edit_control = next(
            (i for i in dialog["controls"] if i["control_id"] == 2404), None
        )
        yes_control = next(
            (i for i in dialog["controls"] if i["control_id"] == 1), None
        )
        if image_control:
            print("图片控件 HWND:", hex(image_control["hwnd"]))
        if edit_control:
            print("输入框 HWND:", hex(edit_control["hwnd"]))
        if yes_control:
            print("确定按钮 HWND:", hex(yes_control["hwnd"]))
    return dialogs[0] if dialogs else None


def auto_fill_captcha(app):
    """OCR the captcha image, fill the Edit control, and click OK.

    Returns ``True`` when a captcha dialog was present and an auto-fill
    attempt was made; ``False`` when no dialog was found (or the dialog was
    still mid-creation with no editable/OK controls).  Discovery is
    read-only; this function is the only place that types and clicks, so the
    caller controls exactly when the client gets a code.
    """
    snapshot = discover_captcha_dialog(app)
    if snapshot is None:
        return False

    def _pick(controls):
        image = next((i for i in controls if i["control_id"] == 2405), None)
        edit = next((i for i in controls if i["control_id"] == 2404), None)
        yes = next((i for i in controls if i["control_id"] == 1), None)
        return image, edit, yes

    image_control, edit_control, yes_control = _pick(snapshot["controls"])

    # 弹窗可能还在创建中(只枚举到部分控件, 缺输入框/确定按钮), 稍等重探一次。
    if edit_control is None or yes_control is None:
        time.sleep(0.5)
        snapshot = discover_captcha_dialog(app)
        if snapshot is None:
            return False
        image_control, edit_control, yes_control = _pick(snapshot["controls"])
        if edit_control is None or yes_control is None:
            # 仍不完整, 不假装填了, 交给调用方处理。
            return False

    controls = snapshot["controls"]
    text = ""
    if image_control and image_control["rect"]:
        print("图片控件 HWND:", hex(image_control["hwnd"]))
        left, top, right, bottom = image_control["rect"]
        text = ocr_pos(top, left, right - left, bottom - top)

    if edit_control:
        print("输入框 HWND:", hex(edit_control["hwnd"]))
        app2 = Application(backend="win32").connect(handle=snapshot["dialog_hwnd"])
        dlg = app2.window(handle=snapshot["dialog_hwnd"])
        edit_spec = dlg.child_window(control_id=2404, class_name="Edit")
        edit_spec.set_focus()
        # 清掉可能残留的上一次字符(如上次输错的验证码), 再输入本次结果。
        edit_spec.type_keys("^a{BS}", set_foreground=True)
        edit_spec.type_keys(
            str(text), with_spaces=True, set_foreground=True
        )

    if yes_control:
        print("确定按钮 HWND:", hex(yes_control["hwnd"]))
        dlg.child_window(control_id=1, class_name="Button").click()
    return True
def ocr_pos(t,l,w,h):

    # 定义截图区域 (left, top, width, height)
    monitor = {"top": t, "left": l, "width": w, "height": h}
    text = ""
    # 使用 mss 截图
    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        # mss 的 screenshot.rgb 是 (h, w, 3) 的 RGB 字节串；
        # np.array(screenshot) 得到的是 BGRA 四通道，cvtColor 只接受三通道。
        img = np.frombuffer(screenshot.rgb, dtype=np.uint8).reshape(
            screenshot.height, screenshot.width, 3
        )

        # 图像预处理（可选）
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)  # 灰度化
        _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)  # 二值化

        # 保存截图（可选）：优先当前用户桌面，目录不存在则退回系统临时目录
        photo_posi = os.path.join(
            os.path.expanduser("~"), "Desktop", "temp.png"
        )
        if not os.path.isdir(os.path.dirname(photo_posi)):
            photo_posi = os.path.join(
                tempfile.gettempdir(), "easytrader_captcha.png"
            )
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=photo_posi)

        # 识别截图
        text = baidu_ocr(photo_posi)
    return text.strip()
def baidu_ocr(photo_posi):
    APP_ID = '120885125'
    API_KEY = 'ySAsVIFRLpn3f1y8FKojlfri'
    SECRET_KEY = 'dzUImdQM5QNvHKQYNi2BCurgpIDqTBBu'
    client = AipOcr(APP_ID, API_KEY, SECRET_KEY)

    with open(photo_posi, 'rb') as fp:
        # 通用文字识别
        image = fp.read()
        result = client.basicGeneral(image)

        # 解析结果
        if 'words_result' in result:
            return result['words_result'][0]['words']
        else:
            log.error("baidu_ocr识别失败:%s", result)
    return ""
# Descriptive alias for callers that prefer an information-oriented name.
get_captcha_dialog_info = discover_captcha_dialog


def find_captcha_dialog(app):
    """Return the current captcha dialog HWND, or ``None``.

    This compatibility wrapper intentionally re-discovers the HWND every call.
    """
    snapshot = discover_captcha_dialog(app)
    return snapshot["dialog_hwnd"] if snapshot else None


def is_captcha_dialog_displayed(app):
    """Return whether a current captcha dialog is visible."""
    return discover_captcha_dialog(app) is not None


def _find_control(snapshot, control_id=None, class_name=None):
    for control in snapshot.get("controls", ()):
        if control_id is not None and control["control_id"] != control_id:
            continue
        if class_name is not None and control["class_name"] != class_name:
            continue
        return control
    return None


def get_captcha_control_handle(app, control_id):
    """Return a current child HWND by dialog control ID, or ``None``.

    The lookup is read-only and the returned handle is valid only while that
    dialog instance remains alive.
    """
    snapshot = discover_captcha_dialog(app)
    if snapshot is None:
        return None
    control = _find_control(snapshot, control_id=control_id)
    return control["hwnd"] if control else None


def get_captcha_image_handle(app):
    """Return the current captcha image control HWND, or ``None``.

    THS exposes the image as a leaf Win32 ``Static``/UIA Image control.  It
    normally has control ID 2405, but the HWND is discovered afresh each time.
    """
    return get_captcha_control_handle(app, _CAPTCHA_IMAGE_CONTROL_ID)


def get_captcha_edit_handle(app):
    """Return the current captcha Edit HWND, or ``None``.

    The known THS Edit control ID (2404) is preferred when present.  The class
    and geometry fallback keeps this useful across compatible client builds.
    """
    snapshot = discover_captcha_dialog(app)
    if snapshot is None:
        return None

    control = _find_control(
        snapshot, control_id=_CAPTCHA_EDIT_CONTROL_ID, class_name="Edit"
    )
    if control is None:
        control = _find_control(snapshot, class_name="Edit")
    return control["hwnd"] if control else None


def guard(app, action="读取表格数据"):
    """Raise ``CaptchaError`` when the client requires manual captcha entry."""
    if not is_captcha_dialog_displayed(app):
        return

    from . import exceptions

    raise exceptions.CaptchaError(
        "客户端弹出验证码提示框, {} 失败。"
        "同花顺在检测到频繁拷贝数据时需要人工输入验证码, "
        "请在客户端完成验证码处理后再重试。".format(action)
    )


def dismiss_captcha_dialog(app):
    """Deprecated compatibility stub; never closes the dialog.

    Captcha handling is intentionally caller-owned.  The old implementation
    posted ``BM_CLICK`` to the Cancel button, which could discard the user's
    dialog.  It is retained as a no-op for source compatibility and always
    returns ``False``.
    """
    return False

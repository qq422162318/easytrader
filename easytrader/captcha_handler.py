# -*- coding: utf-8 -*-
"""Read-only discovery and explicit auto-filling of the THS captcha dialog.

The dialog and all of its child HWNDs are created dynamically by Windows.
Call :func:`discover_captcha_dialog` each time the dialog is needed; never
persist an HWND across a dialog close/reopen or a client restart.

Discovery only reports window metadata.  Screen capture, OCR, typing and
clicking happen only through :func:`auto_fill_captcha`; its optional OCR
packages are not required to import this module or inspect a dialog.
"""
import os
import re
import tempfile
import time

import win32con
import win32gui
import win32process
from pywinauto import Application

from . import exceptions
from .log import log

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
    attempt was made; ``False`` when no complete dialog or OCR text was
    found.  Discovery is
    read-only; this function is the only place that types and clicks, so the
    caller controls exactly when the client gets a code.
    """
    snapshot = discover_captcha_dialog(app)
    if snapshot is None:
        return False

    def _pick(snapshot):
        image = _find_control(snapshot, control_id=_CAPTCHA_IMAGE_CONTROL_ID)
        edit = _find_control(
            snapshot, control_id=_CAPTCHA_EDIT_CONTROL_ID, class_name="Edit"
        )
        yes = _find_control(snapshot, control_id=1, class_name="Button")
        return image, edit, yes
    try:
        image_control, edit_control, yes_control = _pick(snapshot)

        # 弹窗可能仍在创建中；图像、输入框及确定按钮都必须就绪。
        if (image_control is None or not image_control.get("rect")
                or edit_control is None or yes_control is None):
            time.sleep(0.5)
            snapshot = discover_captcha_dialog(app)
            if snapshot is None:
                return False
            image_control, edit_control, yes_control = _pick(snapshot)
            if (image_control is None or not image_control.get("rect")
                    or edit_control is None or yes_control is None):
                return False

        left, top, right, bottom = image_control["rect"]
        if right <= left or bottom <= top:
            return False
        text = ocr_pos(top, left, right - left, bottom - top).strip()
        if not re.fullmatch(r"[0-9]{4}", text):
            # 此拷贝验证码为四位数字；空白/残缺识别保留人工输入，不点击确定。
            return False

        app2 = Application(backend="win32").connect(handle=snapshot["dialog_hwnd"])
        dlg = app2.window(handle=snapshot["dialog_hwnd"])
        edit_spec = dlg.child_window(control_id=_CAPTCHA_EDIT_CONTROL_ID,
                                     class_name="Edit")
        # 同花顺的 Edit 不可靠地支持 Ctrl+A/文本读回；不能因读回空串重填。
        # 保持弹窗在前台、明确设置键盘焦点，再选中整行替换原有验证码。
        dlg.set_focus()
        edit_spec.set_keyboard_focus()
        edit_spec.type_keys(
            "{HOME}+{END}{BACKSPACE}", set_foreground=False,
            vk_packet=False, pause=0.1,
        )
        edit_spec.type_keys(
            text, with_spaces=True, set_foreground=False,
            vk_packet=False, pause=0.1,
        )
        # 激活本次发现的验证码按钮，避免模拟鼠标点击未触发确认。
        win32gui.SendMessage(yes_control["hwnd"], win32con.BM_CLICK, 0, 0)
    except Exception as exc:
        # SDK 异常可能含带凭据的 OAuth URL，不打印完整异常内容。
        log.warning("自动填写验证码失败 (%s)", type(exc).__name__)
        return False
    return True


def ocr_pos(t, l, w, h):
    try:
        import mss
    except ImportError as exc:
        raise exceptions.CaptchaError(
            "自动填写验证码需要 mss，请安装后重试或手工填写验证码"
        ) from exc

    monitor = {"top": t, "left": l, "width": w, "height": h}
    # 每次截图使用独立临时目录，不覆盖桌面文件，也不与并发调用串图。
    with tempfile.TemporaryDirectory(prefix="easytrader-captcha-") as folder:
        photo_posi = os.path.join(folder, "captcha.png")
        with mss.mss() as sct:
            screenshot = sct.grab(monitor)

        # 预处理结果必须写入实际上传的 PNG，而非仍上传 screenshot.rgb。
        from PIL import Image, ImageOps
        width, height = screenshot.size
        size = (width * 3, height * 3)
        try:
            import cv2
            import numpy as np
        except ImportError:
            # Pillow 已是项目依赖；没有可选 OpenCV 时仍执行相同预处理。
            image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            gray = ImageOps.grayscale(image).resize(size, Image.Resampling.BICUBIC)
            binary = gray.point(lambda value: 255 if value > 150 else 0)
        else:
            # mss 原始字节为 BGRA，不能按三通道 RGB/BGR 处理。
            image = np.frombuffer(screenshot.bgra, dtype=np.uint8).reshape(height, width, 4)
            gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
            gray = cv2.resize(gray, size, interpolation=cv2.INTER_CUBIC)
            _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
            binary = Image.fromarray(binary)
            gray = Image.fromarray(gray)
        # 留白避免首尾数字贴边；图像仍只包含验证码控件。
        ImageOps.expand(binary, border=8, fill=255).save(photo_posi, format="PNG")
        text = baidu_ocr(photo_posi).strip()
        if re.fullmatch(r"[0-9]{4}", text):
            return text
        # 二值化偶尔切断笔画；不重新截图、不重填，最多再试同图的灰度版本。
        ImageOps.expand(gray, border=8, fill=255).save(photo_posi, format="PNG")
        return baidu_ocr(photo_posi).strip()


def baidu_ocr(photo_posi):
    try:
        from aip import AipOcr
    except ImportError as exc:
        raise exceptions.CaptchaError(
            "自动填写验证码需要 baidu-aip，请安装后重试或手工填写验证码"
        ) from exc
    APP_ID = '120885125'
    API_KEY = 'ySAsVIFRLpn3f1y8FKojlfri'
    SECRET_KEY = 'dzUImdQM5QNvHKQYNi2BCurgpIDqTBBu'
    client = AipOcr(APP_ID, API_KEY, SECRET_KEY)

    with open(photo_posi, 'rb') as fp:
        # 通用文字识别
        image = fp.read()
        result = client.basicGeneral(image)

        # 解析结果
        words_result = result.get('words_result') or []
        if words_result:
            # 验证码可能被识别成多个片段，按百度返回顺序拼接而非只取首段。
            text = "".join(item.get("words", "") for item in words_result)
            # 干扰线可能被识别成 ◆ 等符号。仅去掉非字母数字字符；
            # 保留疑似字母，让 auto_fill_captcha 的四位数字校验拒绝误识别。
            return "".join(char for char in text if char.isalnum())
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

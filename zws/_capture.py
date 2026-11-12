"""自检用：把一个窗口的内容截成图片。只在 Windows 上可用，需要 Pillow；正常使用浮窗时不会导入这个文件。

用的是 PrintWindow，截的是窗口自己画的内容，不是屏幕。所以自检时窗口可以几乎全透明、鼠标穿透，
不挡主人正在做的事，截出来的图也不会带进桌面上的其他东西。
"""
import ctypes
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x20, 0x80000, 0x08000000
PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _api():
    u, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    u.GetParent.restype, u.GetParent.argtypes = wintypes.HWND, [wintypes.HWND]
    u.GetWindowLongW.restype, u.GetWindowLongW.argtypes = ctypes.c_long, [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongW.restype, u.SetWindowLongW.argtypes = ctypes.c_long, [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowDC.restype, u.GetWindowDC.argtypes = wintypes.HDC, [wintypes.HWND]
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = wintypes.HDC, [wintypes.HDC]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype, g.SelectObject.argtypes = wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    return u, g


def toplevel(tk_window):
    """Tk 窗口 → 它在系统里的顶层窗口句柄。"""
    return _api()[0].GetParent(tk_window.winfo_id())


def ghost(hwnd):
    """让窗口鼠标穿透、不抢焦点。透明度由调用方用 Tk 的 -alpha 设。"""
    u = _api()[0]
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)


def grab(hwnd):
    """窗口内容 → PIL.Image（RGB）；失败返回 None。"""
    from PIL import Image
    u, g = _api()
    rect = wintypes.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    wdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(wdc)
    bmp = g.CreateCompatibleBitmap(wdc, w, h)
    old = g.SelectObject(mdc, bmp)
    try:
        if not u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        bmi = BITMAPINFOHEADER()
        bmi.biSize, bmi.biWidth, bmi.biHeight = ctypes.sizeof(BITMAPINFOHEADER), w, -h
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return None
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        g.SelectObject(mdc, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, wdc)

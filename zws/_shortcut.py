"""Windows 快捷方式（.lnk）的读写，直接调系统的 IShellLinkW 接口。

不用 WScript.Shell：它在英文区域的 Windows 上处理不了中文路径（助手的名字、文件夹名多半是中文），
而且失败了不报错。这里全程走 Unicode 接口。只在 Windows 上导入。
"""
import ctypes
from ctypes import POINTER, byref, c_void_p, wintypes

HRESULT = ctypes.HRESULT
ole32 = ctypes.OleDLL("ole32")
shell32 = ctypes.OleDLL("shell32")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ole32.CLSIDFromString(text, byref(self))


ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, POINTER(GUID)]
ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p)]
ole32.CoTaskMemFree.argtypes = [c_void_p]
ole32.CoTaskMemFree.restype = None
shell32.SHGetKnownFolderPath.argtypes = [POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, POINTER(c_void_p)]

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
FOLDERS = {"desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "startup": "{B97D20BB-F46A-4C97-BA10-5E3608430854}"}
# 接口方法在虚表里的位置
QUERY, RELEASE = 0, 2
SET_DESCRIPTION, SET_WORKDIR, GET_ARGS, SET_ARGS, SET_PATH = 7, 9, 10, 11, 20
LOAD, SAVE = 5, 6


def _call(obj, index, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


class _Link:
    """一个 ShellLink 对象和它的 IPersistFile 接口，用完自动释放。"""

    def __enter__(self):
        self.inited = ole32.CoInitializeEx(None, 2) >= 0
        self.link, self.file = c_void_p(), c_void_p()
        ole32.CoCreateInstance(byref(GUID(CLSID_ShellLink)), None, 1, byref(GUID(IID_IShellLinkW)), byref(self.link))
        _call(self.link, QUERY, HRESULT, [POINTER(GUID), POINTER(c_void_p)], byref(GUID(IID_IPersistFile)), byref(self.file))
        return self

    def __exit__(self, *exc):
        for obj in (self.file, self.link):
            if obj:
                _call(obj, RELEASE, wintypes.ULONG, [])
        if self.inited:
            ole32.CoUninitialize()


def known_folder(where):
    """'desktop' / 'startup' → 这台电脑上对应文件夹的路径（桌面被 OneDrive 接管时也对）。"""
    out = c_void_p()
    shell32.SHGetKnownFolderPath(byref(GUID(FOLDERS[where])), 0, None, byref(out))
    try:
        return ctypes.wstring_at(out)
    finally:
        ole32.CoTaskMemFree(out)


def write(path, target, args, workdir, description=""):
    """写一个快捷方式。失败抛 OSError。"""
    with _Link() as k:
        _call(k.link, SET_PATH, HRESULT, [wintypes.LPCWSTR], str(target))
        _call(k.link, SET_ARGS, HRESULT, [wintypes.LPCWSTR], str(args))
        _call(k.link, SET_WORKDIR, HRESULT, [wintypes.LPCWSTR], str(workdir))
        _call(k.link, SET_DESCRIPTION, HRESULT, [wintypes.LPCWSTR], str(description)[:250])
        _call(k.file, SAVE, HRESULT, [wintypes.LPCWSTR, wintypes.BOOL], str(path), True)


def read_args(path):
    """读一个快捷方式的启动参数；读不了返回 None。"""
    try:
        with _Link() as k:
            _call(k.file, LOAD, HRESULT, [wintypes.LPCWSTR, wintypes.DWORD], str(path), 0)
            buf = ctypes.create_unicode_buffer(4096)
            _call(k.link, GET_ARGS, HRESULT, [wintypes.LPWSTR, ctypes.c_int], buf, 4096)
            return buf.value
    except OSError:
        return None

"""助手的文件夹管理：新建、列出、启动浮窗、建快捷方式、移进回收区。

每个助手一个文件夹，默认在仓库目录下的 assistants/（已被 .gitignore 排除）；
设了环境变量 ZWS_HOME 就放到那里。
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import schema, store

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "assistant.pyw"


class HomeError(Exception):
    pass


def base_dir():
    p = Path(os.environ["ZWS_HOME"]) if os.environ.get("ZWS_HOME") else ROOT / "assistants"
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(name):
    """助手名 → 文件夹名：只留文字、数字、下划线和连字符（中文可以），最长 24。"""
    s = re.sub(r"[^\w\-]", "", (name or "").strip(), flags=re.UNICODE).strip("_-")[:24]
    return s or "assistant"


def home_of(slug):
    """文件夹名 → 路径。名字必须是 safe_name 能产出的那种，防止跳出 assistants 目录。"""
    if not isinstance(slug, str) or not slug or slug != safe_name(slug) or slug.startswith("."):
        raise HomeError("助手的名字不合法")
    p = base_dir() / slug
    if not (p / "plan.json").is_file():
        raise HomeError("没有这个助手")
    return p


def exists(name):
    return (base_dir() / safe_name(name) / "plan.json").is_file()


def create(plan, form=None, mode="new"):
    """写出一个助手。mode: new 另建（重名自动加序号）/ update 覆盖同名助手的配置，保留打勾记录。"""
    plan, _ = schema.normalize(plan)
    slug = safe_name(plan["assistant"]["name"])
    root = base_dir()
    if mode == "update" and (root / slug / "plan.json").is_file():
        home = root / slug
    else:
        home, i = root / slug, 2
        while home.exists():
            home = root / f"{slug}-{i}"
            i += 1
        home.mkdir(parents=True)
    store.write_json(home / "plan.json", plan)
    if form is not None:
        store.write_json(home / "form.json", form)
    meta = store.read_json(home / "meta.json", {}) or {}
    meta.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
    meta["updated"] = time.strftime("%Y-%m-%d %H:%M")
    store.write_json(home / "meta.json", meta)
    return home


def listing():
    out = []
    for d in sorted(base_dir().iterdir(), key=lambda p: p.name):
        if not d.is_dir() or d.name.startswith(".") or not (d / "plan.json").is_file():
            continue
        plan = store.read_json(d / "plan.json", {}) or {}
        a = plan.get("assistant") if isinstance(plan.get("assistant"), dict) else {}
        meta = store.read_json(d / "meta.json", {}) or {}
        out.append({"slug": d.name, "name": str(a.get("name") or d.name), "owner": str(a.get("owner") or ""),
                    "accent": schema.color(a.get("accent"), schema.ACCENTS[0]),
                    "created": str(meta.get("created", "")), "updated": str(meta.get("updated", "")),
                    "running": store.is_running(d), "path": str(d), "has_form": (d / "form.json").is_file()})
    return out


def read_form(slug):
    return store.read_json(home_of(slug) / "form.json")


def read_plan(slug):
    return store.read_json(home_of(slug) / "plan.json")


def pythonw():
    """不带黑窗口的解释器（Windows 上是 pythonw.exe）。"""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return str(w)
    return str(exe)


def launch(slug):
    """启动浮窗。已经开着就不再开第二个。返回 'started' 或 'running'。"""
    home = home_of(slug)
    if store.is_running(home):
        return "running"
    kw = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([pythonw(), str(ENTRY), "--home", str(home)], **kw)
    return "started"


def shortcut(slug, where="desktop"):
    """建快捷方式。where: desktop 桌面 / startup 开机自启。返回快捷方式的路径。

    同名的快捷方式已经在了：是这个助手自己的就更新它；是别的东西的绝不覆盖，换个名字。
    """
    home = home_of(slug)
    name = safe_name((read_plan(slug) or {}).get("assistant", {}).get("name") or slug)
    if os.name == "nt":
        from . import _shortcut
        try:
            folder = Path(os.environ.get("ZWS_SHORTCUT_DIR") or _shortcut.known_folder(where))
            args = f'"{ENTRY}" --home "{home}"'
            i = 1
            while True:
                lnk = folder / (f"{name}.lnk" if i == 1 else f"{name} ({i}).lnk")
                if not lnk.exists() or _shortcut.read_args(lnk) == args:
                    break
                i += 1
            _shortcut.write(lnk, pythonw(), args, ROOT, "桌面助手 " + name)
        except OSError as e:
            raise HomeError(f"创建快捷方式失败：{e}")
        return str(lnk)
    if where != "desktop":
        raise HomeError("开机自启目前只支持 Windows")
    sh = home / "start.sh"
    sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ENTRY}" --home "{home}"\n', encoding="utf-8")
    sh.chmod(0o755)
    return str(sh)


def open_folder(slug):
    home = home_of(slug)
    if os.name == "nt":
        os.startfile(str(home))                               # noqa: S606  打开的是自己建的文件夹
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(home)])
    return str(home)


def trash(slug):
    """不直接删：移到 assistants/.trash/ 下，反悔了可以手动挪回来。浮窗开着时不让移。"""
    home = home_of(slug)
    if store.is_running(home):
        raise HomeError("这个助手的浮窗还开着，先关掉再删")
    dest = base_dir() / ".trash"
    dest.mkdir(exist_ok=True)
    target = dest / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(home), str(target))
    return str(target)

"""重新生成 README 里的三张图。开发用，普通使用不需要。

    python tools/make_shots.py

需要：Windows、Pillow（pip install pillow）、Chrome 或 Edge。
图里的人物和数据都来自 examples/（虚构的学生「小林」），不读任何真实的助手数据。
浮窗是用几乎全透明、鼠标穿透的窗口截的，过程中不会挡住屏幕。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "img"
NOW = "2026-11-12 21:15"                                        # 周四晚上：有一条生成规则正好触发


def find_browser():
    names = ["chrome", "msedge", "chromium", "google-chrome"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("LOCALAPPDATA", "")):
        for rel in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
            p = Path(base) / rel
            if base and p.is_file():
                return str(p)
    sys.exit("没找到 Chrome 或 Edge")


def page_shot(browser, url, path, size, profile):
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1.5",
                    f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=8000", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check", f"--screenshot={path}", url],
                   check=True, capture_output=True, timeout=120)


def rounded(img, radius):
    from PIL import Image, ImageDraw
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def main():
    from PIL import Image
    from zws import homes, server, store
    tmp = Path(tempfile.mkdtemp(prefix="zws-shots-"))
    os.environ["ZWS_HOME"] = str(tmp / "assistants")
    try:
        plan = store.read_json(ROOT / "examples" / "plan-student.json")
        form = store.read_json(ROOT / "examples" / "form-student.json")
        home = homes.create(plan, form)
        OUT.mkdir(parents=True, exist_ok=True)

        # 1. 浮窗：跑一遍自检，拿日程、待办、记录三张
        shots = tmp / "shots"
        subprocess.run([sys.executable, str(ROOT / "assistant.pyw"), "--home", str(home), "--now", NOW, "--selftest", str(shots)],
                       check=True, timeout=180)
        imgs = [rounded(Image.open(shots / f"{n}.png"), 14) for n in ("schedule", "todo", "log")]
        gap, pad = 36, 44
        w = sum(i.size[0] for i in imgs) + gap * (len(imgs) - 1) + pad * 2
        h = max(i.size[1] for i in imgs) + pad * 2
        canvas = Image.new("RGB", (w, h), "#0e0c13")
        x = pad
        for im in imgs:
            canvas.paste(im, (x, pad), im)
            x += im.size[0] + gap
        canvas.save(OUT / "widget.png", optimize=True)

        # 2. 生成器：填表页和结果页
        srv, _app = server.make_server(0, strict=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}/"
        browser = find_browser()
        page_shot(browser, base + "#example=student&step=4", OUT / "studio-form.png", (1240, 860), tmp / "p1")
        page_shot(browser, base + f"#load={home.name}", OUT / "studio-result.png", (1240, 1180), tmp / "p2")
        srv.shutdown()
        for p in sorted(OUT.glob("*.png")):
            print(p.relative_to(ROOT), Image.open(p).size, f"{p.stat().st_size // 1024} KB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

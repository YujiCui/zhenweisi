"""测试共用的东西：示例表单、基线 plan、一个假的 claude 命令。测试不联网，也不调真的 Claude Code。"""
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zws import generate  # noqa: E402


def example(name):
    return json.loads((ROOT / "examples" / f"form-{name}.json").read_text(encoding="utf-8"))


def base_plan(name="student"):
    return generate.baseline(example(name))[1]


FAKE = r"""
import json, os, sys
args = sys.argv[1:]
d = os.environ["FAKE_DIR"]
if "--version" in args:
    print("9.9.9 (Claude Code)")
elif args[:2] == ["auth", "status"]:
    p = os.path.join(d, "auth.json")
    print(open(p, encoding="utf-8").read() if os.path.exists(p) else
          json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}))
elif "-p" in args:
    if os.path.exists(os.path.join(d, "reject")) and "--safe-mode" in args:
        sys.stderr.write("error: unknown option '--safe-mode'\n")
        sys.exit(1)
    if os.path.exists(os.path.join(d, "hang")):
        import time
        time.sleep(60)
    prompt = sys.stdin.buffer.read().decode("utf-8")
    n_path = os.path.join(d, "calls")
    n = int(open(n_path).read()) if os.path.exists(n_path) else 0
    open(n_path, "w").write(str(n + 1))
    open(os.path.join(d, "prompt%d.txt" % n), "w", encoding="utf-8").write(prompt)
    open(os.path.join(d, "args%d.json" % n), "w", encoding="utf-8").write(json.dumps(args, ensure_ascii=False))
    replies = sorted(f for f in os.listdir(d) if f.startswith("reply"))
    text = open(os.path.join(d, replies[min(n, len(replies) - 1)]), encoding="utf-8").read() if replies else ""
    out = {"type": "result", "is_error": False, "result": text, "duration_ms": 1200}
    sys.stdout.buffer.write(json.dumps(out).encode("utf-8"))
"""


def fake_claude(tmp, replies=(), auth=None):
    """在 tmp 里放一个假的 claude，返回它的路径。replies 按调用次序依次返回，用完了重复最后一个。"""
    tmp = Path(tmp)
    (tmp / "fake_claude.py").write_text(FAKE, encoding="utf-8")
    for i, r in enumerate(replies):
        (tmp / f"reply{i}.txt").write_text(r, encoding="utf-8")
    if auth is not None:
        (tmp / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    if os.name == "nt":
        exe = tmp / "claude.cmd"
        exe.write_text('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, tmp / "fake_claude.py"), encoding="ascii")
    else:
        exe = tmp / "claude"
        exe.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, tmp / "fake_claude.py"), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    os.environ["FAKE_DIR"] = str(tmp)
    os.environ["ZWS_CLAUDE"] = str(exe)
    return str(exe)


def fake_calls(tmp):
    p = Path(tmp) / "calls"
    return int(p.read_text()) if p.exists() else 0

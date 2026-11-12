"""调用本机的 Claude Code（命令行）。生成器的「自动」方式和浮窗的「问 …」都走这里。

不需要 API key：用的是本机 Claude Code 已有的登录。订阅登录不另计费；按量计费的登录方式会被识别出来，
由调用方决定要不要继续。每次调用都是一次性的：不带任何工具、不读项目和用户的配置、不留会话记录。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 这些变量会让 Claude Code 改用按量计费的身份（API key、第三方云等），子进程里一律去掉
ENV_DROP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR",
            "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_IDENTITY_TOKEN",
            "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_AWS_API_KEY",
            "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_GATEWAY",
            "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD")
ENV_SET = {"DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
SUBSCRIPTIONS = {"pro", "max", "team", "enterprise", "claude_pro", "claude_max", "claude_team", "claude_enterprise"}
MODELS = ("opus", "sonnet", "haiku", "fable")

# 一次性、无工具、不读本机配置的调用参数；老版本不认其中某些参数时退到 MINIMAL
ISOLATED = ["--no-session-persistence", "--safe-mode", "--restricted", "--strict-mcp-config", "--tools", "",
            "--permission-prompts", "none", "--disable-slash-commands"]


def clean_env(env=None):
    e = dict(os.environ if env is None else env)
    for k in ENV_DROP:
        e.pop(k, None)
    e.update(ENV_SET)
    return e


def _ver(p):
    for part in reversed(Path(p).parts):
        if re.fullmatch(r"\d+(\.\d+)+", part):
            return tuple(int(x) for x in part.split("."))
    return ()


def _verified(exe):
    """桌面版自带的 Claude Code：同目录有校验标记、文件大小对得上，才算下载完整。"""
    d = Path(exe).parent
    try:
        pay = json.loads((d / ".payload").read_text(encoding="utf-8"))
        ver = (d / ".verified").read_text(encoding="utf-8").strip().lower()
        return len(ver) >= 12 and ver.startswith(d.name.lower()) and Path(exe).stat().st_size == int(pay.get("size", -1))
    except (OSError, ValueError, TypeError):
        return False


def find(configured=""):
    """找本机的 Claude Code：指定的路径 → PATH → 桌面版自带的（取版本最高的）→ 常见安装位置。找不到返回 None。"""
    if configured and Path(configured).is_file():
        return str(configured)
    if os.environ.get("ZWS_CLAUDE"):
        p = os.environ["ZWS_CLAUDE"]
        return p if Path(p).is_file() else None
    w = shutil.which("claude")
    if w:
        return w
    la = os.environ.get("LOCALAPPDATA")
    if la and Path(la).exists():
        cands = [x for x in Path(la).glob("Claude-Data/*/claude-code/*/*/claude.exe") if _verified(x)]
        if cands:
            cands.sort(key=lambda x: (_ver(x), x.stat().st_mtime), reverse=True)
            return str(cands[0])
    home = Path.home()
    for x in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
              home / ".claude" / "local" / "claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        try:
            if x.is_file():
                return str(x)
        except OSError:
            pass
    return None


def _run(cmd, timeout, stdin=None, cwd=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, cwd=cwd, env=clean_env(),
                          creationflags=NO_WINDOW)


def version(exe):
    try:
        out = _run([exe, "--version"], 15).stdout.decode("utf-8", "replace")
        m = re.search(r"\d+(?:\.\d+)+", out)
        return m.group(0) if m else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def auth_status(exe):
    try:
        out = _run([exe, "auth", "status"], 20, cwd=tempfile.gettempdir()).stdout.decode("utf-8", "replace")
        i = out.find("{")
        st = json.loads(out[i:]) if i >= 0 else None
        return st if isinstance(st, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def billing(st):
    """auth status 的结果 → (方式, 说明)。方式：subscription 订阅 / metered 按量计费 / logged_out 未登录 / unknown。"""
    if not isinstance(st, dict):
        return "unknown", "读不到 Claude Code 的登录状态"
    if not st.get("loggedIn"):
        return "logged_out", "本机 Claude Code 还没登录"
    provider = str(st.get("apiProvider", ""))
    if provider and provider != "firstParty":
        return "metered", f"登录的是第三方云（{provider}），按量计费"
    method = str(st.get("authMethod", ""))
    if method != "claude.ai":
        return "metered", f"登录方式不是 Claude 订阅账号（{method or '未知'}），可能按量计费"
    if st.get("apiKeySource") not in (None, "", "none"):
        return "metered", "检测到 API key，按量计费"
    sub = str(st.get("subscriptionType") or "").lower()
    if sub and sub not in SUBSCRIPTIONS:
        return "unknown", f"订阅类型是 {sub}，不确定是否另外计费"
    return "subscription", "订阅登录，不另计费"


def extra_usage(cfg_dir=None):
    """账户有没有开「超额付费」：True 开着 / False 关着 / None 读不到。只读本机文件。"""
    p = (Path(cfg_dir) if cfg_dir else Path.home()) / ".claude.json"
    try:
        acc = json.loads(p.read_text(encoding="utf-8")).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return None
    v = acc.get("hasExtraUsageEnabled")
    return v if isinstance(v, bool) else None


def check(exe=None):
    """给界面用的一份状态：找没找到、版本、登录方式、有没有额外花钱的可能。"""
    exe = exe or find()
    if not exe:
        return {"found": False, "version": "", "mode": "missing", "text": "没找到本机的 Claude Code", "cost_risk": ""}
    mode, text = billing(auth_status(exe))
    risk = ""
    if mode == "metered":
        risk = text
    elif mode == "unknown":
        risk = text + "，没法确认会不会另外计费"
    elif mode == "subscription" and extra_usage() is True:
        risk = "账户开了超额付费：订阅额度用完后会按量扣费"
    return {"found": True, "version": version(exe), "mode": mode, "text": text, "cost_risk": risk}


def command(exe, system, model="", effort="", budget=1.0, isolated=True):
    cmd = [exe, "-p", "--output-format", "json"]
    if isolated:
        cmd += ISOLATED + ["--max-budget-usd", f"{budget:g}"]
    cmd += ["--system-prompt", system]
    if model in MODELS:
        cmd += ["--model", model]
    if effort in ("low", "medium", "high", "xhigh", "max"):
        cmd += ["--effort", effort]
    return cmd


def err_cn(raw):
    """Claude Code 的英文报错 → 中文说明。"""
    t = (raw or "").lower()
    if "not logged in" in t or "/login" in t or "invalid api key" in t:
        return "本机 Claude Code 还没登录：先在终端运行 claude 并登录"
    if "unknown option" in t or "unknown argument" in t:
        return "本机 Claude Code 的版本太旧，不认这次调用的参数；更新后再试"
    if "budget" in t:
        return "这次调用超过了单次预算上限，已停止"
    if "credit balance" in t or "billing" in t:
        return "账户提示余额或计费问题，已停止"
    if "limit" in t and ("usage" in t or "reached" in t or "reset" in t):
        return "订阅额度用完了，等额度恢复后再试"
    if "rate limit" in t:
        return "请求太频繁，稍后再试"
    if "overload" in t or re.search(r"\b5\d\d\b", t):
        return "服务繁忙，稍后再试"
    if "timeout" in t or "timed out" in t:
        return "连接超时，稍后再试"
    if not t:
        return "没有返回内容"
    return "调用失败：" + (raw or "").strip().splitlines()[0][:120]


def parse_result(out, err=b"", rc=0):
    """claude -p --output-format json 的输出 → dict(ok, text, err, secs)。"""
    txt = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
    et = err.decode("utf-8", "replace") if isinstance(err, bytes) else (err or "")
    data = None
    i = txt.find("{")
    if i >= 0:
        try:
            data = json.loads(txt[i:])
        except ValueError:
            j = txt.rfind("\n{")
            try:
                data = json.loads(txt[j + 1:]) if j >= 0 else None
            except ValueError:
                data = None
    if not isinstance(data, dict):
        raw = (et.strip() or txt.strip() or f"退出码 {rc}")[:500]
        return {"ok": False, "text": "", "err": err_cn(raw), "raw": raw, "secs": None}
    res = str(data.get("result") or "").strip()
    bad = bool(data.get("is_error")) or not res
    raw = (res or str(data.get("subtype") or data.get("terminal_reason") or ""))[:500] if bad else ""
    return {"ok": not bad, "text": "" if bad else res, "err": err_cn(raw) if bad else "", "raw": raw,
            "secs": round((data.get("duration_ms") or 0) / 1000, 1)}


def run(exe, user, system, model="", effort="", timeout=420, cancel=None, budget=1.0):
    """跑一次。user 走标准输入。返回 dict(ok, text, err, secs)；被取消时 err 为「已取消」。"""
    work = tempfile.mkdtemp(prefix="zws-")           # 空目录：不带进任何项目的说明文件
    try:
        res = None
        for isolated in (True, False):
            res = _once(command(exe, system, model, effort, budget, isolated), user, timeout, cancel, work)
            low = (res.get("raw") or "").lower()
            if res["ok"] or not isolated or not ("unknown option" in low or "unknown argument" in low):
                break
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _once(cmd, user, timeout, cancel, cwd):
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, env=clean_env(), creationflags=NO_WINDOW)
    except OSError as e:
        return {"ok": False, "text": "", "err": f"启动 Claude Code 失败：{e}", "raw": str(e), "secs": None}
    box = {}

    def pump():
        try:
            box["out"], box["err"] = proc.communicate(user.encode("utf-8"))
        except (OSError, ValueError) as e:
            box["exc"] = e

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    t0 = time.time()
    why = ""
    while th.is_alive():
        th.join(0.2)
        if cancel is not None and cancel.is_set():
            why = "已取消"
        elif time.time() - t0 > timeout:
            why = f"超过 {int(timeout)} 秒没有结果，已停止"
        if why:
            try:
                proc.kill()
            except OSError:
                pass
            th.join(5)
            return {"ok": False, "text": "", "err": why, "raw": why, "secs": round(time.time() - t0, 1)}
    if "exc" in box:
        return {"ok": False, "text": "", "err": f"读取 Claude Code 的输出失败：{box['exc']}", "raw": str(box["exc"]), "secs": None}
    res = parse_result(box.get("out", b""), box.get("err", b""), proc.returncode)
    if not res.get("secs"):
        res["secs"] = round(time.time() - t0, 1)
    return res

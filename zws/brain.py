"""浮窗的决策层：纯本地计算，不联网、不调模型。

输入 plan、当天状态、历史和当前时间，输出：今天的时间块、现在的状态、要提醒的事（带依据）、
近 14 天的统计、指令解析、记忆检索、问答时交给模型的当前状态。
"""
import math
import re
from datetime import date, datetime, timedelta

from . import schema as S
from .schema import WD, WD_CN, PASSIVE, BLOCK_TYPES, hm, fmt

SMALL_N = 6     # 样本不到 6 个的统计照实标「样本少」；按反馈降级也至少要 6 次


# ---------------- 时间 ----------------

def ds_min(plan):
    return hm(plan["day_start"])


def logical(now, plan):
    """(逻辑日, 距逻辑日 0 点的分钟数)。换日时间之前算前一天，分钟数接着往上数。"""
    m = now.hour * 60 + now.minute
    if m < ds_min(plan):
        return now.date() - timedelta(days=1), m + 1440
    return now.date(), m


def wd_of(d):
    return WD[d.weekday()]


def wake_m(plan, d):
    return hm(plan["wake"].get(wd_of(d), plan["wake"]["default"]))


def sleep_m(plan, d):
    s = hm(plan["sleep"].get(wd_of(d), plan["sleep"]["default"]))
    return s + 1440 if s <= wake_m(plan, d) else s


def dur(m):
    m = int(round(m))
    if m < 60:
        return f"{m} 分"
    return f"{m // 60} 小时" + (f" {m % 60} 分" if m % 60 else "")


def due_text(due, today):
    """截止时间怎么说：今天只说钟点，明天说「明天 10:00」，再远说日期。只有日期的不带钟点。"""
    n = (due.date() - today).days
    day = "" if n == 0 else ("明天" if n == 1 else f"{due.month}/{due.day}")
    clock = "" if due.strftime("%H:%M") == "23:59" else f"{due:%H:%M}"
    return " ".join(x for x in (day, clock) if x) or "今天"


def days_left(iso_date, d):
    return (date.fromisoformat(iso_date) - d).days


# ---------------- 今天的时间块 ----------------

def block_id(b):
    return f"{b['t']}|{b['name']}"


def blocks_for(plan, d):
    """某一天的时间块：按星期取模板、按 days 过滤、算出起止分钟，重叠的截到下一块开始。"""
    wd, ds = wd_of(d), ds_min(plan)
    rows = []
    for b in plan["templates"].get(plan["week"][wd], []):
        if b.get("days") and wd not in b["days"]:
            continue
        s = hm(b["t"])
        rows.append((s + 1440 if s < ds else s, b))
    rows.sort(key=lambda x: x[0])
    sl = sleep_m(plan, d)
    out = []
    for i, (s, b) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else None
        if b.get("m"):
            e = s + b["m"]
        elif nxt is not None:
            e = nxt
        else:
            e = max(sl, s + 30)
        if nxt is not None and e > nxt:
            e = nxt
        if e <= s:
            continue
        out.append({"id": block_id(b), "s": s, "e": e, "t": b["t"], "name": b["name"], "type": b["type"],
                    "dnd": bool(b.get("dnd")), "note": b.get("note", ""), "fixed": bool(b.get("fixed"))})
    return out


# ---------------- 当天状态里的几项读数 ----------------

def open_todos(state):
    return [t for t in state.get("todos", []) if not t.get("done")]


def todo_due(t):
    try:
        return datetime.strptime(t["due"], "%Y-%m-%d %H:%M") if t.get("due") else None
    except (ValueError, TypeError):
        return None


def intake(state):
    food = state.get("food", [])
    return sum(f.get("kcal", 0) for f in food), sum(f.get("p", 0) for f in food)


def review_written(plan, state):
    t = (state.get("review") or "").strip()
    return bool(t) and t != plan["review_template"].strip()


def training_today(plan, state, wd, blocks=None):
    """今天该练的训练节的键；没有返回 None。队列模式下，日程里今天没有训练块就不算该练。"""
    tr = plan["training"]
    if not plan["modules"]["training"] or not tr["programs"]:
        return None
    if tr["mode"] == "split":
        return tr["split"].get(wd)
    if not tr["queue"]:
        return None
    if plan["modules"]["schedule"] and blocks is not None and not any(b["type"] == "train" for b in blocks):
        return None
    return tr["queue"][state.get("train_idx", 0) % len(tr["queue"])]


def streak(hist, state, name, today):
    """习惯连续完成的天数（今天勾了就含今天）。"""
    n = 1 if state.get("habits", {}).get(name) else 0
    by_day = {h.get("day"): h for h in hist}
    d = today - timedelta(days=1)
    while True:
        h = by_day.get(d.isoformat())
        if not h or name not in h.get("habits_done", []):
            return n
        n += 1
        d -= timedelta(days=1)


# ---------------- 状态 ----------------

def status(plan, state, now):
    d, nowm = logical(now, plan)
    blocks = blocks_for(plan, d) if plan["modules"]["schedule"] else []
    done = set(state.get("done", {}))
    cur = next((b for b in blocks if b["s"] <= nowm < b["e"]), None)
    nxt = next((b for b in blocks if b["s"] > nowm), None)
    active = [b for b in blocks if b["type"] not in PASSIVE]
    due = [b for b in active if b["e"] <= nowm]
    late = [b for b in due if b["id"] not in done]
    until = (state.get("focus") or {}).get("until", 0)
    left = until - now.timestamp()
    focus_left = math.ceil(left / 60) if left > 0 else 0
    wk, sl = wake_m(plan, d), sleep_m(plan, d)
    return {
        "now": now, "d": d, "wd": wd_of(d), "nowm": nowm, "day_type": plan["week"][wd_of(d)],
        "blocks": blocks, "cur": cur, "nxt": nxt, "done": done, "active": active, "due": due, "late": late,
        "pct": round(100 * (len(due) - len(late)) / len(due)) if due else None,
        "wake": wk, "sleep": sl, "to_sleep": sl - nowm,
        "focus_left": focus_left, "dnd": bool((cur and cur["dnd"]) or focus_left),
    }


# ---------------- 提醒 ----------------

def _iv(iid, text, why, score, tab=None, src="内置", cooldown=120):
    return {"id": iid, "text": text, "why": why, "score": score, "tab": tab, "src": src, "cooldown": cooldown}


def base_id(iid):
    """反馈统计按这个键归类：late:… 归成 late；习惯、周期事务、联络人和生成规则各算各的。"""
    head = iid.split(":", 1)[0]
    return iid if head in ("habit", "r", "maint", "people") else head


def rule_hit(when, plan, state, st):
    nowm, ds, now = st["nowm"], ds_min(plan), st["now"]

    def adj(v):
        t = hm(v)
        return t + 1440 if t < ds else t

    for k, v in when.items():
        if k == "after":
            ok = nowm >= adj(v)
        elif k == "before":
            ok = nowm < adj(v)
        elif k == "weekdays":
            ok = st["wd"] in v
        elif k == "day_types":
            ok = st["day_type"] in v
        elif k == "habit_undone":
            ok = plan["modules"]["habits"] and not state.get("habits", {}).get(v)
        elif k == "block_type_now":
            ok = bool(st["cur"]) and st["cur"]["type"] in v
        elif k == "no_block_now":
            ok = st["cur"] is None
        elif k == "done_pct_below":
            ok = st["pct"] is not None and st["pct"] < v
        elif k == "water_pct_below":
            ok = plan["modules"]["water"] and 100 * state.get("water_ml", 0) / plan["water"]["goal_ml"] < v
        elif k == "protein_pct_below":
            target = plan["nutrition"]["protein"]
            ok = plan["modules"]["nutrition"] and target > 0 and 100 * intake(state)[1] / target < v
        elif k == "kcal_pct_above":
            target = plan["nutrition"]["kcal"]
            ok = plan["modules"]["nutrition"] and target > 0 and 100 * intake(state)[0] / target > v
        elif k == "todo_due_within_h":
            ok = plan["modules"]["todo"] and any(
                due and (due - now).total_seconds() <= v * 3600 for due in map(todo_due, open_todos(state)))
        elif k == "todo_open_at_least":
            ok = plan["modules"]["todo"] and len(open_todos(state)) >= v
        elif k == "min_to_sleep_below":
            ok = 0 < st["to_sleep"] <= v
        elif k == "past_sleep_min":
            ok = -st["to_sleep"] >= v
        elif k == "training_undone":
            ok = training_today(plan, state, st["wd"], st["blocks"]) is not None and not state.get("train_done")
        elif k == "review_undone":
            ok = plan["modules"]["review"] and not review_written(plan, state)
        else:
            ok = False
        if not ok:
            return False
    return True


def interventions(plan, state, st, hist=()):
    """现在所有成立的提醒（没排序、没过滤）。每条都带依据。"""
    m, now, nowm, d, ts = plan["modules"], st["now"], st["nowm"], st["d"], st["to_sleep"]
    out = []

    if m["schedule"]:
        recent = [b for b in st["late"] if nowm - b["e"] >= 10]
        tally = f"今天到点 {len(st['due'])} 项，已勾 {len(st['due']) - len(st['late'])} 项"
        if len(recent) >= 3:
            out.append(_iv("late", f"有 {len(recent)} 个时间块结束后没勾",
                           f"最早的是 {fmt(recent[0]['s'])}「{recent[0]['name']}」；{tally}", 60, "schedule"))
        elif recent:
            b = recent[-1]
            mins = nowm - b["e"]
            out.append(_iv(f"late:{b['id']}", f"「{b['name']}」{fmt(b['e'])} 已结束，还没勾",
                           f"结束 {dur(mins)}；{tally}", 50 + min(15, mins // 10), "schedule"))

    if m["todo"]:
        best = None
        for t in open_todos(state):
            due = todo_due(t)
            if due and (due - now).total_seconds() <= 86400 and (best is None or due < best[0]):
                best = (due, t)
        if best:
            due, t = best
            mins = (due - now).total_seconds() / 60
            name, why = t["text"][:16], f"截止 {due:%m/%d %H:%M}"
            if mins < 0:
                out.append(_iv(f"todo:{t['id']}", f"待办「{name}」已过期 {dur(-mins)}", why, 85, "todo"))
            elif mins <= 180:
                out.append(_iv(f"todo:{t['id']}", f"待办「{name}」还有 {dur(mins)} 到期", why, 80, "todo"))
            else:
                out.append(_iv(f"todo:{t['id']}", f"待办「{name}」{due_text(due, d)} 到期", why, 62, "todo"))

    if ts <= 0:
        tom = d + timedelta(days=1)
        can = 1440 + wake_m(plan, tom) - nowm
        out.append(_iv("past_sleep", f"已过睡觉线 {dur(-ts)}",
                       f"睡觉线 {fmt(st['sleep'])}；明天 {fmt(wake_m(plan, tom))} 起床，现在睡还能睡 {dur(can)}", 88))
    elif ts <= plan["wrap_before_sleep"]:
        out.append(_iv("sleep_wrap", f"距睡觉线 {ts} 分，开始收尾",
                       f"睡觉线 {fmt(st['sleep'])}，提前 {plan['wrap_before_sleep']} 分提醒", 70))

    if m["habits"]:
        for h in plan["habits"]:
            if state.get("habits", {}).get(h["name"]):
                continue
            by = hm(h["by"]) if h.get("by") else None
            if by is not None and by < ds_min(plan):
                by += 1440
            k = streak(hist, state, h["name"], d)
            why = f"已连续 {k} 天" if k else "还没有连续记录"
            iid = f"habit:{h['name']}"
            if by is not None and nowm >= by:
                out.append(_iv(iid, f"习惯「{h['name']}」过了 {fmt(by)} 还没做", why, 66, "log"))
            elif by is not None and by - nowm <= 30:
                out.append(_iv(iid, f"习惯「{h['name']}」计划 {fmt(by)} 前完成，还有 {by - nowm} 分", why, 58, "log"))
            elif by is None and 0 < ts <= 120:
                out.append(_iv(iid, f"习惯「{h['name']}」今天还没做", f"{why}；距睡觉线 {dur(ts)}", 54, "log"))

    if m["water"] and nowm > st["wake"] + 120 and ts > 0:
        goal = plan["water"]["goal_ml"]
        span = max(60, st["sleep"] - st["wake"] - 60)
        exp = goal * min(1.0, (nowm - st["wake"]) / span)
        got = state.get("water_ml", 0)
        if got < exp * 0.6:
            out.append(_iv("water", f"喝水 {got}/{goal} ml，按进度应到 {int(exp // 50 * 50)} ml",
                           "目标按清醒时间均匀分配，落后四成以上才提醒", 40, "log"))

    for key, mod, verb in (("maint", "maintenance", "到期"), ("people", "people", "")):
        if not m[mod]:
            continue
        for it in plan[mod]:
            last = state.get(key, {}).get(it["name"])
            if not last:
                continue                                    # 第一次记过之后才开始提醒
            try:
                over = (d - date.fromisoformat(last)).days - it["every"]
            except ValueError:
                continue
            if over < 0:
                continue
            why = f"每 {it['every']} 天一次，上次 {last[5:].replace('-', '/')}"
            if key == "maint":
                text = f"「{it['name']}」到期" + (f"，已超 {over} 天" if over else "")
            else:
                text = f"该联系 {it['name']} 了" + (f"，已超 {over} 天" if over else "")
                if it.get("topic"):
                    why += f"；要谈：{it['topic']}"
            out.append(_iv(f"{key}:{it['name']}", text, why, min(70, 46 + over // 2), "todo"))

    if m["countdowns"]:
        for c in plan["countdowns"] + [g for g in plan["goals"] if g.get("date")]:
            n = days_left(c["date"], d)
            if n in (0, 1, 3, 7):
                out.append(_iv(f"cd:{c['name']}", f"「{c['name']}」" + ("就是今天" if n == 0 else f"还有 {n} 天"),
                               f"日期 {c['date']}", 72 if n <= 1 else 45, "todo", cooldown=720))

    if m["review"] and not review_written(plan, state):
        last_e = max((b["e"] for b in st["active"]), default=None)
        near_sleep = 0 < ts <= max(plan["wrap_before_sleep"], 60)
        day_over = last_e is not None and nowm >= last_e and nowm - st["wake"] >= 480
        if near_sleep or day_over:
            out.append(_iv("review", "今天的复盘还没写", "复盘页按模板写几行，写完进历史", 52, "review"))

    for r in plan["rules"]:
        if not rule_hit(r["when"], plan, state, st):
            continue
        iv = _iv("r:" + r["id"], r["say"], r.get("why") or S.describe_when(r["when"]),
                 max(20, min(90, r["priority"])), r.get("tab"), "生成规则", r["cooldown_min"])
        # 同一件事一屏只出现一次：生成规则和内置提醒撞了，留生成规则的说法，分数取高的，依据并到一起
        same = {SHADOW[k](v) for k, v in r["when"].items() if k in SHADOW}
        for old in [x for x in out if x["id"] in same]:
            out.remove(old)
            iv["score"] = max(iv["score"], old["score"])
            iv["why"] = f"{iv['why'].rstrip('。；，. ')}；{old['why']}"
        out.append(iv)
    return out


# 生成规则的条件 → 说的是同一件事的内置提醒的 id
SHADOW = {
    "habit_undone": lambda v: f"habit:{v}",
    "past_sleep_min": lambda v: "past_sleep",
    "min_to_sleep_below": lambda v: "sleep_wrap",
    "review_undone": lambda v: "review",
    "water_pct_below": lambda v: "water",
}


def rank(ivs, state, st):
    """过滤静音的、勿扰时段分数不够的；采纳率低的降级；按分数排序。"""
    now_ts = st["now"].timestamp()
    mute, fb = state.get("mute", {}), state.get("feedback", {})
    out = []
    for iv in ivs:
        if mute.get(iv["id"], 0) > now_ts:
            continue
        f = fb.get(base_id(iv["id"]), {})
        a, n = f.get("adopt", 0), f.get("adopt", 0) + f.get("dismiss", 0)
        iv = dict(iv, n=n, adopt=a)
        if n >= SMALL_N and a / n < 0.2:
            iv["score"] -= 25
            iv["why"] += f"；近 {n} 次只采纳 {a} 次，已降级"
        if st["dnd"] and iv["score"] < 80:
            continue
        out.append(iv)
    out.sort(key=lambda v: -v["score"])
    return out


def idle_line(plan, st):
    """没有提醒时助手条显示的一句话。"""
    lines = plan["assistant"]["lines"]
    part = "morning" if st["nowm"] < st["wake"] + 240 else ("night" if st["to_sleep"] <= 180 else "day")
    if lines.get(part):
        return lines[part]
    if st["cur"] and st["nxt"]:                                 # 上面一行已经在显示当前块，这里说下一项
        return f"下一项 {fmt(st['nxt']['s'])} {st['nxt']['name']}"
    if st["due"]:                                               # 不在时间块里：上面一行已经在说下一项了
        return f"今天到点 {len(st['due'])} 项，已勾 {len(st['due']) - len(st['late'])} 项"
    return "现在没有要处理的事"


# ---------------- 历史与统计 ----------------

def summarize(plan, state, d):
    """一天结束时写进历史的摘要。"""
    blocks = blocks_for(plan, d) if plan["modules"]["schedule"] else []
    active = [b for b in blocks if b["type"] not in PASSIVE]
    done = set(state.get("done", {}))
    missed = [b for b in active if b["id"] not in done]
    kcal, protein = intake(state)
    return {
        "day": d.isoformat(), "wd": wd_of(d), "day_type": plan["week"][wd_of(d)],
        "blocks": len(active), "done": len(active) - len(missed), "missed_types": [b["type"] for b in missed],
        "habits": len(plan["habits"]),
        "habits_done": [h["name"] for h in plan["habits"] if state.get("habits", {}).get(h["name"])],
        "water_ml": state.get("water_ml", 0), "kcal": kcal, "protein": protein,
        "focus_min": state.get("focus_min", 0), "trained": bool(state.get("train_done")),
        "review": (state.get("review") or "").strip() if review_written(plan, state) else "",
        "notes": list(state.get("notes", [])),
    }


def insights(plan, hist, state, today):
    """近 14 天的几条统计，每条带样本量 n。"""
    out = []
    rows = [h for h in hist if h.get("blocks")][-14:]
    if rows:
        tot, dn = sum(h["blocks"] for h in rows), sum(h["done"] for h in rows)
        out.append({"text": f"近 {len(rows)} 天时间块完成率 {round(100 * dn / tot)}%", "n": len(rows)})
        by = {}
        for h in rows:
            a = by.setdefault(h["wd"], [0, 0, 0])
            a[0] += h["done"]
            a[1] += h["blocks"]
            a[2] += 1
        if len(by) >= 2:
            wd, (a, b, n) = min(by.items(), key=lambda kv: kv[1][0] / kv[1][1])
            if a / b < dn / tot - 0.1:
                out.append({"text": f"周{WD_CN[wd]}完成率最低，{round(100 * a / b)}%", "n": n})
        miss = {}
        for h in rows:
            for t in h.get("missed_types", []):
                miss[t] = miss.get(t, 0) + 1
        if miss:
            t, c = max(miss.items(), key=lambda kv: kv[1])
            if c >= 2 and t in BLOCK_TYPES:
                out.append({"text": f"最常漏的是「{BLOCK_TYPES[t][0]}」类时间块，共 {c} 次", "n": len(rows)})
    best = max(((streak(hist, state, h["name"], today), h["name"]) for h in plan["habits"]), default=None)
    if best and best[0] >= 2:
        out.append({"text": f"「{best[1]}」已连续 {best[0]} 天", "n": best[0]})
    fm = [h["focus_min"] for h in hist[-14:] if h.get("focus_min")]
    if fm:
        out.append({"text": f"有专注记录的日子平均 {round(sum(fm) / len(fm))} 分钟", "n": len(fm)})
    return out


def week_bars(plan, hist, state, st):
    """最近 7 天（含今天）的完成率，给复盘页画柱子：[(星期字, 百分比或 None, 是否今天)]。"""
    by_day = {h.get("day"): h for h in hist}
    out = []
    for i in range(6, -1, -1):
        d = st["d"] - timedelta(days=i)
        if i == 0:
            out.append((WD_CN[wd_of(d)], st["pct"], True))
        else:
            h = by_day.get(d.isoformat())
            out.append((WD_CN[wd_of(d)], round(100 * h["done"] / h["blocks"]) if h and h.get("blocks") else None, False))
    return out


# ---------------- 指令 ----------------

HELP = [
    ("待办 交报告 周五 17:00", "加一条待办，日期和时间可省"),
    ("完成 报告", "勾掉名称里含这个词的待办"),
    ("喝水", "记一杯；「喝水 500」记 500 ml"),
    ("吃 鸡胸肉 220 40", "记一餐：名称、千卡、蛋白克数"),
    ("体重 68.5", "记一次体重"),
    ("专注 25", "开始专注计时；「停止专注」结束"),
    ("记住 …", "写进记忆"),
    ("回忆 关键词", "查记忆"),
    ("忘掉 关键词", "删掉含这个词的记忆"),
    ("笔记 …", "记一条当天笔记"),
    ("办了 理发", "周期事务记一次"),
    ("联系 张三", "联络记一次"),
    ("问 …", "向本机 Claude Code 提问"),
    ("撤销", "撤销上一条指令"),
]

_CN_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_DAY_WORDS = {"今天": 0, "今晚": 0, "明天": 1, "明晚": 1, "后天": 2, "大后天": 3}


def parse_due(s, today):
    """从一句话里摘出日期和时间。返回 (剩下的文字, 'YYYY-MM-DD HH:MM' 或 None)。只有日期时按 23:59。"""
    d, hhmm = None, None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            s = s[:m.start()] + " " + s[m.end():]
        except ValueError:
            pass
    if d is None:
        m = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})[日号]?|(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", s)
        if m:
            mo, dd = int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))
            try:
                d = date(today.year, mo, dd)
                if d < today:
                    d = date(today.year + 1, mo, dd)
                s = s[:m.start()] + " " + s[m.end():]
            except ValueError:
                d = None
    if d is None:
        m = re.search(r"(下)?(?:周|星期|礼拜)([一二三四五六日天])", s)
        if m:
            target = _CN_WD[m.group(2)]
            ahead = (7 - today.weekday()) + target if m.group(1) else (target - today.weekday()) % 7
            d = today + timedelta(days=ahead)
            s = s[:m.start()] + " " + s[m.end():]
    evening = False
    if d is None:
        m = re.search(r"大后天|后天|明天|明晚|今天|今晚", s)
        if m:
            d = today + timedelta(days=_DAY_WORDS[m.group(0)])
            evening = m.group(0) in ("今晚", "明晚")
            s = s[:m.start()] + " " + s[m.end():]
    m = re.search(r"(上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*(?:[:：](\d{2})|点\s*(半|\d{1,2})?\s*分?)", s)
    if m:
        h = int(m.group(2))
        mi = int(m.group(3)) if m.group(3) else (30 if m.group(4) == "半" else int(m.group(4) or 0))
        if (m.group(1) in ("下午", "晚上") or (evening and not m.group(1))) and h < 12:
            h += 12
        if h <= 23 and mi <= 59:
            hhmm = f"{h:02d}:{mi:02d}"
            s = s[:m.start()] + " " + s[m.end():]
    if d is None and hhmm is None:
        return s.strip(), None
    s = re.sub(r"\s*(之前|以前|前|截止)\s*$", "", " ".join(s.split()))
    return s.strip(), f"{(d or today).isoformat()} {hhmm or '23:59'}"


def parse_command(s, today):
    """一句指令 → dict(kind=…)。认不出的是 unknown。"""
    s = " ".join((s or "").split())
    if not s:
        return {"kind": "empty"}
    if s in ("?", "？", "帮助", "help"):
        return {"kind": "help"}
    if s in ("撤销", "undo"):
        return {"kind": "undo"}
    if s in ("停止专注", "结束专注", "取消专注"):
        return {"kind": "focus_stop"}
    head, _, rest = s.partition(" ")
    rest = rest.strip()
    for sep in ("：", ":"):                                  # 「待办：交报告」这种写法
        if sep in head and not rest:
            head, _, rest = s.partition(sep)
            head, rest = head.strip(), rest.strip()
            break
    head = head.rstrip("：:")
    low = head.lower()
    if low in ("待办", "todo"):
        if not rest:
            return {"kind": "error", "msg": "待办后面要写内容，例：待办 交报告 周五 17:00"}
        txt, due = parse_due(rest, today)
        return {"kind": "todo", "text": (txt or rest)[:60], "due": due}
    if low in ("完成", "done"):
        return {"kind": "done", "q": rest} if rest else {"kind": "error", "msg": "完成后面要写待办名称里的词"}
    if low == "喝水" or re.fullmatch(r"喝水\d+.*", low):
        arg = rest or head[2:]
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(杯|ml|毫升)?", arg.strip(), re.I) if arg.strip() else None
        if not arg.strip():
            return {"kind": "water", "cups": 1}
        if not m:
            return {"kind": "error", "msg": "例：喝水 / 喝水 2 杯 / 喝水 500"}
        v = float(m.group(1))
        if m.group(2) == "杯" or (not m.group(2) and v <= 12):
            return {"kind": "water", "cups": v}
        return {"kind": "water", "ml": int(v)}
    if low == "吃":
        m = re.fullmatch(r"(.+?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?", rest)
        if not rest or not m:
            return {"kind": "error", "msg": "例：吃 鸡胸肉 220 40（名称、千卡、蛋白克数）"}
        return {"kind": "eat", "name": m.group(1).strip()[:16],
                "kcal": int(float(m.group(2))) if m.group(2) else None,
                "p": int(float(m.group(3))) if m.group(3) else None}
    if low == "体重":
        m = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)\s*(kg|公斤)?", rest, re.I)
        return {"kind": "weight", "kg": round(float(m.group(1)), 1)} if m else {"kind": "error", "msg": "例：体重 68.5"}
    if low == "专注":
        if not rest:
            return {"kind": "focus", "min": None}
        m = re.fullmatch(r"(\d{1,3})\s*(分|分钟|min)?", rest, re.I)
        return {"kind": "focus", "min": int(m.group(1))} if m else {"kind": "error", "msg": "例：专注 25"}
    simple = {"记住": "remember", "回忆": "recall", "忘掉": "forget", "笔记": "note", "办了": "maint",
              "联系": "contact", "问": "ask"}
    if low in simple:
        if not rest:
            return {"kind": "error", "msg": f"「{head}」后面要写内容"}
        return {"kind": simple[low], "text": rest[:400]}
    return {"kind": "unknown", "text": s}


# ---------------- 记忆 ----------------

def _grams(s):
    s = re.sub(r"\s+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def recall(q, mems, limit=5):
    """按双字重合度找记忆，原词命中的优先，再按新旧排。"""
    g = _grams(q)
    scored = []
    for m in mems:
        txt = m.get("text", "")
        inter = len(g & _grams(txt))
        hit = bool(q) and q.lower() in txt.lower()
        if not inter and not hit:
            continue
        scored.append((inter / max(1, len(g)) + (0.5 if hit else 0), m.get("ts", 0), m))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [m for _, _, m in scored[:limit]]


# ---------------- 问答的上下文 ----------------

def qa_context(plan, state, st, mems, question, limit=3000):
    """一问一答时交给模型的当前状态，不超过 limit 个字。"""
    a, m, now = plan["assistant"], plan["modules"], st["now"]
    lines = [f"现在：{now:%Y-%m-%d %H:%M}（周{WD_CN[st['wd']]}，{plan['day_types'][st['day_type']]}）",
             f"日程日：{st['d']:%m/%d}；{plan['day_start']} 之前仍算前一天"]
    who = f"助手名「{a['name']}」，语气{S.TONES[a['tone']]}"
    if a["owner"]:
        who += f"，主人称呼「{a['owner']}」"
    lines.append(who)
    if plan["owner"]["priorities"]:
        lines.append("主人的优先顺序：" + " > ".join(plan["owner"]["priorities"]))
    if plan["owner"]["floors"]:
        lines.append("主人的底线：" + "；".join(plan["owner"]["floors"]))
    if plan["goals"]:
        lines.append("目标：" + "；".join(g["name"] + (f"（{g['date']}）" if g.get("date") else "") for g in plan["goals"]))
    if st["blocks"]:
        lines.append("今天的时间块：" + "；".join(
            f"{fmt(b['s'])}–{fmt(b['e'])} {b['name']}" + ("（已勾）" if b["id"] in st["done"] else "")
            for b in st["blocks"][:24]))
        if st["pct"] is not None:
            lines.append(f"到点的时间块完成率 {st['pct']}%（{len(st['due']) - len(st['late'])}/{len(st['due'])}）")
    lines.append(f"睡觉线 {fmt(st['sleep'])}，" + (f"还有 {dur(st['to_sleep'])}" if st["to_sleep"] > 0 else f"已过 {dur(-st['to_sleep'])}"))
    if m["todo"]:
        todos = open_todos(state)[:10]
        if todos:
            lines.append("未完成待办：" + "；".join(t["text"] + (f"（{t['due']}）" if t.get("due") else "") for t in todos))
    if m["habits"] and plan["habits"]:
        lines.append("习惯：" + "；".join(
            h["name"] + ("（已做）" if state.get("habits", {}).get(h["name"]) else "（未做）") for h in plan["habits"]))
    if m["water"]:
        lines.append(f"喝水 {state.get('water_ml', 0)}/{plan['water']['goal_ml']} ml")
    if m["nutrition"]:
        kcal, p = intake(state)
        lines.append(f"已吃 {kcal} kcal、蛋白 {p} g"
                     + (f"（目标 {plan['nutrition']['kcal']} kcal、{plan['nutrition']['protein']} g）"
                        if plan["nutrition"]["kcal"] else ""))
    if state.get("notes"):
        lines.append("今天的笔记：" + "；".join(state["notes"][-5:]))
    hits = recall(question, mems, 5) if m["memory"] else []
    if hits:
        lines.append("相关记忆：" + "；".join(x["text"] for x in hits))
    return "\n".join(lines)[:limit]

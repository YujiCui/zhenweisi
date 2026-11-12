"""plan.json 的结构、默认值和校验。生成器和浮窗共用这一份。

校验是宽容的：字段缺了补默认值，写错的丢掉并记一条提示，不抛异常。
AI 产出的内容只会作为数据经过这里的校验，不会被当成代码执行。
"""
import re
from datetime import date

VERSION = 1
WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WD_CN = dict(zip(WD, "一二三四五六日"))

# 时间块类型：键 → (名称, 颜色)
BLOCK_TYPES = {
    "work": ("工作", "#5b8fe0"),
    "study": ("学习", "#a07cf2"),
    "class": ("上课", "#8a7bf0"),
    "meeting": ("会议", "#4aa3c7"),
    "create": ("创作", "#d681c8"),
    "train": ("训练", "#e5534b"),
    "meal": ("用餐", "#d4a017"),
    "chore": ("杂务", "#8fa15e"),
    "care": ("照护", "#e08a5b"),
    "commute": ("通勤", "#8b8496"),
    "social": ("社交", "#3fb27f"),
    "rest": ("休息", "#5fae9e"),
    "free": ("自由", "#5d566b"),
    "flex": ("机动", "#77708a"),
}
PASSIVE = {"free", "flex", "rest", "commute", "social"}      # 不要求勾选、不计入完成率
DEEP = {"work", "study", "class", "meeting", "create"}        # 默认勿扰的类型

# 模块：键, 名称, 说明, 默认是否开启
MODULES = [
    ("schedule", "日程", "按星期排好的时间块，到点提示，做完勾掉", True),
    ("todo", "待办", "带截止时间的待办，临期和过期会提醒", True),
    ("habits", "习惯", "每天打勾，记连续天数", True),
    ("focus", "专注", "专注计时，期间只放行紧急提醒", True),
    ("review", "复盘", "每晚按模板写几行，附近 14 天的统计", True),
    ("memory", "记忆", "「记住 …」「回忆 …」「忘掉 …」", True),
    ("countdowns", "倒计时", "重要日期还剩几天", True),
    ("water", "喝水", "按杯记录，对照当天目标", False),
    ("nutrition", "饮食", "记热量和蛋白，对照目标", False),
    ("training", "训练", "训练计划轮转，练完进下一节", False),
    ("maintenance", "周期事务", "每隔若干天要办一次的事", False),
    ("people", "联络", "该定期联系的人", False),
    ("qa", "问答", "手动向本机 Claude Code 提问，需已安装并登录", False),
]
MODULE_KEYS = [m[0] for m in MODULES]

TONES = {"direct": "直接", "warm": "温和", "strict": "严格"}
ACCENTS = ["#a07cf2", "#5b8fe0", "#35b4c4", "#3fb27f", "#d4a017", "#e5534b", "#d681c8", "#9aa0aa"]
TABS = {"schedule": "日程", "todo": "待办", "log": "记录", "review": "复盘"}

# 规则条件：键 → 值的类型
COND = {
    "after": "hm", "before": "hm",
    "weekdays": "wds", "day_types": "keys",
    "habit_undone": "habit",
    "block_type_now": "types", "no_block_now": "flag",
    "done_pct_below": "pct", "water_pct_below": "pct", "protein_pct_below": "pct", "kcal_pct_above": "pct300",
    "todo_due_within_h": "hours", "todo_open_at_least": "count",
    "min_to_sleep_below": "mins", "past_sleep_min": "mins",
    "training_undone": "flag", "review_undone": "flag",
}

DEFAULT_REVIEW = "做对了：\n做错了：\n明天最重要的一件事：\n"


class PlanError(ValueError):
    pass


# ---------------- 小工具 ----------------

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text(v, n, default=""):
    """任意值 → 去掉控制字符、截到 n 个字的字符串。"""
    if isinstance(v, bool) or v is None:
        return default
    if isinstance(v, (int, float)):
        v = str(v)
    if not isinstance(v, str):
        return default
    v = _CTRL.sub("", v.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return v[:n] if v else default


def line(v, n, default=""):
    """单行文本：换行压成空格。"""
    return " ".join(text(v, n * 2, default).split())[:n] or default


def hm(v):
    """'7:30' / '07:30' → 当天分钟数；写错返回 None。"""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*", v)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def fmt(m):
    """分钟数 → 'HH:MM'（超过 24 点的折回）。"""
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def integer(v, lo, hi, default):
    try:
        if isinstance(v, bool):
            raise ValueError
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            raise ValueError
    except (TypeError, ValueError):
        return default
    return int(max(lo, min(hi, round(x))))


def real(v, lo, hi, default):
    try:
        if isinstance(v, bool):
            raise ValueError
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            raise ValueError
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def iso(v):
    """'2027-3-1' / '2027-03-01' → '2027-03-01'；写错返回 None。"""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", v)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def slug(v, default="", n=16):
    """键名：小写字母开头，只留字母、数字、下划线，最长 n。"""
    if not isinstance(v, str):
        return default
    s = re.sub(r"[^a-z0-9_]", "", v.strip().lower().replace("-", "_").replace(" ", "_"))
    s = s.lstrip("0123456789_")[:n]
    return s or default


def color(v, default):
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v.strip()):
        return v.strip().lower()
    return default


def _list(v):
    return v if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _uniq(items, key=lambda x: x):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


# ---------------- 各段的校验 ----------------

def _assistant(v):
    v = _dict(v)
    lines = _dict(v.get("lines"))
    return {
        "name": line(v.get("name"), 12, "真维斯"),
        "owner": line(v.get("owner"), 12),
        "tone": v.get("tone") if v.get("tone") in TONES else "direct",
        "accent": color(v.get("accent"), ACCENTS[0]),
        "lines": {k: line(lines.get(k), 40) for k in ("morning", "day", "night")},
    }


def _modules(v):
    v = _dict(v)
    out = {}
    for key, _name, _desc, on in MODULES:
        out[key] = v[key] if isinstance(v.get(key), bool) else on
    return out


def _clock_map(v, default, w, label):
    """{'default': 'HH:MM', 'sat': 'HH:MM', …}；只给一个字符串也行。"""
    if isinstance(v, str):
        v = {"default": v}
    v = _dict(v)
    out = {}
    d = hm(v.get("default"))
    if d is None:
        if "default" in v:
            w.append(f"{label} 的默认时间写错了，按 {default} 处理")
        d = hm(default)
    out["default"] = fmt(d)
    for wd in WD:
        if wd in v:
            t = hm(v[wd])
            if t is None:
                w.append(f"{label} 里周{WD_CN[wd]}的时间写错了，按默认处理")
            elif fmt(t) != out["default"]:
                out[wd] = fmt(t)
    return out


def block(b, w, where=""):
    """一个时间块 → 合法的 dict；缺开始时间或名称的丢掉。"""
    if not isinstance(b, dict):
        w.append(f"{where}有一项不是时间块，已丢弃")
        return None
    t = hm(b.get("t"))
    name = line(b.get("name"), 24)
    if t is None or not name:
        w.append(f"{where}有个时间块缺开始时间或名称，已丢弃")
        return None
    typ = b.get("type")
    if typ not in BLOCK_TYPES:
        if typ:
            w.append(f"{where}「{name}」的类型 {typ} 不认识，按机动处理")
        typ = "flex"
    out = {"t": fmt(t), "name": name, "type": typ}
    if b.get("m") is not None:
        m = integer(b.get("m"), 0, 960, 0)
        if m >= 5:
            out["m"] = m
    if b.get("dnd") is True:
        out["dnd"] = True
    note = line(b.get("note"), 60)
    if note:
        out["note"] = note
    days = [d for d in WD if d in _list(b.get("days"))]
    if days and len(days) < 7:
        out["days"] = days
    if b.get("fixed") is True:
        out["fixed"] = True
    return out


def _template(v, ds, w, where):
    out = [x for x in (block(b, w, where) for b in _list(v)[:40]) if x]
    out.sort(key=lambda b: (hm(b["t"]) + (1440 if hm(b["t"]) < ds else 0)))
    out = _uniq(out, key=lambda b: (b["t"], b["name"], tuple(b.get("days", ()))))
    if len(out) > 30:
        w.append(f"{where}时间块超过 30 个，只保留前 30 个")
        out = out[:30]
    return out


def _habits(v, w):
    out = []
    for h in _list(v)[:30]:
        if isinstance(h, str):
            h = {"name": h}
        if not isinstance(h, dict):
            continue
        name = line(h.get("name"), 20)
        if not name:
            continue
        item = {"name": name}
        by = hm(h.get("by")) if h.get("by") else None
        if by is not None:
            item["by"] = fmt(by)
        elif h.get("by"):
            w.append(f"习惯「{name}」的完成时间写错了，已忽略")
        out.append(item)
    return _uniq(out, key=lambda h: h["name"])[:12]


def _named_dates(v, w, label, n=12):
    out = []
    for c in _list(v)[:40]:
        if not isinstance(c, dict):
            continue
        name, d = line(c.get("name"), 24), iso(c.get("date"))
        if not name:
            continue
        if d is None:
            w.append(f"{label}「{name}」的日期写错了，已丢弃")
            continue
        out.append({"name": name, "date": d})
    return _uniq(out, key=lambda c: c["name"])[:n]


def _maintenance(v):
    out = []
    for c in _list(v)[:40]:
        if not isinstance(c, dict):
            continue
        name = line(c.get("name"), 24)
        every = integer(c.get("every"), 1, 3650, 0)
        if name and every:
            out.append({"name": name, "every": every})
    return _uniq(out, key=lambda c: c["name"])[:20]


def _people(v):
    out = []
    for c in _list(v)[:40]:
        if not isinstance(c, dict):
            continue
        name = line(c.get("name"), 16)
        every = integer(c.get("every"), 1, 365, 0)
        if not (name and every):
            continue
        item = {"name": name, "every": every}
        topic = line(c.get("topic"), 40)
        if topic:
            item["topic"] = topic
        out.append(item)
    return _uniq(out, key=lambda c: c["name"])[:20]


def _goals(v, w):
    out = []
    for g in _list(v)[:20]:
        if isinstance(g, str):
            g = {"name": g}
        if not isinstance(g, dict):
            continue
        name = line(g.get("name"), 40)
        if not name:
            continue
        item = {"name": name}
        if g.get("date"):
            d = iso(g.get("date"))
            if d:
                item["date"] = d
            else:
                w.append(f"目标「{name}」的日期写错了，已忽略")
        metric = line(g.get("metric"), 40)
        if metric:
            item["metric"] = metric
        out.append(item)
    return _uniq(out, key=lambda g: g["name"])[:8]


def _nutrition(v):
    v = _dict(v)
    kcal = integer(v.get("kcal"), 0, 6000, 0)
    food = []
    for f in _list(v.get("quick_food"))[:30]:
        if not isinstance(f, dict):
            continue
        name = line(f.get("name"), 16)
        if name:
            food.append({"name": name, "kcal": integer(f.get("kcal"), 0, 3000, 0), "p": integer(f.get("p"), 0, 300, 0)})
    return {
        "kcal": kcal if kcal >= 800 else 0,
        "protein": integer(v.get("protein"), 0, 400, 0),
        "quick_food": _uniq(food, key=lambda f: f["name"])[:12],
    }


def _training(v, w):
    v = _dict(v)
    programs = {}
    for key, p in list(_dict(v.get("programs")).items())[:12]:
        k = slug(key)
        if not k or not isinstance(p, dict):
            continue
        ex = []
        for e in _list(p.get("ex"))[:20]:
            if isinstance(e, dict):
                e = [e.get("name"), e.get("sets"), e.get("reps")]
            if not isinstance(e, (list, tuple)) or not e:
                continue
            name = line(e[0], 16)
            if not name:
                continue
            sets = integer(e[1] if len(e) > 1 else 0, 0, 20, 0)
            reps = line(e[2] if len(e) > 2 else "", 10)
            ex.append([name, sets, reps])
        programs[k] = {"name": line(p.get("name"), 16, k), "ex": ex[:12]}
    queue = [slug(q) for q in _list(v.get("queue"))[:30]]
    bad = [q for q in queue if q not in programs]
    if bad:
        w.append("训练队列里有不存在的训练节，已去掉")
    queue = [q for q in queue if q in programs][:14]
    if programs and not queue:
        queue = list(programs)
    split = {}
    for wd in WD:
        k = _dict(v.get("split")).get(wd)
        split[wd] = slug(k) if isinstance(k, str) and slug(k) in programs else None
    mode = v.get("mode") if v.get("mode") in ("queue", "split") else "queue"
    return {"mode": mode, "queue": queue, "split": split, "programs": programs}


def rule(r, w, habits=(), day_types=()):
    """一条规则 → 合法的 dict；没有任何可用条件或没有话术的丢掉。"""
    if not isinstance(r, dict):
        return None
    say = line(r.get("say"), 48)
    rid = slug(r.get("id"), n=28)
    if not say:
        return None
    when = {}
    for k, v in _dict(r.get("when")).items():
        kind = COND.get(k)
        val = None
        if kind == "hm":
            t = hm(v)
            val = fmt(t) if t is not None else None
        elif kind == "wds":
            val = [d for d in WD if d in _list(v)] or None
        elif kind == "keys":
            val = [x for x in _list(v) if x in day_types] or None
        elif kind == "habit":
            name = line(v, 20)
            val = name if name in habits else None
        elif kind == "types":
            if isinstance(v, str):
                v = [v]
            val = [x for x in _list(v) if x in BLOCK_TYPES] or None
        elif kind == "flag":
            val = True if v is True else None
        elif kind == "pct":
            val = integer(v, 1, 100, 0) or None
        elif kind == "pct300":
            val = integer(v, 1, 300, 0) or None
        elif kind == "hours":
            val = integer(v, 1, 720, 0) or None
        elif kind == "count":
            val = integer(v, 1, 200, 0) or None
        elif kind == "mins":
            val = integer(v, 0, 720, -1)
            val = None if val < 0 else val
        if val is None:
            w.append(f"规则「{say}」的条件 {k} 不可用，已去掉")
        else:
            when[k] = val
    if not when:
        w.append(f"规则「{say}」没有可用的触发条件，已丢弃")
        return None
    out = {"id": rid or "rule", "when": when, "say": say}
    why = line(r.get("why"), 80)
    if why:
        out["why"] = why
    out["priority"] = integer(r.get("priority"), 1, 100, 50)
    if r.get("tab") in TABS:
        out["tab"] = r["tab"]
    out["cooldown_min"] = integer(r.get("cooldown_min"), 10, 1440, 180)
    return out


def _rules(v, w, habits, day_types):
    out, used = [], set()
    for r in _list(v)[:40]:
        x = rule(r, w, habits, day_types)
        if not x:
            continue
        base, i = x["id"], 2
        while x["id"] in used:
            x["id"] = f"{base[:25]}_{i}"
            i += 1
        used.add(x["id"])
        out.append(x)
    if len(out) > 16:
        w.append("规则超过 16 条，只保留前 16 条")
    return out[:16]


# ---------------- 总入口 ----------------

def normalize(raw):
    """任何 dict → (合法的 plan, 提示列表)。只有输入不是对象时才抛 PlanError。"""
    if not isinstance(raw, dict):
        raise PlanError("plan 必须是一个 JSON 对象")
    w = []
    p = {"version": VERSION}
    p["assistant"] = _assistant(raw.get("assistant"))
    p["modules"] = _modules(raw.get("modules"))

    ds = hm(raw.get("day_start")) if raw.get("day_start") is not None else 300
    if ds is None or ds > 8 * 60:
        w.append("day_start 要在 00:00–08:00 之间，按 05:00 处理")
        ds = 300
    p["day_start"] = fmt(ds)
    p["wake"] = _clock_map(raw.get("wake"), "07:30", w, "wake")
    p["sleep"] = _clock_map(raw.get("sleep"), "23:30", w, "sleep")
    p["wrap_before_sleep"] = integer(raw.get("wrap_before_sleep"), 0, 180, 45)

    # 日子的类型：day_types 给名称，week 把星期映射过去，templates 是每类的时间块
    types = {}
    src = raw.get("day_types")
    if isinstance(src, list):
        src = {k: k for k in src if isinstance(k, str)}
    for k, label in list(_dict(src).items())[:10]:
        key = slug(k)
        if key:
            types[key] = line(label, 16, key)
    tpl_src = _dict(raw.get("templates"))
    if not types:
        for k in list(tpl_src)[:10]:
            if slug(k):
                types[slug(k)] = slug(k)
    if not types:
        types = {"day": "每天"}
    p["day_types"] = types
    first = next(iter(types))
    week_src = _dict(raw.get("week"))
    p["week"] = {}
    for wd in WD:
        k = slug(week_src.get(wd)) if isinstance(week_src.get(wd), str) else ""
        if k not in types:
            if wd in week_src:
                w.append(f"week 里周{WD_CN[wd]}指向不存在的类型，按「{types[first]}」处理")
            k = first
        p["week"][wd] = k
    tpl_by_slug = {slug(k): v for k, v in tpl_src.items() if isinstance(k, str)}
    p["templates"] = {k: _template(tpl_by_slug.get(k), ds, w, f"「{types[k]}」") for k in types}
    extra = [k for k in tpl_by_slug if k and k not in types]
    if extra:
        w.append("templates 里有 day_types 没登记的类型，已忽略：" + "、".join(extra[:5]))

    p["habits"] = _habits(raw.get("habits"), w)
    water = _dict(raw.get("water"))
    p["water"] = {"cup_ml": integer(water.get("cup_ml"), 50, 1000, 250),
                  "goal_ml": integer(water.get("goal_ml"), 500, 6000, 2000)}
    p["nutrition"] = _nutrition(raw.get("nutrition"))
    p["training"] = _training(raw.get("training"), w)
    p["countdowns"] = _named_dates(raw.get("countdowns"), w, "倒计时")
    p["maintenance"] = _maintenance(raw.get("maintenance"))
    p["people"] = _people(raw.get("people"))
    p["goals"] = _goals(raw.get("goals"), w)
    owner = _dict(raw.get("owner"))
    p["owner"] = {
        "priorities": _uniq([x for x in (line(s, 12) for s in _list(owner.get("priorities"))[:12]) if x])[:6],
        "floors": _uniq([x for x in (line(s, 40) for s in _list(owner.get("floors"))[:20]) if x])[:8],
    }
    p["rules"] = _rules(raw.get("rules"), w, [h["name"] for h in p["habits"]], list(types))
    p["review_template"] = text(raw.get("review_template"), 300, DEFAULT_REVIEW)
    if not p["review_template"].endswith("\n"):
        p["review_template"] += "\n"
    p["focus_min"] = integer(raw.get("focus_min"), 5, 180, 25)
    qa = _dict(raw.get("qa"))
    p["qa"] = {"model": slug(qa.get("model"), "sonnet"), "claude_path": text(qa.get("claude_path"), 300)}
    ui = _dict(raw.get("ui"))
    p["ui"] = {"alpha": round(real(ui.get("alpha"), 0.5, 1.0, 1.0), 2), "width": integer(ui.get("width"), 300, 520, 360)}

    if p["modules"]["schedule"] and not any(p["templates"].values()):
        w.append("日程模块开着，但没有任何时间块")
    return p, _uniq(w)


def default_plan():
    return normalize({})[0]


# ---------------- 给人看的说明 ----------------

def describe_when(when):
    """规则的触发条件 → 一句中文。"""
    parts = []
    g = when.get
    if g("weekdays"):
        parts.append("周" + "".join(WD_CN[d] for d in g("weekdays")))
    if g("day_types"):
        parts.append("日子类型为 " + " / ".join(g("day_types")))
    if g("after") and g("before"):
        parts.append(f"{g('after')}–{g('before')}")
    elif g("after"):
        parts.append(f"{g('after')} 之后")
    elif g("before"):
        parts.append(f"{g('before')} 之前")
    if g("block_type_now"):
        parts.append("正在" + " / ".join(BLOCK_TYPES[t][0] for t in g("block_type_now")))
    if g("no_block_now"):
        parts.append("当前没有时间块")
    if g("habit_undone"):
        parts.append(f"习惯「{g('habit_undone')}」未完成")
    if g("done_pct_below"):
        parts.append(f"时间块完成率低于 {g('done_pct_below')}%")
    if g("water_pct_below"):
        parts.append(f"喝水不到目标的 {g('water_pct_below')}%")
    if g("protein_pct_below"):
        parts.append(f"蛋白不到目标的 {g('protein_pct_below')}%")
    if g("kcal_pct_above"):
        parts.append(f"热量超过目标的 {g('kcal_pct_above')}%")
    if g("todo_due_within_h"):
        parts.append(f"有待办 {g('todo_due_within_h')} 小时内到期")
    if g("todo_open_at_least"):
        parts.append(f"未完成待办不少于 {g('todo_open_at_least')} 条")
    if g("min_to_sleep_below") is not None:
        parts.append(f"距睡觉线不到 {g('min_to_sleep_below')} 分钟")
    if g("past_sleep_min") is not None:
        parts.append(f"过了睡觉线 {g('past_sleep_min')} 分钟以上")
    if g("training_undone"):
        parts.append("今天的训练没完成")
    if g("review_undone"):
        parts.append("今天的复盘没写")
    return "，".join(parts)


def spec():
    """界面和提示词用的词表。"""
    return {
        "version": VERSION,
        "weekdays": [[d, "周" + WD_CN[d]] for d in WD],
        "block_types": [[k, v[0], v[1]] for k, v in BLOCK_TYPES.items()],
        "modules": [{"key": k, "name": n, "desc": d, "on": on} for k, n, d, on in MODULES],
        "tones": [[k, v] for k, v in TONES.items()],
        "accents": ACCENTS,
        "tabs": [[k, v] for k, v in TABS.items()],
    }

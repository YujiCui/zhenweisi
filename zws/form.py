"""需求表（网页上填的那份）的清洗。表单是不可信输入：长度、类型、取值都在这里收紧。"""
from . import schema as S
from .schema import WD, WD_CN, hm, fmt, line, text, integer

ROLES = {"student": "学生", "office": "上班", "freelance": "自由职业", "founder": "创业", "other": "其他"}
ABOUT_MAX = 2000


def blank():
    return clean({})[0]


def _clock(v, default):
    t = hm(v) if isinstance(v, str) else None
    return fmt(t) if t is not None else default


def clean(raw):
    """任意 dict → (合法的表单, 提示列表)。"""
    raw = raw if isinstance(raw, dict) else {}
    w = []
    f = {
        "owner": line(raw.get("owner"), 12),
        "assistant": line(raw.get("assistant"), 12, "真维斯"),
        "role": raw.get("role") if raw.get("role") in ROLES else "other",
        "tone": raw.get("tone") if raw.get("tone") in S.TONES else "direct",
        "accent": S.color(raw.get("accent"), S.ACCENTS[0]),
    }
    wake, sleep = S._dict(raw.get("wake")), S._dict(raw.get("sleep"))
    f["wake"] = {"workday": _clock(wake.get("workday"), "07:30"), "restday": _clock(wake.get("restday"), "09:00")}
    f["sleep"] = {"workday": _clock(sleep.get("workday"), "23:30"), "restday": _clock(sleep.get("restday"), "00:30")}
    rest = raw.get("rest_days")
    f["rest_days"] = [d for d in WD if d in rest] if isinstance(rest, list) else ["sat", "sun"]

    fixed = []
    for it in S._list(raw.get("fixed"))[:30]:
        if not isinstance(it, dict):
            continue
        name = line(it.get("name"), 16)
        s, e = hm(it.get("start")) if it.get("start") else None, hm(it.get("end")) if it.get("end") else None
        days = [d for d in WD if d in S._list(it.get("days"))]
        if not name and s is None and e is None and not days:
            continue                                            # 空行
        if not name or s is None or e is None or not days:
            w.append(f"固定安排「{name or '未命名'}」缺名称、星期或起止时间，已忽略")
            continue
        dur = (e - s) % 1440
        if dur < 10 or dur > 16 * 60:
            w.append(f"固定安排「{name}」的时长要在 10 分钟到 16 小时之间，已忽略")
            continue
        fixed.append({"name": name, "type": it.get("type") if it.get("type") in S.BLOCK_TYPES else "work",
                      "days": days, "start": fmt(s), "end": fmt(e)})
    f["fixed"] = fixed[:12]
    for wd in WD:
        spans = sorted((hm(x["start"]), hm(x["start"]) + (hm(x["end"]) - hm(x["start"])) % 1440, x["name"])
                       for x in f["fixed"] if wd in x["days"])
        for a, b in zip(spans, spans[1:]):
            if b[0] < a[1]:
                w.append(f"周{WD_CN[wd]}的固定安排「{a[2]}」和「{b[2]}」时间重叠，后一个会被截短")

    f["modules"] = S._modules(raw.get("modules"))
    f["water_goal_ml"] = integer(raw.get("water_goal_ml"), 500, 6000, 2000)
    kcal = integer(raw.get("kcal"), 0, 6000, 0)
    f["kcal"] = kcal if kcal >= 800 else 0
    f["protein"] = integer(raw.get("protein"), 0, 400, 0)
    f["train_per_week"] = integer(raw.get("train_per_week"), 0, 7, 3)
    f["train_note"] = line(raw.get("train_note"), 60)
    f["focus_min"] = integer(raw.get("focus_min"), 5, 180, 25)
    f["habits"] = [h["name"] for h in S._habits(raw.get("habits"), w)]
    f["goals"] = [{"name": g["name"], "date": g.get("date", "")} for g in S._goals(raw.get("goals"), w)]
    f["floors"] = S._uniq([x for x in (line(s, 40) for s in S._list(raw.get("floors"))[:20]) if x])[:8]
    f["countdowns"] = S._named_dates(raw.get("countdowns"), w, "倒计时")
    f["maintenance"] = S._maintenance(raw.get("maintenance"))
    f["people"] = S._people(raw.get("people"))
    f["about"] = text(raw.get("about"), ABOUT_MAX)
    return f, S._uniq(w)

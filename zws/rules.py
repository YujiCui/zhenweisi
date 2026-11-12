"""不用 AI 的基线方案：只按表单字段，用固定规则排出一份能直接用的 plan。

它有三个用途：「不用 AI」时就是最终结果；网页右侧的实时预览；交给 AI 修改的起点。
表单里「用自己的话说」那段自由文本，这里读不懂，不会用到。
"""
from . import schema as S
from .schema import WD, WD_CN, DEEP, hm, fmt

TRAIN_DAYS = {0: [], 1: ["wed"], 2: ["tue", "fri"], 3: ["mon", "wed", "fri"], 4: ["mon", "tue", "thu", "fri"],
              5: ["mon", "tue", "wed", "fri", "sat"], 6: ["mon", "tue", "wed", "thu", "fri", "sat"], 7: list(WD)}

PROGRAMS = {
    "push": ("推", [["卧推", 4, "6–8"], ["上斜哑铃推", 3, "8–10"], ["肩推", 3, "8–10"], ["侧平举", 3, "12–15"], ["三头下压", 3, "10–12"]]),
    "pull": ("拉", [["引体或下拉", 4, "6–10"], ["杠铃划船", 4, "8"], ["坐姿划船", 3, "10"], ["面拉", 3, "15"], ["弯举", 3, "10–12"]]),
    "legs": ("腿", [["深蹲", 4, "5–8"], ["罗马尼亚硬拉", 3, "8"], ["腿举", 3, "10–12"], ["腿弯举", 3, "10–12"], ["提踵", 4, "12–15"]]),
    "upper": ("上肢", [["卧推", 4, "6–8"], ["划船", 4, "8"], ["肩推", 3, "8–10"], ["下拉", 3, "10"], ["弯举", 3, "12"]]),
    "lower": ("下肢", [["深蹲", 4, "5–8"], ["罗马尼亚硬拉", 3, "8"], ["腿举", 3, "12"], ["腿弯举", 3, "12"], ["提踵", 4, "15"]]),
    "full_a": ("全身 A", [["深蹲", 3, "6–8"], ["卧推", 3, "6–8"], ["划船", 3, "8–10"], ["平板支撑", 3, "45 秒"]]),
    "full_b": ("全身 B", [["硬拉", 3, "5"], ["肩推", 3, "8"], ["下拉", 3, "8–10"], ["弓步", 3, "10"]]),
}


def fixed_by_weekday(form):
    """{星期: [(开始, 结束, 名称, 类型), …]}，按开始时间排好；跨过午夜的结束时间 > 1440。"""
    out = {wd: [] for wd in WD}
    for f in form["fixed"]:
        s = hm(f["start"])
        e = s + (hm(f["end"]) - s) % 1440
        for wd in f["days"]:
            out[wd].append((s, e, f["name"], f["type"]))
    for wd in WD:
        out[wd].sort()
    return out


def wake_sleep(form):
    """表单的作息 → plan 的 wake / sleep。睡觉时间看第二天是不是休息日。"""
    rest = set(form["rest_days"])
    wake = {"default": form["wake"]["workday"]}
    sleep = {"default": form["sleep"]["workday"]}
    for i, wd in enumerate(WD):
        if wd in rest:
            wake[wd] = form["wake"]["restday"]
        if WD[(i + 1) % 7] in rest:
            sleep[wd] = form["sleep"]["restday"]
    return wake, sleep


def _wd_text(wds):
    return "周" + "".join(WD_CN[d] for d in wds)


def group_days(form):
    """把固定安排相同的星期归成一类。返回 [(键, 名称, [星期], 固定安排, 是否休息日)]。"""
    fx = fixed_by_weekday(form)
    rest = set(form["rest_days"])
    groups = {}
    for wd in WD:
        groups.setdefault((tuple(fx[wd]), wd in rest), []).append(wd)
    rows = []
    for (items, is_rest), wds in groups.items():
        if items:
            dom = max(items, key=lambda it: it[1] - it[0])
            base = dom[3] if dom[3] in ("class", "work") else "busy"
            label = {"class": "上课日", "work": "工作日"}.get(base, dom[2][:8] + "日")
        elif is_rest:
            base, label = "rest", "休息日"
        elif form["role"] == "student":
            base, label = "free", "无课日"
        else:
            base, label = "work", "工作日"
        rows.append([base, label, wds, list(items), is_rest])
    labels = [r[1] for r in rows]
    used = {}
    out = []
    for base, label, wds, items, is_rest in rows:
        used[base] = used.get(base, 0) + 1
        key = base if used[base] == 1 else f"{base}{used[base]}"
        if labels.count(label) > 1:
            label = f"{label}（{_wd_text(wds)}）"
        out.append((key, label, wds, items, is_rest))
    return out


def _slot(placed, target, dur, lo, hi, step=15):
    """在 [lo, hi] 里找一段不和已排内容重叠、离 target 最近的 dur 分钟。找不到返回 None。"""
    best = None
    t = -(-lo // step) * step
    while t + dur <= hi:
        if all(t + dur <= a or t >= b for a, b, _ in placed):
            if best is None or abs(t - target) < abs(best - target):
                best = t
        t += step
    return best


def _template(form, wds, items, is_rest, train_wds):
    rest = set(form["rest_days"])
    W = hm(form["wake"]["restday" if is_rest else "workday"])
    sleeps = []
    for wd in wds:
        s = hm(form["sleep"]["restday" if WD[(WD.index(wd) + 1) % 7] in rest else "workday"])
        sleeps.append(s + 1440 if s <= W else s)
    SL = min(sleeps)
    placed = []

    def put(s, dur, name, typ, **kw):
        b = {"t": fmt(s), "name": name, "type": typ}
        if dur:
            b["m"] = dur
        b.update(kw)
        placed.append((s, s + (dur or 0), b))

    for s, e, name, typ in items:
        kw = {"fixed": True}
        if typ in DEEP:
            kw["dnd"] = True
        put(s, e - s, name, typ, **kw)

    # 三餐
    late = W >= 630
    t = _slot(placed, W + 20, 40 if late else 25, W + 10, W + 150)
    first_meal = t if t is not None else W + 20
    if t is not None:
        put(t, 40 if late else 25, "早午饭" if late else "早餐", "meal")
    lunch_target = first_meal + 210 if late else 735
    t = _slot(placed, lunch_target, 40, lunch_target - 60, lunch_target + 150)
    if t is not None and t + 40 <= SL - 180:
        put(t, 40, "午饭", "meal")

    # 训练：优先排在最后一个固定安排之后
    train_end = None
    mine = [d for d in wds if d in train_wds]
    if form["modules"]["training"] and mine:
        last_fixed = max((e for _, e, _, _ in items), default=None)
        target = last_fixed + 30 if last_fixed is not None and 930 <= last_fixed + 30 <= 1200 else 1020
        t = _slot(placed, target, 75, W + 150, min(SL - 120, 1305))
        if t is not None:
            kw = {"dnd": True}
            others = [d for d in wds if d not in mine]
            if others:
                kw["days"] = mine
            put(t, 75, "训练", "train", **kw)
            if others:
                placed.append((t, t + 75, {"t": fmt(t), "m": 75, "name": "机动", "type": "flex", "days": others}))
            train_end = t + 75

    dinner_target = max(1125, lunch_target + 240)
    if train_end is not None and 1050 <= train_end <= 1260:
        dinner_target = train_end + 15
    t = _slot(placed, dinner_target, 45, dinner_target - 75, dinner_target + 135)
    if t is not None and t + 45 <= SL - 90:
        put(t, 45, "晚饭", "meal")

    # 主线：学生排学习，其他人排工作；休息日不排。固定安排已经占掉的专注时间从额度里扣，
    # 一天的主线加固定安排不超过 7 小时，从早往后排，排满就停，剩下的时间留白。
    deep = sum(e - s for s, e, _, typ in items if typ in DEEP)
    budget = min(360, 420 - deep)
    main = None
    if not is_rest and budget >= 45:
        main = ("学习", "study") if form["role"] == "student" else ("工作", "work")
    if main:
        lo, hi = W + 30, min(SL - 60, 1290)
        cur = lo
        for a, b, _ in sorted(placed, key=lambda x: x[0]) + [(hi, hi, None)]:
            end = min(a, hi)
            while end - cur >= 45 and budget >= 45:
                d = min(120, end - cur, budget)
                put(cur, d, main[0], main[1], dnd=True)
                cur += d + 15
                budget -= d
            cur = max(cur, b)
    if is_rest:
        t = _slot(placed, 870, 90, 780, 1050)
        if t is not None:
            put(t, 90, "杂务与采购", "chore")

    last = max((e for _, e, _ in placed), default=W + 60)
    if SL - last >= 30:
        put(last, 0, "自由", "free")
    return [b for _, _, b in sorted(placed, key=lambda x: (x[0], x[2].get("days", [""])[0]))]


def _training(form):
    if not form["modules"]["training"] or not form["train_per_week"]:
        return {"mode": "queue", "queue": [], "programs": {}}
    n, note = form["train_per_week"], form["train_note"].lower()
    strength = any(k in note for k in ("力量", "健身", "撸铁", "举铁", "增肌", "全身"))
    if "推拉腿" in note or "ppl" in note:
        keys = ["push", "pull", "legs"]
    elif "上下肢" in note or "上肢" in note:
        keys = ["upper", "lower"]
    elif strength or not note:
        keys = ["full_a", "full_b"] if n <= 2 else (["upper", "lower"] if n == 4 else ["push", "pull", "legs"])
    else:
        return {"mode": "queue", "queue": ["main"], "programs": {"main": {"name": form["train_note"][:16], "ex": []}}}
    return {"mode": "queue", "queue": keys,
            "programs": {k: {"name": PROGRAMS[k][0], "ex": [list(e) for e in PROGRAMS[k][1]]} for k in keys}}


def build(form):
    """清洗过的表单 → (plan, 提示)。"""
    m = form["modules"]
    wake, sleep = wake_sleep(form)
    train_wds = TRAIN_DAYS[form["train_per_week"]] if m["training"] else []
    day_types, week, templates = {}, {}, {}
    for key, label, wds, items, is_rest in group_days(form):
        day_types[key] = label
        for wd in wds:
            week[wd] = key
        templates[key] = _template(form, wds, items, is_rest, train_wds)
    raw = {
        "assistant": {"name": form["assistant"], "owner": form["owner"], "tone": form["tone"], "accent": form["accent"]},
        "modules": dict(m),
        "wake": wake, "sleep": sleep,
        "day_types": day_types, "week": week, "templates": templates,
        "habits": [{"name": h} for h in form["habits"]] if m["habits"] else [],
        "water": {"goal_ml": form["water_goal_ml"]},
        "nutrition": {"kcal": form["kcal"], "protein": form["protein"]} if m["nutrition"] else {},
        "training": _training(form),
        "countdowns": form["countdowns"] if m["countdowns"] else [],
        "maintenance": form["maintenance"] if m["maintenance"] else [],
        "people": form["people"] if m["people"] else [],
        "goals": [{k: v for k, v in g.items() if v} for g in form["goals"]],
        "owner": {"floors": form["floors"]},
        "focus_min": form["focus_min"],
    }
    return S.normalize(raw)

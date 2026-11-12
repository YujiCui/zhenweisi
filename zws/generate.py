"""生成管线：表单 → 基线方案 →（可选）AI 改写 → 合并校验 → plan。

三种方式：
  offline  不用 AI，直接用基线
  claude   自动：调本机 Claude Code
  manual   手动：给出提示词，用户贴到任意 AI 聊天里，再把回答贴回来（import_reply）
AI 改不了的东西在 merge 里重新钉死：固定安排、作息、模块开关、助手名字和配色。
"""
import copy
from datetime import date, timedelta

from . import brain, claude_cli, form as F, prompt, rules, schema as S
from .schema import WD, DEEP, hm, fmt


class GenError(Exception):
    pass


class Cancelled(GenError):
    pass


# 规则条件依赖的模块：模块关着，这条规则永远不会触发，直接去掉
COND_MODULE = {"habit_undone": "habits", "water_pct_below": "water", "protein_pct_below": "nutrition",
               "kcal_pct_above": "nutrition", "todo_due_within_h": "todo", "todo_open_at_least": "todo",
               "training_undone": "training", "review_undone": "review", "done_pct_below": "schedule",
               "block_type_now": "schedule", "no_block_now": "schedule"}


def baseline(raw_form):
    form, fw = F.clean(raw_form)
    plan, pw = rules.build(form)
    return form, plan, S._uniq(fw + pw)


def _enforce_fixed(tpl, items, ds):
    """把表单里的固定安排放回模板：AI 自称 fixed 的块丢掉重放；和固定安排冲突的块截短或去掉。"""
    rows = []
    for b in tpl:
        if b.get("fixed"):
            continue
        s = hm(b["t"])
        rows.append([s + 1440 if s < ds else s, b])
    rows.sort(key=lambda r: r[0])
    out, dropped = [], []
    for i, (s, b) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else None
        e = s + b["m"] if b.get("m") else (nxt if nxt is not None else s + 60)
        keep = True
        for fs, fe, _name, _typ in items:
            if fs <= s < fe:
                keep = False
                break
            if s < fs < e:
                if fs - s >= 10:
                    b = dict(b, m=fs - s)
                    e = fs
                else:
                    keep = False
                    break
        if keep:
            out.append(b)
        else:
            dropped.append(b["name"])
    for fs, fe, name, typ in items:
        fb = {"t": fmt(fs), "m": fe - fs, "name": name, "type": typ, "fixed": True}
        if typ in DEEP:
            fb["dnd"] = True
        out.append(fb)
    return out, dropped


def merge(form, base, ai):
    """基线 + AI 给的片段 → (plan, 提示)。"""
    w = []
    out = copy.deepcopy(base)
    ai = ai if isinstance(ai, dict) else {}
    m = form["modules"]
    ds = hm(base["day_start"])
    fx = rules.fixed_by_weekday(form)
    sig = {wd: tuple(fx[wd]) for wd in WD}

    tpl = ai.get("templates")
    if m["schedule"] and isinstance(tpl, dict) and tpl:
        types = week = templates = None
        if isinstance(ai.get("week"), dict) and isinstance(ai.get("day_types"), (dict, list)):
            cand, w0 = S.normalize({"day_types": ai["day_types"], "week": ai["week"], "templates": tpl,
                                    "day_start": base["day_start"]})
            used = [k for k in cand["day_types"] if k in set(cand["week"].values())]
            same_fixed = all(len({sig[wd] for wd in WD if cand["week"][wd] == k}) == 1 for k in used)
            if same_fixed and all(cand["templates"][k] for k in used):
                types = {k: cand["day_types"][k] for k in used}
                week, templates = cand["week"], {k: cand["templates"][k] for k in used}
                w += w0
            else:
                w.append("AI 重新划分了星期，但和固定安排对不上，已沿用基线的分法")
        if types is None:
            types, week = base["day_types"], base["week"]
            cand, w0 = S.normalize({"day_types": types, "week": week, "templates": tpl, "day_start": base["day_start"]})
            w += [x for x in w0 if "没登记" not in x]
            templates = {}
            for k in types:
                if cand["templates"].get(k):
                    templates[k] = cand["templates"][k]
                else:
                    templates[k] = base["templates"][k]
                    w.append(f"AI 没给「{types[k]}」排时间块，沿用基线")
        for k in types:
            wds = [wd for wd in WD if week[wd] == k]
            templates[k], dropped = _enforce_fixed(templates[k], list(sig[wds[0]]) if wds else [], ds)
            if dropped:
                w.append(f"「{types[k]}」里和固定安排冲突的时间块已去掉：" + "、".join(dropped[:4]))
        out["day_types"], out["week"], out["templates"] = types, week, templates

    if m["habits"] and isinstance(ai.get("habits"), list):
        ai_h = S._habits(ai["habits"], w)
        by_name = {h["name"]: h for h in ai_h}
        mine = [by_name.get(h, {"name": h}) for h in form["habits"]]
        out["habits"] = mine + [h for h in ai_h if h["name"] not in set(form["habits"])]

    a = ai.get("assistant")
    if isinstance(a, dict) and isinstance(a.get("lines"), dict):
        out["assistant"]["lines"] = a["lines"]
    o = ai.get("owner")
    if isinstance(o, dict) and isinstance(o.get("priorities"), list):
        out["owner"]["priorities"] = o["priorities"]

    n = ai.get("nutrition")
    if m["nutrition"] and isinstance(n, dict):
        if not form["kcal"] and n.get("kcal"):
            out["nutrition"]["kcal"] = n["kcal"]
        if not form["protein"] and n.get("protein"):
            out["nutrition"]["protein"] = n["protein"]
        if isinstance(n.get("quick_food"), list):
            out["nutrition"]["quick_food"] = n["quick_food"]
    t = ai.get("training")
    if m["training"] and isinstance(t, dict) and isinstance(t.get("programs"), dict) and t["programs"]:
        out["training"] = t

    if "wrap_before_sleep" in ai:
        out["wrap_before_sleep"] = ai["wrap_before_sleep"]
    if m["review"] and isinstance(ai.get("review_template"), str) and ai["review_template"].strip():
        out["review_template"] = ai["review_template"]
    for key in ("countdowns", "maintenance", "people"):
        if m[key] and isinstance(ai.get(key), list):
            have = {x["name"] for x in base[key]}
            out[key] = base[key] + [x for x in ai[key] if isinstance(x, dict) and x.get("name") not in have]
    if isinstance(ai.get("goals"), list):
        metric = {g.get("name"): g.get("metric") for g in ai["goals"] if isinstance(g, dict)}
        for g in out["goals"]:
            if metric.get(g["name"]):
                g["metric"] = metric[g["name"]]
    if isinstance(ai.get("rules"), list):
        out["rules"] = ai["rules"]

    plan, w2 = S.normalize(out)
    usable = []
    for r in plan["rules"]:
        off = [k for k in r["when"] if COND_MODULE.get(k) and not m[COND_MODULE[k]]]
        if off:
            w.append(f"规则「{r['say']}」依赖没开启的模块，已去掉")
        else:
            usable.append(r)
    plan["rules"] = usable
    return plan, S._uniq(w + w2)


def _result(engine, form, plan, notes, warnings, used_ai, secs=None):
    return {"engine": engine, "form": form, "plan": plan, "notes": notes, "warnings": warnings,
            "used_ai": used_ai, "secs": secs}


def run(raw_form, engine="offline", model="", allow_cost=False, cancel=None, log=None):
    """offline 或 claude 两种方式的入口。出错抛 GenError（消息可以直接给用户看）。"""
    log = log or (lambda s: None)
    form, base, warn = baseline(raw_form)
    if engine == "offline":
        note = "按固定规则生成，没有调用 AI。"
        if form["about"]:
            note += "「用自己的话说」那段没有用到。"
        return _result(engine, form, base, [note], warn, False)
    if engine != "claude":
        raise GenError("不认识的生成方式")

    exe = claude_cli.find()
    if not exe:
        raise GenError("没找到本机的 Claude Code。可以改用「手动」或「不用 AI」。")
    info = claude_cli.check(exe)
    if info["mode"] == "logged_out":
        raise GenError("本机 Claude Code 还没登录：先在终端运行 claude 并登录，再回来生成。")
    if info["cost_risk"] and not allow_cost:
        raise GenError(info["cost_risk"] + "。确认要继续的话，勾选「我知道这次可能另外计费」。")

    user = prompt.build(form, base)
    log("已交给本机 Claude Code，等它写完")
    plan = notes = None
    secs = 0.0
    err = ""
    for attempt in (1, 2):
        r = claude_cli.run(exe, user if attempt == 1 else prompt.repair(user, err), prompt.SYSTEM,
                           model=model, cancel=cancel)
        secs += r.get("secs") or 0
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消")
        if not r["ok"]:
            raise GenError(r["err"])
        ai, notes, err = prompt.parse_reply(r["text"])
        if not err:
            plan, mw = merge(form, base, ai)
            warn = S._uniq(warn + mw)
            break
        log("输出解析失败，重试一次")
    if plan is None:
        warn.append(f"AI 的输出两次都解析失败（{err}），已退回基线方案")
        return _result(engine, form, base, [], warn, False, round(secs, 1))
    return _result(engine, form, plan, notes, warn, True, round(secs, 1))


def manual_prompt(raw_form):
    form, base, _ = baseline(raw_form)
    return prompt.manual(form, base)


def import_reply(raw_form, reply):
    """手动方式：把 AI 聊天里的回答贴回来。"""
    form, base, warn = baseline(raw_form)
    ai, notes, err = prompt.parse_reply(reply)
    if err:
        raise GenError(f"贴进来的内容不能用：{err}。让 AI 只输出 JSON 再贴一次。")
    plan, mw = merge(form, base, ai)
    return _result("manual", form, plan, notes, S._uniq(warn + mw), True)


def preview(plan, today=None):
    """给网页预览用：本周每天的时间块，加上规则的中文说明。"""
    d0 = today or date.today()
    monday = d0 - timedelta(days=d0.weekday())
    days = {}
    for i, wd in enumerate(WD):
        d = monday + timedelta(days=i)
        key = plan["week"][wd]
        days[wd] = {
            "type": key, "label": plan["day_types"][key],
            "wake": fmt(brain.wake_m(plan, d)), "sleep": fmt(brain.sleep_m(plan, d)),
            "blocks": [{"t": fmt(b["s"]), "s": b["s"], "e": b["e"], "name": b["name"], "type": b["type"],
                        "dnd": b["dnd"], "fixed": b["fixed"], "note": b["note"]}
                       for b in (brain.blocks_for(plan, d) if plan["modules"]["schedule"] else [])],
        }
    return {"today": WD[d0.weekday()], "days": days,
            "rules": [{"when": S.describe_when(r["when"]), "say": r["say"], "why": r.get("why", ""),
                       "priority": r["priority"]} for r in plan["rules"]]}

import unittest
from datetime import date, datetime

import helpers  # noqa: F401
from zws import brain, schema as S
from zws.store import new_state

TODAY = date(2026, 11, 12)                                              # 周四


def plan(**kw):
    raw = {"day_types": {"d": "每天"}, "wake": {"default": "07:00"}, "sleep": {"default": "23:00"},
           "templates": {"d": [
               {"t": "08:00", "m": 60, "name": "学习", "type": "study", "dnd": True},
               {"t": "09:00", "m": 30, "name": "休息", "type": "rest"},
               {"t": "12:00", "m": 60, "name": "午饭", "type": "meal"},
               {"t": "20:00", "name": "自由", "type": "free"}]},
           "habits": [{"name": "阅读", "by": "21:00"}, {"name": "拉伸"}]}
    raw.update(kw)
    return S.normalize(raw)[0]


def at(h, m=0, day=12):
    return datetime(2026, 11, day, h, m)


def state(**kw):
    s = new_state(TODAY.isoformat())
    s.update(kw)
    return s


def ivs(p, s, now, hist=()):
    st = brain.status(p, s, now)
    return {v["id"]: v for v in brain.interventions(p, s, st, hist)}, st


class Time(unittest.TestCase):
    def test_before_day_start_is_still_yesterday(self):
        self.assertEqual(brain.logical(at(3, 0), plan()), (date(2026, 11, 11), 1620))
        self.assertEqual(brain.logical(at(5, 0), plan()), (TODAY, 300))

    def test_sleep_after_midnight(self):
        p = plan(sleep={"default": "00:30"})
        self.assertEqual(brain.sleep_m(p, TODAY), 1470)

    def test_due_text(self):
        self.assertEqual(brain.due_text(datetime(2026, 11, 12, 21, 0), TODAY), "21:00")
        self.assertEqual(brain.due_text(datetime(2026, 11, 13, 10, 0), TODAY), "明天 10:00")
        self.assertEqual(brain.due_text(datetime(2026, 11, 20, 23, 59), TODAY), "11/20")
        self.assertEqual(brain.due_text(datetime(2026, 11, 12, 23, 59), TODAY), "今天")


class Blocks(unittest.TestCase):
    def test_ends_days_filter_and_clipping(self):
        p = plan(templates={"d": [
            {"t": "08:00", "m": 90, "name": "甲", "type": "work"},         # 和乙重叠：截到 09:00
            {"t": "09:00", "name": "乙", "type": "work"},                  # 没写时长：到下一块
            {"t": "10:00", "m": 30, "name": "只在周五", "type": "work", "days": ["fri"]},
            {"t": "11:00", "name": "丙", "type": "free"},                  # 最后一块：到睡觉线
            {"t": "00:30", "m": 20, "name": "夜宵", "type": "meal"}]})     # 0 点后：算当天晚上，排最后
        got = [(b["name"], b["s"], b["e"]) for b in brain.blocks_for(p, TODAY)]
        self.assertEqual(got, [("甲", 480, 540), ("乙", 540, 660), ("丙", 660, 1470), ("夜宵", 1470, 1490)])
        fri = [b["name"] for b in brain.blocks_for(p, date(2026, 11, 13))]
        self.assertEqual(fri, ["甲", "乙", "只在周五", "丙", "夜宵"])

    def test_status(self):
        p, s = plan(), state()
        st = brain.status(p, s, at(8, 30))
        self.assertEqual((st["cur"]["name"], st["nxt"]["name"], st["dnd"], st["pct"]), ("学习", "休息", True, None))
        st = brain.status(p, s, at(10, 0))
        self.assertEqual((st["cur"], st["nxt"]["name"], [b["name"] for b in st["late"]], st["pct"]), (None, "午饭", ["学习"], 0))
        s["done"]["08:00|学习"] = "09:01"
        st = brain.status(p, s, at(10, 0))
        self.assertEqual((st["late"], st["pct"], st["to_sleep"]), ([], 100, 780))

    def test_focus_counts_as_dnd(self):
        s = state(focus={"start": at(10, 0).timestamp(), "until": at(10, 25).timestamp(), "min": 25})
        st = brain.status(plan(), s, at(10, 5))
        self.assertEqual((st["focus_left"], st["dnd"]), (20, True))
        self.assertEqual(brain.status(plan(), s, at(10, 30))["focus_left"], 0)


class Builtins(unittest.TestCase):
    def test_late_block(self):
        got, _ = ivs(plan(), state(), at(9, 5))
        self.assertNotIn("late:08:00|学习", got)                          # 结束不到 10 分钟，先不催
        got, _ = ivs(plan(), state(), at(10, 0))
        iv = got["late:08:00|学习"]
        self.assertEqual((iv["text"], iv["score"], iv["tab"]), ("「学习」09:00 已结束，还没勾", 56, "schedule"))
        self.assertIn("今天到点 1 项，已勾 0 项", iv["why"])

    def test_three_late_blocks_become_one_line(self):
        p = plan(templates={"d": [{"t": f"{8 + i:02d}:00", "m": 50, "name": f"块{i}", "type": "work"} for i in range(4)]})
        got, _ = ivs(p, state(), at(13, 0))
        self.assertEqual(got["late"]["text"], "有 4 个时间块结束后没勾")
        self.assertFalse(any(k.startswith("late:") for k in got))

    def test_todo_deadlines(self):
        mk = lambda due: state(todos=[{"id": 1, "text": "交报告", "due": due, "done": None}])
        got, _ = ivs(plan(), mk("2026-11-12 09:00"), at(10, 0))
        self.assertEqual((got["todo:1"]["text"], got["todo:1"]["score"]), ("待办「交报告」已过期 1 小时", 85))
        got, _ = ivs(plan(), mk("2026-11-12 11:30"), at(10, 0))
        self.assertEqual((got["todo:1"]["text"], got["todo:1"]["score"]), ("待办「交报告」还有 1 小时 30 分 到期", 80))
        got, _ = ivs(plan(), mk("2026-11-13 09:00"), at(10, 0))
        self.assertEqual((got["todo:1"]["text"], got["todo:1"]["score"]), ("待办「交报告」明天 09:00 到期", 62))
        got, _ = ivs(plan(), mk("2026-11-14 09:00"), at(10, 0))
        self.assertNotIn("todo:1", got)                                 # 超过 24 小时不提
        got, _ = ivs(plan(), state(todos=[{"id": 1, "text": "x", "due": "2026-11-12 09:00", "done": "2026-11-12 08:00"}]), at(10, 0))
        self.assertNotIn("todo:1", got)

    def test_sleep_line(self):
        got, _ = ivs(plan(), state(), at(22, 30))
        self.assertEqual(got["sleep_wrap"]["text"], "距睡觉线 30 分，开始收尾")
        got, _ = ivs(plan(), state(), at(22, 0))
        self.assertNotIn("sleep_wrap", got)
        got, _ = ivs(plan(), state(), at(23, 30))
        self.assertEqual(got["past_sleep"]["text"], "已过睡觉线 30 分")
        self.assertIn("现在睡还能睡 7 小时 30 分", got["past_sleep"]["why"])
        got, _ = ivs(plan(), state(day="2026-11-11"), datetime(2026, 11, 12, 1, 0))   # 凌晨 1 点还算前一天
        self.assertEqual(got["past_sleep"]["text"], "已过睡觉线 2 小时")

    def test_habits(self):
        got, _ = ivs(plan(), state(), at(20, 0))
        self.assertNotIn("habit:阅读", got)
        got, _ = ivs(plan(), state(), at(20, 40))
        self.assertEqual((got["habit:阅读"]["text"], got["habit:阅读"]["score"]), ("习惯「阅读」计划 21:00 前完成，还有 20 分", 58))
        got, _ = ivs(plan(), state(), at(21, 10))
        self.assertEqual((got["habit:阅读"]["text"], got["habit:阅读"]["score"]), ("习惯「阅读」过了 21:00 还没做", 66))
        self.assertEqual(got["habit:拉伸"]["text"], "习惯「拉伸」今天还没做")     # 没定时间的：睡前两小时内提
        got, _ = ivs(plan(), state(habits={"阅读": True, "拉伸": True}), at(21, 10))
        self.assertFalse(any(k.startswith("habit:") for k in got))

    def test_water_only_when_far_behind(self):
        p = plan(modules={"water": True})
        got, _ = ivs(p, state(), at(15, 0))
        self.assertEqual(got["water"]["text"], "喝水 0/2000 ml，按进度应到 1050 ml")
        got, _ = ivs(p, state(water_ml=700), at(15, 0))
        self.assertNotIn("water", got)
        got, _ = ivs(plan(), state(), at(15, 0))
        self.assertNotIn("water", got)                                  # 模块没开

    def test_cycles_start_after_first_record(self):
        p = plan(modules={"maintenance": True, "people": True}, maintenance=[{"name": "换滤芯", "every": 30}],
                 people=[{"name": "爸妈", "every": 7, "topic": "视频"}])
        got, _ = ivs(p, state(), at(10, 0))
        self.assertFalse(any(k.startswith(("maint:", "people:")) for k in got))
        got, _ = ivs(p, state(maint={"换滤芯": "2026-10-12"}, people={"爸妈": "2026-11-05"}), at(10, 0))
        self.assertEqual(got["maint:换滤芯"]["text"], "「换滤芯」到期，已超 1 天")
        self.assertEqual(got["people:爸妈"]["text"], "该联系 爸妈 了")
        self.assertIn("要谈：视频", got["people:爸妈"]["why"])
        got, _ = ivs(p, state(maint={"换滤芯": "2026-11-01"}), at(10, 0))
        self.assertNotIn("maint:换滤芯", got)

    def test_countdowns_and_dated_goals(self):
        p = plan(countdowns=[{"name": "考试", "date": "2026-11-15"}, {"name": "远的", "date": "2026-11-30"}],
                 goals=[{"name": "交稿", "date": "2026-11-13"}])
        got, _ = ivs(p, state(), at(10, 0))
        self.assertEqual(got["cd:考试"]["text"], "「考试」还有 3 天")
        self.assertEqual((got["cd:交稿"]["text"], got["cd:交稿"]["score"]), ("「交稿」还有 1 天", 72))
        self.assertNotIn("cd:远的", got)

    def test_review(self):
        got, _ = ivs(plan(), state(), at(22, 30))
        self.assertIn("review", got)
        got, _ = ivs(plan(), state(review="做对了：按时睡\n"), at(22, 30))
        self.assertNotIn("review", got)
        got, _ = ivs(plan(), state(review=plan()["review_template"]), at(22, 30))   # 只有模板不算写了
        self.assertIn("review", got)


class GeneratedRules(unittest.TestCase):
    def hit(self, when, now, s=None, **plan_kw):
        p = plan(**plan_kw)
        r = S.rule({"id": "t", "when": when, "say": "x"}, [], [h["name"] for h in p["habits"]], list(p["day_types"]))
        self.assertIsNotNone(r, when)
        s = s or state()
        return brain.rule_hit(r["when"], p, s, brain.status(p, s, now))

    def test_each_condition(self):
        self.assertTrue(self.hit({"after": "09:00"}, at(9, 0)))
        self.assertFalse(self.hit({"after": "09:00"}, at(8, 59)))
        self.assertTrue(self.hit({"before": "09:00"}, at(8, 59)))
        self.assertTrue(self.hit({"after": "23:00"}, datetime(2026, 11, 13, 0, 30), state()))    # 凌晨仍算「23 点之后」
        self.assertFalse(self.hit({"before": "09:00"}, datetime(2026, 11, 13, 0, 30), state()))
        self.assertTrue(self.hit({"weekdays": ["thu"]}, at(10, 0)))
        self.assertFalse(self.hit({"weekdays": ["fri"]}, at(10, 0)))
        self.assertTrue(self.hit({"day_types": ["d"]}, at(10, 0)))
        self.assertTrue(self.hit({"habit_undone": "阅读"}, at(10, 0)))
        self.assertFalse(self.hit({"habit_undone": "阅读"}, at(10, 0), state(habits={"阅读": True})))
        self.assertTrue(self.hit({"block_type_now": ["study"]}, at(8, 30)))
        self.assertFalse(self.hit({"block_type_now": ["study"]}, at(10, 0)))
        self.assertTrue(self.hit({"no_block_now": True}, at(10, 0)))
        self.assertTrue(self.hit({"done_pct_below": 50}, at(10, 0)))
        self.assertFalse(self.hit({"done_pct_below": 50}, at(8, 30)))                          # 还没有到点的块
        self.assertTrue(self.hit({"min_to_sleep_below": 60}, at(22, 30)))
        self.assertFalse(self.hit({"min_to_sleep_below": 60}, at(23, 30)))
        self.assertTrue(self.hit({"past_sleep_min": 20}, at(23, 30)))
        self.assertFalse(self.hit({"past_sleep_min": 45}, at(23, 30)))
        self.assertTrue(self.hit({"review_undone": True}, at(10, 0)))
        todo = state(todos=[{"id": 1, "text": "a", "due": "2026-11-12 20:00", "done": None}, {"id": 2, "text": "b", "due": None, "done": None}])
        self.assertTrue(self.hit({"todo_due_within_h": 12}, at(10, 0), todo))
        self.assertFalse(self.hit({"todo_due_within_h": 2}, at(10, 0), todo))
        self.assertTrue(self.hit({"todo_open_at_least": 2}, at(10, 0), todo))
        self.assertFalse(self.hit({"todo_open_at_least": 3}, at(10, 0), todo))

    def test_module_conditions(self):
        on = {"modules": {"water": True, "nutrition": True, "training": True}, "nutrition": {"kcal": 2000, "protein": 100},
              "training": {"mode": "split", "split": {"thu": "a"}, "programs": {"a": {"name": "甲", "ex": []}}}}
        self.assertTrue(self.hit({"water_pct_below": 50}, at(10, 0), **on))
        self.assertFalse(self.hit({"water_pct_below": 50}, at(10, 0), state(water_ml=1200), **on))
        self.assertFalse(self.hit({"water_pct_below": 50}, at(10, 0)))                         # 模块没开
        food = state(food=[{"name": "x", "kcal": 2300, "p": 30}])
        self.assertTrue(self.hit({"kcal_pct_above": 110}, at(10, 0), food, **on))
        self.assertTrue(self.hit({"protein_pct_below": 50}, at(10, 0), food, **on))
        self.assertFalse(self.hit({"protein_pct_below": 20}, at(10, 0), food, **on))
        self.assertTrue(self.hit({"training_undone": True}, at(10, 0), **on))
        self.assertFalse(self.hit({"training_undone": True}, at(10, 0), state(train_done=True), **on))
        self.assertFalse(self.hit({"training_undone": True}, datetime(2026, 11, 13, 10, 0), state(day="2026-11-13"), **on))

    def test_rule_replaces_the_builtin_about_the_same_thing(self):
        p = plan(rules=[{"id": "read", "when": {"habit_undone": "阅读", "after": "20:00"}, "say": "去读书", "why": "你说想多读书",
                         "priority": 40, "tab": "log"}])
        got, _ = ivs(p, state(), at(21, 10))
        self.assertNotIn("habit:阅读", got)                               # 同一件事只留一条
        iv = got["r:read"]
        self.assertEqual((iv["text"], iv["score"], iv["src"], iv["tab"]), ("去读书", 66, "生成规则", "log"))
        self.assertEqual(iv["why"], "你说想多读书；还没有连续记录")
        self.assertIn("habit:拉伸", got)


class Ranking(unittest.TestCase):
    def test_mute_dnd_and_demotion(self):
        p = plan(rules=[{"id": "soft", "when": {"after": "08:00"}, "say": "普通提醒", "priority": 50},
                        {"id": "hard", "when": {"after": "08:00"}, "say": "紧急提醒", "priority": 90}])
        s = state()
        st = brain.status(p, s, at(8, 30))                              # 「学习」是勿扰块
        self.assertEqual([v["id"] for v in brain.rank(brain.interventions(p, s, st), s, st)], ["r:hard"])
        st = brain.status(p, s, at(10, 0))
        ids = [v["id"] for v in brain.rank(brain.interventions(p, s, st), s, st)]
        self.assertEqual(ids[:2], ["r:hard", "late:08:00|学习"])
        s["mute"]["r:hard"] = at(11, 0).timestamp()
        self.assertNotIn("r:hard", [v["id"] for v in brain.rank(brain.interventions(p, s, st), s, st)])
        s["feedback"]["late"] = {"adopt": 1, "dismiss": 4}              # 5 次：样本不够，不动
        late = [v for v in brain.rank(brain.interventions(p, s, st), s, st) if v["id"].startswith("late")][0]
        self.assertEqual((late["score"], late["n"]), (56, 5))
        s["feedback"]["late"] = {"adopt": 1, "dismiss": 5}              # 6 次里只采纳 1 次：降级
        late = [v for v in brain.rank(brain.interventions(p, s, st), s, st) if v["id"].startswith("late")][0]
        self.assertEqual(late["score"], 31)
        self.assertIn("近 6 次只采纳 1 次，已降级", late["why"])

    def test_feedback_keys(self):
        self.assertEqual(brain.base_id("late:08:00|学习"), "late")
        self.assertEqual(brain.base_id("todo:3"), "todo")
        self.assertEqual(brain.base_id("habit:阅读"), "habit:阅读")
        self.assertEqual(brain.base_id("r:phone"), "r:phone")

    def test_idle_line(self):
        p = plan(assistant={"lines": {"night": "收尾。"}})
        self.assertEqual(brain.idle_line(p, brain.status(p, state(), at(8, 30))), "下一项 09:00 休息")
        self.assertEqual(brain.idle_line(p, brain.status(p, state(), at(10, 0))), "今天到点 1 项，已勾 0 项")
        self.assertEqual(brain.idle_line(p, brain.status(p, state(), at(21, 0))), "收尾。")
        self.assertEqual(brain.idle_line(plan(templates={"d": []}), brain.status(plan(templates={"d": []}), state(), at(10, 0))),
                         "现在没有要处理的事")


class Commands(unittest.TestCase):
    def test_due(self):
        for text, want in (
                ("交报告 周五 17:00", ("交报告", "2026-11-13 17:00")),
                ("交报告 下周一", ("交报告", "2026-11-16 23:59")),
                ("开会 明天下午3点", ("开会", "2026-11-13 15:00")),
                ("买票 11/20", ("买票", "2026-11-20 23:59")),
                ("续费 1月5日", ("续费", "2027-01-05 23:59")),
                ("体检 2026-12-05 9:30", ("体检", "2026-12-05 09:30")),
                ("今晚8点半 给妈妈打电话", ("给妈妈打电话", "2026-11-12 20:30")),
                ("给妈妈打电话 今晚 8 点半", ("给妈妈打电话", "2026-11-12 20:30")),
                ("开会 下午 3 点 15 分", ("开会", "2026-11-12 15:15")),
                ("周四 复盘", ("复盘", "2026-11-12 23:59")),
                ("交表 18:00前", ("交表", "2026-11-12 18:00")),
                ("背单词 30 个", ("背单词 30 个", None)),
                ("写周报", ("写周报", None))):
            self.assertEqual(brain.parse_due(text, TODAY), want, text)

    def test_parse(self):
        pc = lambda s: brain.parse_command(s, TODAY)
        self.assertEqual(pc("待办 交报告 周五 17:00"), {"kind": "todo", "text": "交报告", "due": "2026-11-13 17:00"})
        self.assertEqual(pc("待办：买菜"), {"kind": "todo", "text": "买菜", "due": None})
        self.assertEqual(pc("  TODO   写总结  "), {"kind": "todo", "text": "写总结", "due": None})
        self.assertEqual(pc("完成 报告"), {"kind": "done", "q": "报告"})
        self.assertEqual(pc("喝水"), {"kind": "water", "cups": 1})
        self.assertEqual(pc("喝水 2杯"), {"kind": "water", "cups": 2.0})
        self.assertEqual(pc("喝水 500"), {"kind": "water", "ml": 500})
        self.assertEqual(pc("喝水500ml"), {"kind": "water", "ml": 500})
        self.assertEqual(pc("吃 鸡胸肉 220 40"), {"kind": "eat", "name": "鸡胸肉", "kcal": 220, "p": 40})
        self.assertEqual(pc("吃 蛋白粉"), {"kind": "eat", "name": "蛋白粉", "kcal": None, "p": None})
        self.assertEqual(pc("体重 68.5"), {"kind": "weight", "kg": 68.5})
        self.assertEqual(pc("专注"), {"kind": "focus", "min": None})
        self.assertEqual(pc("专注 40 分钟"), {"kind": "focus", "min": 40})
        self.assertEqual(pc("停止专注"), {"kind": "focus_stop"})
        self.assertEqual(pc("记住 导师周四在办公室"), {"kind": "remember", "text": "导师周四在办公室"})
        self.assertEqual(pc("问 今天先做什么"), {"kind": "ask", "text": "今天先做什么"})
        self.assertEqual(pc("办了 理发")["kind"], "maint")
        self.assertEqual(pc("联系 张三")["kind"], "contact")
        self.assertEqual(pc("？")["kind"], "help")
        self.assertEqual(pc("撤销")["kind"], "undo")
        self.assertEqual(pc("")["kind"], "empty")
        self.assertEqual(pc("随便写一句")["kind"], "unknown")
        for bad in ("待办", "记住", "体重 很重", "喝水 很多", "专注 很久", "完成"):
            self.assertEqual(pc(bad)["kind"], "error", bad)

    def test_recall(self):
        mems = [{"text": "导师周四下午在办公室", "ts": 1}, {"text": "健身房周一闭馆", "ts": 2}, {"text": "导师喜欢先看摘要", "ts": 3}]
        self.assertEqual([m["ts"] for m in brain.recall("导师", mems)], [3, 1])
        self.assertEqual([m["ts"] for m in brain.recall("健身房几点关", mems)], [2])
        self.assertEqual(brain.recall("火星", mems), [])


class History(unittest.TestCase):
    def test_summary_streak_insights(self):
        p = plan()
        s = state(done={"08:00|学习": "09:00"}, habits={"阅读": True}, water_ml=900, focus_min=50, review="做对了：早睡\n")
        row = brain.summarize(p, s, TODAY)
        self.assertEqual((row["blocks"], row["done"], row["missed_types"], row["habits_done"]), (2, 1, ["meal"], ["阅读"]))
        self.assertEqual(row["review"], "做对了：早睡")
        hist = []
        for i in range(1, 9):                                           # 前 8 天：阅读每天做，周三的时间块都没勾
            d = date(2026, 11, 12 - i)
            done = 0 if d.weekday() == 2 else 2
            hist.append({"day": d.isoformat(), "wd": S.WD[d.weekday()], "blocks": 2, "done": done,
                         "missed_types": [] if done else ["study", "meal"], "habits_done": ["阅读"], "focus_min": 30})
        hist.sort(key=lambda h: h["day"])
        self.assertEqual(brain.streak(hist, s, "阅读", TODAY), 9)
        self.assertEqual(brain.streak(hist, state(), "阅读", TODAY), 8)
        self.assertEqual(brain.streak(hist, s, "拉伸", TODAY), 0)
        texts = {x["text"]: x["n"] for x in brain.insights(p, hist, s, TODAY)}
        self.assertEqual(texts["近 8 天时间块完成率 75%"], 8)
        self.assertEqual(texts["周三完成率最低，0%"], 2)
        self.assertEqual(texts["「阅读」已连续 9 天"], 9)
        self.assertEqual(texts["有专注记录的日子平均 30 分钟"], 8)
        bars = brain.week_bars(p, hist, s, brain.status(p, s, at(13, 30)))
        self.assertEqual([b[0] for b in bars], list("五六日一二三四"))
        self.assertEqual((bars[-1], bars[-2][1], bars[0][1]), (("四", 50, True), 0, 100))

    def test_qa_context(self):
        p = plan(owner={"floors": ["不熬过 00:30"], "priorities": ["睡眠", "学业"]})
        s = state(todos=[{"id": 1, "text": "交报告", "due": "2026-11-13 17:00", "done": None}])
        ctx = brain.qa_context(p, s, brain.status(p, s, at(10, 0)), [{"text": "导师周四在办公室", "ts": 1}], "导师什么时候在")
        for part in ("现在：2026-11-12 10:00（周四", "底线：不熬过 00:30", "睡眠 > 学业", "08:00–09:00 学习", "交报告（2026-11-13 17:00）",
                     "相关记忆：导师周四在办公室"):
            self.assertIn(part, ctx)
        self.assertLessEqual(len(brain.qa_context(p, s, brain.status(p, s, at(10, 0)), [], "x", limit=120)), 120)


if __name__ == "__main__":
    unittest.main()

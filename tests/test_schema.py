import unittest

import helpers
from zws import schema as S


class Normalize(unittest.TestCase):
    def test_empty_gives_valid_defaults(self):
        p, w = S.normalize({})
        self.assertEqual(w, ["日程模块开着，但没有任何时间块"])
        self.assertEqual(p["assistant"]["name"], "真维斯")
        self.assertEqual(set(p["week"]), set(S.WD))
        self.assertEqual(p["day_start"], "05:00")
        self.assertEqual(set(p["modules"]), set(S.MODULE_KEYS))

    def test_idempotent(self):
        p1, _ = S.normalize(helpers.base_plan("office"))
        p2, w = S.normalize(p1)
        self.assertEqual(p1, p2)
        self.assertEqual(w, [])

    def test_rejects_non_object(self):
        for bad in (None, [], "x", 3):
            with self.assertRaises(S.PlanError):
                S.normalize(bad)

    def test_blocks_cleaned_sorted_and_capped(self):
        raw = {"day_types": {"d": "每天"}, "templates": {"d": [
            {"t": "23:00", "name": "晚"}, {"t": "01:00", "name": "凌晨", "type": "free"},
            {"t": "8:30", "name": "早", "type": "外星人", "m": "45", "dnd": True, "days": ["mon", "xx"]},
            {"name": "没时间"}, {"t": "25:00", "name": "坏时间"}, "不是对象"]}}
        p, w = S.normalize(raw)
        self.assertEqual([b["name"] for b in p["templates"]["d"]], ["早", "晚", "凌晨"])   # 凌晨 1 点算前一天晚上
        b = p["templates"]["d"][0]
        self.assertEqual((b["t"], b["type"], b["m"], b["dnd"], b["days"]), ("08:30", "flex", 45, True, ["mon"]))
        self.assertTrue(any("外星人" in x for x in w))
        self.assertEqual(sum("已丢弃" in x for x in w), 2)               # 两条提示：缺时间或名称的、不是对象的
        many = [{"t": f"{6 + i // 4:02d}:{15 * (i % 4):02d}", "name": f"块{i}"} for i in range(40)]
        p, w = S.normalize({"templates": {"d": many}})
        self.assertEqual(len(p["templates"]["d"]), 30)

    def test_week_falls_back_to_first_type(self):
        p, w = S.normalize({"day_types": {"a": "甲", "b": "乙"}, "week": {"mon": "b", "tue": "不存在"}})
        self.assertEqual((p["week"]["mon"], p["week"]["tue"], p["week"]["sun"]), ("b", "a", "a"))
        self.assertTrue(any("周二" in x for x in w))

    def test_text_is_sanitized_and_capped(self):
        p, _ = S.normalize({"assistant": {"name": "阿\x00默\x07" + "长" * 50, "accent": "red", "tone": "凶"},
                            "habits": ["读书\n十页", {"name": "x" * 99}, 5, {"name": ""}]})
        self.assertEqual(len(p["assistant"]["name"]), 12)
        self.assertNotIn("\x00", p["assistant"]["name"])
        self.assertEqual(p["assistant"]["accent"], S.ACCENTS[0])
        self.assertEqual(p["assistant"]["tone"], "direct")
        self.assertEqual(p["habits"][0]["name"], "读书 十页")
        self.assertEqual(len(p["habits"][1]["name"]), 20)
        self.assertEqual(len(p["habits"]), 2)

    def test_numbers_clamped(self):
        p, _ = S.normalize({"water": {"goal_ml": 10 ** 9, "cup_ml": -3}, "focus_min": "abc",
                            "wrap_before_sleep": float("nan"), "nutrition": {"kcal": 300, "protein": True},
                            "ui": {"alpha": 0.1, "width": 9999}})
        self.assertEqual(p["water"], {"cup_ml": 50, "goal_ml": 6000})
        self.assertEqual(p["focus_min"], 25)
        self.assertEqual(p["wrap_before_sleep"], 45)
        self.assertEqual(p["nutrition"]["kcal"], 0)                     # 低于 800 当作没设
        self.assertEqual(p["nutrition"]["protein"], 0)
        self.assertEqual(p["ui"], {"alpha": 0.5, "width": 520})

    def test_dates_validated(self):
        p, w = S.normalize({"countdowns": [{"name": "考试", "date": "2027-1-5"}, {"name": "坏", "date": "2027-02-30"}],
                            "goals": [{"name": "目标", "date": "明年"}]})
        self.assertEqual(p["countdowns"], [{"name": "考试", "date": "2027-01-05"}])
        self.assertEqual(p["goals"], [{"name": "目标"}])
        self.assertEqual(sum("日期写错了" in x for x in w), 2)

    def test_training_queue_must_reference_programs(self):
        p, w = S.normalize({"training": {"queue": ["push", "ghost"], "programs": {"Push": {"name": "推", "ex": [
            ["卧推", 4, "6-8"], {"name": "肩推", "sets": 3, "reps": "8"}, [], "坏的"]}}}})
        self.assertEqual(p["training"]["queue"], ["push"])
        self.assertEqual(p["training"]["programs"]["push"]["ex"], [["卧推", 4, "6-8"], ["肩推", 3, "8"]])
        self.assertTrue(any("训练队列" in x for x in w))


class Rules(unittest.TestCase):
    def test_rule_conditions_validated(self):
        raw = {"habits": ["阅读"], "rules": [
            {"id": "Late Night!", "when": {"after": "23:00", "habit_undone": "阅读", "发明的条件": 1}, "say": "去读书",
             "priority": 999},
            {"id": "x", "when": {"habit_undone": "不存在的习惯"}, "say": "丢掉我"},
            {"id": "y", "when": {"after": "23:00"}, "say": ""},
            {"id": "x", "when": {"weekdays": ["sat", "nope"], "block_type_now": "study"}, "say": "周六学习"},
            {"id": "x", "when": {"past_sleep_min": 0}, "say": "过线", "tab": "不存在"}, "不是对象"]}
        p, w = S.normalize(raw)
        self.assertEqual([r["id"] for r in p["rules"]], ["late_night", "x", "x_2"])
        r = p["rules"][0]
        self.assertEqual(r["when"], {"after": "23:00", "habit_undone": "阅读"})
        self.assertEqual(r["priority"], 100)
        self.assertEqual(p["rules"][1]["when"], {"weekdays": ["sat"], "block_type_now": ["study"]})
        self.assertEqual(p["rules"][2]["when"], {"past_sleep_min": 0})
        self.assertNotIn("tab", p["rules"][2])
        self.assertTrue(any("发明的条件" in x for x in w))
        self.assertTrue(any("没有可用的触发条件" in x for x in w))

    def test_rules_capped(self):
        p, w = S.normalize({"rules": [{"id": f"r{i}", "when": {"after": "10:00"}, "say": f"话{i}"} for i in range(30)]})
        self.assertEqual(len(p["rules"]), 16)
        self.assertTrue(any("16" in x for x in w))

    def test_describe_when_reads_naturally(self):
        txt = S.describe_when({"weekdays": ["mon", "wed"], "after": "22:00", "habit_undone": "阅读", "past_sleep_min": 30})
        self.assertEqual(txt, "周一三，22:00 之后，习惯「阅读」未完成，过了睡觉线 30 分钟以上")


if __name__ == "__main__":
    unittest.main()

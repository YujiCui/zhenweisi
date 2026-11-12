import unittest
from datetime import date, timedelta

import helpers
from zws import brain, form as F, rules, schema as S

MON = date(2026, 11, 9)


class Baseline(unittest.TestCase):
    def test_examples_build_clean(self):
        for name in ("student", "office", "freelance"):
            form, fw = F.clean(helpers.example(name))
            plan, pw = rules.build(form)
            self.assertEqual(fw + pw, [], name)
            self.assertEqual(set(plan["week"]), set(S.WD))
            self.assertTrue(all(plan["templates"][k] for k in plan["day_types"]), name)

    def test_fixed_blocks_land_on_their_weekdays(self):
        form, _ = F.clean(helpers.example("student"))
        plan, _ = rules.build(form)
        for i, wd in enumerate(S.WD):
            fixed = [(b["t"], b["name"]) for b in brain.blocks_for(plan, MON + timedelta(days=i)) if b["fixed"]]
            want = [(f["start"], f["name"]) for f in form["fixed"] if wd in f["days"]]
            self.assertEqual(fixed, want, wd)

    def test_blocks_do_not_overlap_and_stay_awake(self):
        for name in ("student", "office", "freelance"):
            plan = helpers.base_plan(name)
            for i in range(7):
                d = MON + timedelta(days=i)
                blocks = brain.blocks_for(plan, d)
                self.assertGreaterEqual(blocks[0]["s"], brain.wake_m(plan, d), (name, i))
                self.assertLessEqual(blocks[-1]["e"], brain.sleep_m(plan, d), (name, i))
                for a, b in zip(blocks, blocks[1:]):
                    self.assertLessEqual(a["e"], b["s"], (name, i, a["name"], b["name"]))

    def test_sleep_follows_next_day(self):
        form, _ = F.clean(helpers.example("student"))
        wake, sleep = rules.wake_sleep(form)
        self.assertEqual(wake, {"default": "07:30", "sat": "09:00", "sun": "09:00"})
        self.assertEqual(sleep, {"default": "23:30", "fri": "00:30", "sat": "00:30"})   # 周五、周六晚上第二天休息

    def test_training_only_on_training_days(self):
        plan = helpers.base_plan("student")                             # 每周 3 次：周一三五
        for i, wd in enumerate(S.WD):
            has = any(b["type"] == "train" for b in brain.blocks_for(plan, MON + timedelta(days=i)))
            self.assertEqual(has, wd in ("mon", "wed", "fri"), wd)

    def test_day_type_labels_are_distinct(self):
        plan = helpers.base_plan("student")
        self.assertEqual(len(set(plan["day_types"].values())), len(plan["day_types"]))
        self.assertIn("上课日（周一三五）", plan["day_types"].values())

    def test_long_fixed_day_gets_no_extra_work(self):
        plan = helpers.base_plan("office")                              # 上班 9.5 小时，不再往晚上塞工作块
        tue = brain.blocks_for(plan, MON + timedelta(days=1))
        self.assertEqual([b["name"] for b in tue if b["type"] == "work"], ["上班"])

    def test_disabled_modules_leave_no_content(self):
        raw = helpers.example("office")
        raw["modules"].update(habits=False, countdowns=False, maintenance=False, people=False, nutrition=False)
        plan = rules.build(F.clean(raw)[0])[0]
        self.assertEqual((plan["habits"], plan["countdowns"], plan["maintenance"], plan["people"]), ([], [], [], []))
        self.assertEqual(plan["nutrition"]["kcal"], 0)


class FormClean(unittest.TestCase):
    def test_garbage_becomes_defaults(self):
        f, w = F.clean({"role": "海盗", "wake": "早", "rest_days": "sat", "fixed": [{"name": "半截"}, 7], "about": 123,
                        "train_per_week": 99, "modules": {"todo": "yes"}})
        self.assertEqual(f["role"], "other")
        self.assertEqual(f["wake"], {"workday": "07:30", "restday": "09:00"})
        self.assertEqual(f["rest_days"], ["sat", "sun"])
        self.assertEqual(f["fixed"], [])
        self.assertEqual(f["about"], "123")
        self.assertEqual(f["train_per_week"], 7)
        self.assertIs(f["modules"]["todo"], True)                       # 不是布尔值就用默认
        self.assertEqual(len(w), 1)

    def test_overlapping_fixed_is_reported(self):
        f, w = F.clean({"fixed": [{"name": "上课", "days": ["mon"], "start": "09:00", "end": "12:00"},
                                  {"name": "实习", "days": ["mon"], "start": "11:00", "end": "15:00"}]})
        self.assertEqual(len(f["fixed"]), 2)
        self.assertTrue(any("重叠" in x for x in w))

    def test_about_is_capped(self):
        f, _ = F.clean({"about": "字" * 5000})
        self.assertEqual(len(f["about"]), F.ABOUT_MAX)


if __name__ == "__main__":
    unittest.main()

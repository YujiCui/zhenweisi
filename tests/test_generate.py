import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import helpers
from zws import brain, claude_cli, generate, schema as S

MON = date(2026, 11, 9)


def day_blocks(plan, wd):
    return brain.blocks_for(plan, MON + timedelta(days=S.WD.index(wd)))


class Merge(unittest.TestCase):
    def setUp(self):
        self.form, self.base, _ = generate.baseline(helpers.example("student"))
        self.cls = self.base["week"]["mon"]                             # 周一三五：08:30–12:00 上课

    def test_fixed_blocks_cannot_be_moved_or_deleted(self):
        tpl = {k: [{"t": "09:00", "name": "AI 排的", "type": "study", "m": 60}] for k in self.base["day_types"]}
        plan, _ = generate.merge(self.form, self.base, {"templates": tpl})
        for wd in S.WD:
            fixed = [(b["t"], b["e"] - b["s"], b["name"]) for b in day_blocks(plan, wd) if b["fixed"]]
            want = [(f["start"], 210, f["name"]) for f in self.form["fixed"] if wd in f["days"]]
            self.assertEqual(fixed, want, wd)

    def test_conflicts_with_fixed_are_clipped_or_dropped(self):
        tpl = {self.cls: [
            {"t": "08:00", "name": "早读", "type": "study", "m": 60},          # 拖进课里：截到 08:30
            {"t": "10:00", "name": "插进课里", "type": "study", "m": 30},       # 开在课里：去掉
            {"t": "08:30", "name": "上课", "type": "class", "m": 60, "fixed": True},   # AI 自称的固定块：以表单为准
            {"t": "13:00", "name": "下午", "type": "study", "m": 60}]}
        plan, w = generate.merge(self.form, self.base, {"templates": tpl})
        got = [(b["t"], b["e"] - b["s"], b["name"]) for b in day_blocks(plan, "mon")]
        self.assertEqual(got, [("08:00", 30, "早读"), ("08:30", 210, "上课"), ("13:00", 60, "下午")])
        self.assertTrue(any("冲突" in x and "插进课里" in x for x in w))
        self.assertTrue(any("沿用基线" in x for x in w))                 # 其他类型 AI 没给，用基线
        self.assertEqual(plan["templates"][self.base["week"]["tue"]], self.base["templates"][self.base["week"]["tue"]])

    def test_ai_cannot_touch_schedule_identity_or_modules(self):
        ai = {"wake": {"default": "11:00"}, "sleep": {"default": "03:00"}, "day_start": "07:00",
              "modules": {"nutrition": True, "schedule": False},
              "assistant": {"name": "换个名字", "accent": "#000000", "tone": "strict", "lines": {"day": "先做最难的。"}}}
        plan, w = generate.merge(self.form, self.base, ai)
        want = copy.deepcopy(self.base)
        want["assistant"]["lines"]["day"] = "先做最难的。"
        self.assertEqual(plan, want)
        self.assertEqual(w, [])

    def test_regrouping_that_breaks_fixed_is_rejected(self):
        ai = {"day_types": {"all": "每天"}, "week": {wd: "all" for wd in S.WD},
              "templates": {"all": [{"t": "09:00", "name": "学习", "type": "study", "m": 60}]}}
        plan, w = generate.merge(self.form, self.base, ai)
        self.assertEqual(plan["week"], self.base["week"])
        self.assertTrue(any("对不上" in x for x in w))

    def test_valid_regrouping_is_accepted(self):
        ai = {"day_types": dict(self.base["day_types"], sunplan="周日"), "week": dict(self.base["week"], sun="sunplan"),
              "templates": dict(copy.deepcopy(self.base["templates"]),
                                sunplan=[{"t": "10:00", "name": "排下周", "type": "chore", "m": 60}])}
        plan, w = generate.merge(self.form, self.base, ai)
        self.assertEqual(w, [])
        self.assertEqual(plan["week"]["sun"], "sunplan")
        self.assertEqual([b["name"] for b in day_blocks(plan, "sun")], ["排下周"])
        self.assertEqual(plan["week"]["sat"], self.base["week"]["sat"])

    def test_user_habits_stay_first_and_ai_adds(self):
        ai = {"habits": [{"name": "新习惯"}, {"name": "背单词 30 个", "by": "20:00"}]}
        plan, _ = generate.merge(self.form, self.base, ai)
        self.assertEqual(plan["habits"], [{"name": "背单词 30 个", "by": "20:00"}, {"name": "阅读 20 分"}, {"name": "新习惯"}])

    def test_rules_for_disabled_modules_are_dropped(self):
        raw = helpers.example("student")
        raw["modules"]["water"] = False
        form, base, _ = generate.baseline(raw)
        ai = {"rules": [{"id": "w", "when": {"water_pct_below": 50}, "say": "喝水"},
                        {"id": "s", "when": {"after": "23:00"}, "say": "睡觉"}]}
        plan, w = generate.merge(form, base, ai)
        self.assertEqual([r["id"] for r in plan["rules"]], ["s"])
        self.assertTrue(any("没开启的模块" in x for x in w))

    def test_user_numbers_win_over_ai(self):
        form, base, _ = generate.baseline(helpers.example("office"))    # 用户填了 1900 kcal / 110 g
        ai = {"nutrition": {"kcal": 2600, "protein": 180, "quick_food": [{"name": "酸奶", "kcal": 110, "p": 10}]},
              "water": {"goal_ml": 4000}, "goals": [{"name": "拿到数据分析师认证", "metric": "考试通过"}, {"name": "AI 加的目标"}]}
        plan, _ = generate.merge(form, base, ai)
        self.assertEqual((plan["nutrition"]["kcal"], plan["nutrition"]["protein"]), (1900, 110))
        self.assertEqual(plan["nutrition"]["quick_food"][0]["name"], "酸奶")
        self.assertEqual(plan["water"]["goal_ml"], 1800)
        self.assertEqual(plan["goals"], [{"name": "拿到数据分析师认证", "date": "2027-03-20", "metric": "考试通过"}])

    def test_garbage_from_ai_changes_nothing(self):
        for ai in (None, [], "x", 7, {"templates": "nope", "habits": 5, "rules": {"a": 1}, "training": [], "owner": "x"}):
            plan, _ = generate.merge(self.form, self.base, ai)
            self.assertEqual(plan, self.base, repr(ai))


class Engines(unittest.TestCase):
    def test_offline(self):
        res = generate.run(helpers.example("student"), "offline")
        self.assertFalse(res["used_ai"])
        self.assertIn("没有调用 AI", res["notes"][0])
        self.assertIn("没有用到", res["notes"][0])                       # 写了自由描述但没被用上，要说明

    def test_unknown_engine(self):
        with self.assertRaises(generate.GenError):
            generate.run({}, "gpt")

    def test_manual_roundtrip(self):
        p = generate.manual_prompt(helpers.example("freelance"))
        self.assertIn("自由插画师", p)
        res = generate.import_reply(helpers.example("freelance"), '```json\n{"plan": {"habits": [{"name": "早上先画一小时"}]}, "notes": ["x"]}\n```')
        self.assertTrue(res["used_ai"])
        self.assertIn("早上先画一小时", [h["name"] for h in res["plan"]["habits"]])
        with self.assertRaises(generate.GenError):
            generate.import_reply(helpers.example("freelance"), "我是一段和 JSON 无关的话")

    def test_preview_covers_the_week(self):
        pv = generate.preview(helpers.base_plan("student"), date(2026, 11, 12))
        self.assertEqual(pv["today"], "thu")
        self.assertEqual(set(pv["days"]), set(S.WD))
        self.assertTrue(all(d["blocks"] for d in pv["days"].values()))
        self.assertEqual(pv["days"]["fri"]["sleep"], "00:30")


GOOD = json.dumps({"plan": {"habits": [{"name": "六级听力一套", "by": "21:00"}],
                            "rules": [{"id": "phone", "when": {"after": "23:00"}, "say": "手机放远，准备睡。", "why": "你说睡前刷手机"}]},
                   "notes": ["听力设成每日习惯"]}, ensure_ascii=False)


class WithFakeClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zws-test-")
        self.env = dict(os.environ)
        patcher = mock.patch.object(claude_cli, "extra_usage", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_generation(self):
        helpers.fake_claude(self.tmp, [GOOD])
        log = []
        res = generate.run(helpers.example("student"), "claude", model="sonnet", log=log.append)
        self.assertTrue(res["used_ai"])
        self.assertEqual(res["notes"], ["听力设成每日习惯"])
        self.assertIn("六级听力一套", [h["name"] for h in res["plan"]["habits"]])
        self.assertEqual(res["plan"]["rules"][0]["say"], "手机放远，准备睡。")
        self.assertTrue(log)
        args = json.loads((Path(self.tmp) / "args0.json").read_text(encoding="utf-8"))
        sent = (Path(self.tmp) / "prompt0.txt").read_text(encoding="utf-8")
        self.assertIn("刷手机拖到一点多", sent)                          # 用户写的内容只走标准输入
        self.assertFalse(any("刷手机" in a or "小林" in a for a in args))
        for flag in ("-p", "--safe-mode", "--restricted", "--no-session-persistence", "--disable-slash-commands"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--tools") + 1], "")           # 不带任何工具
        self.assertEqual(args[args.index("--model") + 1], "sonnet")

    def test_one_retry_when_reply_is_not_json(self):
        helpers.fake_claude(self.tmp, ["抱歉，我没法按这个格式输出。", GOOD])
        res = generate.run(helpers.example("student"), "claude")
        self.assertTrue(res["used_ai"])
        self.assertEqual(helpers.fake_calls(self.tmp), 2)
        self.assertIn("上一次的输出不能用", (Path(self.tmp) / "prompt1.txt").read_text(encoding="utf-8"))

    def test_baseline_after_two_bad_replies(self):
        helpers.fake_claude(self.tmp, ["不是 JSON", "还不是"])
        res = generate.run(helpers.example("student"), "claude")
        self.assertFalse(res["used_ai"])
        self.assertEqual(helpers.fake_calls(self.tmp), 2)
        self.assertEqual(res["plan"], helpers.base_plan("student"))
        self.assertTrue(any("退回基线" in x for x in res["warnings"]))

    def test_metered_login_needs_explicit_consent(self):
        helpers.fake_claude(self.tmp, [GOOD], auth={"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"})
        with self.assertRaises(generate.GenError) as cm:
            generate.run(helpers.example("student"), "claude")
        self.assertIn("计费", str(cm.exception))
        self.assertEqual(helpers.fake_calls(self.tmp), 0)               # 没同意之前一次都不调
        self.assertTrue(generate.run(helpers.example("student"), "claude", allow_cost=True)["used_ai"])

    def test_extra_usage_counts_as_cost_risk(self):
        helpers.fake_claude(self.tmp, [GOOD])
        with mock.patch.object(claude_cli, "extra_usage", return_value=True):
            with self.assertRaises(generate.GenError) as cm:
                generate.run(helpers.example("student"), "claude")
        self.assertIn("超额付费", str(cm.exception))

    def test_logged_out(self):
        helpers.fake_claude(self.tmp, [GOOD], auth={"loggedIn": False})
        with self.assertRaises(generate.GenError) as cm:
            generate.run(helpers.example("student"), "claude")
        self.assertIn("还没登录", str(cm.exception))

    def test_missing_cli(self):
        os.environ["ZWS_CLAUDE"] = str(Path(self.tmp) / "nope.exe")
        with self.assertRaises(generate.GenError) as cm:
            generate.run(helpers.example("student"), "claude")
        self.assertIn("没找到", str(cm.exception))

    def test_old_cli_gets_minimal_flags(self):
        helpers.fake_claude(self.tmp, [GOOD])
        (Path(self.tmp) / "reject").write_text("1")                     # 假装这个版本不认 --safe-mode
        res = generate.run(helpers.example("student"), "claude")
        self.assertTrue(res["used_ai"])
        args = json.loads((Path(self.tmp) / "args0.json").read_text(encoding="utf-8"))
        self.assertNotIn("--safe-mode", args)
        self.assertIn("--system-prompt", args)

    def test_cancel_stops_quickly(self):
        helpers.fake_claude(self.tmp, [GOOD])
        (Path(self.tmp) / "hang").write_text("1")
        ev = threading.Event()
        threading.Timer(0.8, ev.set).start()
        t0 = time.time()
        with self.assertRaises(generate.Cancelled):
            generate.run(helpers.example("student"), "claude", cancel=ev)
        self.assertLess(time.time() - t0, 15)


if __name__ == "__main__":
    unittest.main()

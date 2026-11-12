import json
import unittest

import helpers
from zws import generate, prompt


class Build(unittest.TestCase):
    def setUp(self):
        self.form, self.base, _ = generate.baseline(helpers.example("student"))

    def test_prompt_carries_form_and_base(self):
        u = prompt.build(self.form, self.base)
        form_part = u.split("# form\n")[1].split("\n\n# base\n")[0]
        base_part = u.split("# base\n")[1]
        self.assertEqual(json.loads(form_part)["about"], self.form["about"])
        self.assertNotIn("accent", json.loads(form_part))               # 配色和 AI 无关，不给
        self.assertIn('"fixed":true', base_part)
        self.assertEqual(json.loads(base_part)["week"], self.base["week"])
        self.assertNotIn("__TYPES__", u)
        self.assertNotIn("__DAY_START__", u)
        self.assertIn("0 点到 05:00 之间算前一天晚上", u)

    def test_system_is_safe_on_a_command_line(self):
        self.assertNotIn("\n", prompt.SYSTEM)
        for ch in "\"'&|<>^%!`$\\":
            self.assertNotIn(ch, prompt.SYSTEM)

    def test_manual_prompt_is_self_contained(self):
        m = prompt.manual(self.form, self.base)
        self.assertTrue(m.startswith(prompt.SYSTEM))
        self.assertIn("# 硬约束", m)

    def test_repair_keeps_original_and_names_the_error(self):
        r = prompt.repair("原提示词", "回答里没有找到完整的 JSON 对象")
        self.assertTrue(r.startswith("原提示词"))
        self.assertIn("回答里没有找到完整的 JSON 对象", r)


class Extract(unittest.TestCase):
    def test_plain_fenced_and_embedded(self):
        want = {"plan": {"habits": []}}
        for text in ('{"plan": {"habits": []}}',
                     '```json\n{"plan": {"habits": []}}\n```',
                     '好的，下面是配置：\n```\n{"plan": {"habits": []}}\n```\n希望有帮助。',
                     '配置如下 {"plan": {"habits": []}} 以上。',
                     '﻿{"plan": {"habits": []}}'):
            self.assertEqual(prompt.extract_json(text), want, text)

    def test_braces_inside_strings(self):
        text = '说明 {不是 json} 然后 {"plan": {"rules": [{"say": "别写 } 和 { 这种", "x": "引号 \\" 也行"}]}}'
        self.assertEqual(prompt.extract_json(text)["plan"]["rules"][0]["say"], "别写 } 和 { 这种")

    def test_nothing_usable(self):
        for text in ("", "没有 JSON", "[1, 2, 3]", '{"plan": ', "{{{"):
            self.assertIsNone(prompt.extract_json(text), text)


class ParseReply(unittest.TestCase):
    def test_wrapped_plan_with_notes(self):
        plan, notes, err = prompt.parse_reply('{"plan": {"habits": [{"name": "阅读"}]}, "notes": ["假设一", "", 5, "假设二"]}')
        self.assertIsNone(err)
        self.assertEqual(plan, {"habits": [{"name": "阅读"}]})
        self.assertEqual(notes, ["假设一", "5", "假设二"])

    def test_bare_plan(self):
        plan, notes, err = prompt.parse_reply('{"templates": {"d": []}, "notes": "只有一条"}')
        self.assertIsNone(err)
        self.assertEqual(plan, {"templates": {"d": []}})
        self.assertEqual(notes, ["只有一条"])

    def test_errors_are_explained(self):
        for text, word in (("", "空的"), ("抱歉，我不能输出 JSON。", "没有找到"), ('{"hello": 1}', "没有 plan"),
                           ('{"plan": {"unknown": 1}}', "没有任何可用")):
            plan, notes, err = prompt.parse_reply(text)
            self.assertIsNone(plan)
            self.assertIn(word, err)


if __name__ == "__main__":
    unittest.main()

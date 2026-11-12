"""浮窗的冒烟测试：真的把窗口开起来，把每个页面和每类指令走一遍。

Windows 上窗口是几乎全透明、鼠标穿透的，不会打扰正在用电脑的人。没有图形界面的环境自动跳过。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from zws import store

HAS_DISPLAY = os.name == "nt" or sys.platform == "darwin" or bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(HAS_DISPLAY, "没有图形界面")
class Selftest(unittest.TestCase):
    def run_widget(self, plan, now):
        tmp = Path(tempfile.mkdtemp(prefix="zws-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home = tmp / "home"
        home.mkdir()
        store.write_json(home / "plan.json", plan)
        r = subprocess.run([sys.executable, str(helpers.ROOT / "assistant.pyw"), "--home", str(home), "--now", now,
                            "--selftest", str(tmp / "out")], capture_output=True, timeout=180)
        report = json.loads((tmp / "out" / "selftest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["errors"], 0, report.get("trace"))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertFalse((home / "error.log").exists())
        return report, store.read_json(home / "state.json"), store.read_json(home / "memory.json")

    def test_generated_plan(self):
        plan = json.loads((helpers.ROOT / "examples" / "plan-student.json").read_text(encoding="utf-8"))
        report, state, mem = self.run_widget(plan, "2026-11-12 21:15")
        replies = dict(report["replies"])
        self.assertEqual(replies["待办 交实验报告 周五 17:00"], "已加待办：交实验报告，11/13 17:00 截止")
        self.assertEqual(replies["喝水 500"], "已记 500 ml，今天 750/2000 ml")
        self.assertEqual(replies["回忆 导师"], "记得：导师周四下午在办公室")
        self.assertEqual(replies["乱写一句"], "没听懂。输入 ? 看全部指令。")
        self.assertIn("模块没开", replies["吃 鸡胸肉 220 40"])
        self.assertEqual([t["text"] for t in state["todos"] if not t["done"]], ["交实验报告", "给导师回邮件"])
        self.assertEqual(state["water_ml"], 750)
        self.assertEqual(mem[0]["text"], "导师周四下午在办公室")
        self.assertTrue(state["feedback"])                              # 采纳和「不用」都记进了反馈
        widths = {tuple(v)[0] for v in report["sizes"].values()}
        self.assertEqual(len(widths), 1, report["sizes"])               # 换页、展开依据都不会把窗口撑宽

    def test_every_module_on(self):
        raw = helpers.example("office")
        raw["modules"] = {k: True for k in raw["modules"]}
        raw["train_per_week"] = 3
        from zws import generate
        plan = generate.baseline(raw)[1]
        plan["nutrition"]["quick_food"] = [{"name": "鸡胸肉", "kcal": 165, "p": 31}]
        plan["maintenance"].append({"name": "理发", "every": 30})
        plan["people"].append({"name": "导师", "every": 14})
        report, state, _ = self.run_widget(plan, "2026-11-13 20:30")
        replies = dict(report["replies"])
        self.assertIn("已记：鸡胸肉 220 kcal、蛋白 40 g", replies["吃 鸡胸肉 220 40"])
        self.assertEqual(replies["办了 理发"], "已记：理发，30 天后再提醒。")
        self.assertEqual(replies["联系 导师"], "已记：导师，14 天后再提醒。")
        self.assertEqual(state["maint"], {"理发": "2026-11-13"})
        if os.name == "nt" and importlib.util.find_spec("PIL"):         # 装了 Pillow 才出图
            self.assertEqual(report["shots"], ["schedule", "todo", "log", "review", "folded"])

    def test_minimal_plan(self):
        report, state, _ = self.run_widget({"modules": {k: False for k in ("schedule", "habits", "review", "countdowns", "focus", "memory")}},
                                           "2026-11-12 10:00")
        self.assertEqual(report["errors"], 0)
        self.assertIn("模块没开", dict(report["replies"])["专注 25"])


if __name__ == "__main__":
    unittest.main()

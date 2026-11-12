import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import helpers
from zws import store


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zws-test-"))
        self.plan = helpers.base_plan("student")
        store.write_json(self.home / "plan.json", self.plan)
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_broken_plan_falls_back_to_last_good(self):
        s = store.Store(self.home)
        plan, warn, err = s.read_plan()
        self.assertIsNone(err)
        self.assertEqual(plan, self.plan)
        (self.home / "plan.json").write_text('{"assistant": {"name": "半截', encoding="utf-8")
        plan2, _, err = s.read_plan()
        self.assertEqual(plan2, self.plan)                              # 改坏了：沿用上一版
        self.assertIn("沿用上一版", err)
        (self.home / "plan.last-good.json").unlink()
        plan3, _, err = s.read_plan()
        self.assertEqual(plan3["assistant"]["name"], "真维斯")           # 连上一版都没有：默认配置，照样能开
        self.assertIn("默认配置", err)

    def test_plan_with_bom_and_warnings(self):
        (self.home / "plan.json").write_text("﻿" + json.dumps({"habits": ["阅读"], "week": {"mon": "nope"}}), encoding="utf-8")
        plan, warn, err = store.Store(self.home).read_plan()
        self.assertIsNone(err)
        self.assertEqual(plan["habits"], [{"name": "阅读"}])
        self.assertTrue(warn)

    def test_state_survives_restart_and_bad_fields(self):
        s = store.Store(self.home).load()
        s.rollover(self.plan, datetime(2026, 11, 12, 10, 0))
        s.state["water_ml"] = 750
        s.state["todos"].append({"id": s.next_id(), "text": "交报告", "due": None, "done": None})
        s.save()
        s2 = store.Store(self.home).load()
        self.assertEqual((s2.state["water_ml"], s2.state["todos"][0]["text"], s2.state["next_id"]), (750, "交报告", 2))
        (self.home / "state.json").write_text(json.dumps({"day": "2026-11-12", "water_ml": "很多", "todos": "x", "done": {"a": "1"}}),
                                              encoding="utf-8")
        s3 = store.Store(self.home).load()
        self.assertEqual((s3.state["water_ml"], s3.state["todos"], s3.state["done"]), (0, [], {"a": "1"}))   # 类型不对的字段回到默认
        (self.home / "state.json").write_text("不是 JSON", encoding="utf-8")
        self.assertEqual(store.Store(self.home).load().state["todos"], [])

    def test_rollover(self):
        s = store.Store(self.home).load()
        self.assertTrue(s.rollover(self.plan, datetime(2026, 11, 12, 10, 0)))
        self.assertFalse(s.rollover(self.plan, datetime(2026, 11, 13, 2, 0)))        # 凌晨 2 点还是 12 号这一天
        s.state["habits"]["背单词 30 个"] = True
        s.state["water_ml"] = 1200
        s.state["mute"]["late"] = 9e12
        s.state["todos"] = [{"id": 1, "text": "留着", "due": None, "done": None},
                            {"id": 2, "text": "刚完成", "due": None, "done": "2026-11-12 09:00", "done_day": "2026-11-12"},
                            {"id": 3, "text": "很久以前完成的", "due": None, "done": "2026-10-01 09:00", "done_day": "2026-10-01"}]
        s.state["feedback"]["late"] = {"adopt": 1, "dismiss": 0}
        self.assertTrue(s.rollover(self.plan, datetime(2026, 11, 13, 5, 0)))
        self.assertEqual((s.state["day"], s.state["habits"], s.state["water_ml"], s.state["mute"]), ("2026-11-13", {}, 0, {}))
        self.assertEqual([t["text"] for t in s.state["todos"]], ["留着", "刚完成"])    # 完成的待办留 14 天
        self.assertEqual(s.state["feedback"]["late"]["adopt"], 1)                    # 长期数据不清
        self.assertEqual((s.hist[-1]["day"], s.hist[-1]["habits_done"], s.hist[-1]["water_ml"]), ("2026-11-12", ["背单词 30 个"], 1200))
        s2 = store.Store(self.home).load()
        self.assertEqual(len(s2.hist), 1)

    def test_single_instance_lock(self):
        a, b = store.Store(self.home), store.Store(self.home)
        self.assertFalse(store.is_running(self.home))
        self.assertTrue(a.lock())
        self.assertFalse(b.lock())
        self.assertTrue(store.is_running(self.home))
        a.unlock()
        self.assertFalse(store.is_running(self.home))
        self.assertTrue(b.lock())
        b.unlock()

    def test_snapshot_restore(self):
        s = store.Store(self.home).load()
        s.mem.append({"id": 1, "text": "记一条", "ts": 1})
        snap = s.snapshot()
        s.state["water_ml"] = 999
        s.mem.clear()
        s.restore(snap)
        self.assertEqual((s.state["water_ml"], s.mem[0]["text"]), (0, "记一条"))
        self.assertEqual(store.read_json(self.home / "memory.json")[0]["text"], "记一条")


if __name__ == "__main__":
    unittest.main()

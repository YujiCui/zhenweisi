import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers
from zws import homes, server, store


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="zws-test-")
        cls.old_home = os.environ.get("ZWS_HOME")
        os.environ["ZWS_HOME"] = cls.tmp
        cls.srv, cls.app = server.make_server(0, strict=True)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        if cls.old_home is None:
            os.environ.pop("ZWS_HOME", None)
        else:
            os.environ["ZWS_HOME"] = cls.old_home
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, token=True, host=None, origin=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-ZWS-Token"] = self.app.token if token is True else token
        if origin:
            headers["Origin"] = origin
        data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        text = r.read().decode("utf-8")
        conn.close()
        try:
            return r.status, json.loads(text), r
        except ValueError:
            return r.status, text, r

    # ---- 页面与访问控制 ----
    def test_page_carries_token_and_csp(self):
        code, html, r = self.call("GET", "/", token=False)
        self.assertEqual(code, 200)
        self.assertIn(self.app.token, html)
        self.assertNotIn("__ZWS_TOKEN__", html)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        for path in ("/app.js", "/style.css"):
            self.assertEqual(self.call("GET", path, token=False)[0], 200)
        for path in ("/zws/server.py", "/../studio.py", "/assistants/x/plan.json", "/web/app.js"):
            self.assertEqual(self.call("GET", path, token=False)[0], 404, path)

    def test_only_local_callers_with_the_token(self):
        self.assertEqual(self.call("GET", "/api/boot", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/boot", token="wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/boot", host="evil.example:80")[0], 403)         # DNS 重绑定
        self.assertEqual(self.call("GET", "/", token=False, host="evil.example")[0], 403)
        self.assertEqual(self.call("POST", "/api/preview", {"form": {}}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("POST", "/api/preview", {"form": {}}, origin=f"http://127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.call("GET", "/api/boot", host=f"localhost:{self.port}")[0], 200)

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/preview", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/preview", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.call("POST", "/api/preview", raw=b" " * (server.MAX_BODY + 10))[0], 413)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.call("GET", "/api/job?id=missing")[0], 404)
        code, data, _ = self.call("POST", "/api/generate", {"form": {}, "engine": "gpt"})
        self.assertEqual((code, data["error"]), (400, "不认识的生成方式"))

    # ---- 生成 ----
    def test_boot_and_preview(self):
        code, boot, _ = self.call("GET", "/api/boot")
        self.assertEqual(code, 200)
        self.assertEqual([e["key"] for e in boot["examples"]], ["student", "office", "freelance"])
        self.assertEqual(len(boot["spec"]["modules"]), 13)
        code, pv, _ = self.call("POST", "/api/preview", {"form": helpers.example("student")})
        self.assertEqual(code, 200)
        self.assertEqual(pv["warnings"], [])
        self.assertEqual(len(pv["preview"]["days"]), 7)
        code, pv2, _ = self.call("POST", "/api/preview", {"plan": {"habits": ["阅读"], "week": {"mon": "x"}}})
        self.assertEqual(pv2["plan"]["habits"], [{"name": "阅读"}])
        self.assertTrue(pv2["warnings"])

    def test_offline_job_and_manual_flow(self):
        code, data, _ = self.call("POST", "/api/generate", {"form": helpers.example("office"), "engine": "offline"})
        self.assertEqual(code, 200)
        for _ in range(100):
            code, job, _ = self.call("GET", "/api/job?id=" + data["job"])
            if job["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "done")
        self.assertFalse(job["result"]["used_ai"])
        self.assertEqual(job["result"]["plan"]["assistant"]["name"], "阿默")
        self.assertIn("preview", job["result"])
        code, data, _ = self.call("POST", "/api/prompt", {"form": helpers.example("office")})
        self.assertIn("想转数据分析", data["prompt"])
        code, data, _ = self.call("POST", "/api/import", {"form": helpers.example("office"), "reply": "没有 JSON"})
        self.assertEqual(code, 400)
        self.assertIn("不能用", data["error"])
        code, data, _ = self.call("POST", "/api/import", {"form": helpers.example("office"),
                                                         "reply": '{"plan": {"habits": [{"name": "午饭自己带"}]}}'})
        self.assertEqual(code, 200)
        self.assertIn("午饭自己带", [h["name"] for h in data["plan"]["habits"]])

    # ---- 助手的文件夹 ----
    def test_create_update_list_delete(self):
        plan = helpers.base_plan("freelance")                           # 助手名「灯塔」
        code, a, _ = self.call("POST", "/api/create", {"plan": plan, "form": helpers.example("freelance")})
        self.assertEqual((code, a["slug"]), (200, "灯塔"))
        home = Path(self.tmp) / "灯塔"
        self.assertEqual(store.read_json(home / "plan.json"), plan)
        self.assertEqual(store.read_json(home / "form.json")["owner"], "陈默")
        code, again, _ = self.call("POST", "/api/create", {"plan": plan})
        self.assertEqual(again, {"exists": True, "name": "灯塔"})          # 重名：先问，不擅自覆盖
        (home / "state.json").write_text('{"day": "2026-11-12", "water_ml": 500}', encoding="utf-8")
        plan2 = dict(plan, focus_min=30)
        code, upd, _ = self.call("POST", "/api/create", {"plan": plan2, "mode": "update"})
        self.assertEqual(upd["slug"], "灯塔")
        self.assertEqual(store.read_json(home / "plan.json")["focus_min"], 30)
        self.assertEqual(store.read_json(home / "state.json")["water_ml"], 500)          # 记录还在
        code, new, _ = self.call("POST", "/api/create", {"plan": plan, "mode": "new"})
        self.assertEqual(new["slug"], "灯塔-2")
        code, data, _ = self.call("GET", "/api/assistants")
        self.assertEqual([x["slug"] for x in data["assistants"]], ["灯塔", "灯塔-2"])
        self.assertFalse(data["assistants"][0]["running"])
        code, loaded, _ = self.call("POST", "/api/load", {"slug": "灯塔"})
        self.assertEqual((loaded["form"]["owner"], loaded["plan"]["focus_min"]), ("陈默", 30))
        code, gone, _ = self.call("POST", "/api/delete", {"slug": "灯塔-2"})
        self.assertEqual(code, 200)
        self.assertIn(".trash", gone["moved_to"])
        self.assertTrue(Path(gone["moved_to"]).is_dir())                 # 进回收区，不是真删
        self.assertEqual(len(self.call("GET", "/api/assistants")[1]["assistants"]), 1)

    def test_running_assistant_cannot_be_deleted(self):
        home = homes.create(dict(helpers.base_plan("student"), assistant={"name": "开着的"}))
        s = store.Store(home)
        self.assertTrue(s.lock())
        try:
            code, data, _ = self.call("POST", "/api/delete", {"slug": home.name})
            self.assertEqual(code, 400)
            self.assertIn("还开着", data["error"])
            self.assertEqual(self.call("POST", "/api/launch", {"slug": home.name})[1], {"state": "running"})
        finally:
            s.unlock()

    def test_slugs_cannot_escape_the_assistants_dir(self):
        outside = Path(self.tmp).parent / "zws-outside"
        outside.mkdir(exist_ok=True)
        (outside / "plan.json").write_text("{}", encoding="utf-8")
        self.addCleanup(shutil.rmtree, outside, True)
        for slug in ("../zws-outside", "..", ".trash", "a/b", "a\\b", "", "C:", str(outside)):
            for path in ("/api/launch", "/api/delete", "/api/open", "/api/shortcut", "/api/load"):
                code, data, _ = self.call("POST", path, {"slug": slug})
                self.assertEqual(code, 400, (path, slug, data))
        self.assertTrue((outside / "plan.json").is_file())
        self.assertEqual(self.call("POST", "/api/launch", {"slug": ["x"]})[0], 400)


@unittest.skipUnless(os.name == "nt", "快捷方式只在 Windows 上建")
class Shortcuts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zws-test-"))
        self.env = dict(os.environ)
        os.environ["ZWS_HOME"] = str(self.tmp / "assistants")
        os.environ["ZWS_SHORTCUT_DIR"] = str(self.tmp / "桌面")              # 不碰真的桌面
        (self.tmp / "桌面").mkdir()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chinese_paths_and_never_overwriting_someone_elses_shortcut(self):
        from zws import _shortcut
        home = homes.create(helpers.base_plan("student"))                   # 助手名「真维斯」
        foreign = self.tmp / "桌面" / "真维斯.lnk"                             # 桌面上已经有一个同名的、别的程序的快捷方式
        _shortcut.write(foreign, sys.executable, '"D:\\别的程序\\main.pyw"', self.tmp, "别的程序")
        mine = Path(homes.shortcut(home.name))
        self.assertEqual(mine.name, "真维斯 (2).lnk")
        self.assertEqual(_shortcut.read_args(foreign), '"D:\\别的程序\\main.pyw"')          # 原来那个没被动
        self.assertEqual(_shortcut.read_args(mine), f'"{homes.ENTRY}" --home "{home}"')    # 中文路径原样保留
        self.assertEqual(Path(homes.shortcut(home.name)), mine)             # 再建一次：更新自己的，不会越建越多
        self.assertEqual(len(list((self.tmp / "桌面").glob("*.lnk"))), 2)
        self.assertIsNone(_shortcut.read_args(self.tmp / "不存在.lnk"))


class Names(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(homes.safe_name("真维斯"), "真维斯")
        self.assertEqual(homes.safe_name("  a/b\\c:d*e?  "), "abcde")
        self.assertEqual(homes.safe_name("../../etc"), "etc")
        self.assertEqual(homes.safe_name("..."), "assistant")
        self.assertEqual(homes.safe_name(""), "assistant")
        self.assertEqual(len(homes.safe_name("长" * 80)), 24)


if __name__ == "__main__":
    unittest.main()

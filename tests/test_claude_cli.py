import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
from zws import claude_cli as C, prompt


class Billing(unittest.TestCase):
    def test_classification(self):
        sub = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}
        self.assertEqual(C.billing(sub), ("subscription", "订阅登录，不另计费"))             # 不显示订阅档位
        self.assertEqual(C.billing(dict(sub, subscriptionType=None))[0], "subscription")
        self.assertEqual(C.billing(dict(sub, subscriptionType="free"))[0], "unknown")
        self.assertEqual(C.billing({"loggedIn": False})[0], "logged_out")
        self.assertEqual(C.billing(None)[0], "unknown")
        self.assertEqual(C.billing(dict(sub, authMethod="api_key"))[0], "metered")
        self.assertEqual(C.billing(dict(sub, authMethod="oauth_token"))[0], "metered")
        self.assertEqual(C.billing(dict(sub, apiProvider="bedrock"))[0], "metered")
        self.assertEqual(C.billing(dict(sub, apiKeySource="ANTHROPIC_API_KEY"))[0], "metered")

    def test_extra_usage_reads_local_file_only(self):
        d = Path(tempfile.mkdtemp(prefix="zws-test-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(C.extra_usage(d))
        (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"hasExtraUsageEnabled": True}}), encoding="utf-8")
        self.assertIs(C.extra_usage(d), True)
        (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"hasExtraUsageEnabled": False}}), encoding="utf-8")
        self.assertIs(C.extra_usage(d), False)
        (d / ".claude.json").write_text("坏的", encoding="utf-8")
        self.assertIsNone(C.extra_usage(d))

    def test_env_drops_metered_identities(self):
        e = C.clean_env({"ANTHROPIC_API_KEY": "sk-x", "ANTHROPIC_BASE_URL": "http://x", "CLAUDE_CODE_USE_BEDROCK": "1", "PATH": "p"})
        self.assertEqual(e["PATH"], "p")
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"):
            self.assertNotIn(k, e)
        self.assertEqual(e["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")


class Command(unittest.TestCase):
    def test_isolated_command(self):
        cmd = C.command("claude", prompt.SYSTEM, model="opus", effort="medium", budget=0.3)
        self.assertEqual(cmd[:4], ["claude", "-p", "--output-format", "json"])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "0.3")
        self.assertEqual(cmd[cmd.index("--system-prompt") + 1], prompt.SYSTEM)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("--model", C.command("claude", "s", model="; rm -rf /"))     # 不在名单里的模型名不传
        self.assertEqual(C.command("claude", "s", isolated=False), ["claude", "-p", "--output-format", "json", "--system-prompt", "s"])

    def test_parse_result(self):
        ok = C.parse_result(json.dumps({"result": " 好 ", "is_error": False, "duration_ms": 2500}).encode())
        self.assertEqual((ok["ok"], ok["text"], ok["secs"]), (True, "好", 2.5))
        noisy = C.parse_result(b"warning: something\n" + json.dumps({"result": "x", "is_error": False}).encode())
        self.assertTrue(noisy["ok"])
        bad = C.parse_result(json.dumps({"result": "Not logged in. Please run /login", "is_error": True}).encode())
        self.assertEqual((bad["ok"], bad["err"]), (False, "本机 Claude Code 还没登录：先在终端运行 claude 并登录"))
        self.assertFalse(C.parse_result(json.dumps({"result": "", "is_error": False}).encode())["ok"])
        crash = C.parse_result(b"", b"error: unknown option '--safe-mode'\n", 1)
        self.assertEqual(crash["err"], "本机 Claude Code 的版本太旧，不认这次调用的参数；更新后再试")
        self.assertIn("unknown option", crash["raw"])

    def test_error_messages(self):
        self.assertIn("额度用完", C.err_cn("Claude usage limit reached. Your limit will reset at 3pm"))
        self.assertIn("预算", C.err_cn("Exceeded USD budget"))
        self.assertIn("繁忙", C.err_cn("API Error: 529 Overloaded"))
        self.assertEqual(C.err_cn(""), "没有返回内容")
        self.assertTrue(C.err_cn("something odd happened\nsecond line").startswith("调用失败：something odd happened"))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zws-test-")
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_check_and_run(self):
        exe = helpers.fake_claude(self.tmp, ["回答：先做最难的那件。"])
        self.assertEqual(C.find(), exe)
        self.assertEqual(C.version(exe), "9.9.9")
        self.assertEqual(C.billing(C.auth_status(exe))[0], "subscription")
        res = C.run(exe, "【问题】\n今天先做什么？引号 \" 和 % & | 都在标准输入里\n", prompt.SYSTEM, model="sonnet")
        self.assertEqual((res["ok"], res["text"]), (True, "回答：先做最难的那件。"))
        sent = (Path(self.tmp) / "prompt0.txt").read_text(encoding="utf-8")
        self.assertIn('引号 " 和 % & | 都在标准输入里', sent)

    def test_timeout(self):
        exe = helpers.fake_claude(self.tmp, ["x"])
        (Path(self.tmp) / "hang").write_text("1")
        res = C.run(exe, "问", prompt.SYSTEM, timeout=1.5)
        self.assertFalse(res["ok"])
        self.assertIn("没有结果", res["err"])

    def test_missing_executable(self):
        res = C.run(str(Path(self.tmp) / "nope.exe"), "问", prompt.SYSTEM)
        self.assertFalse(res["ok"])
        self.assertIn("启动 Claude Code 失败", res["err"])


if __name__ == "__main__":
    unittest.main()

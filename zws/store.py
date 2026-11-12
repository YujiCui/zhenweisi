"""一个助手的全部本地数据：plan.json（配置）、state.json（当天与长期状态）、history.json、memory.json。

都在这个助手自己的文件夹里，纯 JSON，删掉文件夹就是卸载。写入先写临时文件再替换，断电不会留半个文件。
"""
import copy
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from . import brain, schema

HISTORY_MAX = 800
DAILY = {"done": {}, "habits": {}, "water_ml": 0, "food": [], "review": "", "notes": [], "focus": None,
         "focus_min": 0, "mute": {}, "train_done": False}
KEEP = {"todos": [], "weights": [], "maint": {}, "people": {}, "train_idx": 0, "feedback": {}, "ui": {}, "next_id": 1}


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, indent=1)
    for i in range(5):
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
            os.replace(tmp, path)
            return
        except OSError:
            if i == 4:
                raise
            time.sleep(0.05 * (i + 1))          # Windows 上文件被别的进程短暂占着时重试


def new_state(day=""):
    s = copy.deepcopy(DAILY)
    s.update(copy.deepcopy(KEEP))
    s["v"] = 1
    s["day"] = day
    return s


class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.p_plan = self.home / "plan.json"
        self.p_good = self.home / "plan.last-good.json"
        self.p_state = self.home / "state.json"
        self.p_hist = self.home / "history.json"
        self.p_mem = self.home / "memory.json"
        self.state = new_state()
        self.hist = []
        self.mem = []
        self._lock = None

    # ---- 配置 ----
    def plan_mtime(self):
        try:
            return self.p_plan.stat().st_mtime_ns
        except OSError:
            return 0

    def read_plan(self):
        """→ (plan, 提示, 错误)。plan.json 改坏了就沿用上一版能用的，并把错误交给界面显示。"""
        try:
            with open(self.p_plan, encoding="utf-8-sig") as f:
                plan, w = schema.normalize(json.load(f))
        except (OSError, ValueError) as e:
            good = read_json(self.p_good)
            if isinstance(good, dict):
                return schema.normalize(good)[0], [], f"plan.json 读不了，沿用上一版：{str(e)[:80]}"
            return schema.default_plan(), [], f"plan.json 读不了，先用默认配置：{str(e)[:80]}"
        try:
            if read_json(self.p_good) != plan:
                write_json(self.p_good, plan)
        except OSError:
            pass
        return plan, w, None

    # ---- 状态 ----
    def load(self):
        s = read_json(self.p_state)
        base = new_state()
        if isinstance(s, dict):
            for k, v in base.items():
                if k in s and isinstance(s[k], type(v)) or (k in s and v is None):
                    base[k] = s[k]
        self.state = base
        h = read_json(self.p_hist)
        self.hist = [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []
        m = read_json(self.p_mem)
        self.mem = [x for x in m if isinstance(x, dict) and x.get("text")] if isinstance(m, list) else []
        return self

    def save(self):
        write_json(self.p_state, self.state)

    def save_hist(self):
        write_json(self.p_hist, self.hist[-HISTORY_MAX:])

    def save_mem(self):
        write_json(self.p_mem, self.mem)

    def rollover(self, plan, now):
        """逻辑日变了：把昨天的摘要写进历史，清空当天的勾选和记录。返回是否换了日。"""
        today = brain.logical(now, plan)[0]
        day = self.state.get("day")
        if day == today.isoformat():
            return False
        if day:
            try:
                self.hist = [h for h in self.hist if h.get("day") != day]
                self.hist.append(brain.summarize(plan, self.state, date.fromisoformat(day)))
                self.hist.sort(key=lambda h: h.get("day", ""))
                self.save_hist()
            except ValueError:
                pass
        for k, v in DAILY.items():
            self.state[k] = copy.deepcopy(v)
        keep_from = (today - timedelta(days=14)).isoformat()         # 完成的待办留 14 天
        self.state["todos"] = [t for t in self.state.get("todos", [])
                               if not t.get("done") or (t.get("done_day") or "") >= keep_from]
        self.state["day"] = today.isoformat()
        self.save()
        return True

    def next_id(self):
        n = int(self.state.get("next_id", 1))
        self.state["next_id"] = n + 1
        return n

    # ---- 撤销用的快照 ----
    def snapshot(self):
        return copy.deepcopy(self.state), copy.deepcopy(self.mem)

    def restore(self, snap):
        self.state, self.mem = copy.deepcopy(snap[0]), copy.deepcopy(snap[1])
        self.save()
        self.save_mem()

    # ---- 单实例锁：进程退出或崩溃时系统自动释放 ----
    def lock(self):
        """拿到锁返回 True；同一个助手已经开着返回 False。"""
        self.home.mkdir(parents=True, exist_ok=True)
        f = open(self.home / ".lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._lock = f
        return True

    def unlock(self):
        if self._lock:
            try:
                self._lock.close()
            except OSError:
                pass
            self._lock = None


def is_running(home):
    """这个助手的浮窗是不是开着（试着拿一下锁）。"""
    s = Store(home)
    if not (Path(home) / ".lock").exists():
        return False
    if s.lock():
        s.unlock()
        return False
    return True

"""Local JSON memory: owner rules + caller history."""
import json, os, shutil, threading, time

PATH = os.environ.get("GATEKEEPER_MEMORY", "data/memory.json")
EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "memory.example.json")


class Memory:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self._seen = set()  # callers already counted this session
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            shutil.copy(EXAMPLE, path)

    def _load(self):
        with open(self.path) as f:
            return json.load(f)

    def _save(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.path)

    @property
    def rules(self):
        return self._load()["rules"]

    @staticmethod
    def _key(caller):
        return " ".join(str(caller).lower().split())[:80]

    def remember(self, caller, note):
        k = self._key(caller)
        if not k:
            return {"error": "caller name required"}
        with self._lock:
            d = self._load()
            c = d["callers"].setdefault(k, {"name": str(caller)[:80], "times_contacted": 0, "notes": []})
            if k not in self._seen:  # one contact per session, however many notes
                self._seen.add(k)
                c["times_contacted"] += 1
            c["notes"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "note": str(note)[:500]})
            c["notes"] = c["notes"][-20:]
            self._save(d)
            return {"ok": True, "times_contacted": c["times_contacted"]}

    def recall(self, caller):
        c = self._load()["callers"].get(self._key(caller))
        return c or {"name": caller, "times_contacted": 0, "notes": []}

    def times_contacted(self, caller):
        return self.recall(caller)["times_contacted"]

    def is_vip(self, caller):
        k = self._key(caller)
        return any(v.lower() in k for v in self.rules["vips"])

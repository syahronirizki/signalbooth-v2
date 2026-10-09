"""Self-check for storage.py, outside the browser: python3 py/test_storage.py

Stubs PyScript's `window` with a localStorage that throws past a quota,
the way a real browser does. Not mounted in pyscript.toml, so it never ships.
"""

import json
import sys
import types


class FakeLocalStorage:
    def __init__(self, quota):
        self.data, self.quota = {}, quota

    def getItem(self, key):
        return self.data.get(key)

    def setItem(self, key, value):
        # One quota across all keys, like a browser's localStorage.
        others = sum(len(v) for k, v in self.data.items() if k != key)
        if others + len(value) > self.quota:
            raise Exception("QuotaExceededError")
        self.data[key] = value


ls = FakeLocalStorage(quota=1000)
sys.modules["pyscript"] = types.SimpleNamespace(window=types.SimpleNamespace(localStorage=ls))
import storage  # noqa: E402


def fresh(data=None):
    ls.data = dict(data or {})
    storage._gallery = None


# Settings: wrong-typed or unknown cached values fall back to defaults.
fresh({storage.SETTINGS_KEY: json.dumps({"intensity": "loud", "mirror": "yes", "theme_index": 2.0, "junk": 1})})
s = storage.load_settings()
assert s["intensity"] == 1.0 and s["mirror"] is True and s["theme_index"] == 2 and "junk" not in s, s
fresh({storage.SETTINGS_KEY: "{not json"})
assert storage.load_settings() == storage.DEFAULT_SETTINGS

# New keys: wrong types fall back, and settings saved before they existed still load.
fresh({storage.SETTINGS_KEY: json.dumps({"background": 5, "timer": "10"})})
s = storage.load_settings()
assert s["background"] == "none" and s["timer"] == 3, s
fresh({storage.SETTINGS_KEY: json.dumps({"mirror": False, "background": "neon", "timer": 10})})
s = storage.load_settings()
assert s["mirror"] is False and s["background"] == "neon" and s["timer"] == 10, s
fresh({storage.SETTINGS_KEY: json.dumps({"mirror": False})})
s = storage.load_settings()
assert s["background"] == "none" and s["timer"] == 3, s

# Gallery: quota full drops oldest shots, keeps the newest, reports how many.
fresh()
shot = "x" * 200  # ~4 shots fit under the 1000-char quota once JSON-wrapped
results = [storage.add_photo(shot, "aura") for _ in range(8)]
kept = storage.load_gallery()
assert 0 < len(kept) < 8 and results[0] == 0 and sum(results) == 8 - len(kept), results
assert json.loads(ls.data[storage.GALLERY_KEY]) == kept  # cache matches what's stored

# A shot too big to ever fit: nothing written, strip untouched.
before = ls.data[storage.GALLERY_KEY]
assert storage.add_photo("x" * 5000, "aura") is None
assert ls.data[storage.GALLERY_KEY] == before and len(storage.load_gallery()) == len(kept)

# Once the strip has filled the quota, a slightly longer settings write must
# still fit — the strip leaves headroom when it has to drop shots.
fresh()
for _ in range(8):
    storage.add_photo(shot, "aura")
assert storage.save_settings(dict(storage.DEFAULT_SETTINGS, last_effect="softfocus", background="aurora"))
assert storage.load_settings()["background"] == "aurora"
kept = storage.load_gallery()

# Delete and clear go all the way to an empty, stored list.
assert storage.delete_photo(kept[0]["id"]) == 0 and len(storage.load_gallery()) == len(kept) - 1
assert storage.clear_gallery() == 0 and ls.data[storage.GALLERY_KEY] == "[]"

# Corrupt gallery JSON reads as empty instead of crashing the app.
fresh({storage.GALLERY_KEY: "[{]"})
assert storage.load_gallery() == []

print("storage ok")

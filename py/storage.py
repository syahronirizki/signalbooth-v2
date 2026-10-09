"""
storage.py — the entire persistence layer, and it's just localStorage.

No database, no server, no IndexedDB — the project called for localStorage
only, so this module is deliberately small: read JSON out, write JSON in,
and fail safely if the browser's storage quota is full.

The gallery is a few MB of base64 JPEGs, so it's parsed out of localStorage
once per page load and then kept in memory; every write goes through to
localStorage and only updates the in-memory copy once the browser accepted it.
"""

import json
import time

from pyscript import window

GALLERY_KEY = "signalbooth:gallery"
SETTINGS_KEY = "signalbooth:settings"
ONBOARDED_KEY = "signalbooth:onboarded"
MAX_GALLERY_ITEMS = 24

DEFAULT_SETTINGS = {
    "mirror": True,
    "show_skeleton": False,
    "palm_shutter": True,  # hold ✋ still to fire the shutter, hands-free
    "intensity": 1.0,
    "last_effect": "idle",
    "theme_index": 0,
    "background": "none",  # virtual background key, see py/backgrounds.py
    "timer": 3,  # self-timer seconds before the shutter; 0 = shoot at once
}

_gallery = None  # in-memory copy of the strip; None until first read


def _get_raw(key):
    try:
        return window.localStorage.getItem(key)
    except Exception as err:
        print(f"[storage] read failed for {key}: {err}")
        return None


def _set_raw(key, value):
    try:
        window.localStorage.setItem(key, value)
        return True
    except Exception as err:
        # Most likely QuotaExceededError — the caller decides how to recover.
        print(f"[storage] write failed for {key}: {err}")
        return False


def _coerce(value, default):
    """Keeps a stored value only if it has the default's type, so a stale or
    hand-edited cache can't feed a string into the render loop."""
    if isinstance(default, bool) or not isinstance(default, (int, float)):
        return value if type(value) is type(default) else default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return type(default)(value)
    return default


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(_get_raw(SETTINGS_KEY) or "{}")
    except (ValueError, TypeError):
        return settings
    if isinstance(data, dict):
        for key, default in DEFAULT_SETTINGS.items():
            if key in data:
                settings[key] = _coerce(data[key], default)
    return settings


def save_settings(settings):
    return _set_raw(SETTINGS_KEY, json.dumps(settings))


def is_onboarded():
    return bool(_get_raw(ONBOARDED_KEY))


def mark_onboarded():
    _set_raw(ONBOARDED_KEY, "1")


def load_gallery():
    global _gallery
    if _gallery is None:
        try:
            data = json.loads(_get_raw(GALLERY_KEY) or "[]")
        except (ValueError, TypeError):
            data = []
        _gallery = [p for p in data if isinstance(p, dict) and "id" in p and "dataUrl" in p] if isinstance(data, list) else []
    return list(_gallery)


def gallery_size():
    """Rough bytes the strip takes in localStorage (base64 is ASCII)."""
    return sum(len(p["dataUrl"]) for p in load_gallery())


def save_gallery(items):
    """Writes the strip, dropping the oldest shots one at a time until the
    browser's quota accepts it. Returns how many were dropped to make room,
    or None if nothing could be written (the stored strip is left as it was)."""
    global _gallery
    items = items[-MAX_GALLERY_ITEMS:]
    for dropped in range(max(1, len(items))):
        kept = items[dropped:]
        if _set_raw(GALLERY_KEY, json.dumps(kept)):
            if dropped and len(kept) > 1:
                # We're at the quota's edge: give up one more shot so small
                # writes (settings) still fit. A shrinking write can't fail.
                kept = kept[1:]
                _set_raw(GALLERY_KEY, json.dumps(kept))
                dropped += 1
            _gallery = kept
            return dropped
    return None


def add_photo(data_url, effect_name):
    now = int(time.time() * 1000)
    items = load_gallery()
    taken = {p["id"] for p in items}
    while f"shot-{now}" in taken:  # ids must stay unique — delete goes by id
        now += 1
    items.append({"id": f"shot-{now}", "dataUrl": data_url, "effect": effect_name, "ts": now})
    return save_gallery(items)


def delete_photo(photo_id):
    return save_gallery([p for p in load_gallery() if p["id"] != photo_id])


def clear_gallery():
    return save_gallery([])

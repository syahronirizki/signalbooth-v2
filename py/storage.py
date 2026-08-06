"""
storage.py — the entire persistence layer, and it's just localStorage.

No database, no server, no IndexedDB — the project called for localStorage
only, so this module is deliberately small: read JSON out, write JSON in,
and fail safely if the browser's storage quota is full.
"""

import json
import time

from pyscript import window

GALLERY_KEY = "signalbooth:gallery"
SETTINGS_KEY = "signalbooth:settings"
MAX_GALLERY_ITEMS = 24

DEFAULT_SETTINGS = {
    "mirror": True,
    "show_skeleton": False,
    "intensity": 1.0,
    "last_effect": "idle",
    "theme_index": 0,
}


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


def load_settings():
    raw = _get_raw(SETTINGS_KEY)
    if not raw:
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(raw)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except (ValueError, TypeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    return _set_raw(SETTINGS_KEY, json.dumps(settings))


def load_gallery():
    raw = _get_raw(GALLERY_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def save_gallery(items):
    trimmed = items[-MAX_GALLERY_ITEMS:]
    ok = _set_raw(GALLERY_KEY, json.dumps(trimmed))
    if not ok and len(trimmed) > 1:
        # Storage is likely full. Drop the oldest half and try once more
        # rather than silently losing the newest photo.
        trimmed = trimmed[len(trimmed) // 2 :]
        ok = _set_raw(GALLERY_KEY, json.dumps(trimmed))
    return trimmed if ok else load_gallery()


def add_photo(data_url, effect_name):
    items = load_gallery()
    items.append(
        {
            "id": f"shot-{int(time.time() * 1000)}",
            "dataUrl": data_url,
            "effect": effect_name,
            "ts": int(time.time() * 1000),
        }
    )
    return save_gallery(items)


def delete_photo(photo_id):
    items = [p for p in load_gallery() if p["id"] != photo_id]
    save_gallery(items)
    return items


def clear_gallery():
    _set_raw(GALLERY_KEY, json.dumps([]))
    return []

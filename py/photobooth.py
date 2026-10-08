"""
photobooth.py — the capture flow (timer → snapshot → flash), the strip
grid, and the photo viewer. Their DOM — every tile, the empty state, the
viewer's share/save/delete — is built here, in Python, and written straight
into the page. This is the clearest example in the project of Python
actually doing DOM manipulation rather than just reacting to it.
"""

import asyncio
import base64

from pyodide.ffi import to_js
from pyscript import document, window

import effects
import storage

ARM_SECONDS = 3
_busy = False
_viewer = {"id": None, "share": None}  # the open shot's id + its ready-made share payload


async def start_countdown(canvas, effect_name, mirrored, seconds, notify=print):
    """Counts down `seconds` (0 shoots straight away), then captures and
    flashes. Ignored if a capture is already running, so a held palm can't
    queue up a dozen photos. `notify` gets a one-line caption saying whether
    the shot was saved."""
    global _busy
    if _busy:
        return
    _busy = True
    el = document.getElementById("countdown")
    btn = document.getElementById("capture-btn")
    btn.classList.add("shutter--busy")
    try:
        if seconds:
            el.classList.remove("hidden")
        for n in range(seconds, 0, -1):
            el.innerText = str(n)
            _replay(el, "countdown--tick")
            await asyncio.sleep(1)
        el.innerText = ""
        notify(capture(canvas, effect_name, mirrored))
        _replay(document.getElementById("flash"), "flash-play")
        _replay(document.getElementById("gallery-btn"), "last-shot--pop")
        await asyncio.sleep(0.3)
    finally:
        el.classList.add("hidden")
        btn.classList.remove("shutter--busy")
        _busy = False


def _replay(el, cls):
    el.classList.remove(cls)
    _ = el.offsetWidth  # force reflow so back-to-back runs re-trigger the animation
    el.classList.add(cls)


def capture(canvas, effect_name, mirrored):
    """Exports what's on screen as a JPEG: only the visible part of the
    canvas (a phone held upright sees a slice of a landscape frame), and
    mirrored if the live view is — the visible box is centered, so
    mirroring the crop is the same as cropping the mirror."""
    x, y, w, h = effects.safe_rect()
    export = document.createElement("canvas")
    export.width = w
    export.height = h
    ectx = export.getContext("2d")
    if mirrored:
        ectx.translate(w, 0)
        ectx.scale(-1, 1)
    ectx.drawImage(canvas, x, y, w, h, 0, 0, w, h)

    dropped = storage.add_photo(export.toDataURL("image/jpeg", 0.82), effect_name)
    render_gallery()
    if dropped is None:
        return "Storage full · not saved"
    if dropped:
        return f"Saved · {dropped} oldest removed"
    return f"Saved · {len(storage.load_gallery())} in strip"


def _when(item):
    """The shot's time in the viewer's own locale (Pyodide's clock is UTC)."""
    opts = to_js({"dateStyle": "medium", "timeStyle": "short"}, dict_converter=window.Object.fromEntries)
    return window.Date.new(item.get("ts") or 0).toLocaleString(None, opts)


def render_gallery():
    """Rebuilds the strip grid plus everything that summarizes it: the
    last-shot thumbnail and count on the camera, the size line, Clear all."""
    grid = document.getElementById("gallery-grid")
    grid.innerHTML = ""
    items = list(reversed(storage.load_gallery()))
    count = len(items)

    thumb = document.getElementById("last-shot-img")
    thumb.classList.toggle("hidden", not count)
    if count:
        thumb.src = items[0]["dataUrl"]
    document.getElementById("strip-count").innerText = str(count) if count else ""
    document.getElementById("gallery-btn").setAttribute(
        "aria-label", f"Open your photo strip, {count} shot{'' if count == 1 else 's'}"
    )
    size = storage.gallery_size()
    size_label = f"{size / 1e6:.1f} MB" if size >= 1e5 else f"{max(1, round(size / 1e3))} KB"
    document.getElementById("gallery-meta").innerText = (
        f"{count} of {storage.MAX_GALLERY_ITEMS} · {size_label} on this device" if count else ""
    )
    document.getElementById("gallery-clear").classList.toggle("hidden", not count)

    if not items:
        empty = document.createElement("p")
        empty.className = "gallery-empty"
        empty.innerText = "No shots yet. Hold ✋ still, tap the shutter, or press space."
        grid.appendChild(empty)
        return

    for item in items:
        # No listener per tile: main.py delegates clicks on the grid by data-open.
        tile = document.createElement("button")
        tile.className = "shot-tile"
        tile.setAttribute("data-open", item["id"])
        tile.setAttribute("aria-label", f"Open photo from {_when(item)}")
        img = document.createElement("img")
        img.src = item["dataUrl"]
        img.alt = ""
        tile.appendChild(img)
        grid.appendChild(tile)


def open_viewer(photo_id):
    item = next((p for p in storage.load_gallery() if p["id"] == photo_id), None)
    if item is None:
        return
    _viewer["id"] = photo_id
    _viewer["share"] = _share_payload(item)
    img = document.getElementById("viewer-img")
    img.src = item["dataUrl"]
    img.alt = f"Photo from {_when(item)}"
    document.getElementById("viewer-meta").innerText = _when(item)
    save = document.getElementById("viewer-save")
    save.href = item["dataUrl"]
    save.setAttribute("download", f"signalbooth-{photo_id}.jpg")
    document.getElementById("viewer-share").classList.toggle("hidden", _viewer["share"] is None)
    document.getElementById("viewer").classList.remove("hidden")
    document.getElementById("viewer-back").focus()


def close_viewer(refocus=True):
    """Closes the viewer if it's open and returns whether it was. Focus goes
    back to the tile that opened it."""
    viewer = document.getElementById("viewer")
    if viewer.classList.contains("hidden"):
        return False
    viewer.classList.add("hidden")
    tile = document.querySelector(f'[data-open="{_viewer["id"]}"]')
    _viewer.update(id=None, share=None)
    if refocus and tile:
        tile.focus()
    return True


def _share_payload(item):
    """The navigator.share() payload for a shot, or None where the browser
    can't share files. Built when the viewer opens, not on tap: iOS only
    allows share() while the tap is still fresh, so nothing may be awaited
    between the click and the call."""
    nav = window.navigator
    if not hasattr(nav, "canShare"):
        return None
    try:
        raw = base64.b64decode(item["dataUrl"].split(",", 1)[1])
        opts = to_js({"type": "image/jpeg"}, dict_converter=window.Object.fromEntries)
        file = window.File.new(to_js([to_js(raw)]), f"signalbooth-{item['id']}.jpg", opts)
        payload = to_js({"files": [file], "title": "Signalbooth"}, dict_converter=window.Object.fromEntries)
        return payload if nav.canShare(payload) else None
    except Exception as err:
        print(f"[photobooth] share unavailable: {err}")
        return None


def share_current(notify=print):
    payload = _viewer["share"]
    if payload is not None:
        asyncio.ensure_future(_await_share(window.navigator.share(payload), notify))


async def _await_share(promise, notify):
    try:
        await promise
    except Exception as err:  # AbortError just means the share sheet was dismissed
        if "AbortError" not in str(err):
            notify("Couldn't share")


def delete_current():
    photo_id = _viewer["id"]
    close_viewer(refocus=False)
    if photo_id:
        delete(photo_id)


def confirm_then(btn, prompt, action):
    """Two-tap confirm for destructive buttons: the first tap arms the button
    and relabels it with `prompt`, a second tap within ARM_SECONDS runs
    `action`, otherwise it quietly disarms. Cheaper than a modal, much harder
    to fat-finger than a bare delete."""
    if btn.classList.contains("is-armed"):
        _disarm(btn)
        action()
        return
    btn.setAttribute("data-label", btn.textContent)
    btn.classList.add("is-armed")
    btn.textContent = prompt
    asyncio.ensure_future(_disarm_later(btn))


def _disarm(btn):
    if btn.classList.contains("is-armed"):
        btn.classList.remove("is-armed")
        btn.textContent = btn.getAttribute("data-label")


async def _disarm_later(btn):
    await asyncio.sleep(ARM_SECONDS)
    _disarm(btn)


def delete(photo_id):
    storage.delete_photo(photo_id)
    render_gallery()


def clear_all():
    storage.clear_gallery()
    render_gallery()

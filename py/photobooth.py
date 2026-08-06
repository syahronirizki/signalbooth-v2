"""
photobooth.py — the capture flow (countdown → snapshot → flash) and the
gallery drawer. The gallery's DOM — every thumbnail, the empty state, the
save/delete links — is built here, in Python, and written straight into
the page. This is the clearest example in the project of Python actually
doing DOM manipulation rather than just reacting to it.
"""

import asyncio

from pyodide.ffi import create_proxy
from pyscript import document

import storage

COUNTDOWN_SECONDS = 3
_busy = False


async def start_countdown(canvas, video, effect_name, mirrored=True):
    """Runs a 3-2-1 countdown, then captures and flashes. Ignored if a
    capture is already in progress, so a held peace sign can't queue up
    a dozen photos."""
    global _busy
    if _busy:
        return
    _busy = True
    el = document.getElementById("countdown")
    el.classList.remove("hidden")
    try:
        for n in range(COUNTDOWN_SECONDS, 0, -1):
            el.innerText = str(n)
            await asyncio.sleep(1)
        el.innerText = ""
        capture(canvas, video, effect_name, mirrored=mirrored)
        _flash()
        await asyncio.sleep(0.3)
    finally:
        el.classList.add("hidden")
        _busy = False


def _flash():
    flash = document.getElementById("flash")
    flash.classList.remove("flash-play")
    _ = flash.offsetWidth  # force reflow so back-to-back captures re-trigger the animation
    flash.classList.add("flash-play")


def capture(canvas, video, effect_name, mirrored=True):
    """Composites the current canvas (video + live effect) to a JPEG data
    URL. If the live view is mirrored, the export is mirrored too, so the
    saved photo matches what was actually on screen — not a backwards
    version of it."""
    width, height = canvas.width, canvas.height
    export = document.createElement("canvas")
    export.width = width
    export.height = height
    ectx = export.getContext("2d")

    if mirrored:
        ectx.translate(width, 0)
        ectx.scale(-1, 1)
    ectx.drawImage(canvas, 0, 0, width, height)

    data_url = export.toDataURL("image/jpeg", 0.82)
    storage.add_photo(data_url, effect_name)
    render_gallery()
    return data_url


def render_gallery():
    grid = document.getElementById("gallery-grid")
    grid.innerHTML = ""
    items = list(reversed(storage.load_gallery()))

    if not items:
        empty = document.createElement("p")
        empty.className = "gallery-empty"
        empty.innerText = "No shots yet. Try a peace sign \u270c\ufe0f, or tap the shutter."
        grid.appendChild(empty)
        return

    for item in items:
        grid.appendChild(_build_shot_card(item))


def _build_shot_card(item):
    card = document.createElement("div")
    card.className = "shot-card"

    img = document.createElement("img")
    img.src = item["dataUrl"]
    img.alt = f"Capture with the {item['effect']} effect"
    card.appendChild(img)

    row = document.createElement("div")
    row.className = "shot-row"

    save_link = document.createElement("a")
    save_link.innerText = "Save"
    save_link.href = item["dataUrl"]
    save_link.setAttribute("download", f"signalbooth-{item['id']}.jpg")
    save_link.className = "shot-link"
    row.appendChild(save_link)

    delete_btn = document.createElement("button")
    delete_btn.innerText = "Delete"
    delete_btn.className = "shot-link shot-link--danger"
    delete_btn.addEventListener("click", create_proxy(_make_delete_handler(item["id"])))
    row.appendChild(delete_btn)

    card.appendChild(row)
    return card


def _make_delete_handler(photo_id):
    def handler(evt):
        storage.delete_photo(photo_id)
        render_gallery()

    return handler


def clear_all():
    storage.clear_gallery()
    render_gallery()

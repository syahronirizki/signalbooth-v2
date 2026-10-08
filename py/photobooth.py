"""
photobooth.py — the capture flow (countdown → snapshot → flash) and the
gallery drawer. The gallery's DOM — every thumbnail, the empty state, the
save/delete links — is built here, in Python, and written straight into
the page. This is the clearest example in the project of Python actually
doing DOM manipulation rather than just reacting to it.
"""

import asyncio

from pyscript import document

import storage

COUNTDOWN_SECONDS = 3
ARM_SECONDS = 3
_busy = False


async def start_countdown(canvas, video, effect_name, mirrored=True, notify=print):
    """Runs a 3-2-1 countdown, then captures and flashes. Ignored if a
    capture is already in progress, so a held palm can't queue up a dozen
    photos. `notify` gets a one-line caption saying whether the shot was saved."""
    global _busy
    if _busy:
        return
    _busy = True
    el = document.getElementById("countdown")
    btn = document.getElementById("capture-btn")
    el.classList.remove("hidden")
    btn.classList.add("capture-btn--busy")
    try:
        for n in range(COUNTDOWN_SECONDS, 0, -1):
            el.innerText = str(n)
            _replay(el, "countdown--tick")
            await asyncio.sleep(1)
        el.innerText = ""
        notify(capture(canvas, video, effect_name, mirrored=mirrored))
        _replay(document.getElementById("flash"), "flash-play")
        await asyncio.sleep(0.3)
    finally:
        el.classList.add("hidden")
        btn.classList.remove("capture-btn--busy")
        _busy = False


def _replay(el, cls):
    el.classList.remove(cls)
    _ = el.offsetWidth  # force reflow so back-to-back runs re-trigger the animation
    el.classList.add(cls)


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

    dropped = storage.add_photo(export.toDataURL("image/jpeg", 0.82), effect_name)
    render_gallery()
    if dropped is None:
        return "Storage full · not saved"
    if dropped:
        return f"Saved · {dropped} oldest removed"
    return f"Saved · {len(storage.load_gallery())} in strip"


def render_gallery():
    """Rebuilds the strip plus everything that summarizes it: the count
    badge on the Strip button, the size line, and the Clear all button."""
    grid = document.getElementById("gallery-grid")
    grid.innerHTML = ""
    items = list(reversed(storage.load_gallery()))
    count = len(items)

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
        empty.innerText = "No shots yet. Hold \u270b still, tap the shutter, or press space."
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

    # No listener per card: main.py delegates clicks on the grid by data-delete.
    delete_btn = document.createElement("button")
    delete_btn.innerText = "Delete"
    delete_btn.className = "shot-link shot-link--danger"
    delete_btn.setAttribute("data-delete", item["id"])
    row.appendChild(delete_btn)

    card.appendChild(row)
    return card


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

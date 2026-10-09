"""
main.py — Signalbooth's entry point.

This is where the JS layer (handTracker.js — webcam + ML models only) meets
the Python layer that owns everything else: interpreting gestures, picking
and rendering the active effect and background, running the photobooth
flow, wiring up every control on the camera screen, and reading/writing
localStorage.

handTracker.js calls window.__sbFrameCallback once per video frame with a
plain {"hands": [...], "t": ...} object. on_frame() below is that callback:
it's the whole app's heartbeat.

Two kinds of thing can react to a gesture, and the difference is the main
idea in this file:

  * **Effects** are persistent. A gesture switches to one and it stays until
    something else switches away, so you can set a look and then move freely.
  * **Moments** are momentary. They play while their gesture is held (plus a
    short tail so they don't cut off the instant tracking blinks) and then
    they're gone — the thinking pad, the love rain, the portal.

The dial — a scroll-snap carousel around the shutter, like a Stories camera —
shows either effects or virtual backgrounds; whatever rests under the
shutter is the active one.
"""

import asyncio
import time

from pyscript import document, when, window
from pyodide.ffi import create_proxy, to_js

import backgrounds
import effects
import gestures
import photobooth
import storage

# ---------------------------------------------------------------- state --

settings = storage.load_settings()
tracker = gestures.HandTracker()

TIMER_STEPS = (3, 10, 0)  # what the timer button cycles through, in seconds

state = {
    "effect": settings["last_effect"],
    "background": settings["background"],
    "timer": settings["timer"],
    "facing": "user",  # not persisted: the app always opens on the selfie camera
    "mode": "effects",  # what the dial shows: "effects" or "backgrounds"
    "flipping": False,
    "ready": False,  # camera running; shots before that would be a blank 1×1
    "theme_index": settings["theme_index"],
    "intensity": settings["intensity"],
    "last_frame_t": time.time(),
    "combo": 0,  # frames-worth of held 👍, shown as the hype counter
    "combo_since": 0.0,
}

# Eased 0..1 amounts driving the canvas filter on the webcam frame itself.
# Easing rather than snapping is what makes the blur read as a lens racking
# focus instead of a dropped frame.
filter_amounts = {"blur": 0.0, "gray": 0.0, "love": 0.0, "warm": 0.0}

overlay = {"name": None, "until": 0.0, "started": 0.0}

EFFECT_ORDER = ["idle", "aura", "spark", "laser", "rainbow", "softfocus", "hype", "rain"]
EFFECT_META = {
    "idle": {"label": "Idle", "icon": "○", "signal": None},
    "aura": {"label": "Aura Glow", "icon": "✋", "signal": "open_palm"},
    "spark": {"label": "Spark Burst", "icon": "✊", "signal": "fist"},
    "laser": {"label": "Laser Trail", "icon": "☝", "signal": "point"},
    "rainbow": {"label": "Rainbow", "icon": "\U0001f308", "signal": None},
    "softfocus": {"label": "Soft Focus", "icon": "✌️", "signal": "peace"},
    "hype": {"label": "Hype", "icon": "\U0001f44d", "signal": "thumb_up"},
    "rain": {"label": "Rain Mood", "icon": "\U0001f44e", "signal": "thumb_down"},
}
SIGNAL_TO_EFFECT = {v["signal"]: k for k, v in EFFECT_META.items() if v["signal"]}

# Settings come back from localStorage, which can outlive the code that wrote
# it: an effect or background that's since been renamed, a timer value the
# button no longer offers, an intensity off the slider.
if state["effect"] not in EFFECT_META:
    state["effect"] = settings["last_effect"] = "idle"
if state["background"] not in backgrounds.BACKGROUND_META:
    state["background"] = settings["background"] = "none"
if state["timer"] not in TIMER_STEPS:
    state["timer"] = settings["timer"] = 3
state["intensity"] = settings["intensity"] = min(1.6, max(0.4, state["intensity"]))

# How long each moment keeps playing after its gesture stops being seen.
# Held gestures refresh this every frame, so the number is really "tail
# length" — except for heart hands, which is a set-piece that plays out.
MOMENT_SECONDS = {
    "ok": 2.6,
    "finger_heart": 0.9,
    "heart_hands": 5.0,
    "ily": 1.2,
    "double_palm": 1.0,
}

canvas = document.getElementById("stage")
ctx = canvas.getContext("2d")
video = document.getElementById("camera-feed")
# Scratch canvas the person is cut out on before landing on a background.
person_canvas = document.createElement("canvas")
person_ctx = person_canvas.getContext("2d")
carousel = document.getElementById("carousel")
hud_top = document.getElementById("hud-top")
hud_bottom = document.getElementById("hud-bottom")

_CENTER = {"block": "nearest", "inline": "center"}  # smoothness is CSS scroll-behavior

# ------------------------------------------------------------- helpers --


def set_canvas_size(w, h):
    canvas.width = w
    canvas.height = h
    # On desktop the stage is framed in the camera's own shape
    # (css/stories.css), so nothing is cropped and photos keep full res.
    stage_wrap = document.getElementById("stage-wrap")
    stage_wrap.style.setProperty("--cam-w", str(w))
    stage_wrap.style.setProperty("--cam-h", str(h))


def is_mirrored():
    """The selfie view mirrors (if the setting says so); the back camera never does."""
    return bool(settings["mirror"]) and state["facing"] == "user"


def apply_mirror():
    canvas.style.transform = "scaleX(-1)" if is_mirrored() else "scaleX(1)"
    btn = document.getElementById("mirror-btn")
    btn.setAttribute("aria-pressed", "true" if is_mirrored() else "false")
    btn.disabled = state["facing"] != "user"  # the back camera never mirrors


def update_readout(frame_state):
    document.getElementById("gesture-label").innerText = frame_state["label"]
    readout = document.getElementById("readout")
    readout.classList.toggle("readout--live", bool(frame_state["present"]))


def toast(message):
    """A short caption for what just fired. The readout says what the hand is
    doing; this says what the app did about it."""
    el = document.getElementById("toast")
    el.innerText = message
    el.classList.remove("toast--show")
    _ = el.offsetWidth  # reflow, so back-to-back toasts re-run the animation
    el.classList.add("toast--show")


# ----------------------------------------------------------------- dial --


def _dial_items():
    """(key, meta) pairs for whatever the dial is showing."""
    if state["mode"] == "effects":
        return [(key, EFFECT_META[key]) for key in EFFECT_ORDER]
    return [(key, backgrounds.BACKGROUND_META[key]) for key in backgrounds.BACKGROUND_ORDER]


def build_dial():
    """Fills the dial for the current mode and centers the active item."""
    effects_mode = state["mode"] == "effects"
    carousel.innerHTML = ""
    carousel.setAttribute("aria-label", "Effects" if effects_mode else "Backgrounds")
    for number, (key, meta) in enumerate(_dial_items(), start=1):
        btn = document.createElement("button")
        btn.className = "dial-item"
        btn.setAttribute("data-key", key)
        btn.setAttribute("aria-label", meta["label"])
        if effects_mode:
            btn.setAttribute("aria-keyshortcuts", str(number))
            btn.innerHTML = f'<span aria-hidden="true">{meta["icon"]}</span>'
        else:
            btn.style.background = meta["swatch"]
        carousel.appendChild(btn)
    document.getElementById("mode-effects").setAttribute("aria-pressed", "true" if effects_mode else "false")
    document.getElementById("mode-backgrounds").setAttribute("aria-pressed", "false" if effects_mode else "true")
    mark_active()


def mark_active(scroll=True):
    """Syncs the dial to the active key: its pressed state, the label above
    the shutter, and — unless the user's finger is what moved it — scrolls
    it to the middle, under the shutter."""
    key = state["effect"] if state["mode"] == "effects" else state["background"]
    document.getElementById("active-label").innerText = dict(_dial_items())[key]["label"]
    for item in carousel.querySelectorAll(".dial-item"):
        active = item.getAttribute("data-key") == key
        item.setAttribute("aria-pressed", "true" if active else "false")
        item.tabIndex = 0 if active else -1  # one Tab stop for the dial; arrows turn it
        if active and scroll:
            item.scrollIntoView(to_js(_CENTER, dict_converter=window.Object.fromEntries))


def choose(key, scroll=True):
    if state["mode"] == "effects":
        set_effect(key, scroll=scroll)
    else:
        set_background(key, scroll=scroll)


_settle_token = [0]


async def _settle(token, delay=0.14):
    # A debounced scroll end (`scrollend` isn't in every mobile browser yet):
    # once the dial has held still for a beat, the item under the shutter wins.
    await asyncio.sleep(delay)
    if token != _settle_token[0]:
        return
    box = carousel.getBoundingClientRect()
    middle = box.left + box.width / 2
    nearest, best = None, None
    for item in carousel.querySelectorAll(".dial-item"):
        r = item.getBoundingClientRect()
        gap = abs(r.left + r.width / 2 - middle)
        if best is None or gap < best:
            nearest, best = item, gap
    if nearest:
        choose(nearest.getAttribute("data-key"), scroll=False)


# -------------------------------------------------------------- actions --


def set_effect(key, announce=False, scroll=True):
    if key == state["effect"]:
        return
    state["effect"] = key
    settings["last_effect"] = key
    storage.save_settings(settings)
    effects.reset_pools()  # don't leave the old effect's particles hanging
    if key != "hype":
        state["combo"] = 0
    if state["mode"] == "effects":
        mark_active(scroll)
    if announce:
        toast(EFFECT_META[key]["label"])


def set_background(key, scroll=True):
    """Switches the virtual background. Falls back to the real room when the
    segmenter couldn't load on this device, and snaps the dial back to say so."""
    if key != "none" and getattr(window, "__sbSegmenterState", "loading") == "failed":
        toast("Backgrounds unavailable here")
        key, scroll = "none", True
    if key != state["background"]:
        state["background"] = settings["background"] = key
        storage.save_settings(settings)
        window.__sbSegment = key != "none"
    if state["mode"] == "backgrounds":
        mark_active(scroll)


def cycle_theme():
    state["theme_index"] = (state["theme_index"] + 1) % len(effects.THEME_ORDER)
    settings["theme_index"] = state["theme_index"]
    storage.save_settings(settings)
    document.getElementById("theme-btn").setAttribute(
        "data-theme", effects.theme_name(state["theme_index"])
    )
    toast(f"Theme · {effects.theme_name(state['theme_index'])}")


def trigger_moment(name, now):
    if overlay["name"] != name:
        overlay["started"] = now
        effects.reset_pools()
    overlay["name"] = name
    overlay["until"] = now + MOMENT_SECONDS[name]


def shoot():
    if not state["ready"]:
        return
    asyncio.ensure_future(
        photobooth.start_countdown(canvas, state["effect"], is_mirrored(), state["timer"], notify=toast)
    )


def toggle_mirror():
    if state["facing"] != "user":
        toast("Back camera isn't mirrored")
        return
    settings["mirror"] = not settings["mirror"]
    storage.save_settings(settings)
    apply_mirror()
    toast("Mirror on" if settings["mirror"] else "Mirror off")


def show_timer():
    secs = state["timer"]
    document.getElementById("timer-value").innerText = f"{secs}s" if secs else "off"
    document.getElementById("timer-btn").setAttribute(
        "aria-label", f"Self-timer, {secs} seconds" if secs else "Self-timer off"
    )


def cycle_timer():
    state["timer"] = TIMER_STEPS[(TIMER_STEPS.index(state["timer"]) + 1) % len(TIMER_STEPS)]
    settings["timer"] = state["timer"]
    storage.save_settings(settings)
    show_timer()
    toast(f"Timer · {state['timer']}s" if state["timer"] else "Timer off")


async def flip_camera():
    """Front ↔ back. handTracker.js restarts the stream (and puts the old
    camera back if the new one won't open); a fresh signalbooth:ready then
    resizes the canvas."""
    if state["flipping"]:
        return
    state["flipping"] = True
    target = "environment" if state["facing"] == "user" else "user"
    try:
        ok = await window.__sbSetFacing(target)
    finally:
        state["flipping"] = False
    if not ok:
        toast("Couldn't switch camera")
        return
    state["facing"] = target
    apply_mirror()
    document.getElementById("flip-btn").setAttribute(
        "aria-label", "Switch to front camera" if target == "environment" else "Switch to back camera"
    )


def _ease(current, target, dt, rate=7.0):
    return current + (target - current) * min(1.0, dt * rate)


def _filter_targets(effect, moment):
    """Which webcam-frame filters the current effect + moment want."""
    return {
        "blur": 1.0 if effect == "softfocus" else 0.0,
        "gray": 1.0 if effect == "rain" else 0.0,
        "warm": 1.0 if effect == "hype" else 0.0,
        "love": 1.0 if moment in ("heart_hands", "finger_heart") else 0.0,
    }


# ------------------------------------------------------------ main loop --


def on_frame(frame_js):
    """Called once per video frame by handTracker.js. This is the app's
    entire per-frame cycle: read the gesture, react to it, draw the camera
    (or you on a background) plus the active effect and any moment on top."""
    frame = frame_js.to_py()

    now = time.time()
    dt = max(0.0, min(0.05, now - state["last_frame_t"]))
    state["last_frame_t"] = now

    frame_state = tracker.process(frame, canvas.width, canvas.height)
    update_readout(frame_state)

    for event_name, payload in frame_state["events"]:
        if event_name == "signal_changed":
            effect_key = SIGNAL_TO_EFFECT.get(payload)
            if effect_key:
                set_effect(effect_key, announce=True)
            elif payload in MOMENT_SECONDS:
                toast(gestures.SIGNAL_LABELS.get(payload, payload))
        elif event_name == "palm_hold" and settings["palm_shutter"]:
            shoot()

    signal = frame_state["signal"]
    if signal in MOMENT_SECONDS:
        trigger_moment(signal, now)
    if overlay["name"] and now >= overlay["until"]:
        overlay["name"] = None

    moment = overlay["name"]
    effect = state["effect"]

    # 👍 held over time climbs a combo counter; letting go resets it.
    if effect == "hype" and signal == "thumb_up":
        if state["combo"] == 0:
            state["combo_since"] = now
        state["combo"] = min(999, int((now - state["combo_since"]) * 8) + 1)
    else:
        state["combo"] = 0
        state["combo_since"] = now

    targets = _filter_targets(effect, moment)
    for key, target in targets.items():
        filter_amounts[key] = _ease(filter_amounts[key], target, dt)
    look = effects.video_filter(filter_amounts)

    # A phone rotated mid-session turns its camera track sideways without a
    # new loadeddata; follow the video's shape or every frame gets stretched.
    if video.videoWidth and (video.videoWidth != canvas.width or video.videoHeight != canvas.height):
        set_canvas_size(video.videoWidth, video.videoHeight)
    colors = effects.theme_colors(state["theme_index"])
    intensity = state["intensity"]
    w, h = canvas.width, canvas.height

    # ---- webcam frame, or you cut out onto a virtual background ----
    if state["background"] != "none" and getattr(window, "__sbSegmenterState", "") == "failed":
        set_background("none")  # the segmenter died mid-session: back to the real room
        toast("Backgrounds unavailable here")
    mask = getattr(window, "__sbMask", None)
    if state["background"] != "none" and mask:
        backgrounds.draw_background(ctx, state["background"], video, now, w, h, colors, look)
        backgrounds.composite_person(ctx, person_canvas, person_ctx, mask, video, w, h, look)
    else:
        ctx.filter = look
        ctx.drawImage(video, 0, 0, w, h)
        ctx.filter = "none"  # overlays stay sharp over a blurred frame

    # The canvas is shown with object-fit: cover, so its edges are cropped
    # whenever the window isn't the camera's shape, and the HUD bars cover
    # its top and bottom. Effects need to know what's readable before they
    # place any text; captures need to know what's visible.
    effects.set_safe_box(
        w, h, canvas.clientWidth, canvas.clientHeight, hud_top.offsetHeight, hud_bottom.offsetHeight
    )
    mirrored = is_mirrored()
    t = now

    # ---- persistent effect ----
    if not frame_state["present"] and effect not in ("softfocus", "rain"):
        effects.draw_idle(ctx, t, w, h)
    elif effect == "aura":
        effects.draw_aura_glow(ctx, frame_state, colors, t, intensity)
    elif effect == "spark":
        effects.draw_spark_burst(ctx, frame_state, colors, dt, intensity)
    elif effect == "laser":
        effects.draw_laser_trail(ctx, frame_state, colors, intensity)
    elif effect == "rainbow":
        effects.draw_rainbow_trail(ctx, frame_state, t, intensity)
    elif effect == "softfocus":
        effects.draw_soft_focus(ctx, frame_state, colors, t, dt, w, h, intensity, mirrored)
    elif effect == "hype":
        effects.draw_hype(ctx, frame_state, colors, t, dt, w, h, intensity, mirrored, state["combo"])
    elif effect == "rain":
        effects.draw_rain_mood(ctx, frame_state, t, dt, w, h, intensity, mirrored)

    # ---- moment on top ----
    if moment:
        age = now - overlay["started"]
        if moment == "ok":
            effects.draw_gwenchana(ctx, frame_state, colors, t, age, w, h, mirrored)
        elif moment == "finger_heart":
            effects.draw_finger_heart(ctx, frame_state, t, dt, w, h, intensity, mirrored)
        elif moment == "heart_hands":
            effects.draw_heart_hands(ctx, frame_state, t, age, dt, w, h, intensity, mirrored)
        elif moment == "ily":
            effects.draw_star_shower(ctx, frame_state, colors, t, dt, w, h, intensity, mirrored)
        elif moment == "double_palm":
            effects.draw_portal(ctx, frame_state, colors, t, w, h, intensity, mirrored)

    # ---- shutter feedback + debug ----
    if settings["palm_shutter"]:
        effects.draw_palm_hold_ring(
            ctx,
            frame_state,
            colors,
            tracker.palm_hold_progress((frame.get("t") or 0.0) / 1000.0),
            mirrored,
            w,
        )

    if settings["show_skeleton"] and frame_state["present"]:
        for hand in frame_state["hands"]:
            effects.draw_skeleton(ctx, hand["landmarks"], w, h)


window.__sbFrameCallback = create_proxy(on_frame)

# --------------------------------------------------------------- events --


# The guide and the two drawers are overlays: only one is open at a time, the
# stage behind goes `inert` (so Tab can't wander under it), and closing puts
# focus back on whatever opened it.
OVERLAYS = ("onboarding", "gallery-drawer", "settings-drawer")
_return_focus = [None]


def _open_overlay_id():
    for el_id in OVERLAYS:
        if not document.getElementById(el_id).classList.contains("hidden"):
            return el_id
    return None


def open_overlay(el_id):
    if _open_overlay_id() is None:
        _return_focus[0] = document.activeElement
    for other in OVERLAYS:
        document.getElementById(other).classList.toggle("hidden", other != el_id)
    # The guide brings its own backdrop; drawers share the scrim.
    document.getElementById("scrim").classList.toggle("hidden", el_id == "onboarding")
    document.querySelector(".stage-wrap").inert = True
    document.getElementById(el_id).querySelector("button").focus()


def close_overlays():
    open_id = _open_overlay_id()
    if open_id is None:
        return
    if open_id == "onboarding":
        storage.mark_onboarded()
    photobooth.close_viewer(refocus=False)
    document.getElementById(open_id).classList.add("hidden")
    document.getElementById("scrim").classList.add("hidden")
    document.querySelector(".stage-wrap").inert = False
    if _return_focus[0]:
        _return_focus[0].focus()
    _return_focus[0] = None


def _on_camera_ready(evt):
    detail = evt.detail
    state["ready"] = True
    set_canvas_size(detail.width, detail.height)
    apply_mirror()
    cameras = getattr(detail, "cameras", 1) or 1
    document.getElementById("flip-btn").classList.toggle("invisible", cameras < 2)
    document.getElementById("boot-screen").classList.add("hidden")
    mark_active()  # layout is final now; center whatever was restored
    if not storage.is_onboarded() and _open_overlay_id() is None:
        open_overlay("onboarding")


window.addEventListener("signalbooth:ready", create_proxy(_on_camera_ready))


@when("click", "#onboarding-dismiss")
def dismiss_onboarding(evt):
    close_overlays()


@when("click", "#onboarding")
def on_onboarding_backdrop(evt):
    if evt.target.id == "onboarding":  # the dimmed area, not the card
        close_overlays()


@when("click", "#scrim")
def on_scrim_click(evt):
    close_overlays()


@when("click", "#help-btn")
def show_help(evt):
    open_overlay("onboarding")


@when("click", "#theme-btn")
def on_theme_click(evt):
    cycle_theme()


@when("click", "#capture-btn")
def on_capture_click(evt):
    shoot()


@when("click", "#timer-btn")
def on_timer_click(evt):
    cycle_timer()


@when("click", "#mirror-btn")
def on_mirror_click(evt):
    toggle_mirror()


@when("click", "#flip-btn")
def on_flip_click(evt):
    asyncio.ensure_future(flip_camera())


@when("click", "#mode-effects, #mode-backgrounds")
def on_mode_click(evt):
    mode = "effects" if evt.currentTarget.id == "mode-effects" else "backgrounds"
    if mode != state["mode"]:
        state["mode"] = mode
        build_dial()


@when("keydown", "#carousel")
def on_dial_key(evt):
    """Left/Right turn the dial from the keyboard. Only the active item is a
    Tab stop, so tabbing past the dial can't scroll it onto a new effect."""
    step = {"ArrowRight": 1, "ArrowLeft": -1}.get(evt.key)
    if not step:
        return
    evt.preventDefault()  # no native scroll; choose() centers the new item
    keys = [key for key, _ in _dial_items()]
    current = state["effect"] if state["mode"] == "effects" else state["background"]
    target = keys[max(0, min(len(keys) - 1, keys.index(current) + step))]
    choose(target)
    # preventScroll: focus's own scroll-into-view would cut short choose()'s
    # smooth centering, and the settle would then snap back to the old item.
    carousel.querySelector(f'[data-key="{target}"]').focus(
        to_js({"preventScroll": True}, dict_converter=window.Object.fromEntries)
    )


@when("click", "#carousel")
def on_dial_click(evt):
    item = evt.target.closest(".dial-item")
    if item:
        choose(item.getAttribute("data-key"))


# Where the browser fires `scrollend`, it marks the true end of a swipe and its
# snap; a finger pausing mid-drag shouldn't pick whatever is passing under the
# shutter. Elsewhere a short lull in scroll events stands in for it.
_HAS_SCROLLEND = hasattr(window, "onscrollend")


@when("scroll", "#carousel")
def on_dial_scroll(evt):
    if _HAS_SCROLLEND:
        return
    _settle_token[0] += 1
    asyncio.ensure_future(_settle(_settle_token[0]))


@when("scrollend", "#carousel")
def on_dial_scrollend(evt):
    _settle_token[0] += 1
    asyncio.ensure_future(_settle(_settle_token[0], 0))


@when("click", "#gallery-btn")
def open_gallery(evt):
    open_overlay("gallery-drawer")  # already current: render_gallery runs on every change


@when("click", "#settings-btn")
def open_settings(evt):
    open_overlay("settings-drawer")


@when("click", "#gallery-close, #settings-close")
def close_drawer(evt):
    close_overlays()


def _refocus_strip():
    # The pressed button is gone (deleted, or hidden with the viewer); keep
    # keyboard focus in the drawer instead of letting it fall to <body>.
    document.getElementById("gallery-close").focus()


@when("click", "#gallery-grid")
def on_gallery_click(evt):
    tile = evt.target.closest("[data-open]")
    if tile:
        photobooth.open_viewer(tile.getAttribute("data-open"))


@when("click", "#viewer-back")
def on_viewer_back(evt):
    photobooth.close_viewer()


@when("click", "#viewer-share")
def on_viewer_share(evt):
    photobooth.share_current(notify=toast)


@when("click", "#viewer-delete")
def on_viewer_delete(evt):
    photobooth.confirm_then(
        evt.currentTarget, "Sure?", lambda: (photobooth.delete_current(), _refocus_strip())
    )


@when("click", "#gallery-clear")
def clear_gallery_click(evt):
    count = len(storage.load_gallery())
    photobooth.confirm_then(
        evt.currentTarget, f"Delete all {count}?", lambda: (photobooth.clear_all(), _refocus_strip())
    )


@when("change", "#skeleton-toggle")
def on_skeleton_toggle(evt):
    settings["show_skeleton"] = bool(evt.target.checked)
    storage.save_settings(settings)


@when("change", "#palmhold-toggle")
def on_palmhold_toggle(evt):
    settings["palm_shutter"] = bool(evt.target.checked)
    storage.save_settings(settings)


def show_intensity(value):
    document.getElementById("intensity-value").innerText = f"{value:.1f}×"


@when("input", "#intensity-slider")
def on_intensity_input(evt):
    # Live while dragging; written to localStorage once, on release.
    state["intensity"] = float(evt.target.value)
    show_intensity(state["intensity"])


@when("change", "#intensity-slider")
def on_intensity_change(evt):
    settings["intensity"] = state["intensity"]
    storage.save_settings(settings)


@when("keydown", "body")
def on_key(evt):
    """Keyboard shortcuts, for demoing the app without waving at it. They
    stand down while an overlay is open, and for any focused control that
    owns the key itself — space on a button presses that button."""
    key = (evt.key or "").lower()
    if key == "escape":
        if not photobooth.close_viewer():  # Esc in the viewer steps back to the grid first
            close_overlays()
        return
    tag = evt.target.tagName
    if (
        evt.repeat
        or evt.metaKey
        or evt.ctrlKey
        or evt.altKey
        or _open_overlay_id()
        or tag in ("INPUT", "TEXTAREA", "SELECT")
        or (key == " " and tag in ("BUTTON", "A"))
    ):
        return
    if key == " ":
        evt.preventDefault()
        shoot()
    elif key == "t":
        cycle_theme()
    elif key == "m":
        toggle_mirror()
    elif key in "12345678":
        index = int(key) - 1
        if index < len(EFFECT_ORDER):
            set_effect(EFFECT_ORDER[index], announce=True)


# ------------------------------------------------------------------ init --

window.__sbSegment = state["background"] != "none"
build_dial()
show_timer()
apply_mirror()
document.getElementById("skeleton-toggle").checked = settings["show_skeleton"]
document.getElementById("palmhold-toggle").checked = settings["palm_shutter"]
document.getElementById("intensity-slider").value = str(settings["intensity"])
show_intensity(settings["intensity"])
document.getElementById("theme-btn").setAttribute(
    "data-theme", effects.theme_name(state["theme_index"])
)
photobooth.render_gallery()

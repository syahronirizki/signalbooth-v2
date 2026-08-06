"""
main.py — Signalbooth's entry point.

This is where the JS layer (handTracker.js — webcam + ML model only) meets
the Python layer that owns everything else: interpreting gestures, picking
and rendering the active effect, running the photobooth flow, wiring up
every button and toggle in the UI, and reading/writing localStorage.

handTracker.js calls window.__sbFrameCallback once per video frame with a
plain {"hands": [...], "t": ...} object. on_frame() below is that callback:
it's the whole app's heartbeat.

Two kinds of thing can react to a gesture, and the difference is the main
idea in this file:

  * **Effects** are persistent. A gesture switches to one and it stays until
    something else switches away, so you can set a look and then move freely.
    They're also selectable by hand from the chip dock.
  * **Moments** are momentary. They play while their gesture is held (plus a
    short tail so they don't cut off the instant tracking blinks) and then
    they're gone — the thinking pad, the love rain, the portal.
"""

import asyncio
import time

from pyscript import document, when, window
from pyodide.ffi import create_proxy

import effects
import gestures
import photobooth
import storage

# ---------------------------------------------------------------- state --

settings = storage.load_settings()
tracker = gestures.HandTracker()

state = {
    "effect": settings["last_effect"],
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

# ------------------------------------------------------------- helpers --


def set_canvas_size(w, h):
    canvas.width = w
    canvas.height = h


def apply_mirror():
    canvas.style.transform = "scaleX(-1)" if settings["mirror"] else "scaleX(1)"


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


def build_effect_dock():
    dock = document.getElementById("effect-dock")
    dock.innerHTML = ""
    for key in EFFECT_ORDER:
        meta = EFFECT_META[key]
        btn = document.createElement("button")
        active = key == state["effect"]
        btn.className = "effect-chip" + (" effect-chip--active" if active else "")
        btn.setAttribute("data-effect", key)
        btn.setAttribute("title", meta["label"])
        btn.innerHTML = (
            f'<span class="effect-chip__icon">{meta["icon"]}</span>'
            f'<span class="effect-chip__label">{meta["label"]}</span>'
        )
        btn.addEventListener("click", create_proxy(_make_effect_handler(key)))
        dock.appendChild(btn)


def _make_effect_handler(key):
    def handler(evt):
        set_effect(key, announce=True)

    return handler


def set_effect(key, announce=False):
    if key == state["effect"]:
        return
    state["effect"] = key
    settings["last_effect"] = key
    storage.save_settings(settings)
    effects.reset_pools()  # don't leave the old effect's particles hanging
    if key != "hype":
        state["combo"] = 0
    for chip in document.querySelectorAll(".effect-chip"):
        chip.classList.toggle("effect-chip--active", chip.getAttribute("data-effect") == key)
    if announce:
        toast(EFFECT_META[key]["label"])


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
    asyncio.ensure_future(
        photobooth.start_countdown(canvas, video, state["effect"], settings["mirror"])
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
    entire per-frame cycle: read the gesture, react to it, draw the video
    plus the active effect and any moment on top."""
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

    # ---- webcam frame, filtered ----
    targets = _filter_targets(effect, moment)
    for key, target in targets.items():
        filter_amounts[key] = _ease(filter_amounts[key], target, dt)

    ctx.filter = effects.video_filter(filter_amounts)
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    ctx.filter = "none"  # overlays stay sharp over a blurred frame

    colors = effects.theme_colors(state["theme_index"])
    intensity = state["intensity"]
    w, h = canvas.width, canvas.height
    # The canvas is displayed with object-fit: cover, so its edges are cropped
    # off whenever the window isn't the camera's aspect ratio. Effects need to
    # know what's actually on screen before they place any text.
    effects.set_safe_box(w, h, canvas.clientWidth, canvas.clientHeight)
    mirrored = bool(settings["mirror"])
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


def _on_camera_ready(evt):
    detail = evt.detail
    set_canvas_size(detail.width, detail.height)
    apply_mirror()
    document.getElementById("boot-screen").classList.add("hidden")
    if not window.localStorage.getItem("signalbooth:onboarded"):
        document.getElementById("onboarding").classList.remove("hidden")


window.addEventListener("signalbooth:ready", create_proxy(_on_camera_ready))


@when("click", "#onboarding-dismiss")
def dismiss_onboarding(evt):
    document.getElementById("onboarding").classList.add("hidden")
    window.localStorage.setItem("signalbooth:onboarded", "1")


@when("click", "#help-btn")
def show_help(evt):
    document.getElementById("onboarding").classList.remove("hidden")


@when("click", "#theme-btn")
def on_theme_click(evt):
    cycle_theme()


@when("click", "#capture-btn")
def on_capture_click(evt):
    shoot()


@when("click", "#gallery-btn")
def open_gallery(evt):
    photobooth.render_gallery()
    document.getElementById("gallery-drawer").classList.remove("hidden")


@when("click", "#gallery-close")
def close_gallery(evt):
    document.getElementById("gallery-drawer").classList.add("hidden")


@when("click", "#gallery-clear")
def clear_gallery_click(evt):
    photobooth.clear_all()


@when("click", "#settings-btn")
def open_settings(evt):
    document.getElementById("settings-drawer").classList.remove("hidden")


@when("click", "#settings-close")
def close_settings(evt):
    document.getElementById("settings-drawer").classList.add("hidden")


@when("change", "#mirror-toggle")
def on_mirror_toggle(evt):
    settings["mirror"] = bool(evt.target.checked)
    storage.save_settings(settings)
    apply_mirror()


@when("change", "#skeleton-toggle")
def on_skeleton_toggle(evt):
    settings["show_skeleton"] = bool(evt.target.checked)
    storage.save_settings(settings)


@when("change", "#palmhold-toggle")
def on_palmhold_toggle(evt):
    settings["palm_shutter"] = bool(evt.target.checked)
    storage.save_settings(settings)


@when("input", "#intensity-slider")
def on_intensity_change(evt):
    state["intensity"] = float(evt.target.value)
    settings["intensity"] = state["intensity"]
    storage.save_settings(settings)


@when("keydown", "body")
def on_key(evt):
    """Keyboard shortcuts, for demoing the app without waving at it."""
    key = (evt.key or "").lower()
    if key == " ":
        evt.preventDefault()
        shoot()
    elif key == "t":
        cycle_theme()
    elif key == "m":
        el = document.getElementById("mirror-toggle")
        el.checked = not el.checked
        settings["mirror"] = bool(el.checked)
        storage.save_settings(settings)
        apply_mirror()
    elif key in "12345678":
        index = int(key) - 1
        if index < len(EFFECT_ORDER):
            set_effect(EFFECT_ORDER[index], announce=True)


# ------------------------------------------------------------------ init --

build_effect_dock()
document.getElementById("mirror-toggle").checked = settings["mirror"]
document.getElementById("skeleton-toggle").checked = settings["show_skeleton"]
document.getElementById("palmhold-toggle").checked = settings["palm_shutter"]
document.getElementById("intensity-slider").value = str(settings["intensity"])
document.getElementById("theme-btn").setAttribute(
    "data-theme", effects.theme_name(state["theme_index"])
)
photobooth.render_gallery()

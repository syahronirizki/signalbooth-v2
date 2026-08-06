"""
main.py — Signalbooth's entry point.

This is where the JS layer (handTracker.js — webcam + ML model only) meets
the Python layer that owns everything else: interpreting gestures, picking
and rendering the active effect, running the photobooth flow, wiring up
every button and toggle in the UI, and reading/writing localStorage.

handTracker.js calls window.__sbFrameCallback once per video frame with a
plain {"hands": [...], "t": ...} object. on_frame() below is that callback:
it's the whole app's heartbeat.
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
}

EFFECT_ORDER = ["idle", "aura", "spark", "laser", "rainbow"]
EFFECT_META = {
    "idle": {"label": "Idle", "icon": "\u25cb", "gesture": None},
    "aura": {"label": "Aura Glow", "icon": "\u270b", "gesture": "Open_Palm"},
    "spark": {"label": "Spark Burst", "icon": "\u270a", "gesture": "Closed_Fist"},
    "laser": {"label": "Laser Trail", "icon": "\u261d", "gesture": "Pointing_Up"},
    "rainbow": {"label": "Rainbow", "icon": "\U0001f44d", "gesture": "Thumb_Up"},
}
GESTURE_TO_EFFECT = {v["gesture"]: k for k, v in EFFECT_META.items() if v["gesture"]}

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


def build_effect_dock():
    dock = document.getElementById("effect-dock")
    dock.innerHTML = ""
    for key in EFFECT_ORDER:
        meta = EFFECT_META[key]
        btn = document.createElement("button")
        active = key == state["effect"]
        btn.className = "effect-chip" + (" effect-chip--active" if active else "")
        btn.setAttribute("data-effect", key)
        btn.innerHTML = (
            f'<span class="effect-chip__icon">{meta["icon"]}</span>'
            f'<span class="effect-chip__label">{meta["label"]}</span>'
        )
        btn.addEventListener("click", create_proxy(_make_effect_handler(key)))
        dock.appendChild(btn)


def _make_effect_handler(key):
    def handler(evt):
        set_effect(key)

    return handler


def set_effect(key):
    state["effect"] = key
    settings["last_effect"] = key
    storage.save_settings(settings)
    for chip in document.querySelectorAll(".effect-chip"):
        chip.classList.toggle("effect-chip--active", chip.getAttribute("data-effect") == key)


def cycle_theme():
    state["theme_index"] = (state["theme_index"] + 1) % len(effects.THEME_ORDER)
    settings["theme_index"] = state["theme_index"]
    storage.save_settings(settings)


# ------------------------------------------------------------ main loop --


def on_frame(frame_js):
    """Called once per video frame by handTracker.js. This is the app's
    entire per-frame cycle: read the gesture, react to it, draw the video
    plus the active effect on top."""
    frame = frame_js.to_py()

    now = time.time()
    dt = max(0.0, min(0.05, now - state["last_frame_t"]))
    state["last_frame_t"] = now

    frame_state = tracker.process(frame, canvas.width, canvas.height)
    update_readout(frame_state)

    for event_name, payload in frame_state["events"]:
        if event_name == "gesture_changed":
            effect_key = GESTURE_TO_EFFECT.get(payload)
            if effect_key:
                set_effect(effect_key)
            elif payload == "Victory":
                asyncio.ensure_future(
                    photobooth.start_countdown(canvas, video, state["effect"], settings["mirror"])
                )
        elif event_name == "pinch_start":
            cycle_theme()

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

    colors = effects.theme_colors(state["theme_index"])
    intensity = state["intensity"]
    effect = state["effect"]

    if not frame_state["present"]:
        effects.draw_idle(ctx, now, canvas.width, canvas.height)
    elif effect == "aura":
        effects.draw_aura_glow(ctx, frame_state, colors, now, intensity)
    elif effect == "spark":
        effects.draw_spark_burst(ctx, frame_state, colors, dt, intensity)
    elif effect == "laser":
        effects.draw_laser_trail(ctx, frame_state, colors, intensity)
    elif effect == "rainbow":
        effects.draw_rainbow_trail(ctx, frame_state, now, intensity)

    if settings["show_skeleton"] and frame_state["present"]:
        hand = (frame.get("hands") or [None])[0]
        if hand:
            effects.draw_skeleton(ctx, hand["landmarks"], canvas.width, canvas.height)


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


@when("click", "#capture-btn")
def on_capture_click(evt):
    asyncio.ensure_future(
        photobooth.start_countdown(canvas, video, state["effect"], settings["mirror"])
    )


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


@when("input", "#intensity-slider")
def on_intensity_change(evt):
    state["intensity"] = float(evt.target.value)
    settings["intensity"] = state["intensity"]
    storage.save_settings(settings)


# ------------------------------------------------------------------ init --

build_effect_dock()
document.getElementById("mirror-toggle").checked = settings["mirror"]
document.getElementById("skeleton-toggle").checked = settings["show_skeleton"]
document.getElementById("intensity-slider").value = str(settings["intensity"])
photobooth.render_gallery()

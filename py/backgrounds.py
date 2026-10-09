"""
backgrounds.py — virtual backgrounds: the room behind you swapped for a
scene, live and in saved photos.

handTracker.js runs MediaPipe's selfie segmenter and leaves its answer on a
small canvas (window.__sbMask) — opaque where you are, clear where the room
is. Everything else is plain 2D-canvas compositing, no per-pixel Python:

  1. draw_background()  paints the scene over the whole frame
  2. composite_person() keeps only you from the camera frame (the mask,
     drawn with "destination-in", on a scratch canvas) and lays that on top

Scenes are drawn in code rather than loaded as images: nothing to ship or
download, they move, and they pick up the active color theme.
"""

import math

BACKGROUND_ORDER = ["none", "blur", "neon", "sunset", "aurora"]
BACKGROUND_META = {
    "none": {"label": "Room", "swatch": "linear-gradient(135deg, #3a3044, #15111a)"},
    "blur": {
        "label": "Portrait Blur",
        "swatch": "radial-gradient(circle at 50% 38%, #e9dccb 0 20%, #6d6377 48%, #2a2332 100%)",
    },
    "neon": {
        "label": "Neon City",
        "swatch": "linear-gradient(180deg, #0d0614 0%, #3a1450 56%, #8b7ff0 58%, #0b0612 62%)",
    },
    "sunset": {
        "label": "Sunset",
        "swatch": "linear-gradient(180deg, #2b1036 0%, #b8476a 40%, #e8a33d 58%, #1d2a4a 60%, #0f1830 100%)",
    },
    "aurora": {
        "label": "Aurora",
        "swatch": "linear-gradient(160deg, #061325 15%, #1f8a78 45%, #8b7ff0 70%, #061325 100%)",
    },
}

BLUR_PX = 18  # how soft "Portrait Blur" makes the room

# Fixed once, so the skyline and the stars hold still from frame to frame.
_SKYLINE = [
    (i / 16, 0.10 + 0.22 * abs(0.5 * math.sin(i * 12.9898) + 0.5 * math.sin(i * 4.1414)))
    for i in range(16)
]
_STARS = [
    (0.5 + 0.5 * math.sin(i * 91.7), 0.55 * (0.5 + 0.5 * math.sin(i * 47.3)), 1 + i % 3)
    for i in range(28)
]


def draw_background(ctx, name, video, t, w, h, colors, video_filter="none"):
    """Paints the `name` scene over the whole frame. `video_filter` is the
    active effect's webcam filter (Soft Focus blur, Rain Mood gray); the
    scene gets it too, so you and the world behind you share one look."""
    if name == "blur":
        lead = "" if video_filter == "none" else video_filter + " "
        ctx.filter = f"{lead}blur({BLUR_PX}px) brightness(0.85)"
        pad = BLUR_PX * 2  # blur pulls clear pixels in from the edges; overdraw so the border stays solid
        ctx.drawImage(video, -pad, -pad, w + 2 * pad, h + 2 * pad)
        ctx.filter = "none"
        return
    ctx.filter = video_filter
    if name == "neon":
        _neon_city(ctx, t, w, h, colors)
    elif name == "sunset":
        _sunset(ctx, t, w, h, colors)
    elif name == "aurora":
        _aurora(ctx, t, w, h, colors)
    ctx.filter = "none"
    ctx.globalAlpha = 1.0
    ctx.globalCompositeOperation = "source-over"


def composite_person(ctx, scratch, sctx, mask, video, w, h, video_filter="none"):
    """Draws only the person: the camera frame, kept where the mask is
    opaque, built on a scratch canvas so the scene underneath survives. The
    mask is low-res and gets upscaled with smoothing, which softens the edge."""
    if scratch.width != w or scratch.height != h:
        scratch.width = w
        scratch.height = h
    sctx.globalCompositeOperation = "copy"  # replaces last frame, no clearRect needed
    sctx.filter = video_filter
    sctx.drawImage(video, 0, 0, w, h)
    sctx.filter = "none"
    sctx.globalCompositeOperation = "destination-in"
    sctx.drawImage(mask, 0, 0, w, h)
    sctx.globalCompositeOperation = "source-over"
    ctx.drawImage(scratch, 0, 0)


def _neon_city(ctx, t, w, h, colors):
    """Synthwave skyline: dusk sky, towers with one breathing window each,
    a glowing horizon, and a grid floor rolling toward you."""
    horizon = h * 0.62
    sky = ctx.createLinearGradient(0, 0, 0, horizon)
    sky.addColorStop(0, "#0d0614")
    sky.addColorStop(1, "#3a1450")
    ctx.fillStyle = sky
    ctx.fillRect(0, 0, w, horizon)
    ctx.fillStyle = "#0b0612"
    ctx.fillRect(0, horizon, w, h - horizon)

    tower_w = w / len(_SKYLINE)
    for i, (x_frac, height) in enumerate(_SKYLINE):
        x, tower_h = x_frac * w, h * height
        ctx.globalAlpha = 1.0
        ctx.fillStyle = "#1a0b26"
        ctx.fillRect(x, horizon - tower_h, tower_w * 0.9, tower_h)
        ctx.fillStyle = colors["a"]
        ctx.globalAlpha = 0.45 + 0.4 * (0.5 + 0.5 * math.sin(t * 0.8 + i * 1.7))
        ctx.fillRect(x + tower_w * 0.35, horizon - tower_h * 0.7, tower_w * 0.18, tower_h * 0.08)
    ctx.globalAlpha = 1.0

    ctx.strokeStyle = colors["b"]
    ctx.shadowColor = colors["b"]
    ctx.shadowBlur = 24
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.moveTo(0, horizon)
    ctx.lineTo(w, horizon)
    ctx.stroke()
    ctx.shadowBlur = 0

    ctx.globalAlpha = 0.55
    ctx.lineWidth = 1.5
    ctx.beginPath()
    for i in range(-10, 11):  # rails converging on the vanishing point
        ctx.moveTo(w / 2 + i * w * 0.02, horizon)
        ctx.lineTo(w / 2 + i * w * 0.16, h)
    roll = (t * 0.6) % 1.0
    for j in range(8):  # cross-ties, bunched up near the horizon, rolling forward
        y = horizon + ((j + roll) / 8) ** 2 * (h - horizon)
        ctx.moveTo(0, y)
        ctx.lineTo(w, y)
    ctx.stroke()
    ctx.globalAlpha = 1.0


def _sunset(ctx, t, w, h, colors):
    """A big striped sun sinking into a calm sea, with a shimmering reflection."""
    horizon = h * 0.64
    sky = ctx.createLinearGradient(0, 0, 0, horizon)
    sky.addColorStop(0, "#2b1036")
    sky.addColorStop(0.55, "#b8476a")
    sky.addColorStop(1, colors["a"])
    ctx.fillStyle = sky
    ctx.fillRect(0, 0, w, horizon)

    r = min(w, h) * 0.22
    cx = w / 2
    cy = horizon - r * 0.35 + math.sin(t * 0.5) * r * 0.04
    sun = ctx.createLinearGradient(0, cy - r, 0, cy + r)
    sun.addColorStop(0, "#fff1c9")
    sun.addColorStop(1, colors["a"])
    ctx.fillStyle = sun
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, math.tau)
    ctx.fill()
    ctx.fillStyle = sky  # slats in sky color: the gradient lives in canvas space, so it lines up
    for i in range(5):
        ctx.fillRect(cx - r, cy + r * (0.12 + i * 0.17), 2 * r, r * 0.03 * (i + 1))

    sea = ctx.createLinearGradient(0, horizon, 0, h)
    sea.addColorStop(0, "#1d2a4a")
    sea.addColorStop(1, "#0f1830")
    ctx.fillStyle = sea
    ctx.fillRect(0, horizon, w, h - horizon)
    ctx.fillStyle = colors["a"]
    for i in range(9):  # the reflection: shrinking, flickering bars
        half = r * (1.1 - i * 0.09) * (0.8 + 0.2 * math.sin(t * 2 + i))
        ctx.globalAlpha = 0.5 - i * 0.045
        ctx.fillRect(cx - half, horizon + (i + 1) * (h - horizon) / 10, 2 * half, 3)
    ctx.globalAlpha = 1.0


def _aurora(ctx, t, w, h, colors):
    """Night sky with twinkling stars and three soft ribbons of light."""
    sky = ctx.createLinearGradient(0, 0, 0, h)
    sky.addColorStop(0, "#061325")
    sky.addColorStop(1, "#0b0f1f")
    ctx.fillStyle = sky
    ctx.fillRect(0, 0, w, h)

    ctx.fillStyle = "#ECE6F2"
    for x_frac, y_frac, size in _STARS:
        ctx.globalAlpha = 0.35 + 0.35 * math.sin(t * 1.5 + x_frac * 20)
        ctx.fillRect(x_frac * w, y_frac * h, size, size)

    base_filter = ctx.filter
    ctx.filter = ("" if base_filter == "none" else base_filter + " ") + "blur(14px)"
    ctx.globalCompositeOperation = "lighter"  # overlapping ribbons glow brighter
    ctx.globalAlpha = 0.35
    steps = 12
    for k, color in enumerate(("#1f8a78", colors["b"], colors["a"])):
        top = h * (0.18 + k * 0.12)
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.moveTo(0, top)
        for s in range(steps + 1):
            ctx.lineTo(s / steps * w, top + math.sin(s * 0.7 + t * (0.6 + k * 0.2) + k) * h * 0.05)
        for s in range(steps, -1, -1):
            ctx.lineTo(s / steps * w, top + h * 0.16 + math.sin(s * 0.5 + t * 0.4 + k * 2) * h * 0.04)
        ctx.closePath()
        ctx.fill()
    ctx.filter = base_filter
    ctx.globalCompositeOperation = "source-over"
    ctx.globalAlpha = 1.0

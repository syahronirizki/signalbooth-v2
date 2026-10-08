# Stories Camera Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Signalbooth into a phone-first, Instagram-Stories-style camera with live virtual backgrounds, a swipeable effect/background dial around the shutter, a self-timer, front/back camera switching, on-screen-accurate captures, and an IG-style strip with a share-capable viewer.

**Architecture:** The JS/Python split stays: `js/handTracker.js` owns the camera and MediaPipe models (adds the selfie `ImageSegmenter`, camera switching, camera count); Python owns all drawing and DOM. A new `py/backgrounds.py` draws procedural scenes and composites the person over them with plain 2D-canvas operations. The UI moves to a full-bleed stage with frosted-glass HUD (`css/stories.css`), wired from `py/main.py`.

**Tech Stack:** PyScript 2026.7.2 (Pyodide), `@mediapipe/tasks-vision@0.10.3` (`GestureRecognizer` + `ImageSegmenter`), plain HTML/CSS/Canvas, `localStorage`.

**Spec:** `docs/superpowers/specs/2026-10-08-stories-camera-design.md`

## Global Constraints

- No new libraries or CDNs. Only new remote asset: `https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite` (~250KB).
- Persistence is `localStorage` only, and only through `py/storage.py`.
- `js/handTracker.js` = camera + ML only. All drawing and DOM in Python.
- Every tap target ≥ 44×44px; text contrast WCAG AA; everything keyboard-operable; `prefers-reduced-motion` respected.
- 375×812 viewport: no horizontal page scroll; camera fills the screen.
- Toast copy stays short (the toast is single-line, `nowrap`): ≤ 28 characters.
- Camera facing is not persisted; the app always opens on the selfie camera.
- Gallery storage schema unchanged: `{"id", "dataUrl", "effect", "ts"}`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Portrait phone capture** — a phone held upright shows a portrait slice of a landscape camera frame; the saved photo must be that slice (portrait), not the whole landscape frame. → Task 3 (`safe_rect` math test) + Task 6 Step 11 (real export is portrait).
2. **Dial settling** — after a swipe or a programmatic scroll, the item resting under the shutter must become the active effect/background, exactly once. → Task 6 Step 8.
3. **Segmenter unavailable or dying mid-session** — choosing a background must snap back to Room with a toast, and a background already on must fall back to the real camera; hand effects keep working. → Task 5 (JS catches and marks `failed`) + Task 6 Step 10.
4. **Camera switch fails** (phone can't open the back camera) — stay on the current camera, toast "Couldn't switch camera", mirror unchanged. → Task 6 Step 12.
5. **Share unsupported** (desktop Firefox, older Safari) — Share button hidden, Save still works, viewer still opens/closes/deletes. → Task 6 Step 13.

---

### Task 1: Branch and baseline

The previous UX pass (overlays, two-tap delete, cached storage) is uncommitted on `main`. Put it on a feature branch as its own commit so the redesign diffs cleanly against it.

**Files:**
- No code changes. Commits: `README.md`, `css/style.css`, `index.html`, `py/main.py`, `py/photobooth.py`, `py/storage.py`, `py/test_storage.py`, `docs/superpowers/`.

- [ ] **Step 1: Create the branch**

```bash
git switch -c feat/stories-camera
```

- [ ] **Step 2: Confirm the baseline self-check passes**

Run: `python3 py/test_storage.py 2>/dev/null`
Expected: last line `storage ok`

- [ ] **Step 3: Commit the UX pass, then the docs**

```bash
git add README.md css/style.css index.html py/main.py py/photobooth.py py/storage.py py/test_storage.py
git commit -m "feat: interactive UX pass and cached, quota-safe storage" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git add docs/superpowers
git commit -m "docs: stories camera spec and implementation plan" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settings for background and timer

**Files:**
- Modify: `py/storage.py` (`DEFAULT_SETTINGS`)
- Test: `py/test_storage.py`

**Interfaces:**
- Produces: `storage.load_settings()` now always contains `"background": str` (default `"none"`) and `"timer": int` (default `3`). Type-checked by the existing `_coerce`; *value* validation (known background, allowed timer) is `main.py`'s job in Task 6.

- [ ] **Step 1: Write the failing test**

In `py/test_storage.py`, insert immediately before the line `# Gallery: quota full drops oldest shots, keeps the newest, reports how many.`:

```python
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

```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 py/test_storage.py 2>/dev/null`
Expected: FAIL — `KeyError: 'background'`

- [ ] **Step 3: Add the defaults**

In `py/storage.py`, replace:

```python
    "last_effect": "idle",
    "theme_index": 0,
}
```

with:

```python
    "last_effect": "idle",
    "theme_index": 0,
    "background": "none",  # virtual background key, see py/backgrounds.py
    "timer": 3,  # self-timer seconds before the shutter; 0 = shoot at once
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 py/test_storage.py 2>/dev/null`
Expected: last line `storage ok`

- [ ] **Step 5: Commit**

```bash
git add py/storage.py py/test_storage.py
git commit -m "feat(storage): background and timer settings" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: HUD-aware text box and on-screen crop rect

The bottom dial and top bar will cover the stage. Effects place text inside `effects._SAFE`; it must shrink by the HUD heights. Captures must take the *visible* frame (not shrunk by the HUD), exposed as `safe_rect()`.

**Files:**
- Modify: `py/effects.py:97-113` (`_SAFE`, `set_safe_box`) and add `safe_rect()`
- Create: `py/test_render.py`

**Interfaces:**
- Produces:
  - `effects.set_safe_box(canvas_w, canvas_h, client_w, client_h, inset_top=0.0, inset_bottom=0.0) -> None` — insets are client-pixel heights of the HUD bars.
  - `effects.safe_rect() -> tuple[int, int, int, int]` — `(x, y, w, h)` of the visible part of the canvas, in canvas pixels.

- [ ] **Step 1: Write the failing test**

Create `py/test_render.py`:

```python
"""Self-check for the browser-free parts of rendering: python3 py/test_render.py

Checks the safe-box math that keeps effect text out from under the HUD and
crops captures to what's actually on screen.
"""

import effects

# Portrait phone (375×812) over a landscape 1280×720 frame: only a centered
# slice is visible, and the HUD bars cover the top and bottom of it.
effects.set_safe_box(1280, 720, 375, 812, inset_top=60, inset_bottom=200)
x, y, w, h = effects.safe_rect()
assert (y, h) == (0, 720) and w < h and abs(x + w / 2 - 640) <= 1, (x, y, w, h)
scale = 812 / 720
assert abs(effects._SAFE["y0"] - 60 / scale) < 1e-6, effects._SAFE
assert abs(effects._SAFE["y1"] - (720 - 200 / scale)) < 1e-6, effects._SAFE

# A HUD taller than the screen can't squeeze the text box inside out.
effects.set_safe_box(1280, 720, 375, 300, inset_top=200, inset_bottom=200)
assert effects._SAFE["y1"] - effects._SAFE["y0"] > 0, effects._SAFE

# A window the camera's shape: all visible, the crop is the whole frame.
effects.set_safe_box(1280, 720, 1280, 720)
assert effects.safe_rect() == (0, 0, 1280, 720)

# Not laid out yet (0×0 client): fall back to the whole frame.
effects.set_safe_box(1280, 720, 0, 0)
assert effects.safe_rect() == (0, 0, 1280, 720)

print("render ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 py/test_render.py`
Expected: FAIL — `TypeError: set_safe_box() got an unexpected keyword argument 'inset_top'`

- [ ] **Step 3: Implement**

In `py/effects.py`, replace the whole block from `_SAFE = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}` through the end of `set_safe_box` (the closing `)` of `_SAFE.update(...)`) with:

```python
_SAFE = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}  # where text is readable
_VISIBLE = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}  # what's on screen at all


def set_safe_box(canvas_w, canvas_h, client_w, client_h, inset_top=0.0, inset_bottom=0.0):
    """Works out which part of the canvas is on screen (object-fit: cover
    crops it) and, inside that, where text is readable: `inset_top` and
    `inset_bottom` are the client-pixel heights of the HUD bars drawn over
    the stage. Captures use the visible box (safe_rect); text uses _SAFE."""
    if not (canvas_w and canvas_h and client_w and client_h):
        _VISIBLE.update(x0=0.0, y0=0.0, x1=canvas_w or 1.0, y1=canvas_h or 1.0)
        _SAFE.update(_VISIBLE)
        return
    scale = max(client_w / canvas_w, client_h / canvas_h)
    vis_w = min(canvas_w, client_w / scale)
    vis_h = min(canvas_h, client_h / scale)
    _VISIBLE.update(
        x0=(canvas_w - vis_w) / 2,
        x1=(canvas_w + vis_w) / 2,
        y0=(canvas_h - vis_h) / 2,
        y1=(canvas_h + vis_h) / 2,
    )
    _SAFE.update(_VISIBLE)
    top, bottom = inset_top / scale, inset_bottom / scale
    if vis_h - top - bottom >= vis_h * 0.4:  # a HUD taller than the screen can't erase the text box
        _SAFE["y0"] += top
        _SAFE["y1"] -= bottom


def safe_rect():
    """The visible part of the canvas as integer (x, y, w, h) — what a
    capture exports, so the photo matches what was on screen."""
    x0, y0 = int(_VISIBLE["x0"]), int(_VISIBLE["y0"])
    return x0, y0, max(1, int(_VISIBLE["x1"]) - x0), max(1, int(_VISIBLE["y1"]) - y0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 py/test_render.py`
Expected: `render ok`

- [ ] **Step 5: Commit**

```bash
git add py/effects.py py/test_render.py
git commit -m "feat(effects): HUD-aware text box and visible-frame crop rect" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Background scenes and person compositor

**Files:**
- Create: `py/backgrounds.py`
- Modify: `pyscript.toml` (mount the new module)
- Test: `py/test_render.py`

**Interfaces:**
- Consumes: `effects.theme_colors(index) -> {"a": hex, "b": hex, "c": hex}` (existing).
- Produces:
  - `backgrounds.BACKGROUND_ORDER: list[str]` = `["none", "blur", "neon", "sunset", "aurora"]`
  - `backgrounds.BACKGROUND_META: dict[str, {"label": str, "swatch": str}]` — `swatch` is a CSS `background` value for the dial item.
  - `backgrounds.draw_background(ctx, name, video, t, w, h, colors, video_filter="none") -> None` — leaves `ctx.filter == "none"`, `globalAlpha == 1.0`, `globalCompositeOperation == "source-over"`.
  - `backgrounds.composite_person(ctx, scratch, sctx, mask, video, w, h, video_filter="none") -> None` — `scratch` is a canvas, `sctx` its 2D context, `mask` the JS mask canvas.

- [ ] **Step 1: Write the failing test**

In `py/test_render.py`, change the imports at the top from:

```python
import effects
```

to:

```python
import types

import backgrounds
import effects
```

and replace the final line `print("render ok")` with:

```python


class FakeGradient:
    def addColorStop(self, offset, color):
        assert 0 <= offset <= 1, offset  # browsers throw on stops outside 0..1


class FakeCtx:
    """Stands in for CanvasRenderingContext2D: every method call is accepted
    and recorded; property writes (fillStyle, filter, ...) just stick."""

    def __init__(self):
        self.calls = []
        self.filter = "none"
        self.globalAlpha = 1.0
        self.globalCompositeOperation = "source-over"

    def __getattr__(self, name):
        def method(*args):
            self.calls.append(name)
            return FakeGradient()

        return method


# Every scene draws (except the real room), at any time, without leaking state.
colors = effects.theme_colors(0)
for name in backgrounds.BACKGROUND_ORDER:
    meta = backgrounds.BACKGROUND_META[name]
    assert meta["label"] and meta["swatch"], name
    ctx = FakeCtx()
    for t in (0.0, 1.7, 1234.5):
        backgrounds.draw_background(ctx, name, object(), t, 1280, 720, colors, "grayscale(0.5)")
    assert bool(ctx.calls) == (name != "none"), (name, ctx.calls[:5])
    state = (ctx.filter, ctx.globalAlpha, ctx.globalCompositeOperation)
    assert state == ("none", 1.0, "source-over"), (name, state)

# The compositor: camera onto scratch, keep only the person, then onto the stage.
scratch = types.SimpleNamespace(width=0, height=0)
sctx, ctx = FakeCtx(), FakeCtx()
backgrounds.composite_person(ctx, scratch, sctx, object(), object(), 640, 360, "blur(3px)")
assert (scratch.width, scratch.height) == (640, 360)
assert sctx.calls == ["drawImage", "drawImage"] and ctx.calls == ["drawImage"], (sctx.calls, ctx.calls)
assert (sctx.filter, sctx.globalCompositeOperation) == ("none", "source-over")

print("render ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 py/test_render.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'backgrounds'`

- [ ] **Step 3: Implement `py/backgrounds.py`**

```python
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
```

- [ ] **Step 4: Mount it for PyScript**

In `pyscript.toml`, replace:

```toml
"./py/storage.py" = "./storage.py"
```

with:

```toml
"./py/storage.py" = "./storage.py"
"./py/backgrounds.py" = "./backgrounds.py"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 py/test_render.py && python3 py/test_storage.py 2>/dev/null | tail -1`
Expected: `render ok` then `storage ok`

- [ ] **Step 6: Commit**

```bash
git add py/backgrounds.py py/test_render.py pyscript.toml
git commit -m "feat: procedural virtual-background scenes and person compositor" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Selfie segmenter, camera switching, camera count (JS)

**Files:**
- Replace: `js/handTracker.js`

**Interfaces:**
- Produces (all on `window`):
  - `__sbSegment: boolean` — Python sets it; JS runs the segmenter only while true. JS initializes it with `??=` so a value Python set first survives.
  - `__sbMask: HTMLCanvasElement | null` — alpha = person confidence.
  - `__sbSegmenterState: "loading" | "ready" | "failed"`.
  - `__sbSetFacing(facing: "user" | "environment"): Promise<boolean>` — restores the previous camera on failure.
  - `signalbooth:ready` event detail gains `cameras: number`; the event re-fires after every camera switch.

- [ ] **Step 1: Replace `js/handTracker.js`**

```js
/**
 * handTracker.js
 *
 * This file's job is deliberately narrow: run the webcam and MediaPipe over
 * it. GestureRecognizer reads hands every frame; ImageSegmenter (the selfie
 * model) finds where the person is, but only while a virtual background is
 * on. That's the part of this app that has to be JS/WASM — there's no
 * in-browser Python equivalent fast enough for 30-60fps. Every other
 * decision (effects, backgrounds, drawing, the photobooth, localStorage)
 * happens in Python, in py/main.py, which this file hands a plain frame
 * object to once per video frame via window.__sbFrameCallback.
 *
 * The rest of the contract with Python, all on `window`:
 *   __sbSegment         Python sets true while a background is selected
 *   __sbMask            canvas whose alpha means "this pixel is you"
 *   __sbSegmenterState  "loading" | "ready" | "failed"
 *   __sbSetFacing(f)    async; "user" | "environment"; resolves true/false
 */

import {
  GestureRecognizer,
  ImageSegmenter,
  FilesetResolver,
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3";

const WASM_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm";
const MODEL_URL = "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/gesture_recognizer.task";
const SEGMENTER_URL =
  "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite";

const video = document.getElementById("camera-feed");

let gestureRecognizer = null;
let segmenter = null;
let stream = null;
let facing = "user";
let lastVideoTime = -1;
let running = false;

const maskCanvas = document.createElement("canvas");
const maskCtx = maskCanvas.getContext("2d");
let maskImage = null;

window.__sbSegment ??= false; // Python may have set this first; don't clobber it
window.__sbMask = null;
window.__sbSegmenterState = "loading";

function setBootStatus(text) {
  const el = document.getElementById("boot-status");
  if (el) el.textContent = text;
}

function showError(message) {
  document.getElementById("boot-screen")?.classList.add("hidden");
  const screen = document.getElementById("error-screen");
  const msgEl = document.getElementById("error-message");
  if (msgEl) msgEl.textContent = message;
  screen?.classList.remove("hidden");
}

async function initSegmenter(vision) {
  try {
    segmenter = await ImageSegmenter.createFromOptions(vision, {
      baseOptions: { modelAssetPath: SEGMENTER_URL, delegate: "GPU" },
      runningMode: "VIDEO",
      outputCategoryMask: false,
      outputConfidenceMasks: true,
    });
    window.__sbSegmenterState = "ready";
  } catch (err) {
    console.error("Signalbooth: background segmenter unavailable", err);
    window.__sbSegmenterState = "failed";
  }
}

async function startCamera(nextFacing) {
  // Phones can't always hold two cameras open at once, so release the old
  // stream before asking for the new one.
  stream?.getTracks().forEach((track) => track.stop());
  // Ask for a frame shaped like the screen: a phone held upright gets a
  // portrait stream, so photos aren't a thin slice of a landscape frame.
  const portrait = matchMedia("(orientation: portrait)").matches;
  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: nextFacing,
      width: { ideal: portrait ? 720 : 1280 },
      height: { ideal: portrait ? 1280 : 720 },
    },
    audio: false,
  });
  facing = nextFacing;
  video.srcObject = stream;
}

window.__sbSetFacing = async (nextFacing) => {
  const previous = facing;
  try {
    await startCamera(nextFacing);
    return true;
  } catch (err) {
    console.error("Signalbooth: camera switch failed", err);
    try {
      await startCamera(previous); // put the old camera back rather than go dark
    } catch (restoreErr) {
      console.error("Signalbooth: couldn't restore the previous camera", restoreErr);
    }
    return false;
  }
};

async function countCameras() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === "videoinput").length;
  } catch {
    return 1;
  }
}

async function init() {
  try {
    setBootStatus("loading gesture model…");
    const vision = await FilesetResolver.forVisionTasks(WASM_URL);
    initSegmenter(vision); // loads alongside; only backgrounds wait for it
    gestureRecognizer = await GestureRecognizer.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: MODEL_URL,
        delegate: "GPU",
      },
      runningMode: "VIDEO",
      numHands: 2,
    });

    // Attached before the camera starts so the first loadeddata can't be
    // missed. Fires again after every camera switch, so Python can resize.
    video.addEventListener("loadeddata", async () => {
      const cameras = await countCameras();
      window.dispatchEvent(
        new CustomEvent("signalbooth:ready", {
          detail: { width: video.videoWidth, height: video.videoHeight, cameras },
        })
      );
      if (!running) {
        running = true;
        requestAnimationFrame(predictLoop);
      }
    });

    setBootStatus("requesting camera…");
    await startCamera("user");
  } catch (err) {
    console.error("Signalbooth: camera/model init failed", err);
    let message = "Something went wrong starting the camera. Check the console for details.";
    if (location.protocol === "file:") {
      message =
        "Signalbooth needs to run from a local server, not opened directly as a file:// page. See the README for a one-line command.";
    } else if (err && err.name === "NotAllowedError") {
      message =
        "Camera permission was denied. Allow camera access in your browser's address bar, then reload the page.";
    } else if (err && err.name === "NotFoundError") {
      message = "No camera was found on this device.";
    }
    showError(message);
  }
}

function updateMask(now) {
  const result = segmenter.segmentForVideo(video, now);
  try {
    const masks = result.confidenceMasks || [];
    // selfie_segmenter has a single "person" mask; multiclass models list
    // background first. Either way the person is the last one. If a scene
    // ever shows ON you instead of behind you, this model's mask is
    // inverted: use (1 - values[i]) below.
    const mask = masks[masks.length - 1];
    if (!mask) return;
    const values = mask.getAsFloat32Array();
    if (!maskImage || maskImage.width !== mask.width || maskImage.height !== mask.height) {
      maskCanvas.width = mask.width;
      maskCanvas.height = mask.height;
      maskImage = maskCtx.createImageData(mask.width, mask.height);
    }
    const px = maskImage.data;
    for (let i = 0; i < values.length; i++) {
      px[i * 4 + 3] = values[i] * 255; // only alpha matters: Python draws it with destination-in
    }
    maskCtx.putImageData(maskImage, 0, 0);
    window.__sbMask = maskCanvas;
  } finally {
    result.close();
  }
}

function predictLoop() {
  if (!running) return;

  if (video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    const now = performance.now();

    const result = gestureRecognizer.recognizeForVideo(video, now);

    if (window.__sbSegment && segmenter) {
      try {
        updateMask(now);
      } catch (err) {
        // A segmenter that throws once (a lost GPU context, say) keeps
        // throwing. Drop it so hands and the plain camera carry on; Python
        // sees "failed" and puts the real room back.
        console.error("Signalbooth: segmenter failed, backgrounds off", err);
        segmenter = null;
        window.__sbMask = null;
        window.__sbSegmenterState = "failed";
      }
    }

    // Flatten MediaPipe's result objects into plain arrays/dicts before
    // they cross into Python — simpler and cheaper to convert on that side.
    const hands = (result.landmarks || []).map((landmarks, i) => {
      const gestureList = (result.gestures && result.gestures[i]) || [];
      const top = gestureList[0] || { categoryName: "None", score: 0 };
      const handednessList = (result.handedness && result.handedness[i]) || [];
      const handedness = handednessList[0]?.categoryName || "Unknown";
      return {
        landmarks: landmarks.map((p) => [p.x, p.y, p.z]),
        gesture: top.categoryName,
        confidence: top.score,
        handedness,
      };
    });

    window.__sbFrame = { hands, t: now };

    if (typeof window.__sbFrameCallback === "function") {
      try {
        window.__sbFrameCallback(window.__sbFrame);
      } catch (e) {
        console.error("Signalbooth: Python frame callback error", e);
      }
    }
  }

  requestAnimationFrame(predictLoop);
}

init();
```

- [ ] **Step 2: Verify it loads and the segmenter comes up (browser pane)**

Start the dev server (`preview_start` with name `signalbooth`; `.claude/launch.json` runs `python3 -m http.server 8000`). Reload the page, then run in the pane with `javascript_tool`:

```js
for (let i = 0; i < 60 && window.__sbSegmenterState === "loading"; i++) await new Promise(r => setTimeout(r, 500));
({ state: window.__sbSegmenterState, setFacing: typeof window.__sbSetFacing, segment: window.__sbSegment })
```

Expected: `{ state: "ready", setFacing: "function", segment: false }`. The pane blocks the camera, so `read_console_messages` with `onlyErrors: true` should show only `NotAllowedError: Permission denied` lines — no `SyntaxError`, no `segmenter unavailable`.

- [ ] **Step 3: Commit**

```bash
git add js/handTracker.js
git commit -m "feat(tracker): selfie segmenter mask, camera switching, camera count" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Stories camera UI

One task because the markup, styles and both Python modules reference each other's element IDs; the app doesn't run with only some of them swapped.

**Files:**
- Replace: `index.html`
- Create: `css/stories.css`
- Modify: `css/style.css` (remove the old shell/console/strip rules; script in Step 3)
- Replace: `py/photobooth.py`
- Replace: `py/main.py`

**Interfaces:**
- Consumes: `storage.load_settings()` keys `background`, `timer` (Task 2); `effects.set_safe_box(..., inset_top, inset_bottom)`, `effects.safe_rect()` (Task 3); `backgrounds.BACKGROUND_ORDER`, `BACKGROUND_META`, `draw_background`, `composite_person` (Task 4); `window.__sbSegment`, `__sbMask`, `__sbSegmenterState`, `__sbSetFacing`, ready-event `detail.cameras` (Task 5).
- Produces:
  - `photobooth.start_countdown(canvas, effect_name, mirrored, seconds, notify=print)` (coroutine)
  - `photobooth.capture(canvas, effect_name, mirrored) -> str` (toast caption)
  - `photobooth.render_gallery()`, `open_viewer(photo_id)`, `close_viewer(refocus=True) -> bool`, `share_current(notify=print)`, `delete_current()`, `confirm_then(btn, prompt, action)`, `delete(photo_id)`, `clear_all()`
  - Element IDs used across files: `stage-wrap`, `hud-top`, `hud-bottom`, `carousel`, `active-label`, `capture-btn`, `gallery-btn`, `last-shot-img`, `strip-count`, `flip-btn`, `timer-btn`, `timer-value`, `mirror-btn`, `theme-btn`, `settings-btn`, `help-btn`, `mode-effects`, `mode-backgrounds`, `gallery-grid`, `gallery-meta`, `gallery-clear`, `gallery-close`, `viewer`, `viewer-back`, `viewer-meta`, `viewer-img`, `viewer-share`, `viewer-save`, `viewer-delete`.

- [ ] **Step 1: Replace `index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<meta name="theme-color" content="#15111a" />
<title>Signalbooth — Gesture VFX Camera</title>
<meta name="description" content="A gesture-controlled visual effects camera and photobooth. Runs entirely in your browser — no server, no database." />

<!-- PyScript: runs the Python application layer in-browser -->
<link rel="stylesheet" href="https://pyscript.net/releases/2026.7.2/core.css" />
<script type="module" src="https://pyscript.net/releases/2026.7.2/core.js"></script>

<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,450;0,9..144,600;0,9..144,700;1,9..144,500&family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap"
  rel="stylesheet"
/>

<link rel="stylesheet" href="css/style.css" />
<link rel="stylesheet" href="css/stories.css" />
</head>
<body>

  <!-- Boot / power-on sequence -->
  <section id="boot-screen" class="boot-screen" aria-label="Starting up">
    <div class="boot-dial" aria-hidden="true">
      <svg viewBox="0 0 120 120">
        <circle class="boot-dial__track" cx="60" cy="60" r="52" />
        <circle class="boot-dial__sweep" cx="60" cy="60" r="52" />
      </svg>
    </div>
    <p class="boot-word">SIGNAL<span>BOOTH</span></p>
    <p class="boot-status" id="boot-status" role="status">warming up…</p>
  </section>

  <!-- Camera / permission error -->
  <section id="error-screen" class="error-screen hidden" aria-labelledby="error-title">
    <p class="error-title" id="error-title">Camera unavailable</p>
    <p class="error-message" id="error-message"></p>
  </section>

  <!-- Onboarding: gesture legend -->
  <div id="onboarding" class="onboarding hidden" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
    <div class="onboarding-card">
      <p class="onboarding-eyebrow">How to play it</p>
      <h1 class="onboarding-title" id="onboarding-title">Signalbooth reads your hand<br />like an instrument.</h1>
      <ul class="gesture-legend">
        <li><span class="gesture-legend__icon">✌️</span><div><strong>Peace sign</strong><span>Soft focus — the lens blurs</span></div></li>
        <li><span class="gesture-legend__icon">👍</span><div><strong>Thumbs up</strong><span>Hype — likes, confetti, combo</span></div></li>
        <li><span class="gesture-legend__icon">👌</span><div><strong>OK sign</strong><span>“its im fine, gwenchana!”</span></div></li>
        <li><span class="gesture-legend__icon">🫶</span><div><strong>Heart hands</strong><span>I love u + heart rain</span></div></li>
        <li><span class="gesture-legend__icon">🫰</span><div><strong>Finger heart</strong><span>Love beam</span></div></li>
        <li><span class="gesture-legend__icon">👎</span><div><strong>Thumbs down</strong><span>Rain mood</span></div></li>
        <li><span class="gesture-legend__icon">🤟</span><div><strong>I-love-you sign</strong><span>Star shower</span></div></li>
        <li><span class="gesture-legend__icon">✋</span><div><strong>Open palm</strong><span>Aura glow</span></div></li>
        <li><span class="gesture-legend__icon">✊</span><div><strong>Closed fist</strong><span>Spark burst</span></div></li>
        <li><span class="gesture-legend__icon">☝️</span><div><strong>Point up</strong><span>Laser trail</span></div></li>
        <li><span class="gesture-legend__icon">🙌</span><div><strong>Both palms</strong><span>Portal between hands</span></div></li>
        <li><span class="gesture-legend__icon">📸</span><div><strong>Hold ✋ still</strong><span>Fires the shutter</span></div></li>
      </ul>
      <p class="onboarding-note">Swipe the dial around the shutter for effects &amp; backgrounds. Keys: <kbd>space</kbd> shoot · <kbd>T</kbd> theme · <kbd>M</kbd> mirror · <kbd>1</kbd>–<kbd>8</kbd> effects · <kbd>esc</kbd> close</p>
      <button id="onboarding-dismiss" class="btn-brass">Got it — start</button>
    </div>
  </div>

  <main class="stage-wrap" id="stage-wrap">
    <div class="viewport-bezel">
      <video id="camera-feed" autoplay playsinline muted></video>
      <canvas id="stage"></canvas>

      <header class="hud-top" id="hud-top">
        <p class="wordmark">Signal<span>booth</span></p>
        <div class="readout" id="readout">
          <svg class="readout-dial" viewBox="0 0 64 64" aria-hidden="true">
            <circle class="readout-dial__ring" cx="32" cy="32" r="27" />
            <circle class="readout-dial__pulse" cx="32" cy="32" r="27" />
          </svg>
          <span class="readout-label" id="gesture-label">NO SIGNAL</span>
        </div>
        <button id="help-btn" class="glass-btn" aria-label="Show gesture guide">?</button>
      </header>

      <div class="tool-rail" role="group" aria-label="Camera tools">
        <button id="timer-btn" class="glass-btn" aria-label="Self-timer, 3 seconds">
          <span aria-hidden="true">⏱</span>
          <span class="glass-btn__value" id="timer-value" aria-hidden="true">3s</span>
        </button>
        <button id="mirror-btn" class="glass-btn" aria-label="Mirror view" aria-pressed="true">
          <span aria-hidden="true">⇋</span>
        </button>
        <button id="theme-btn" class="glass-btn icon-btn--theme" data-theme="neon" aria-label="Cycle color theme">
          <span class="theme-swatch" aria-hidden="true"></span>
        </button>
        <button id="settings-btn" class="glass-btn" aria-label="Settings">
          <span aria-hidden="true">⚙</span>
        </button>
      </div>

      <div class="countdown hidden" id="countdown"></div>
      <div class="flash" id="flash"></div>
      <p class="toast" id="toast" role="status" aria-live="polite"></p>

      <div class="hud-bottom" id="hud-bottom">
        <p class="active-label" id="active-label">Idle</p>
        <div class="dial-row">
          <button id="gallery-btn" class="last-shot" aria-label="Open your photo strip">
            <img id="last-shot-img" class="hidden" alt="" />
            <span class="count-badge" id="strip-count" aria-hidden="true"></span>
          </button>
          <div class="dial">
            <div class="carousel" id="carousel" role="group" aria-label="Effects"></div>
            <button id="capture-btn" class="shutter" aria-label="Take photo">
              <span class="shutter__ring" aria-hidden="true"></span>
            </button>
          </div>
          <button id="flip-btn" class="glass-btn flip-btn invisible" aria-label="Switch to back camera">
            <span aria-hidden="true">⟲</span>
          </button>
        </div>
        <div class="mode-switch" role="group" aria-label="Dial shows">
          <button id="mode-effects" class="mode-btn" aria-pressed="true">Effects</button>
          <button id="mode-backgrounds" class="mode-btn" aria-pressed="false">Backgrounds</button>
        </div>
      </div>
    </div>
  </main>

  <div id="scrim" class="scrim hidden"></div>

  <div id="gallery-drawer" class="drawer hidden" role="dialog" aria-modal="true" aria-labelledby="gallery-title">
    <div class="drawer-head">
      <div>
        <h2 id="gallery-title">Your strip</h2>
        <p class="drawer-meta" id="gallery-meta"></p>
      </div>
      <button id="gallery-close" class="icon-btn" aria-label="Close strip">×</button>
    </div>
    <div class="drawer-body drawer-body--grid" id="gallery-grid"></div>
    <button id="gallery-clear" class="text-btn">Clear all</button>

    <section id="viewer" class="viewer hidden" aria-label="Photo viewer">
      <div class="viewer-head">
        <button id="viewer-back" class="icon-btn" aria-label="Back to strip">‹</button>
        <p class="viewer-meta" id="viewer-meta"></p>
      </div>
      <img id="viewer-img" class="viewer-img" alt="" />
      <div class="viewer-actions">
        <button id="viewer-share" class="btn-brass viewer-share hidden">Share</button>
        <a id="viewer-save" class="viewer-action" href="#" download>Save</a>
        <button id="viewer-delete" class="viewer-action viewer-action--danger">Delete</button>
      </div>
    </section>
  </div>

  <div id="settings-drawer" class="drawer hidden" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <div class="drawer-head">
      <h2 id="settings-title">Settings</h2>
      <button id="settings-close" class="icon-btn" aria-label="Close settings">×</button>
    </div>
    <div class="drawer-body">
      <label class="toggle-row">
        <span>Show hand skeleton</span>
        <input type="checkbox" id="skeleton-toggle" />
      </label>
      <label class="toggle-row">
        <span>Hold ✋ to shoot</span>
        <input type="checkbox" id="palmhold-toggle" checked />
      </label>
      <label class="slider-row">
        <span class="slider-row__head">Effect intensity <output id="intensity-value" for="intensity-slider">1.0×</output></span>
        <input type="range" id="intensity-slider" min="0.4" max="1.6" step="0.1" value="1" />
      </label>
    </div>
  </div>

  <!-- JS layer: owns the camera + the ML models only -->
  <script type="module" src="js/handTracker.js"></script>

  <!-- Python layer: owns gestures, VFX, backgrounds, DOM, photobooth, localStorage -->
  <script type="py" src="py/main.py" config="pyscript.toml"></script>

</body>
</html>
```

- [ ] **Step 2: Create `css/stories.css`**

```css
/* =========================================================================
   Signalbooth — Stories camera layer
   Everything specific to the camera screen: the full-bleed stage, the
   frosted-glass HUD over it, the dial (effect / background carousel around
   the shutter), bottom sheets, the strip grid and the photo viewer.
   Design tokens live in style.css; these are the camera-only additions.
   ========================================================================= */

:root {
  --glass: rgba(21, 17, 26, 0.42);
  --glass-line: rgba(236, 230, 242, 0.16);
  --glow-brass: 0 0 22px rgba(232, 163, 61, 0.45);
  --spring: cubic-bezier(0.34, 1.56, 0.64, 1);
  --signal: conic-gradient(from 0deg, var(--accent-brass), var(--accent-violet), #e8636b, var(--accent-brass));
  --dial-item: 56px;
  --shutter: 82px;
  --edge-l: max(14px, env(safe-area-inset-left));
  --edge-r: max(14px, env(safe-area-inset-right));
  --edge-t: max(12px, env(safe-area-inset-top));
  --edge-b: max(14px, env(safe-area-inset-bottom));
}

/* Ambient glow behind the stage, seen around the desktop frame. The body
   goes transparent so this layer (z-index -1) isn't painted over; the html
   element keeps the base color. */
body { background: transparent; }

body::before {
  content: "";
  position: fixed;
  inset: -20%;
  z-index: -1;
  background:
    radial-gradient(40% 35% at 25% 30%, rgba(232, 163, 61, 0.16), transparent 70%),
    radial-gradient(45% 40% at 75% 70%, rgba(139, 127, 240, 0.2), transparent 70%);
  animation: ambient-drift 24s ease-in-out infinite alternate;
}

@keyframes ambient-drift {
  to { transform: translate(4%, -3%) rotate(8deg); }
}

/* ---- stage ------------------------------------------------------------ */

.stage-wrap {
  height: 100dvh;
  display: grid;
  place-items: center;
}

.viewport-bezel {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #000;
}

#camera-feed { display: none; }

#stage {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1); /* mirrored by default; main.py sets the real value */
}

/* Vignettes keep the HUD readable over a bright room. */
.viewport-bezel::before,
.viewport-bezel::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  z-index: 2;
  pointer-events: none;
}
.viewport-bezel::before { top: 0; height: 120px; background: linear-gradient(rgba(10, 8, 13, 0.55), transparent); }
.viewport-bezel::after { bottom: 0; height: 260px; background: linear-gradient(transparent, rgba(10, 8, 13, 0.72)); }

/* Desktop: a floating frame in the camera's own shape (main.py sets
   --cam-w / --cam-h), so nothing is cropped and photos keep full res. */
@media (min-width: 768px) {
  .stage-wrap { padding: 24px; }

  .viewport-bezel {
    width: min(100%, calc((100dvh - 48px) * var(--cam-w, 16) / var(--cam-h, 9)));
    height: auto;
    aspect-ratio: var(--cam-w, 16) / var(--cam-h, 9);
    border-radius: var(--radius-lg);
    border: 1px solid var(--bg-panel-line);
    box-shadow: var(--shadow-panel), 0 0 80px rgba(139, 127, 240, 0.14);
  }
}

/* ---- top bar + tool rail --------------------------------------------- */

.hud-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--edge-t) var(--edge-r) 0 var(--edge-l);
}

.wordmark {
  margin: 0 auto 0 0;
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.01em;
  white-space: nowrap;
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.5);
}
.wordmark span { color: var(--accent-brass); }

.hud-top .readout { min-width: 0; }
.readout-label { overflow: hidden; text-overflow: ellipsis; }

.glass-btn {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--glass);
  border: 1px solid var(--glass-line);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
  backdrop-filter: blur(14px) saturate(140%);
  color: var(--paper);
  font-size: 17px;
  line-height: 1;
  transition: transform 0.25s var(--spring), border-color 0.15s var(--ease), box-shadow 0.15s var(--ease);
}
.glass-btn:hover { border-color: rgba(232, 163, 61, 0.5); }
.glass-btn:active { transform: scale(0.9); }
.glass-btn[aria-pressed="true"] { border-color: var(--accent-brass); box-shadow: var(--glow-brass); }

.glass-btn__value {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.04em;
  color: var(--accent-brass);
}

.invisible { visibility: hidden; }

.tool-rail {
  position: absolute;
  top: calc(var(--edge-t) + 60px);
  right: var(--edge-r);
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ---- bottom HUD: label, dial, mode switch ----------------------------- */

.hud-bottom {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 0 var(--edge-r) var(--edge-b) var(--edge-l);
}

.active-label {
  margin: 0;
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 600;
  font-size: 20px;
  color: var(--paper);
  text-shadow: 0 0 18px rgba(232, 163, 61, 0.55), 0 2px 10px rgba(0, 0, 0, 0.6);
}

.dial-row {
  width: 100%;
  max-width: 560px;
  display: grid;
  grid-template-columns: 48px 1fr 48px;
  align-items: center;
  gap: 8px;
}

.dial {
  position: relative;
  min-width: 0;
  height: var(--shutter);
  display: flex;
  align-items: center;
}

/* The carousel: native scroll-snap, padded so the first and last items can
   reach the middle. Edges fade out so it reads as a wheel, not a list. */
.carousel {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 16px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x mandatory;
  scroll-behavior: smooth;
  scrollbar-width: none;
  padding: 12px calc(50% - var(--dial-item) / 2);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 22%, #000 78%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 22%, #000 78%, transparent);
}
.carousel::-webkit-scrollbar { display: none; }

.dial-item {
  flex: 0 0 var(--dial-item);
  height: var(--dial-item);
  display: grid;
  place-items: center;
  border-radius: 50%;
  scroll-snap-align: center;
  font-size: 26px;
  background: var(--glass);
  border: 1px solid var(--glass-line);
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
  transition: transform 0.25s var(--spring), opacity 0.2s var(--ease);
}
.dial-item[aria-pressed="false"] { transform: scale(0.82); opacity: 0.85; }

/* The shutter sits over the middle of the dial. Its ring is a brass→violet
   conic gradient punched into a ring by a mask, so the active item shows
   through the middle — tap it to shoot. */
.shutter {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 1;
  width: var(--shutter);
  height: var(--shutter);
  border-radius: 50%;
  transform: translate(-50%, -50%);
  box-shadow: 0 0 26px rgba(232, 163, 61, 0.35);
  transition: transform 0.2s var(--spring);
}
.shutter:active { transform: translate(-50%, -50%) scale(0.92); }

.shutter__ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--signal);
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
  mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
}
.shutter--busy .shutter__ring { animation: ring-spin 1s linear infinite; }

@keyframes ring-spin {
  to { transform: rotate(1turn); }
}

.last-shot {
  position: relative;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  border: 2px solid var(--paper);
  background: var(--glass);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
  transition: transform 0.25s var(--spring);
}
.last-shot:active { transform: scale(0.9); }
.last-shot img { display: block; width: 100%; height: 100%; object-fit: cover; border-radius: 10px; }
.last-shot .count-badge { position: absolute; top: -8px; right: -8px; }
.last-shot--pop { animation: shot-pop 0.5s var(--spring); }

@keyframes shot-pop {
  from { transform: scale(0.6) rotate(-8deg); }
  to { transform: scale(1) rotate(0); }
}

.flip-btn { justify-self: end; }

.mode-switch { display: flex; gap: 4px; }

.mode-btn {
  position: relative;
  min-height: 44px;
  padding: 0 14px;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: rgba(236, 230, 242, 0.62);
  transition: color 0.15s var(--ease);
}
.mode-btn[aria-pressed="true"] { color: var(--paper); }
.mode-btn[aria-pressed="true"]::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: 6px;
  width: 4px;
  height: 4px;
  margin-left: -2px;
  border-radius: 50%;
  background: var(--accent-brass);
  box-shadow: var(--glow-brass);
}

/* ---- countdown + toast, restyled for the camera ----------------------- */

.countdown {
  font-size: 150px;
  color: transparent;
  background: var(--signal);
  -webkit-background-clip: text;
  background-clip: text;
  text-shadow: none;
  filter: drop-shadow(0 6px 24px rgba(0, 0, 0, 0.5));
}

.toast { bottom: calc(var(--edge-b) + 176px); }

/* ---- bottom sheets (phones) ------------------------------------------ */

.drawer-body { min-height: 0; }

@media (max-width: 767px) {
  .drawer {
    top: auto;
    left: 0;
    width: 100%;
    max-height: 86dvh;
    border-left: none;
    border-top: 1px solid var(--bg-panel-line);
    border-radius: 22px 22px 0 0;
    padding-bottom: env(safe-area-inset-bottom);
    animation: sheet-in 0.34s var(--spring);
  }

  .drawer::before {
    content: "";
    display: block;
    width: 40px;
    height: 4px;
    margin: 10px auto 0;
    border-radius: 2px;
    background: var(--bg-panel-line);
  }
}

@keyframes sheet-in {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}

/* ---- strip grid + viewer ---------------------------------------------- */

.drawer-body--grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 3px;
  padding: 3px;
  align-content: start;
}

.shot-tile {
  aspect-ratio: 1;
  padding: 0;
  overflow: hidden;
  background: var(--bg-panel-raised);
}
.shot-tile img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.25s var(--ease);
}
.shot-tile:hover img { transform: scale(1.04); }

.gallery-empty {
  grid-column: 1 / -1;
  padding: 40px 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
  color: var(--ink-muted);
}

.viewer {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border-radius: inherit;
}

.viewer-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--bg-panel-line);
}

.viewer-meta {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-muted);
}

.viewer-img {
  flex: 1;
  min-height: 0;
  width: 100%;
  object-fit: contain;
  background: #000;
}

.viewer-actions {
  display: flex;
  gap: 10px;
  padding: 14px 18px 18px;
}

.viewer-share { width: auto; flex: 1; }

.viewer-action {
  flex: 1;
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-panel-line);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-decoration: none;
  color: var(--ink);
}
.viewer-action--danger { color: var(--danger); }
.viewer-action--danger.is-armed { background: var(--danger); border-color: var(--danger); color: var(--bg-void); }

/* ---- short screens (a phone on its side, a small window) -------------- */
/* The rail lies down in the top bar instead of running into the dial. */

@media (max-height: 560px) {
  .tool-rail {
    top: var(--edge-t);
    right: calc(var(--edge-r) + 54px);
    flex-direction: row;
    gap: 8px;
  }
  .hud-top .wordmark { margin-right: 0; }
  .hud-top .readout { margin-right: auto; }
  .active-label { display: none; }
}

/* ---- motion ------------------------------------------------------------ */

@supports (animation-timeline: view()) {
  /* Where animation can follow scroll position, items grow as they near
     the shutter — the Stories-dial zoom, with no JS per frame. Never below
     0.8 so every item keeps a 44px tap target. */
  .dial-item {
    animation: dial-focus linear both;
    animation-timeline: view(inline);
  }

  @keyframes dial-focus {
    0%, 100% { transform: scale(0.8); opacity: 0.6; }
    50% { transform: scale(1); opacity: 1; }
  }
}

@media (prefers-reduced-motion: reduce) {
  body::before,
  .dial-item,
  .shutter--busy .shutter__ring,
  .last-shot--pop,
  .drawer {
    animation: none !important;
  }

  .carousel { scroll-behavior: auto; }
}
```

- [ ] **Step 3: Trim `css/style.css`**

Run this script from the repo root. Each `assert` fails loudly if the file isn't in the expected state.

```bash
python3 - <<'EOF'
p = "css/style.css"
s = open(p).read()

def banner(title):
    """Start of the '/* ====' comment line whose title line is `title`."""
    k = s.index("\n   " + title)
    return s.rindex("/*", 0, k)

def cut(start, stop):
    global s
    assert start < stop, (start, stop)
    s = s[:start] + s[stop:]

def drop(text):
    global s
    assert s.count(text) == 1, text[:60]
    s = s.replace(text, "")

# App shell + Viewport bezel: replaced by the stage rules in stories.css.
cut(banner("App shell"), banner("Readout"))
# Console: the old chip dock and capture button are gone.
cut(banner("Console — the control dock"), banner("Drawers (gallery / settings)"))
# Paper-card strip: replaced by the grid + viewer in stories.css.
cut(s.index("/* Photo strip gallery */"), banner("Responsive"))
# Phone console row and old capture sizing.
cut(s.index("/* Phone width: the chip dock"), s.index("@media (max-width: 420px)"))
drop("  .capture-btn { width: 56px; height: 56px; }\n")
drop("  .capture-btn--busy .capture-btn__ring,\n")
drop("\n  .console-group { scroll-behavior: auto; }\n")

# The readout now sits inside the top bar instead of floating over the stage.
old = "  position: absolute;\n  top: 14px;\n  left: 14px;\n  display: flex;\n  align-items: center;\n  gap: 8px;"
assert s.count(old) == 1
s = s.replace(old, "  display: flex;\n  align-items: center;\n  gap: 8px;")
old = "  border-radius: 999px;\n  z-index: 5;\n}"
assert s.count(old) == 1
s = s.replace(old, "  border-radius: 999px;\n}")

for gone in (".console", ".effect-chip", ".capture-btn", ".shot-card", ".app-header", ".viewport-bezel"):
    assert gone not in s, gone
open(p, "w").write(s)
print("style.css trimmed")
EOF
```

Expected: `style.css trimmed`

- [ ] **Step 4: Replace `py/photobooth.py`**

```python
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
```

- [ ] **Step 5: Replace `py/main.py`**

```python
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
    document.getElementById("mirror-btn").setAttribute(
        "aria-pressed", "true" if settings["mirror"] else "false"
    )


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
        if active and scroll:
            item.scrollIntoView(to_js(_CENTER, dict_converter=window.Object.fromEntries))


def choose(key, scroll=True):
    if state["mode"] == "effects":
        set_effect(key, scroll=scroll)
    else:
        set_background(key, scroll=scroll)


_settle_token = [0]


async def _settle(token):
    # A debounced scroll end (`scrollend` isn't in every mobile browser yet):
    # once the dial has held still for a beat, the item under the shutter wins.
    await asyncio.sleep(0.14)
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
    asyncio.ensure_future(
        photobooth.start_countdown(canvas, state["effect"], is_mirrored(), state["timer"], notify=toast)
    )


def toggle_mirror():
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


@when("click", "#carousel")
def on_dial_click(evt):
    item = evt.target.closest(".dial-item")
    if item:
        choose(item.getAttribute("data-key"))


@when("scroll", "#carousel")
def on_dial_scroll(evt):
    _settle_token[0] += 1
    asyncio.ensure_future(_settle(_settle_token[0]))


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
```

- [ ] **Step 6: Static checks**

Run: `python3 -c "import ast; [ast.parse(open(f).read(), f) for f in ('py/main.py', 'py/photobooth.py', 'py/backgrounds.py')]" && python3 py/test_render.py && python3 py/test_storage.py 2>/dev/null | tail -1`
Expected: `render ok` then `storage ok`

- [ ] **Step 7: Layout check at phone size (browser pane)**

`resize_window` preset `mobile` (375×812). Seed a clean state and reload:

```js
localStorage.clear(); localStorage.setItem('signalbooth:onboarded', '1'); location.reload(); 'reloading'
```

Then run the harness (repeat it after every reload in this task):

```js
for (let i = 0; i < 60 && !document.querySelector('.dial-item'); i++) await new Promise(r => setTimeout(r, 500));
await document.fonts.ready;
document.getElementById('boot-screen').classList.add('hidden');
document.getElementById('error-screen').classList.add('hidden');
document.getElementById('carousel').style.scrollBehavior = 'auto'; // a hidden pane freezes smooth scrolling
window.dispatchEvent(new CustomEvent('signalbooth:ready', { detail: { width: 1280, height: 720, cameras: 2 } }));
window.__sbFrameCallback({ hands: [], t: performance.now() });
await new Promise(r => setTimeout(r, 300));
'ready'
```

Then:

```js
const vw = innerWidth, sh = document.getElementById('capture-btn').getBoundingClientRect();
const items = [...document.querySelectorAll('.dial-item')].map(b => b.getBoundingClientRect().height);
const rail = [...document.querySelectorAll('.tool-rail .glass-btn')].map(b => b.getBoundingClientRect().width);
({
  hScroll: document.documentElement.scrollWidth > vw,
  shutterCentered: Math.abs(sh.left + sh.width / 2 - vw / 2) <= 2,
  minDialItem: Math.round(Math.min(...items)),
  minRailBtn: Math.round(Math.min(...rail)),
  flip: getComputedStyle(document.getElementById('flip-btn')).visibility,
  label: document.getElementById('active-label').innerText,
})
```

Expected: `{ hScroll: false, shutterCentered: true, minDialItem: ≥44, minRailBtn: 44, flip: "visible", label: "Idle" }`. Take a screenshot and check by eye: camera area fills the screen, glass rail on the right, dial with ring shutter at the bottom.

- [ ] **Step 8: Dial settles on what's under the shutter (Review Focus 2)**

```js
const c = document.getElementById('carousel');
const target = c.querySelector('[data-key="spark"]');
const cr = c.getBoundingClientRect(), tr = target.getBoundingClientRect();
c.scrollLeft += (tr.left + tr.width / 2) - (cr.left + cr.width / 2);
await new Promise(r => setTimeout(r, 500));
({
  label: document.getElementById('active-label').innerText,
  saved: JSON.parse(localStorage.getItem('signalbooth:settings')).last_effect,
  pressed: c.querySelector('[aria-pressed="true"]').dataset.key,
})
```

Expected: `{ label: "Spark Burst", saved: "spark", pressed: "spark" }`

- [ ] **Step 9: Backgrounds mode and tap-to-pick**

```js
document.getElementById('mode-backgrounds').click();
await new Promise(r => setTimeout(r, 300));
const c = document.getElementById('carousel');
const keys = [...c.querySelectorAll('.dial-item')].map(b => b.dataset.key).join(',');
c.querySelector('[data-key="neon"]').click();
await new Promise(r => setTimeout(r, 500));
({
  keys,
  label: document.getElementById('active-label').innerText,
  saved: JSON.parse(localStorage.getItem('signalbooth:settings')).background,
  segment: window.__sbSegment,
})
```

Expected: `{ keys: "none,blur,neon,sunset,aurora", label: "Neon City", saved: "neon", segment: true }`

- [ ] **Step 10: Segmenter failure falls back to the room (Review Focus 3)**

```js
window.__sbSegmenterState = 'failed';
window.__sbFrameCallback({ hands: [], t: performance.now() }); // neon is on: this frame must give it up
await new Promise(r => setTimeout(r, 300));
const mid = {
  label: document.getElementById('active-label').innerText,
  toast: document.getElementById('toast').innerText,
  segment: window.__sbSegment,
};
document.getElementById('carousel').querySelector('[data-key="sunset"]').click();
await new Promise(r => setTimeout(r, 500));
const result = {
  mid,
  afterPick: document.getElementById('active-label').innerText,
  saved: JSON.parse(localStorage.getItem('signalbooth:settings')).background,
};
window.__sbSegmenterState = 'ready';
result
```

Expected: `mid.label` `"Room"`, `mid.toast` matches `/backgrounds unavailable here/i`, `mid.segment` `false`; `afterPick` `"Room"`; `saved` `"none"`.

- [ ] **Step 11: Timer cycle, and a portrait capture on a portrait screen (Review Focus 1)**

```js
const b = document.getElementById('timer-btn');
b.click(); const one = document.getElementById('timer-value').innerText;
b.click(); const two = document.getElementById('timer-value').innerText;
window.__sbFrameCallback({ hands: [], t: performance.now() });
document.getElementById('capture-btn').click();
await new Promise(r => setTimeout(r, 800));
const shots = JSON.parse(localStorage.getItem('signalbooth:gallery'));
const img = new Image(); img.src = shots[shots.length - 1].dataUrl; await img.decode();
({
  one, two,
  timerLabel: b.getAttribute('aria-label'),
  savedTimer: JSON.parse(localStorage.getItem('signalbooth:settings')).timer,
  w: img.naturalWidth, h: img.naturalHeight,
  toast: document.getElementById('toast').innerText,
  thumb: document.getElementById('last-shot-img').src === img.src,
})
```

Expected: `one` `"10s"`, `two` `"off"`, `timerLabel` `"Self-timer off"`, `savedTimer` `0`, `w < h` (≈ 333 × 720), `toast` matches `/saved · 1 in strip/i`, `thumb` `true`.

- [ ] **Step 12: Camera switch failure and success (Review Focus 4)**

```js
window.__sbSetFacing = async () => false;
document.getElementById('flip-btn').click();
await new Promise(r => setTimeout(r, 300));
const failed = { toast: document.getElementById('toast').innerText, transform: document.getElementById('stage').style.transform };
window.__sbSetFacing = async () => true;
document.getElementById('flip-btn').click();
await new Promise(r => setTimeout(r, 300));
const back = { transform: document.getElementById('stage').style.transform, label: document.getElementById('flip-btn').getAttribute('aria-label') };
document.getElementById('flip-btn').click();
await new Promise(r => setTimeout(r, 300));
({ failed, back, front: document.getElementById('stage').style.transform })
```

Expected: `failed.toast` matches `/couldn't switch camera/i`, `failed.transform` `"scaleX(-1)"`; `back` `{ transform: "scaleX(1)", label: "Switch to front camera" }`; `front` `"scaleX(-1)"`.

- [ ] **Step 13: Strip grid, viewer, no-share fallback, delete (Review Focus 5)**

```js
Object.defineProperty(Navigator.prototype, 'canShare', { value: undefined, configurable: true });
window.__sbFrameCallback({ hands: [], t: performance.now() });
document.getElementById('capture-btn').click(); // a second shot, so one survives the delete
await new Promise(r => setTimeout(r, 800));
document.getElementById('gallery-btn').click();
await new Promise(r => setTimeout(r, 300));
const before = document.querySelectorAll('.shot-tile').length;
document.querySelector('.shot-tile').click();
await new Promise(r => setTimeout(r, 200));
const opened = {
  viewer: !document.getElementById('viewer').classList.contains('hidden'),
  focus: document.activeElement.id,
  shareHidden: document.getElementById('viewer-share').classList.contains('hidden'),
  save: document.getElementById('viewer-save').href.startsWith('data:image/jpeg'),
};
document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
await new Promise(r => setTimeout(r, 200));
const escaped = {
  viewer: !document.getElementById('viewer').classList.contains('hidden'),
  drawer: !document.getElementById('gallery-drawer').classList.contains('hidden'),
  onTile: document.activeElement.classList.contains('shot-tile'),
};
document.querySelector('.shot-tile').click();
await new Promise(r => setTimeout(r, 200));
const del = document.getElementById('viewer-delete');
del.click(); const armed = del.textContent; del.click();
await new Promise(r => setTimeout(r, 300));
({
  before, opened, escaped, armed,
  after: document.querySelectorAll('.shot-tile').length,
  viewerClosed: document.getElementById('viewer').classList.contains('hidden'),
  focus: document.activeElement.id,
})
```

Expected: `before` `2`; `opened` `{ viewer: true, focus: "viewer-back", shareHidden: true, save: true }`; `escaped` `{ viewer: false, drawer: true, onTile: true }`; `armed` `"Sure?"`; `after` `1`; `viewerClosed` `true`; `focus` `"gallery-close"`. Take a screenshot with the strip open: a bottom sheet with a 3-column grid. Then reload the page (removes the `canShare` override).

- [ ] **Step 14: Stale settings are sanitized**

```js
localStorage.setItem('signalbooth:settings', JSON.stringify({ last_effect: 'spark', background: 'beach', timer: 7 }));
location.reload(); 'reloading'
```

Run the harness from Step 7, then:

```js
const effectLabel = document.getElementById('active-label').innerText;
document.getElementById('mode-backgrounds').click();
await new Promise(r => setTimeout(r, 300));
({ effectLabel, background: document.getElementById('active-label').innerText, timer: document.getElementById('timer-value').innerText })
```

Expected: `{ effectLabel: "Spark Burst", background: "Room", timer: "3s" }`

- [ ] **Step 15: Desktop frame keeps the full camera frame**

`resize_window` preset `desktop`, reload, run the harness from Step 7, then:

```js
const b = document.querySelector('.viewport-bezel').getBoundingClientRect();
document.getElementById('timer-btn').click(); document.getElementById('timer-btn').click(); // 3s → 10s → off
window.__sbFrameCallback({ hands: [], t: performance.now() });
document.getElementById('capture-btn').click();
await new Promise(r => setTimeout(r, 800));
const shots = JSON.parse(localStorage.getItem('signalbooth:gallery'));
const img = new Image(); img.src = shots[shots.length - 1].dataUrl; await img.decode();
({ ratio: +(b.width / b.height).toFixed(2), w: img.naturalWidth, h: img.naturalHeight })
```

Expected: `{ ratio: 1.78, w: 1280, h: 720 }` (requires the pane to be ≥768px wide; if `innerWidth < 768`, widen the pane first).

- [ ] **Step 16: Short landscape screen keeps the rail clear of the dial**

`resize_window` width `812`, height `375`, reload, run the harness from Step 7, then:

```js
const rail = document.querySelector('.tool-rail').getBoundingClientRect();
const dial = document.getElementById('hud-bottom').getBoundingClientRect();
const readout = document.getElementById('readout').getBoundingClientRect();
({ clearOfDial: rail.bottom <= dial.top, clearOfReadout: rail.left >= readout.right, hScroll: document.documentElement.scrollWidth > innerWidth })
```

Expected: `{ clearOfDial: true, clearOfReadout: true, hScroll: false }`. Reset with `resize_window` preset `desktop`.

- [ ] **Step 17: Console is clean**

`read_console_messages` with `onlyErrors: true`.
Expected: only `Signalbooth: camera/model init failed NotAllowedError` lines (the pane blocks the camera) — no Python tracebacks, no `segmenter unavailable`.

- [ ] **Step 18: Commit**

```bash
git add index.html css/stories.css css/style.css py/main.py py/photobooth.py
git commit -m "feat: Stories camera UI — dial, tool rail, backgrounds, timer, flip, strip viewer" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, accessibility audit, on-device check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README**

```bash
python3 - <<'EOF'
p = "README.md"
s = open(p).read()

def rep(old, new):
    global s
    assert s.count(old) == 1, old[:70]
    s = s.replace(old, new)

rep(
    "| `js/handTracker.js` | JavaScript | Owns the webcam and MediaPipe's `GestureRecognizer` model. Nothing else. |",
    "| `js/handTracker.js` | JavaScript | Owns the webcam and MediaPipe's models: `GestureRecognizer` for hands, and `ImageSegmenter` (selfie) for the person mask while a virtual background is on. Nothing else. |",
)
rep(
    "Gesture interpretation, the VFX render loop, all DOM manipulation, the photobooth flow, and `localStorage`. |",
    "Gesture interpretation, the VFX render loop, virtual-background compositing, all DOM manipulation, the photobooth flow, and `localStorage`. |",
)
rep(
    """├── index.html            page shell: viewport, console, drawers, overlays
├── pyscript.toml         mounts py/*.py onto PyScript's virtual filesystem
├── css/
│   └── style.css         design system (see "Design" below)
├── js/
│   └── handTracker.js    webcam + MediaPipe GestureRecognizer only
└── py/
    ├── main.py           entry point — wiring, event handlers, render loop
    ├── gestures.py        landmark geometry, debouncing, custom detectors
    ├── effects.py          the VFX: every effect and moment, plus the safe box
    ├── photobooth.py       countdown, capture, gallery DOM
    ├── storage.py           localStorage read/write for settings + gallery
    └── test_storage.py      self-check for storage.py: `python3 py/test_storage.py`""",
    """├── index.html            page shell: camera HUD, dial, drawers, overlays
├── pyscript.toml         mounts py/*.py onto PyScript's virtual filesystem
├── css/
│   ├── style.css         design system (see "Design" below)
│   └── stories.css       the camera screen: stage, HUD, dial, sheets, viewer
├── js/
│   └── handTracker.js    webcam + MediaPipe GestureRecognizer + selfie segmenter
└── py/
    ├── main.py           entry point — wiring, event handlers, render loop, dial
    ├── gestures.py        landmark geometry, debouncing, custom detectors
    ├── effects.py          the VFX: every effect and moment, plus the safe box
    ├── backgrounds.py      virtual-background scenes + person compositing
    ├── photobooth.py       timer, capture, strip grid, viewer + share
    ├── storage.py           localStorage read/write for settings + gallery
    ├── test_storage.py      self-check: `python3 py/test_storage.py`
    └── test_render.py       self-check: `python3 py/test_render.py`""",
)
rep(
    """| The shutter button | Same countdown, no gesture needed |
| <kbd>space</kbd> | Same again |""",
    """| The shutter (the ring in the middle of the dial) | Same, no gesture needed |
| <kbd>space</kbd> | Same again |

The self-timer on the right-hand rail cycles 3s → 10s → off. Every capture
is cropped to what's on screen and lands as the thumbnail bottom-left; tap
it for your strip, and tap a shot to view it, **Share** it (where the
browser can share files), **Save** it, or delete it.""",
)
rep("> swatch button next to **Strip**.", "> swatch button on the right-hand tool rail.")
rep(
    """Effects can also be switched by hand from the chip dock at the bottom of
the screen — gestures aren't the only way in. Keyboard:""",
    """Effects can also be picked from the **dial** — swipe the carousel around
the shutter; whatever comes to rest under the shutter is live. **Effects ·
Backgrounds** below it switches what the dial holds. Keyboard:""",
)
rep(
    "\n## Design\n",
    """
## The camera screen

Laid out like a Stories camera. On a phone the camera fills the screen and
everything floats over it on frosted glass: the gesture readout up top, a
tool rail on the right (self-timer, mirror, theme, settings), and the
**dial** at the bottom — a swipeable carousel with the shutter fixed in
the middle, your last shot on the left, and a front/back camera switch on
the right when the device has two cameras. On desktop the same UI sits in
a frame shaped like the camera, so nothing is cropped.

## Virtual backgrounds

Switch the dial to **Backgrounds** to swap the room behind you: Portrait
Blur, Neon City, Sunset, or Aurora (**Room** turns it off). MediaPipe's
selfie segmenter finds you in each frame and `py/backgrounds.py`
composites you over the scene — live, and in the saved photo. The scenes
are drawn in code and follow the color theme. The segmenter only runs
while a background is on.

## Design
""",
)
rep(
    "- **Color themes:** edit `THEMES` in `py/effects.py`.",
    """- **Color themes:** edit `THEMES` in `py/effects.py`.
- **Add a background:** write a `_my_scene(ctx, t, w, h, colors)` in
  `py/backgrounds.py`, add it to `BACKGROUND_ORDER` / `BACKGROUND_META`
  (a label and a CSS swatch for the dial), and dispatch it in
  `draw_background()`.""",
)
rep(
    "until another gesture or the Idle chip\n  switches away.",
    "until another gesture or the Idle item on the dial\n  switches away.",
)
rep(
    "\n## Roadmap ideas\n",
    """- Virtual backgrounds run a second model every frame; on older phones
  that can cost frame rate — pick **Room** to switch it off. If a scene
  ever shows *on* you instead of behind you, the model's mask is inverted:
  change `values[i]` to `1 - values[i]` in `updateMask()` in
  `js/handTracker.js`.
- **Share** needs a browser that can share files (`navigator.canShare`);
  elsewhere the button is hidden and **Save** still downloads.

## Roadmap ideas
""",
)
open(p, "w").write(s)
print("README updated")
EOF
```

Expected: `README updated`

- [ ] **Step 2: Accessibility audit**

Run the accesslint MCP `audit_live` on `http://localhost:8000/?audit=stories` (a fresh query string so it opens a new tab), `format: compact`, `wait_for: ".dial-item"`, `wait_timeout_ms: 30000`.
Expected: `No accessibility violations found.` Fix anything reported before continuing.

- [ ] **Step 3: Final self-checks**

Run: `python3 py/test_render.py && python3 py/test_storage.py 2>/dev/null | tail -1`
Expected: `render ok` then `storage ok`

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: Stories camera, virtual backgrounds, new controls" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand the on-device check to the user**

The browser pane blocks the camera, so these can only be confirmed on a real device. Report them as **unverified** until the user confirms:

1. On a phone (camera needs a secure context: HTTPS, or a tunnel to the dev server): the camera fills the screen, the dial swipes and snaps, the shutter fires.
2. Backgrounds → Neon City: you appear *in front of* the scene, edges soft. If the scene covers you instead, apply the inversion noted in the README.
3. Frame rate with a background on stays usable; if not, note the device.
4. Flip camera switches to the back camera and back; the back camera isn't mirrored.
5. Share opens the phone's share sheet with the photo.

---

## Self-review notes

- Spec coverage: backgrounds (Tasks 4–6), Stories UI + look (Task 6), capture crop + timer + grid + viewer + share (Tasks 3, 6), storage keys (Task 2), error handling (Tasks 5, 6 Steps 10, 12, 13), testing (Tasks 2–7), out-of-scope items untouched.
- Names cross-checked: `safe_rect`, `set_safe_box(..., inset_top, inset_bottom)`, `draw_background`, `composite_person`, `start_countdown(canvas, effect_name, mirrored, seconds, notify)`, `close_viewer(refocus)`, `__sbSetFacing`, `__sbSegmenterState`, `__sbMask`, `__sbSegment` are spelled the same in every task.

# Signalbooth — Stories camera redesign

Date: 2026-10-08 · Status: approved in chat, pending spec review

## Intent

**Said:** the app looks boring, especially on phones. Wanted: backgrounds in
photos, an Instagram-like layout, a bottom bar that is easy and useful.

**Decided with the user:**

| Question | Answer |
|---|---|
| "Background in photo" | Live **virtual background** — person cut out of the room, placed on a scene, visible live and in saved photos |
| "Layout like Instagram" | **Stories camera UI** — full-bleed camera, shutter-centered carousel, tool rail |
| Look | **Bolder brass/violet** — keep the theremin brand, louder |
| Bottom bar | Effects carousel, last-shot thumbnail, timer + flip camera, background picker |

**Assumed:** phone-first fun photobooth; static files + `localStorage` only (no
server, no new libraries); hand-gesture vocabulary unchanged.

**Success:** on a 375px phone the camera fills the screen, every control is a
≥44px tap, switching effect/background is one swipe, a capture shows where it
went (thumbnail), shares to the phone's share sheet, and saved photos match the
on-screen crop and include the background. accesslint stays at 0 violations.

## Architecture (unchanged split)

- `js/handTracker.js` — camera + ML only. Adds: `ImageSegmenter`
  (`selfie_segmenter.tflite`, float16, ~250KB, same `@mediapipe/tasks-vision@0.10.3`),
  run only while a background is selected; writes a 256×256 alpha mask to a
  canvas exposed as `window.__sbMask`. Adds camera switching via
  `await window.__sbSetFacing("user"|"environment")` (restores the old camera on
  failure), asks for a portrait stream on portrait screens, and reports the
  camera count in the `signalbooth:ready` event.
- `py/` — all drawing and DOM. Background compositing is ~5 canvas calls per frame
  in Python: background → person (camera masked with `destination-in`) → effects.

Rejected: compositing in JS (breaks the README's split), CSS `mask` on `<video>`
(not in saved photos).

## Components

### 1. Background scenes — `py/backgrounds.py` (new)
Procedural, animated, theme-aware canvas scenes; no image assets.
`BACKGROUND_ORDER = ["none", "blur", "neon", "sunset", "aurora"]`,
`BACKGROUND_META` (label + swatch CSS gradient for the carousel),
`draw_background(ctx, name, video, t, w, h, colors)`, and
`composite_person(ctx, person_canvas, mask_canvas, video, w, h)`.
`blur` draws the camera frame itself blurred (portrait mode).

### 2. Stories UI — `index.html`, `css/style.css`, `py/main.py`
- Full-bleed stage (`100dvh`) under 768px; at ≥768px a centered frame shaped
  like the camera (`--cam-w`/`--cam-h` from Python), so desktop photos stay
  uncropped and full resolution.
- Overlays on frosted glass: top bar (wordmark, readout, help); right rail
  (timer, flip — only if ≥2 cameras, mirror, settings).
- Bottom: last-shot thumbnail + count (opens strip) · carousel with a fixed
  center shutter; the item under the shutter is active · tabs
  **EFFECTS · BACKGROUNDS** swap the carousel's items.
- Carousel = native `scroll-snap`; selection read on a debounced scroll end;
  tapping an item centers it; gestures/number keys center the matching item.
- Drawers become bottom sheets under 768px.
- Look: conic brass→violet shutter ring (spins during countdown), active item
  label above the shutter, glow accents, spring easing; reduced-motion respected.

### 3. Capture + gallery — `py/photobooth.py`
- Export crops to the visible box (`effects.safe_rect()`), so the photo
  matches the screen (fixes landscape saves from portrait phones).
- The text-safe box shrinks by the HUD bars' heights, so effect text never
  lands under the top bar or the dial; captures still take the whole view.
- Timer setting: 3s (default) / 10s / off.
- Strip → 3-column square grid; tapping a shot opens a viewer with **Share**
  (`navigator.canShare({files})` → `navigator.share`), **Save** (download),
  **Delete** (two-tap confirm). Share hidden where unsupported.

### 4. Storage — `py/storage.py`
New `DEFAULT_SETTINGS`: `"background": "none"`, `"timer": 3`. Camera facing is
not persisted — the app always opens on the selfie camera (avoids starting
the camera twice on load).
Existing type validation covers them; `main.py` additionally validates
`background in BACKGROUND_ORDER` and `timer in (0, 3, 10)`. Gallery schema
unchanged.

## Error handling

- Segmenter fails to load → background tab shows only "none" + toast
  "Backgrounds unavailable"; effects keep working.
- Flip fails (`getUserMedia` rejects) → stay on current camera, toast.
- Share rejected/cancelled → silent on `AbortError`, toast otherwise.
- Low fps: segmentation runs only when background ≠ none.

## Testing

- `python3 py/test_storage.py` extended for new keys.
- `python3 py/test_render.py` — background scenes and the compositor against a
  fake canvas context, and the safe-box / crop-rect math, without the browser.
- Browser pane at 375×812 and desktop via the fake `signalbooth:ready` event
  (pane blocks the camera); accesslint 0 violations.
- Real-camera check of segmentation edges/fps is manual on the user's device —
  reported as unverified until done.

## Out of scope

Custom uploaded backgrounds, collage/Layout mode, video recording.

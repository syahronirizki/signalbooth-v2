# Signalbooth

A gesture-controlled VFX camera and photobooth that runs entirely in the
browser. Show it your hand — it reads the gesture like an instrument
reads a signal, paints a live effect over the video feed, and doubles as
a photobooth you can capture and keep a strip of shots from. No server,
no database: everything is static files plus `localStorage`.

Throw a ✌️ and the lens goes soft. A 👍 brings confetti and a combo
counter. A 👌 puts a thinking pad on screen insisting *"its im fine,
gwenchana!"* And 🫶 rains hearts in every color while **I LOVE U** rises
across the frame.

## How it actually works

Real-time hand tracking has to run in JavaScript/WebAssembly — that's the
only place a hand-tracking model fast enough for 30–60fps video currently
exists in a browser. There's no practical Python-in-browser equivalent.
So the project is deliberately split along that line, not along "frontend
vs backend":

| Layer | Language | Responsibility |
|---|---|---|
| `js/handTracker.js` | JavaScript | Owns the webcam and MediaPipe's `GestureRecognizer` model. Nothing else. |
| `py/*.py` (via PyScript) | Python, running client-side in the browser | Gesture interpretation, the VFX render loop, all DOM manipulation, the photobooth flow, and `localStorage`. |

Concretely: `handTracker.js` calls `window.__sbFrameCallback` once per
video frame with a plain object — hand landmarks and MediaPipe's
classified gesture. Python (`py/main.py`) receives that, decides what it
means, draws the result to the canvas, and updates the page. Everything
downstream of "here are some hand landmarks" is Python, including the
gestures MediaPipe *doesn't* know — the OK sign 👌, the Korean finger
heart 🫰, and the two-handed heart 🫶 — all computed from raw landmark
geometry.

Nothing is stubbed — this is a working app, not a mockup.

### Signals, effects, and moments

Three ideas carry the interaction, and they're worth knowing before
reading the code:

**Signals** are the app's gesture vocabulary. `py/gestures.py` merges
MediaPipe's classes with its own detectors into one canonical name —
`peace`, `ok`, `finger_heart`, `heart_hands`, `double_palm` — so nothing
downstream has to care which of the two produced a pose. Signals are
debounced over a few frames, so one flickery frame can't fire anything.

**Effects** are persistent. A gesture switches to one and it stays until
something switches away, so you can set a look and then move freely.
They're also selectable by hand from the chip dock at the bottom.

**Moments** are momentary. They play while their gesture is held — plus a
short tail so they don't cut off the instant tracking blinks — and then
they're gone. The thinking pad, the love rain, and the portal are moments.

Two of the effects also filter the webcam frame itself (a canvas `filter`,
not a translucent rectangle painted on top), with the amount eased frame
to frame so the blur reads as a lens racking focus rather than a dropped
frame.

## Tech stack

- **[PyScript](https://pyscript.net/)** (2026.7.2, Pyodide runtime) — runs Python in the browser
- **[MediaPipe Tasks Vision](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer)** (`GestureRecognizer`, `@mediapipe/tasks-vision@0.10.3`) — hand landmark + gesture detection
- Plain HTML/CSS/Canvas — no build step, no framework, no bundler
- `localStorage` — the only persistence

## Project structure

```
signalbooth/
├── index.html            page shell: viewport, console, drawers, overlays
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
    └── storage.py           localStorage read/write for settings + gallery
```

## Running it

Camera access requires a "secure context" — `https://` or `localhost` —
so opening `index.html` directly (`file://`) won't work; the browser
blocks `getUserMedia` on that origin. Serve the folder locally instead:

```bash
# Python (already on your machine if you're reading this)
cd signalbooth
python3 -m http.server 8000
# → open http://localhost:8000

# or, if you'd rather use Node
npx serve .
```

Then allow camera access when the browser prompts. First load pulls the
PyScript runtime, Pyodide, and the MediaPipe model from their CDNs, so it
needs an internet connection and takes a few seconds — that's the
"warming up" boot screen.

**Browser support:** recent Chrome, Edge, or Firefox. MediaPipe's WASM +
GPU delegate path is the best-tested target; if effects look sluggish,
the delegate in `js/handTracker.js` can be switched from `"GPU"` to
`"CPU"` as a fallback.

## Gesture guide

Effects — a gesture switches to one, and it stays:

| Gesture | Effect |
|---|---|
| ✌️ Peace sign | **Soft Focus** — the webcam itself goes blurry, with drifting bokeh and a focus-hunting bracket over your hand |
| 👍 Thumbs up | **Hype** — 👍 bubbles stream up off your thumb, confetti falls, and a combo counter climbs the longer you hold it |
| 👎 Thumbs down | **Rain Mood** — the frame desaturates and it starts raining on you specifically, cloud included |
| ✋ Open palm | **Aura Glow** — a soft radial glow, breathing, centered on your hand |
| ✊ Closed fist | **Spark Burst** — particles erupt from your fist |
| ☝️ Point up | **Laser Trail** — a comet tail follows your fingertip |
| 🌈 (dock only) | **Rainbow** — the trail, hue-cycled |

Moments — these play while you hold them, then they're gone:

| Gesture | Moment |
|---|---|
| 👌 OK sign | A thinking pad types itself out: *"its im fine, gwenchana!"* |
| 🫶 Heart hands (two hands) | **I LOVE U** rises across the frame while hearts rain down in every color |
| 🫰 Finger heart | **Love Beam** — hearts pour out of your fingertips through a pink bloom |
| 🤟 I-love-you sign | **Star Shower** |
| ✋✋ Both palms open | A **portal** opens between your hands |

Taking a photo:

| Action | What happens |
|---|---|
| Hold ✋ still for ~1.8s | Fires the shutter, hands-free. A ring fills while you hold, so the wait has feedback. Turn it off in Settings. |
| The shutter button | Same countdown, no gesture needed |
| <kbd>space</kbd> | Same again |

> **Moved in this version:** ✌️ used to take the photo. It drives Soft
> Focus now, and the shutter moved to the palm-hold above. Theme cycling
> used to be a pinch; a pinch now reads as 👌 or 🫰, so themes moved to the
> swatch button next to **Strip**.

Effects can also be switched by hand from the chip dock at the bottom of
the screen — gestures aren't the only way in. Keyboard: <kbd>space</kbd>
shoot, <kbd>T</kbd> theme, <kbd>M</kbd> mirror, <kbd>1</kbd>–<kbd>8</kbd>
effects.

## Design

Rather than the default "dark background, single neon accent" look a lot
of camera/VFX demos reach for, Signalbooth is styled around the thing
it's actually closest to: a **theremin** — an instrument played with
hand gestures in open air — crossed with an **analog photobooth**. That
shows up as:

- A warm brass + cool violet duotone on a deep aubergine base, instead of
  a single neon accent on black
- The gesture readout in the top-left styled like an oscilloscope dial,
  not a plain text badge
- The photo gallery styled as a strip of prints (slightly rotated cards,
  warm paper tone), not a generic thumbnail grid
- `Fraunces` for display type, `JetBrains Mono` for anything "live" or
  technical (readouts, gesture labels), `Inter` for everything else

Two details that took more care than they look like they did:

- **Text on a mirrored canvas.** The stage is flipped with a CSS
  `scaleX(-1)` in mirror view, so anything readable — every label, the
  thinking pad — would render backwards. `_text()` in `py/effects.py`
  flips it back locally; use that rather than `ctx.fillText`.
- **The safe box.** The canvas is displayed with `object-fit: cover`, so a
  16:9 camera in a portrait window loses most of its *width* — on a phone
  only the middle third is ever on screen. Particles can spill outside
  that harmlessly, but anything you're meant to read is placed and sized
  against `set_safe_box()`, not against the canvas.

## Customizing

- **Add or change an effect:** write a `draw_*` function in `py/effects.py`,
  add an entry to `EFFECT_ORDER` / `EFFECT_META` in `py/main.py`, and
  optionally bind it to a signal via that entry's `"signal"` key.
- **Add a moment:** same, but add it to `MOMENT_SECONDS` in `py/main.py`
  (the value is how long it keeps playing after the gesture stops being
  seen) and draw it in the moment block of `on_frame`.
- **Add a gesture:** MediaPipe's `GestureRecognizer` classifies
  `Open_Palm`, `Closed_Fist`, `Pointing_Up`, `Thumb_Up`, `Thumb_Down`,
  `Victory`, `ILoveYou`, and `None` out of the box; `MP_TO_SIGNAL` in
  `py/gestures.py` maps those onto signals. For anything outside that set,
  compute it from landmark geometry in `_resolve_signal()` — 👌 and 🫰 are
  the worked example, and they're told apart purely by whether the last
  three fingers are extended. `_finger_states()` gives you that,
  orientation-free.
- **Filter the webcam frame:** add an amount to `filter_amounts` in
  `py/main.py`, give it a target in `_filter_targets()`, and render it in
  `video_filter()`. It eases automatically.
- **Color themes:** edit `THEMES` in `py/effects.py`.
- **Particle budget / performance:** the `MAX_*` caps at the top of
  `py/effects.py` are the main knobs if a device struggles.

## Known constraints

- Needs an internet connection on first load (CDN-hosted runtime + model);
  it isn't a fully offline app.
- `localStorage` has a browser-enforced size limit (usually 5–10MB), so
  the gallery caps itself at the 24 most recent shots and drops older
  ones if storage fills up — see `MAX_GALLERY_ITEMS` in `py/storage.py`.
- The webcam filters (Soft Focus, Rain Mood) use the canvas `filter`
  property. It's supported in current Chrome, Edge, Firefox and Safari 17+;
  on anything older those two effects lose their tint and keep only their
  overlay.
- Two hands are tracked and 🫶 / ✋✋ use both, but the single-hand effects
  follow the first hand MediaPipe reports.
- Effects are modes: Soft Focus stays blurred and Rain Mood stays gray
  after you lower your hand, until another gesture or the Idle chip
  switches away. That's deliberate — it's what makes them usable as a look
  rather than a flash.

## Roadmap ideas

- More two-hand combos (the portal is the first one)
- Face landmarking for filters that aren't hand-anchored
- A "burst mode" (3 photos in a row, composited into one strip image)
- Recording short clips instead of stills
- Sound: a shutter tick and a rising tone under the hype combo
- Swap the Pyodide interpreter for PyScript's MicroPython runtime
  (`type="mpy"`) for a lighter, faster-starting build, once a given
  effect's needs are confirmed to fit MicroPython's smaller standard
  library

## Credits

- Hand tracking and gesture classification: [MediaPipe](https://ai.google.dev/edge/mediapipe) (Google)
- In-browser Python: [PyScript](https://pyscript.net/) (Anaconda, Inc.)

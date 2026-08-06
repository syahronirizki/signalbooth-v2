# Signalbooth

A gesture-controlled VFX camera and photobooth that runs entirely in the
browser. Show it your hand — it reads the gesture like an instrument
reads a signal, paints a live effect over the video feed, and doubles as
a photobooth you can capture and keep a strip of shots from. No server,
no database: everything is static files plus `localStorage`.

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
downstream of "here are some hand landmarks" is Python, including a
custom pinch-gesture detector (MediaPipe's built-in classifier doesn't
include pinch) computed from raw landmark geometry.

Nothing is stubbed — this is a working first version, not a mockup.

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
    ├── gestures.py        landmark geometry, debouncing, pinch detection
    ├── effects.py          the VFX: aura glow, spark burst, trails, idle state
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

| Gesture | Effect |
|---|---|
| ✋ Open palm | Aura Glow — a soft radial glow around your hand |
| ✊ Closed fist | Spark Burst — particles erupt from your fist |
| ☝️ Point up | Laser Trail — a comet tail follows your fingertip |
| 👍 Thumbs up | Rainbow Trail — the same trail, hue-cycled |
| 🤏 Pinch (thumb + index) | Cycles the color theme (neon → sunset → ocean → mono) |
| ✌️ Peace sign | Triggers the photobooth countdown |

Effects can also be switched manually from the chip dock at the bottom
of the screen — gestures aren't the only way in.

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

## Customizing

- **Add or change an effect:** write a `draw_*` function in `py/effects.py`,
  add an entry to `EFFECT_ORDER` / `EFFECT_META` in `py/main.py`, and
  optionally map it to a gesture via `GESTURE_TO_EFFECT`.
- **Add a gesture:** MediaPipe's `GestureRecognizer` classifies
  `Open_Palm`, `Closed_Fist`, `Pointing_Up`, `Thumb_Up`, `Thumb_Down`,
  `Victory`, `ILoveYou`, and `None` out of the box — any of those not
  already mapped in `main.py` are free to wire up. For a gesture outside
  that set (like the pinch), compute it from landmark geometry in
  `py/gestures.py`, the way `PINCH_ENTER`/`PINCH_EXIT` do.
- **Color themes:** edit `THEMES` in `py/effects.py`.
- **Particle budget / performance:** `MAX_SPARK_PARTICLES` in
  `py/effects.py` is the main knob if a device struggles.

## Known constraints

- Needs an internet connection on first load (CDN-hosted runtime + model);
  it isn't a fully offline app in this v1.
- `localStorage` has a browser-enforced size limit (usually 5–10MB), so
  the gallery caps itself at the 24 most recent shots and drops older
  ones if storage fills up — see `MAX_GALLERY_ITEMS` in `py/storage.py`.
- Single primary hand drives gesture effects even though two hands are
  tracked — see Roadmap.

## Roadmap ideas

- Two-hand combo gestures (both hands open → a bigger, shared effect)
- Face landmarking for filters that aren't hand-anchored
- A "burst mode" (3 photos in a row, composited into one strip image)
- Recording short clips instead of stills
- Swap the Pyodide interpreter for PyScript's MicroPython runtime
  (`type="mpy"`) for a lighter, faster-starting build, once a given
  effect's needs are confirmed to fit MicroPython's smaller standard
  library

## Credits

- Hand tracking and gesture classification: [MediaPipe](https://ai.google.dev/edge/mediapipe) (Google)
- In-browser Python: [PyScript](https://pyscript.net/) (Anaconda, Inc.)

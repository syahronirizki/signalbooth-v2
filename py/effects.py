"""
effects.py — canvas rendering for every gesture-triggered visual.

Every draw call here goes through PyScript's JS interop straight to the
2D canvas context (ctx.beginPath(), ctx.fillStyle = ..., and so on read
exactly like they would in JavaScript). Particle counts are kept modest
on purpose: each call crosses the Python/JS boundary, so a lean budget
keeps this smooth on modest hardware. Tune the MAX_* caps down if a device
struggles, or see the README for notes on moving the render loop to JS if
you want to push particle counts much higher.

Two things are worth knowing before editing anything below:

1. The stage canvas is mirrored with a CSS ``scaleX(-1)`` when mirror view
   is on, so anything with a readable direction — text, emoji — has to be
   flipped back locally or it renders backwards. ``_text()`` handles that;
   use it instead of calling ctx.fillText directly.
2. The webcam frame is tinted/blurred with the canvas ``filter`` property
   before the overlay is drawn, not by painting a translucent rectangle on
   top. ``video_filter()`` builds that filter string; main.py eases the
   amounts so effects fade in rather than snapping.
"""

import math
import random

TAU = 2 * math.pi

THEME_ORDER = ["neon", "sunset", "ocean", "mono"]
THEMES = {
    "neon": {"a": "#E8A33D", "b": "#8B7FF0", "c": "#FBF3E3"},
    "sunset": {"a": "#F2795C", "b": "#F4B860", "c": "#FBF3E3"},
    "ocean": {"a": "#5CC8F2", "b": "#8B7FF0", "c": "#EAF6FB"},
    "mono": {"a": "#ECE6F2", "b": "#9C93AD", "c": "#FBF3E3"},
}

# "rainy emote ❤️ in any color" — hearts pull from the whole wheel rather
# than the active theme, so the love moments read as their own thing.
LOVE_HUES = (348, 330, 312, 286, 264, 12, 32)

FONT_DISPLAY = '"Fraunces", Georgia, serif'
FONT_MONO = '"JetBrains Mono", ui-monospace, monospace'

MAX_SPARK_PARTICLES = 70
MAX_BOKEH = 24
MAX_HYPE = 34
MAX_HEARTS = 80
MAX_RAIN = 90
MAX_STARS = 54


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "size", "color", "spin", "seed")

    def __init__(self, x, y, vx, vy, life, size, color, spin=0.0):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = self.max_life = life
        self.size = size
        self.color = color
        self.spin = spin
        self.seed = random.random() * TAU

    def step(self, dt, gravity=14.0, drag=0.98):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += gravity * dt
        self.vx *= drag
        self.vy *= drag
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    @property
    def alpha(self):
        return max(0.0, self.life / self.max_life)


_sparks = []
_bokeh = []
_hype = []
_hearts = []
_rain = []
_stars = []


# ------------------------------------------------------------ safe box --

# The stage canvas is displayed with `object-fit: cover`, so a 16:9 camera in
# a portrait window has most of its *width* cropped away — on a phone only the
# middle third or so is ever on screen. Particles can spill outside that with
# no harm, but anything you're meant to read (the thinking pad, the hype
# counter, every label) has to be placed inside the visible region instead of
# inside the canvas. main.py refreshes this once per frame.
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
    # round, not int: a 719.9px-tall view is the whole frame, not a row short
    x0, y0 = round(_VISIBLE["x0"]), round(_VISIBLE["y0"])
    return x0, y0, max(1, round(_VISIBLE["x1"]) - x0), max(1, round(_VISIBLE["y1"]) - y0)


def safe_w():
    return _SAFE["x1"] - _SAFE["x0"]


def safe_h():
    return _SAFE["y1"] - _SAFE["y0"]


def safe_cx():
    return (_SAFE["x0"] + _SAFE["x1"]) / 2


def safe_cy():
    return (_SAFE["y0"] + _SAFE["y1"]) / 2


def _clamp_x(x, margin=0.0):
    return min(max(x, _SAFE["x0"] + margin), _SAFE["x1"] - margin)


def _clamp_y(y, margin=0.0):
    return min(max(y, _SAFE["y0"] + margin), _SAFE["y1"] - margin)


def theme_colors(index):
    name = THEME_ORDER[index % len(THEME_ORDER)]
    return THEMES[name]


def theme_name(index):
    return THEME_ORDER[index % len(THEME_ORDER)]


# ------------------------------------------------------------- primitives --


def _circle(ctx, x, y, r, color, alpha=1.0):
    ctx.globalAlpha = alpha
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(x, y, max(0.1, r), 0, TAU)
    ctx.fill()


def _text(ctx, text, x, y, mirrored, font, color, align="center", alpha=1.0, rotate=0.0):
    """fillText, but un-mirrored so it stays readable in mirror view."""
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.font = font
    ctx.fillStyle = color
    ctx.textAlign = align
    ctx.textBaseline = "middle"
    ctx.translate(x, y)
    if mirrored:
        ctx.scale(-1, 1)
    if rotate:
        ctx.rotate(rotate)
    ctx.fillText(text, 0, 0)
    ctx.restore()


def _round_rect(ctx, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()


def _heart(ctx, x, y, size, color, alpha=1.0, rotate=0.0):
    """A bezier heart, drawn as a real path rather than an emoji glyph — it
    scales cleanly, takes any color, and doesn't depend on which emoji font
    the browser happens to have."""
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.fillStyle = color
    ctx.translate(x, y)
    if rotate:
        ctx.rotate(rotate)
    s = size / 16.0
    ctx.scale(s, s)
    ctx.beginPath()
    ctx.moveTo(0, -4)
    ctx.bezierCurveTo(-2, -12, -14, -10, -14, -2)
    ctx.bezierCurveTo(-14, 6, -4, 10, 0, 16)
    ctx.bezierCurveTo(4, 10, 14, 6, 14, -2)
    ctx.bezierCurveTo(14, -10, 2, -12, 0, -4)
    ctx.closePath()
    ctx.fill()
    ctx.restore()


def _star(ctx, x, y, r, color, alpha=1.0, points=4):
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.fillStyle = color
    ctx.translate(x, y)
    ctx.beginPath()
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * 0.32
        ang = (i / (points * 2)) * TAU - math.pi / 2
        px, py = math.cos(ang) * rad, math.sin(ang) * rad
        if i == 0:
            ctx.moveTo(px, py)
        else:
            ctx.lineTo(px, py)
    ctx.closePath()
    ctx.fill()
    ctx.restore()


def video_filter(amounts):
    """Build the CSS filter string applied to the webcam frame itself.

    ``amounts`` is a dict of eased 0..1 values, so an effect ramps in over a
    few frames instead of snapping — a hard cut to blur(14px) looks like a
    glitch, a ramp looks like a lens racking focus.
    """
    parts = []
    blur = amounts.get("blur", 0.0)
    gray = amounts.get("gray", 0.0)
    love = amounts.get("love", 0.0)
    warm = amounts.get("warm", 0.0)

    if blur > 0.01:
        parts.append(f"blur({blur * 15:.1f}px)")
    if gray > 0.01:
        parts.append(f"grayscale({gray * 0.88:.2f}) brightness({1 - 0.12 * gray:.2f})")
    if love > 0.01:
        parts.append(f"saturate({1 + 0.45 * love:.2f}) contrast({1 + 0.06 * love:.2f})")
    if warm > 0.01:
        parts.append(f"saturate({1 + 0.3 * warm:.2f}) brightness({1 + 0.06 * warm:.2f})")
    return " ".join(parts) if parts else "none"


# ------------------------------------------------------- original effects --


def spawn_sparks(cx, cy, colors, count=4):
    for _ in range(count):
        angle = random.uniform(0, TAU)
        speed = random.uniform(60, 220)
        _sparks.append(
            Particle(
                cx,
                cy,
                math.cos(angle) * speed,
                math.sin(angle) * speed,
                random.uniform(0.35, 0.75),
                random.uniform(2, 5),
                random.choice([colors["a"], colors["b"]]),
            )
        )
    if len(_sparks) > MAX_SPARK_PARTICLES:
        del _sparks[: len(_sparks) - MAX_SPARK_PARTICLES]


def draw_aura_glow(ctx, state, colors, t, intensity):
    """Open palm: a soft radial glow, breathing gently, centered on the hand."""
    if not state["present"]:
        return
    cx, cy = state["center"]
    pulse = 0.75 + 0.25 * math.sin(t * 2.4)
    radius = (40 + state["scale"] * 90) * pulse * intensity
    grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, max(1, radius))
    grad.addColorStop(0, colors["a"] + "aa")
    grad.addColorStop(0.5, colors["b"] + "55")
    grad.addColorStop(1, colors["b"] + "00")
    ctx.globalAlpha = 1.0
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.arc(cx, cy, radius, 0, TAU)
    ctx.fill()
    ctx.globalAlpha = 1.0


def draw_spark_burst(ctx, state, colors, dt, intensity):
    """Closed fist: particles erupt from the fist center while it's held."""
    if state["present"]:
        cx, cy = state["center"]
        spawn_sparks(cx, cy, colors, count=max(1, int(4 * intensity)))

    for p in _sparks:
        p.step(dt)
    for p in list(_sparks):
        if not p.alive:
            _sparks.remove(p)
            continue
        _circle(ctx, p.x, p.y, p.size, p.color, p.alpha)
    ctx.globalAlpha = 1.0


def draw_laser_trail(ctx, state, colors, intensity):
    """Pointing up: a fading comet tail following the index fingertip."""
    trail = state.get("trail") or []
    if len(trail) < 2:
        return
    n = len(trail)
    for i in range(1, n):
        x1, y1 = trail[i - 1]
        x2, y2 = trail[i]
        progress = i / n
        ctx.globalAlpha = progress * 0.9
        ctx.strokeStyle = colors["a"]
        ctx.lineWidth = 2 + progress * 6 * intensity
        ctx.lineCap = "round"
        ctx.beginPath()
        ctx.moveTo(x1, y1)
        ctx.lineTo(x2, y2)
        ctx.stroke()
    tipx, tipy = trail[-1]
    _circle(ctx, tipx, tipy, 6 * intensity, colors["c"], 0.9)
    ctx.globalAlpha = 1.0


def draw_rainbow_trail(ctx, state, t, intensity):
    """The same trail mechanic, hue-cycled over time. Dock-only now that
    👍 drives the hype effect instead."""
    trail = state.get("trail") or []
    if len(trail) < 2:
        return
    n = len(trail)
    for i in range(1, n):
        x1, y1 = trail[i - 1]
        x2, y2 = trail[i]
        progress = i / n
        hue = int((t * 60 + i * 14) % 360)
        ctx.globalAlpha = progress * 0.85
        ctx.strokeStyle = f"hsl({hue}, 85%, 62%)"
        ctx.lineWidth = 3 + progress * 7 * intensity
        ctx.lineCap = "round"
        ctx.beginPath()
        ctx.moveTo(x1, y1)
        ctx.lineTo(x2, y2)
        ctx.stroke()
    ctx.globalAlpha = 1.0


def draw_idle(ctx, t, canvas_w, canvas_h):
    """A quiet ambient effect for when no hand is in frame — a hint that
    the instrument is listening, not a broken screen."""
    cx, cy = canvas_w / 2, canvas_h * 0.88
    for i in range(3):
        phase = t * 0.6 + i * 2.1
        r = 3 + i * 1.4
        x = cx + math.sin(phase) * 26
        y = cy + math.cos(phase * 0.7) * 6
        _circle(ctx, x, y, r, "#ECE6F2", 0.16)
    ctx.globalAlpha = 1.0


# ------------------------------------------------------------ ✌️ soft focus --


def draw_soft_focus(ctx, state, colors, t, dt, w, h, intensity, mirrored):
    """✌️ Peace sign: the webcam itself goes soft.

    The blur is real — it's a canvas filter on the video draw, applied by
    main.py — so this function only paints what a defocused lens adds on top:
    drifting bokeh discs and a vignette that pulls the edges down.
    """
    sw = safe_w()
    if len(_bokeh) < MAX_BOKEH:
        for _ in range(2):
            _bokeh.append(
                Particle(
                    random.uniform(0, w),
                    random.uniform(0, h),
                    random.uniform(-14, 14),
                    random.uniform(-22, -6),
                    random.uniform(2.5, 5.0),
                    random.uniform(sw * 0.02, sw * 0.075) * intensity,
                    random.choice([colors["a"], colors["b"], colors["c"]]),
                )
            )

    for p in list(_bokeh):
        p.step(dt, gravity=0.0, drag=1.0)
        if not p.alive:
            _bokeh.remove(p)
            continue
        breathe = 0.85 + 0.15 * math.sin(t * 1.6 + p.seed)
        _circle(ctx, p.x, p.y, p.size * breathe, p.color, p.alpha * 0.22)
        _circle(ctx, p.x, p.y, p.size * breathe * 0.55, "#FBF3E3", p.alpha * 0.12)

    vignette = ctx.createRadialGradient(w / 2, h / 2, h * 0.25, w / 2, h / 2, h * 0.85)
    vignette.addColorStop(0, "rgba(21,17,26,0)")
    vignette.addColorStop(1, "rgba(21,17,26,0.55)")
    ctx.globalAlpha = 1.0
    ctx.fillStyle = vignette
    ctx.fillRect(0, 0, w, h)

    # A focus-hunting bracket over the hand sells "the lens is searching".
    if state["present"] and state["center"]:
        cx, cy = state["center"]
        r = (34 + state["scale"] * 70) * (1.0 + 0.06 * math.sin(t * 5))
        ctx.globalAlpha = 0.5
        ctx.strokeStyle = colors["c"]
        ctx.lineWidth = 2
        for i in range(4):
            a0 = i * (TAU / 4) + 0.32
            ctx.beginPath()
            ctx.arc(cx, cy, r, a0, a0 + 0.55)
            ctx.stroke()

    _text(
        ctx,
        "SOFT FOCUS",
        safe_cx(),
        _SAFE["y1"] - safe_h() * 0.07,
        mirrored,
        f"600 {safe_w() * 0.03:.0f}px {FONT_MONO}",
        colors["c"],
        alpha=0.55,
    )
    ctx.globalAlpha = 1.0


# ------------------------------------------------------------------ 👍 hype --


def draw_hype(ctx, state, colors, t, dt, w, h, intensity, mirrored, combo):
    """👍 Thumbs up: the crowd goes up.

    Live-stream "like" bubbles stream up from the thumb, confetti flecks fall
    across the frame, and a combo counter climbs the longer the pose is held —
    a reason to keep your thumb up rather than flash it once.
    """
    sw = safe_w()
    if state["present"] and state["center"]:
        cx, cy = state["center"]
        if random.random() < 0.55 * intensity:
            _hype.append(
                Particle(
                    cx + random.uniform(-38, 38),
                    cy + random.uniform(-10, 24),
                    random.uniform(-34, 34),
                    random.uniform(-230, -140),
                    random.uniform(1.2, 2.0),
                    random.uniform(sw * 0.035, sw * 0.06),
                    random.choice([colors["a"], colors["b"], "#FBF3E3"]),
                )
            )
        if random.random() < 0.7 * intensity:
            _hype.append(
                Particle(
                    random.uniform(0, w),
                    -20,
                    random.uniform(-30, 30),
                    random.uniform(60, 150),
                    random.uniform(1.6, 2.6),
                    random.uniform(3, 7),
                    f"hsl({random.randint(0, 359)}, 85%, 64%)",
                    spin=random.uniform(-6, 6),
                )
            )
    if len(_hype) > MAX_HYPE:
        del _hype[: len(_hype) - MAX_HYPE]

    for p in list(_hype):
        p.step(dt, gravity=6.0, drag=0.995)
        if not p.alive:
            _hype.remove(p)
            continue
        if p.spin:  # confetti fleck
            ctx.save()
            ctx.globalAlpha = p.alpha * 0.9
            ctx.fillStyle = p.color
            ctx.translate(p.x, p.y)
            ctx.rotate(p.seed + t * p.spin)
            ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2)
            ctx.restore()
        else:  # a rising 👍 bubble
            wobble = math.sin(t * 3 + p.seed) * 10
            _circle(ctx, p.x + wobble, p.y, p.size * 0.9, p.color, p.alpha * 0.28)
            _text(
                ctx,
                "\U0001f44d",
                p.x + wobble,
                p.y,
                mirrored,
                f"{p.size:.0f}px {FONT_DISPLAY}",
                "#FFFFFF",
                alpha=p.alpha,
            )

    if combo > 0:
        pop = 1.0 + 0.12 * math.sin(t * 9)
        _text(
            ctx,
            f"HYPE ×{combo}",
            safe_cx(),
            _SAFE["y0"] + safe_h() * 0.13,
            mirrored,
            f"700 {safe_w() * 0.09 * pop:.0f}px {FONT_DISPLAY}",
            colors["a"],
            alpha=0.92,
        )
    ctx.globalAlpha = 1.0


# ------------------------------------------------------------ 👌 gwenchana --


GWENCHANA_LINE = "its im fine, gwenchana!"
GWENCHANA_SUB = "괜찮아 · it's ok, really \U0001f60c"


def draw_gwenchana(ctx, state, colors, t, age, w, h, mirrored):
    """👌 OK sign: a thinking pad that insists everything is fine.

    The line types itself out character by character off ``age`` (seconds
    since the pose was recognised), the pad breathes, and three tail bubbles
    tie it back to the hand — a thought bubble, not a chat bubble, because
    the joke is that it's what you're telling yourself.
    """
    anchor = state.get("pinch_point") or state.get("center") or (w / 2, h / 2)
    ax, ay = anchor

    chars = max(0, min(len(GWENCHANA_LINE), int(age / 0.045)))
    line = GWENCHANA_LINE[:chars]
    caret = "▏" if (age % 0.7) < 0.35 and chars < len(GWENCHANA_LINE) else ""

    # Sized and clamped against the visible box, not the canvas — see
    # set_safe_box(). On a portrait phone the canvas is mostly off-screen,
    # and a pad measured in canvas width would be cropped in half.
    sw, sh = safe_w(), safe_h()
    pad_w = min(w * 0.36, sw * 0.9)
    pad_h = pad_w * 0.4
    px = min(max(ax - pad_w / 2, _SAFE["x0"] + sw * 0.03), _SAFE["x1"] - sw * 0.03 - pad_w)
    py = ay - pad_h - sh * 0.16
    if py < _SAFE["y0"] + sh * 0.05:
        py = min(ay + sh * 0.10, _SAFE["y1"] - sh * 0.12 - pad_h)
    py += math.sin(t * 1.8) * sh * 0.006

    # tail bubbles, hand → pad
    for i, frac in enumerate((0.82, 0.9, 0.97)):
        bx = ax + (px + pad_w / 2 - ax) * frac
        by = ay + (py + pad_h - ay) * frac
        _circle(ctx, bx, by, (3 + i * 3.5) * (sw / 1280), "#FBF3E3", 0.85)

    ctx.save()
    ctx.globalAlpha = 0.93
    ctx.fillStyle = "#FBF3E3"
    ctx.shadowColor = "rgba(0,0,0,0.45)"
    ctx.shadowBlur = 24
    ctx.shadowOffsetY = 8
    _round_rect(ctx, px, py, pad_w, pad_h, pad_h * 0.34)
    ctx.fill()
    ctx.restore()

    ctx.globalAlpha = 0.75
    ctx.strokeStyle = colors["a"]
    ctx.lineWidth = 2
    _round_rect(ctx, px + 5, py + 5, pad_w - 10, pad_h - 10, pad_h * 0.28)
    ctx.stroke()

    _text(
        ctx,
        line + caret,
        px + pad_w / 2,
        py + pad_h * 0.40,
        mirrored,
        f"600 {pad_h * 0.23:.0f}px {FONT_DISPLAY}",
        "#2A2332",
    )
    _text(
        ctx,
        GWENCHANA_SUB,
        px + pad_w / 2,
        py + pad_h * 0.72,
        mirrored,
        f"{pad_h * 0.145:.0f}px {FONT_MONO}",
        "#6B5F42",
        alpha=0.9,
    )
    ctx.globalAlpha = 1.0


# ---------------------------------------------------------- 🫰 finger heart --


def draw_finger_heart(ctx, state, t, dt, w, h, intensity, mirrored):
    """🫰 Finger heart: a small love theme, aimed wherever you point it.

    Hearts pop out of the thumb/index contact point in whatever hue they
    feel like, drift up, and spin as they fade. A soft pink bloom sits under
    the whole thing so the emitter reads as a source of light.
    """
    sw = safe_w()
    origin = state.get("pinch_point") or state.get("center")
    if origin:
        ox, oy = origin
        bloom_r = (sw * 0.10) * (1.0 + 0.12 * math.sin(t * 4)) * intensity
        grad = ctx.createRadialGradient(ox, oy, 0, ox, oy, max(1, bloom_r))
        grad.addColorStop(0, "rgba(255,138,178,0.55)")
        grad.addColorStop(0.6, "rgba(214,120,255,0.22)")
        grad.addColorStop(1, "rgba(214,120,255,0)")
        ctx.globalAlpha = 1.0
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.arc(ox, oy, bloom_r, 0, TAU)
        ctx.fill()

        if random.random() < 0.7 * intensity:
            hue = random.choice(LOVE_HUES)
            _hearts.append(
                Particle(
                    ox + random.uniform(-8, 8),
                    oy + random.uniform(-8, 8),
                    random.uniform(-55, 55),
                    random.uniform(-135, -70),
                    random.uniform(0.9, 1.7),
                    random.uniform(sw * 0.024, sw * 0.05),
                    f"hsl({hue}, 88%, 66%)",
                    spin=random.uniform(-3, 3),
                )
            )

        # a sparkle ring that ticks around the emitter
        for i in range(6):
            ang = t * 2.2 + i * (TAU / 6)
            r = bloom_r * 0.72
            _circle(
                ctx,
                ox + math.cos(ang) * r,
                oy + math.sin(ang) * r,
                sw * 0.007,
                "#FFE7F1",
                0.75,
            )

    _step_hearts(ctx, dt, t)
    ctx.globalAlpha = 1.0


def _step_hearts(ctx, dt, t):
    if len(_hearts) > MAX_HEARTS:
        del _hearts[: len(_hearts) - MAX_HEARTS]
    for p in list(_hearts):
        p.step(dt, gravity=-8.0, drag=0.99)
        if not p.alive:
            _hearts.remove(p)
            continue
        _heart(ctx, p.x, p.y, p.size, p.color, p.alpha, rotate=math.sin(t * p.spin + p.seed) * 0.4)


# ----------------------------------------------------------- 🫶 heart hands --


def draw_heart_hands(ctx, state, t, age, dt, w, h, intensity, mirrored):
    """🫶 Two hands, one heart: the big one.

    Three things happen at once — an "I LOVE U" title that pops in, rises and
    fades; a rain of hearts in every color falling across the frame; and a
    heart outline pulsing between the hands, so the gesture itself is traced
    back to you.
    """
    sw, sh = safe_w(), safe_h()
    # ── heart rain ────────────────────────────────────────────────────────
    if random.random() < 0.85 * intensity:
        _hearts.append(
            Particle(
                random.uniform(0, w),
                -safe_w() * 0.05,
                random.uniform(-25, 25),
                random.uniform(70, 165),
                random.uniform(2.6, 4.2),
                random.uniform(sw * 0.024, sw * 0.055),
                f"hsl({random.choice(LOVE_HUES)}, 90%, {random.randint(58, 74)}%)",
                spin=random.uniform(-2.5, 2.5),
            )
        )
    _step_hearts(ctx, dt, t)

    # ── heart traced between the hands ────────────────────────────────────
    point = state.get("heart_point") or state.get("center")
    if point:
        hx, hy = point
        beat = 1.0 + 0.10 * math.sin(t * 6.0)
        size = (w * 0.075) * beat * intensity
        _heart(ctx, hx, hy, size * 1.35, "rgba(255,120,166,0.20)")
        _heart(ctx, hx, hy, size, "rgba(255,158,190,0.55)")

    # ── the title: pop in, drift up, fade out ─────────────────────────────
    intro = min(1.0, age / 0.35)
    ease = 1 - (1 - intro) * (1 - intro)
    rise = (1 - ease) * sh * 0.06 + max(0.0, age - 1.2) * sh * 0.018
    fade = 1.0 if age < 3.4 else max(0.0, 1 - (age - 3.4) / 1.4)
    scale_pop = 0.7 + 0.3 * ease + 0.03 * math.sin(t * 5)
    title_px = sw * 0.16 * scale_pop

    # Sits clear above the traced heart rather than on top of it — the heart
    # is drawn at the hands, which is roughly frame center in normal use.
    cx = safe_cx()
    ty = safe_cy() - sh * 0.24 + rise
    _text(
        ctx,
        "I LOVE U",
        cx,
        ty,
        mirrored,
        f"700 {title_px:.0f}px {FONT_DISPLAY}",
        "rgba(21,17,26,0.35)",
        alpha=fade,
    )
    _text(
        ctx,
        "I LOVE U",
        cx - sw * 0.004,
        ty - sh * 0.004,
        mirrored,
        f"700 {title_px:.0f}px {FONT_DISPLAY}",
        f"hsl({int((t * 70) % 360)}, 90%, 72%)",
        alpha=fade,
    )
    _text(
        ctx,
        "♡  ♡  ♡",
        cx,
        ty + sh * 0.075,
        mirrored,
        f"{sw * 0.05:.0f}px {FONT_MONO}",
        "#FFE7F1",
        alpha=fade * 0.8,
    )
    ctx.globalAlpha = 1.0


# ---------------------------------------------------------- 🤟 star shower --


def draw_star_shower(ctx, state, colors, t, dt, w, h, intensity, mirrored):
    """🤟 The ASL "I love you" sign: stars fall, and they fall toward you."""
    sw = safe_w()
    if random.random() < 0.9 * intensity:
        _stars.append(
            Particle(
                random.uniform(0, w),
                -10,
                random.uniform(-40, 40),
                random.uniform(90, 210),
                random.uniform(1.8, 3.0),
                random.uniform(sw * 0.01, sw * 0.027),
                random.choice([colors["a"], colors["c"], "#FFF0C4"]),
                spin=random.uniform(-4, 4),
            )
        )
    if len(_stars) > MAX_STARS:
        del _stars[: len(_stars) - MAX_STARS]

    for p in list(_stars):
        p.step(dt, gravity=10.0, drag=0.999)
        if not p.alive:
            _stars.remove(p)
            continue
        _star(ctx, p.x, p.y, p.size, p.color, p.alpha * 0.95)
        ctx.globalAlpha = p.alpha * 0.25
        ctx.strokeStyle = p.color
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.moveTo(p.x, p.y)
        ctx.lineTo(p.x - p.vx * 0.06, p.y - p.vy * 0.06)
        ctx.stroke()

    if state["present"] and state["center"]:
        cx, cy = state["center"]
        _text(
            ctx,
            "you rock ⭐",
            _clamp_x(cx, safe_w() * 0.18),
            _clamp_y(cy - safe_h() * 0.14, safe_h() * 0.06),
            mirrored,
            f"600 {safe_w() * 0.055:.0f}px {FONT_DISPLAY}",
            colors["c"],
            alpha=0.9,
        )
    ctx.globalAlpha = 1.0


# ------------------------------------------------------------ 👎 rain mood --


def draw_rain_mood(ctx, state, t, dt, w, h, intensity, mirrored):
    """👎 Thumbs down: the world desaturates (a canvas filter, applied in
    main.py) and it starts raining on you specifically — cloud included."""
    if random.random() < 0.95:
        _rain.append(
            Particle(
                random.uniform(-w * 0.1, w),
                -20,
                random.uniform(70, 110),
                random.uniform(620, 900),
                random.uniform(0.9, 1.4),
                random.uniform(6, 16) * intensity,
                "#AEC6D8",
            )
        )
    if len(_rain) > MAX_RAIN:
        del _rain[: len(_rain) - MAX_RAIN]

    ctx.lineCap = "round"
    for p in list(_rain):
        p.step(dt, gravity=0.0, drag=1.0)
        if not p.alive or p.y > h + 30:
            _rain.remove(p)
            continue
        ctx.globalAlpha = p.alpha * 0.45
        ctx.strokeStyle = p.color
        ctx.lineWidth = 1.6
        ctx.beginPath()
        ctx.moveTo(p.x, p.y)
        ctx.lineTo(p.x - p.vx * 0.02, p.y - p.size)
        ctx.stroke()

    if state["present"] and state["center"]:
        sw, sh = safe_w(), safe_h()
        cx, cy = state["center"]
        cx = _clamp_x(cx, sw * 0.14)
        cloud_y = _clamp_y(cy - sh * 0.28, sh * 0.16)
        drift = math.sin(t * 0.9) * sw * 0.02
        for dx, dy, r in ((-0.075, 0.012, 0.058), (0.0, -0.02, 0.08), (0.085, 0.01, 0.053)):
            _circle(ctx, cx + drift + sw * dx, cloud_y + sh * dy, sw * r, "#5C6172", 0.72)
        _text(
            ctx,
            "meh.",
            cx + drift,
            cloud_y - sh * 0.10,
            mirrored,
            f"600 {sw * 0.045:.0f}px {FONT_MONO}",
            "#C9D3DD",
            alpha=0.7,
        )
    ctx.globalAlpha = 1.0


# --------------------------------------------------------- ✋✋ palm portal --


def draw_portal(ctx, state, colors, t, w, h, intensity, mirrored):
    """Both palms open: a portal opens between your hands.

    The first effect here that genuinely needs two hands — rings expand along
    the axis between the palms, and an arc jitters across the gap.
    """
    hands = state.get("hands") or []
    if len(hands) < 2:
        return
    (ax, ay), (bx, by) = hands[0]["center"], hands[1]["center"]
    mx, my = (ax + bx) / 2, (ay + by) / 2
    gap = max(1.0, math.hypot(bx - ax, by - ay))

    for i in range(4):
        phase = (t * 0.6 + i * 0.25) % 1.0
        r = gap * 0.15 + phase * gap * 0.55 * intensity
        ctx.globalAlpha = (1 - phase) * 0.55
        ctx.strokeStyle = colors["b"] if i % 2 else colors["a"]
        ctx.lineWidth = 2 + (1 - phase) * 5
        ctx.beginPath()
        ctx.arc(mx, my, r, 0, TAU)
        ctx.stroke()

    ctx.globalAlpha = 0.75
    ctx.strokeStyle = colors["c"]
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(ax, ay)
    steps = 7
    for i in range(1, steps):
        f = i / steps
        jitter = (random.random() - 0.5) * gap * 0.12
        ctx.lineTo(ax + (bx - ax) * f + jitter, ay + (by - ay) * f + jitter)
    ctx.lineTo(bx, by)
    ctx.stroke()

    _circle(ctx, mx, my, gap * 0.06, colors["c"], 0.5)
    _text(
        ctx,
        "PORTAL OPEN",
        _clamp_x(mx, safe_w() * 0.12),
        my + gap * 0.42,
        mirrored,
        f"600 {safe_w() * 0.032:.0f}px {FONT_MONO}",
        colors["c"],
        alpha=0.7,
    )
    ctx.globalAlpha = 1.0


# ------------------------------------------------------------------ debug --


HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


def draw_skeleton(ctx, landmarks, canvas_w, canvas_h, color="#8B7FF0"):
    """Optional debug overlay: the raw 21-point hand skeleton."""
    pts = [(p[0] * canvas_w, p[1] * canvas_h) for p in landmarks]
    ctx.globalAlpha = 0.55
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    for a, b in HAND_CONNECTIONS:
        ctx.beginPath()
        ctx.moveTo(pts[a][0], pts[a][1])
        ctx.lineTo(pts[b][0], pts[b][1])
        ctx.stroke()
    for x, y in pts:
        _circle(ctx, x, y, 3, color, 0.8)
    ctx.globalAlpha = 1.0


def draw_palm_hold_ring(ctx, state, colors, progress, mirrored, w):
    """Feedback for the palm-hold shutter: a ring that fills while an open
    palm is held still. Without it, the 1.8s wait just feels like nothing is
    happening."""
    if not state["present"] or not state["center"] or progress <= 0.02:
        return
    cx, cy = state["center"]
    r = 46 + state["scale"] * 60
    ctx.globalAlpha = 0.3
    ctx.strokeStyle = colors["c"]
    ctx.lineWidth = 4
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, TAU)
    ctx.stroke()

    ctx.globalAlpha = 0.95
    ctx.strokeStyle = colors["a"]
    ctx.lineCap = "round"
    ctx.beginPath()
    ctx.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + TAU * min(1.0, progress))
    ctx.stroke()
    _text(
        ctx,
        "HOLD TO SHOOT",
        _clamp_x(cx, safe_w() * 0.16),
        cy + r + safe_w() * 0.045,
        mirrored,
        f"600 {safe_w() * 0.028:.0f}px {FONT_MONO}",
        colors["c"],
        alpha=0.8,
    )
    ctx.globalAlpha = 1.0


def reset_pools():
    """Clear every particle pool — used when the effect changes, so the last
    effect's particles don't linger under the new one."""
    for pool in (_sparks, _bokeh, _hype, _hearts, _rain, _stars):
        pool.clear()

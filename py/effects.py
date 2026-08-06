"""
effects.py — canvas rendering for every gesture-triggered visual.

Every draw call here goes through PyScript's JS interop straight to the
2D canvas context (ctx.beginPath(), ctx.fillStyle = ..., and so on read
exactly like they would in JavaScript). Particle counts are kept modest
on purpose: each call crosses the Python/JS boundary, so a lean budget
keeps this smooth on modest hardware. Tune MAX_SPARK_PARTICLES down if a
device struggles, or see the README for notes on moving the render loop
to JS if you want to push particle counts much higher.
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

MAX_SPARK_PARTICLES = 70


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "size", "color")

    def __init__(self, x, y, vx, vy, life, size, color):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = self.max_life = life
        self.size = size
        self.color = color

    def step(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 14.0 * dt  # gentle gravity
        self.vx *= 0.98
        self.vy *= 0.98
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    @property
    def alpha(self):
        return max(0.0, self.life / self.max_life)


_sparks = []


def theme_colors(index):
    name = THEME_ORDER[index % len(THEME_ORDER)]
    return THEMES[name]


def _circle(ctx, x, y, r, color, alpha=1.0):
    ctx.globalAlpha = alpha
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(x, y, max(0.1, r), 0, TAU)
    ctx.fill()


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
    """Thumbs up: the same trail mechanic, hue-cycled over time."""
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

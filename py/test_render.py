"""Self-check for the browser-free parts of rendering: python3 py/test_render.py

Checks the safe-box math that keeps effect text out from under the HUD and
crops captures to what's actually on screen.
"""

import types

import backgrounds
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

# Client sizes are whole pixels, so a camera-shaped window can compute a
# visible height of 719.9: that's the full frame, not one row short.
effects.set_safe_box(1280, 720, 1515, 852)
assert effects.safe_rect() == (0, 0, 1280, 720), effects.safe_rect()

# Not laid out yet (0×0 client): fall back to the whole frame.
effects.set_safe_box(1280, 720, 0, 0)
assert effects.safe_rect() == (0, 0, 1280, 720)



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

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

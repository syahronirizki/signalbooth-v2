"""
gestures.py — turns the raw per-frame data from handTracker.js into
something the rest of the app can act on.

MediaPipe's GestureRecognizer already classifies the standard gesture set
(Open_Palm, Closed_Fist, Victory, Thumb_Up, Pointing_Up, ...) in JS, because
that needs the trained model running at WASM speed. This module is
everything downstream of that:

  * turning noisy per-frame classifications into a *stable* signal
    (debounced, so one flickery frame can't fire an effect),
  * computing scale-invariant hand geometry (openness, finger extension),
  * detecting the gestures that ISN'T in MediaPipe's built-in set — the OK
    sign, the Korean finger heart, and the two-handed heart — from raw
    landmark geometry,
  * raising edge-triggered events so an action fires once when a signal
    starts, not every frame it's held.

The vocabulary the rest of the app sees is the "signal" — a canonical
lowercase name (``peace``, ``ok``, ``finger_heart``, ``heart_hands``, ...)
that merges MediaPipe's classes with the custom detectors below, so main.py
never has to care which of the two produced a given pose.
"""

import math

# MediaPipe hand landmark indices (21 points per hand)
WRIST = 0
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_PIP = 10
MIDDLE_TIP = 12
RING_PIP = 14
RING_TIP = 16
PINKY_MCP = 17
PINKY_PIP = 18
PINKY_TIP = 20

# Thumb-tip-to-index-tip distance, expressed as a ratio of hand size, at
# which the two are considered "touching". Two thresholds, because a single
# one right at the boundary flickers on and off every other frame.
CONTACT_ENTER = 0.38
CONTACT_EXIT = 0.58

# How close the two hands' matching fingertips must be — again relative to
# hand size — before it reads as a 🫶 heart rather than two loose hands.
HEART_PAIR_TOL = 1.15

GESTURE_HOLD_FRAMES = 3  # frames a raw signal must repeat before it "counts"
TRAIL_LENGTH = 18

# Palm-hold shutter: an open palm held roughly still for this long fires a
# capture. ✌️ used to be the shutter; it drives Soft Focus now.
PALM_HOLD_SECONDS = 1.8
PALM_HOLD_DRIFT = 0.14  # allowed wander, as a ratio of hand size

MP_TO_SIGNAL = {
    "Open_Palm": "open_palm",
    "Closed_Fist": "fist",
    "Pointing_Up": "point",
    "Victory": "peace",
    "Thumb_Up": "thumb_up",
    "Thumb_Down": "thumb_down",
    "ILoveYou": "ily",
    "None": "none",
}

SIGNAL_LABELS = {
    "none": "NO SIGNAL",
    "open_palm": "OPEN PALM",
    "fist": "CLOSED FIST",
    "point": "POINTING",
    "peace": "SOFT FOCUS",
    "thumb_up": "HYPE",
    "thumb_down": "RAIN MOOD",
    "ok": "GWENCHANA",
    "finger_heart": "FINGER HEART",
    "heart_hands": "I LOVE U",
    "ily": "STAR SHOWER",
    "double_palm": "PORTAL",
}


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _centroid(landmarks):
    xs = [p[0] for p in landmarks]
    ys = [p[1] for p in landmarks]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _finger_states(landmarks, wrist, scale):
    """Which fingers are extended, without caring how the hand is rotated.

    For the four fingers the test is orientation-free: an extended finger
    puts its tip further from the wrist than its own middle joint. The thumb
    folds sideways rather than curling, so it gets its own test — how far the
    tip sits from the index knuckle, relative to hand size.
    """

    def straight(pip, tip):
        return _dist(landmarks[tip], wrist) > _dist(landmarks[pip], wrist) * 1.12

    return {
        "thumb": _dist(landmarks[THUMB_TIP], landmarks[INDEX_MCP]) / scale > 0.85,
        "index": straight(INDEX_PIP, INDEX_TIP),
        "middle": straight(MIDDLE_PIP, MIDDLE_TIP),
        "ring": straight(RING_PIP, RING_TIP),
        "pinky": straight(PINKY_PIP, PINKY_TIP),
    }


def _analyse_hand(hand, canvas_w, canvas_h):
    """Everything derivable from one hand's 21 landmarks, in one pass."""
    landmarks = hand["landmarks"]  # list of [x, y, z], normalized 0-1
    wrist = landmarks[WRIST]
    scale = max(_dist(wrist, landmarks[MIDDLE_MCP]), 1e-4)

    cx, cy = _centroid(landmarks)

    # "Openness": how far the fingertips sit from the wrist, relative to
    # hand size — near 0 for a fist, near 1 for a fully open palm. Used to
    # modulate effects continuously, on top of the discrete signal.
    tip_ids = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
    openness_raw = sum(_dist(landmarks[i], wrist) for i in tip_ids) / (4 * scale)

    return {
        "gesture": hand.get("gesture") or "None",
        "handedness": hand.get("handedness") or "Unknown",
        "landmarks": landmarks,
        "scale": scale,
        "fingers": _finger_states(landmarks, wrist, scale),
        "contact": _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / scale,
        "openness": max(0.0, min(2.2, openness_raw)) / 2.2,
        "center": (cx * canvas_w, cy * canvas_h),
        "center_n": (cx, cy),
        "index_tip": (landmarks[INDEX_TIP][0] * canvas_w, landmarks[INDEX_TIP][1] * canvas_h),
        "thumb_tip": (landmarks[THUMB_TIP][0] * canvas_w, landmarks[THUMB_TIP][1] * canvas_h),
        "pinch_point": (
            (landmarks[THUMB_TIP][0] + landmarks[INDEX_TIP][0]) / 2 * canvas_w,
            (landmarks[THUMB_TIP][1] + landmarks[INDEX_TIP][1]) / 2 * canvas_h,
        ),
    }


def is_heart_hands(a, b):
    """🫶 — two hands forming one heart.

    Geometrically that's: the thumb tips meet at the heart's bottom point,
    the index tips meet at the dip along the top, and the index pair sits
    above the thumb pair. Checking both pairs (rather than just "hands are
    near each other") is what keeps a casual two-handed wave from firing it.
    """
    la, lb = a["landmarks"], b["landmarks"]
    scale = (a["scale"] + b["scale"]) / 2.0

    thumb_gap = _dist(la[THUMB_TIP], lb[THUMB_TIP]) / scale
    index_gap = _dist(la[INDEX_TIP], lb[INDEX_TIP]) / scale
    if thumb_gap > HEART_PAIR_TOL or index_gap > HEART_PAIR_TOL:
        return False

    # y grows downward in normalized image space, so "above" is a smaller y.
    index_y = (la[INDEX_TIP][1] + lb[INDEX_TIP][1]) / 2
    thumb_y = (la[THUMB_TIP][1] + lb[THUMB_TIP][1]) / 2
    return index_y < thumb_y


def _resolve_signal(primary, secondary, contact_active):
    """Merge MediaPipe's class with the custom detectors into one name.

    Order matters: two-hand poses outrank one-hand ones, and a thumb/index
    contact outranks whatever MediaPipe made of the hand (it usually reports
    ``None`` for both the OK sign and a finger heart).
    """
    if secondary is not None:
        if is_heart_hands(primary, secondary):
            return "heart_hands"
        if primary["gesture"] == "Open_Palm" and secondary["gesture"] == "Open_Palm":
            return "double_palm"

    if contact_active:
        fingers = primary["fingers"]
        # 👌 keeps the last three fingers up; 🫰 curls them in. That single
        # difference is the whole distinction between the two.
        if fingers["middle"] and fingers["ring"] and fingers["pinky"]:
            return "ok"
        return "finger_heart"

    return MP_TO_SIGNAL.get(primary["gesture"], "none")


_EMPTY = {
    "present": False,
    "two_hands": False,
    "signal": "none",
    "gesture": "None",
    "label": SIGNAL_LABELS["none"],
    "center": None,
    "index_tip": None,
    "pinch_point": None,
    "heart_point": None,
    "scale": 0.0,
    "openness": 0.0,
    "contact": False,
    "hands": [],
}


class HandTracker:
    """Holds just enough memory across frames to debounce the classifier and
    detect transitions — signal just changed, palm has been held still long
    enough to fire the shutter — instead of re-firing an action every single
    frame a pose is held."""

    def __init__(self):
        self.contact = False
        self.last_signal = "none"
        self.stable_signal = "none"
        self.streak = 0
        self.trail = []
        self.palm_since = None
        self.palm_anchor = None
        self.palm_armed = True

    def palm_hold_progress(self, now):
        """0..1 — how far through the palm-hold shutter the current pose is.
        Rendered as a filling ring so the wait has visible feedback."""
        if self.palm_since is None or not self.palm_armed:
            return 0.0
        return max(0.0, min(1.0, (now - self.palm_since) / PALM_HOLD_SECONDS))

    def _reset_palm_hold(self):
        self.palm_since = None
        self.palm_anchor = None

    def _palm_hold_event(self, signal, primary, now):
        """Open palm, held roughly still, fires the shutter once. It re-arms
        only after the palm goes away, so holding the pose can't machine-gun
        the photobooth."""
        if signal != "open_palm" or primary is None:
            self._reset_palm_hold()
            self.palm_armed = True
            return None

        cx, cy = primary["center_n"]
        if self.palm_anchor is None:
            self.palm_anchor = (cx, cy)
            self.palm_since = now
            return None

        drift = _dist((cx, cy), self.palm_anchor) / primary["scale"]
        if drift > PALM_HOLD_DRIFT:
            self.palm_anchor = (cx, cy)
            self.palm_since = now
            return None

        if self.palm_armed and (now - self.palm_since) >= PALM_HOLD_SECONDS:
            self.palm_armed = False
            self._reset_palm_hold()
            return ("palm_hold", None)
        return None

    def process(self, frame, canvas_w, canvas_h):
        """frame is the plain dict handTracker.js hands to Python each frame:
        {"hands": [...], "t": ...}. Returns a dict describing everything this
        frame needs to render and react to."""
        events = []
        hands_raw = frame.get("hands") or []
        now = (frame.get("t") or 0.0) / 1000.0

        if not hands_raw or not canvas_w or not canvas_h:
            self.streak = 0
            self.last_signal = "none"
            self.contact = False
            self._reset_palm_hold()
            self.palm_armed = True
            if self.stable_signal != "none":
                self.stable_signal = "none"
                events.append(("signal_changed", "none"))
            out = dict(_EMPTY)
            out["events"] = events
            out["trail"] = self.trail
            return out

        hands = [_analyse_hand(h, canvas_w, canvas_h) for h in hands_raw[:2]]
        primary = hands[0]
        secondary = hands[1] if len(hands) > 1 else None

        # Hysteresis on the thumb/index contact that both 👌 and 🫰 hang off.
        contact_ratio = primary["contact"]
        self.contact = (
            contact_ratio < CONTACT_EXIT if self.contact else contact_ratio < CONTACT_ENTER
        )

        raw_signal = _resolve_signal(primary, secondary, self.contact)

        if raw_signal == self.last_signal:
            self.streak += 1
        else:
            self.streak = 1
        self.last_signal = raw_signal

        if self.streak >= GESTURE_HOLD_FRAMES and raw_signal != self.stable_signal:
            self.stable_signal = raw_signal
            events.append(("signal_changed", raw_signal))

        hold = self._palm_hold_event(self.stable_signal, primary, now)
        if hold:
            events.append(hold)

        self.trail.append(primary["index_tip"])
        if len(self.trail) > TRAIL_LENGTH:
            self.trail.pop(0)

        heart_point = None
        if self.stable_signal == "heart_hands" and secondary is not None:
            heart_point = _mid(primary["pinch_point"], secondary["pinch_point"])

        return {
            "present": True,
            "two_hands": secondary is not None,
            "signal": self.stable_signal,
            "raw_signal": raw_signal,
            "gesture": primary["gesture"],
            "label": SIGNAL_LABELS.get(self.stable_signal, self.stable_signal.upper()),
            "center": primary["center"],
            "index_tip": primary["index_tip"],
            "pinch_point": primary["pinch_point"],
            "heart_point": heart_point,
            "scale": primary["scale"],
            "openness": primary["openness"],
            "contact": self.contact,
            "hands": hands,
            "events": events,
            "trail": list(self.trail),
        }

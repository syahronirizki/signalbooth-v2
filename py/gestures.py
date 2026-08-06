"""
gestures.py — turns the raw per-frame data from handTracker.js into
something the rest of the app can act on.

MediaPipe's GestureRecognizer already classifies the standard gesture set
(Open_Palm, Closed_Fist, Victory, Thumb_Up, Pointing_Up, ...) in JS, because
that needs the trained model running at WASM speed. This module is
everything downstream of that: turning noisy per-frame classifications into
a *stable* gesture (debounced, so one flickery frame can't fire an effect),
computing scale-invariant hand geometry, detecting the one gesture that
ISN'T in MediaPipe's built-in set (a pinch), and raising edge-triggered
events so an action fires once when a gesture starts, not every frame it's
held.
"""

import math

# MediaPipe hand landmark indices (21 points per hand)
WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9

PINCH_ENTER = 0.055  # normalized thumb-to-index distance that starts a pinch
PINCH_EXIT = 0.08  # a wider exit threshold avoids flicker right at the edge
GESTURE_HOLD_FRAMES = 3  # frames a raw gesture must repeat before it "counts"
TRAIL_LENGTH = 18


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _centroid(landmarks):
    xs = [p[0] for p in landmarks]
    ys = [p[1] for p in landmarks]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _display_label(gesture, pinching):
    if pinching:
        return "PINCH"
    names = {
        "Open_Palm": "OPEN PALM",
        "Closed_Fist": "CLOSED FIST",
        "Victory": "PEACE SIGN",
        "Thumb_Up": "THUMBS UP",
        "Thumb_Down": "THUMBS DOWN",
        "Pointing_Up": "POINTING",
        "ILoveYou": "I LOVE YOU",
        "None": "NO SIGNAL",
    }
    return names.get(gesture, gesture.upper().replace("_", " "))


class HandTracker:
    """Holds just enough memory across frames to debounce the classifier
    and detect transitions — pinch just started, gesture just changed —
    instead of re-firing an action every single frame a pose is held."""

    def __init__(self):
        self.pinching = False
        self.last_raw_gesture = "None"
        self.stable_gesture = "None"
        self.gesture_streak = 0
        self.trail = []

    def process(self, frame, canvas_w, canvas_h):
        """frame is the plain dict hand tracker.js hands to Python each
        frame: {"hands": [...], "t": ...}. Returns a dict describing
        everything this frame needs to render and react to."""
        events = []
        hands = frame.get("hands") or []

        if not hands or not canvas_w or not canvas_h:
            self.gesture_streak = 0
            self.stable_gesture = "None"
            return {
                "present": False,
                "gesture": "None",
                "label": "NO SIGNAL",
                "center": None,
                "index_tip": None,
                "scale": 0.0,
                "openness": 0.0,
                "pinching": False,
                "events": events,
                "trail": self.trail,
            }

        hand = hands[0]
        landmarks = hand["landmarks"]  # list of [x, y, z], normalized 0-1
        raw_gesture = hand.get("gesture") or "None"

        if raw_gesture == self.last_raw_gesture:
            self.gesture_streak += 1
        else:
            self.gesture_streak = 1
        self.last_raw_gesture = raw_gesture

        if self.gesture_streak >= GESTURE_HOLD_FRAMES and raw_gesture != self.stable_gesture:
            events.append(("gesture_changed", raw_gesture))
            self.stable_gesture = raw_gesture

        wrist = landmarks[WRIST]
        middle_mcp = landmarks[MIDDLE_MCP]
        hand_scale = max(_dist(wrist, middle_mcp), 1e-4)

        pinch_dist = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / hand_scale
        was_pinching = self.pinching
        self.pinching = (pinch_dist < PINCH_EXIT) if self.pinching else (pinch_dist < PINCH_ENTER)
        if self.pinching and not was_pinching:
            events.append(("pinch_start", None))

        cx, cy = _centroid(landmarks)
        center_px = (cx * canvas_w, cy * canvas_h)
        index_tip_px = (landmarks[INDEX_TIP][0] * canvas_w, landmarks[INDEX_TIP][1] * canvas_h)

        # "Openness": how far the fingertips sit from the wrist, relative to
        # hand size — near 0 for a fist, near 1 for a fully open palm. Used
        # to modulate effects continuously, on top of the discrete gesture.
        tip_ids = (8, 12, 16, 20)
        openness_raw = sum(_dist(landmarks[i], wrist) for i in tip_ids) / (4 * hand_scale)
        openness = max(0.0, min(2.2, openness_raw)) / 2.2

        self.trail.append(index_tip_px)
        if len(self.trail) > TRAIL_LENGTH:
            self.trail.pop(0)

        return {
            "present": True,
            "gesture": self.stable_gesture,
            "raw_gesture": raw_gesture,
            "label": _display_label(self.stable_gesture, self.pinching),
            "center": center_px,
            "index_tip": index_tip_px,
            "scale": hand_scale,
            "openness": openness,
            "pinching": self.pinching,
            "events": events,
            "trail": list(self.trail),
        }

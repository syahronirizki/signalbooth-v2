/**
 * handTracker.js
 *
 * This file's job is deliberately narrow: get the webcam running and run
 * MediaPipe's GestureRecognizer over it. That's the one part of this app
 * that has to be JS/WASM — there's no in-browser Python equivalent fast
 * enough for 30-60fps hand tracking. Every other decision (which effect
 * to show, how to draw it, the photobooth flow, localStorage) happens in
 * Python, in py/main.py, which this file hands a plain frame object to
 * once per video frame via window.__sbFrameCallback.
 */

import {
  GestureRecognizer,
  FilesetResolver,
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3";

const WASM_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm";
const MODEL_URL = "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/gesture_recognizer.task";

const video = document.getElementById("camera-feed");

let gestureRecognizer = null;
let lastVideoTime = -1;
let running = false;

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

async function init() {
  try {
    setBootStatus("loading gesture model…");
    const vision = await FilesetResolver.forVisionTasks(WASM_URL);
    gestureRecognizer = await GestureRecognizer.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: MODEL_URL,
        delegate: "GPU",
      },
      runningMode: "VIDEO",
      numHands: 2,
    });

    setBootStatus("requesting camera…");
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
      audio: false,
    });
    video.srcObject = stream;

    video.addEventListener("loadeddata", () => {
      running = true;
      window.dispatchEvent(
        new CustomEvent("signalbooth:ready", {
          detail: { width: video.videoWidth, height: video.videoHeight },
        })
      );
      requestAnimationFrame(predictLoop);
    });
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

function predictLoop() {
  if (!running) return;

  if (video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;

    const result = gestureRecognizer.recognizeForVideo(video, performance.now());

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

    window.__sbFrame = { hands, t: performance.now() };

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

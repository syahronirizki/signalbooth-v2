/**
 * handTracker.js
 *
 * This file's job is deliberately narrow: run the webcam and MediaPipe over
 * it. GestureRecognizer reads hands every frame; ImageSegmenter (the selfie
 * model) finds where the person is, but only while a virtual background is
 * on. That's the part of this app that has to be JS/WASM — there's no
 * in-browser Python equivalent fast enough for 30-60fps. Every other
 * decision (effects, backgrounds, drawing, the photobooth, localStorage)
 * happens in Python, in py/main.py, which this file hands a plain frame
 * object to once per video frame via window.__sbFrameCallback.
 *
 * The rest of the contract with Python, all on `window`:
 *   __sbSegment         Python sets true while a background is selected
 *   __sbMask            canvas whose alpha means "this pixel is you"
 *   __sbSegmenterState  "idle" | "loading" | "ready" | "failed"
 *   __sbSetFacing(f)    async; "user" | "environment"; resolves true/false
 *   __sbCamera          the latest signalbooth:ready detail, for a Python
 *                       that boots after the event already fired
 */

import {
  GestureRecognizer,
  ImageSegmenter,
  FilesetResolver,
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3";

const WASM_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm";
const MODEL_URL = "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/gesture_recognizer.task";
const SEGMENTER_URL =
  "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite";

const video = document.getElementById("camera-feed");

let vision = null;
let gestureRecognizer = null;
let segmenter = null;
let stream = null;
let facing = "user";
let lastVideoTime = -1;
let running = false;

const maskCanvas = document.createElement("canvas");
const maskCtx = maskCanvas.getContext("2d");
let maskImage = null;

window.__sbSegment ??= false; // Python may have set this first; don't clobber it
window.__sbMask = null;
window.__sbSegmenterState = "idle"; // loaded on first use: most sessions never pick a background

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

async function initSegmenter() {
  window.__sbSegmenterState = "loading";
  try {
    segmenter = await ImageSegmenter.createFromOptions(vision, {
      baseOptions: { modelAssetPath: SEGMENTER_URL, delegate: "GPU" },
      runningMode: "VIDEO",
      outputCategoryMask: false,
      outputConfidenceMasks: true,
    });
    window.__sbSegmenterState = "ready";
  } catch (err) {
    console.error("Signalbooth: background segmenter unavailable", err);
    window.__sbSegmenterState = "failed";
  }
}

async function startCamera(nextFacing, strict = false) {
  // Phones can't always hold two cameras open at once, so release the old
  // stream before asking for the new one.
  const previousId = stream?.getVideoTracks()[0]?.getSettings().deviceId;
  stream?.getTracks().forEach((track) => track.stop());
  // Always ask in the sensor's own landscape terms. Phones rotate the frames
  // themselves and hand back a portrait stream when held upright; asking for
  // portrait sizes instead makes them crop a narrow slice out of the sensor,
  // which is the "zoomed in" look.
  const size = { width: { ideal: 1280 }, height: { ideal: 720 } };
  try {
    // A switch must really land on the other camera; a plain facingMode is
    // only a preference and quietly hands back whatever camera exists.
    stream = await navigator.mediaDevices.getUserMedia({
      video: { ...size, facingMode: strict ? { exact: nextFacing } : nextFacing },
      audio: false,
    });
  } catch (err) {
    // Some phones don't label their cameras' facing at all, so `exact` can
    // never match. Any camera other than the one just used is the flip.
    if (!strict || !previousId) throw err;
    const other = (await navigator.mediaDevices.enumerateDevices()).find(
      (d) => d.kind === "videoinput" && d.deviceId && d.deviceId !== previousId
    );
    if (!other) throw err;
    stream = await navigator.mediaDevices.getUserMedia({
      video: { ...size, deviceId: { exact: other.deviceId } },
      audio: false,
    });
  }
  facing = nextFacing;
  video.srcObject = stream;
  video.play().catch(() => {}); // iOS doesn't always autoplay a swapped srcObject
}

window.__sbSetFacing = async (nextFacing) => {
  const previous = facing;
  try {
    await startCamera(nextFacing, true);
    return true;
  } catch (err) {
    console.error("Signalbooth: camera switch failed", err);
    try {
      await startCamera(previous); // put the old camera back rather than go dark
    } catch (restoreErr) {
      console.error("Signalbooth: couldn't restore the previous camera", restoreErr);
    }
    return false;
  }
};

async function countCameras() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === "videoinput").length;
  } catch {
    return 1;
  }
}

async function loadGestureModel() {
  vision = await FilesetResolver.forVisionTasks(WASM_URL);
  gestureRecognizer = await GestureRecognizer.createFromOptions(vision, {
    baseOptions: {
      modelAssetPath: MODEL_URL,
      delegate: "GPU",
    },
    runningMode: "VIDEO",
    numHands: 2,
  });
}

async function init() {
  // Attached before the camera starts so the first loadeddata can't be
  // missed. Fires again after every camera switch, so Python can resize.
  video.addEventListener("loadeddata", async () => {
    const cameras = await countCameras();
    window.__sbCamera = { width: video.videoWidth, height: video.videoHeight, cameras };
    window.dispatchEvent(new CustomEvent("signalbooth:ready", { detail: window.__sbCamera }));
    if (!running) {
      running = true;
      requestAnimationFrame(predictLoop);
    }
  });

  try {
    // The camera and the ~10 MB of model download side by side: you see
    // yourself as soon as the camera's up, and hands join once the model lands.
    setBootStatus("requesting camera…");
    await Promise.all([
      startCamera("user"),
      loadGestureModel().catch((err) => {
        // Without the model there are no gestures, but the camera, the
        // shutter and the dial all still work.
        console.error("Signalbooth: gesture model unavailable", err);
      }),
    ]);
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

function updateMask(now) {
  const result = segmenter.segmentForVideo(video, now);
  try {
    const masks = result.confidenceMasks || [];
    // selfie_segmenter has a single "person" mask; multiclass models list
    // background first. Either way the person is the last one. If a scene
    // ever shows ON you instead of behind you, this model's mask is
    // inverted: use (1 - values[i]) below.
    const mask = masks[masks.length - 1];
    if (!mask) return;
    const values = mask.getAsFloat32Array();
    if (!maskImage || maskImage.width !== mask.width || maskImage.height !== mask.height) {
      maskCanvas.width = mask.width;
      maskCanvas.height = mask.height;
      maskImage = maskCtx.createImageData(mask.width, mask.height);
    }
    const px = maskImage.data;
    for (let i = 0; i < values.length; i++) {
      px[i * 4 + 3] = values[i] * 255; // only alpha matters: Python draws it with destination-in
    }
    maskCtx.putImageData(maskImage, 0, 0);
    window.__sbMask = maskCanvas;
  } finally {
    result.close();
  }
}

function predictLoop() {
  if (!running) return;

  // A new srcObject (camera switch) resets currentTime with no frame decoded
  // yet; feeding that empty frame to MediaPipe breaks its graph for good.
  if (video.readyState >= 2 && video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    const now = performance.now();

    const result = gestureRecognizer ? gestureRecognizer.recognizeForVideo(video, now) : {};

    if (window.__sbSegment && vision && window.__sbSegmenterState === "idle") {
      initSegmenter();
    }
    if (window.__sbSegment && segmenter) {
      try {
        updateMask(now);
      } catch (err) {
        // A segmenter that throws once (a lost GPU context, say) keeps
        // throwing. Drop it so hands and the plain camera carry on; Python
        // sees "failed" and puts the real room back.
        console.error("Signalbooth: segmenter failed, backgrounds off", err);
        segmenter = null;
        window.__sbMask = null;
        window.__sbSegmenterState = "failed";
      }
    }

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

    window.__sbFrame = { hands, t: now };

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

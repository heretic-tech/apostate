// Reads what a page can see about the machine, from the window, two iframes
// and three kinds of worker, and posts it all to the test server.
"use strict";

const params = new URLSearchParams(location.search);
const token = params.get("token");
const crossOrigin = params.get("cross");
self.contextName = "window";

function fromFrame(src) {
  return new Promise((resolve) => {
    const frame = document.createElement("iframe");
    const listener = (event) => {
      if (event.source !== frame.contentWindow) return;
      window.removeEventListener("message", listener);
      resolve(event.data);
    };
    window.addEventListener("message", listener);
    frame.style.width = "320px";
    frame.style.height = "200px";
    frame.src = src;
    document.body.appendChild(frame);
  });
}

function fromWorker() {
  return new Promise((resolve) => {
    const worker = new Worker("worker.js");
    worker.onmessage = (event) => resolve(event.data);
    worker.onerror = (event) => resolve({ error: String(event.message) });
  });
}

function fromSharedWorker() {
  return new Promise((resolve) => {
    const worker = new SharedWorker(`shared-worker.js?token=${token}`);
    worker.port.onmessage = (event) => resolve(event.data);
    worker.onerror = (event) => resolve({ error: String(event.message) });
    worker.port.start();
  });
}

async function fromServiceWorker() {
  const registration = await navigator.serviceWorker.register(`sw.js?token=${token}`);
  const worker = registration.installing || registration.waiting || registration.active;
  if (worker.state !== "activated") {
    await new Promise((resolve) => worker.addEventListener("statechange", () => worker.state === "activated" && resolve()));
  }
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = (event) => resolve(event.data);
    registration.active.postMessage("collect", [channel.port2]);
  });
}

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function canvasHash() {
  const canvas = document.createElement("canvas");
  canvas.width = 280;
  canvas.height = 60;
  const context = canvas.getContext("2d");
  context.fillStyle = "#f60";
  context.fillRect(10, 5, 120, 40);
  context.fillStyle = "#069";
  context.font = "16px Arial";
  context.fillText("Apostate, 0123456789", 4, 30);
  context.strokeStyle = "rgba(102, 204, 0, 0.7)";
  context.beginPath();
  context.arc(200, 30, 20, 0, Math.PI * 2);
  context.stroke();
  return sha256(context.getImageData(0, 0, canvas.width, canvas.height).data.buffer);
}

async function audioHash() {
  const context = new OfflineAudioContext(1, 44100, 44100);
  const oscillator = context.createOscillator();
  oscillator.type = "triangle";
  oscillator.frequency.value = 10000;
  const compressor = context.createDynamicsCompressor();
  oscillator.connect(compressor);
  compressor.connect(context.destination);
  oscillator.start(0);
  const buffer = await context.startRendering();
  return sha256(buffer.getChannelData(0).slice(4500, 5000).buffer);
}

function fontWidths(families) {
  const context = document.createElement("canvas").getContext("2d");
  const text = "mmmmmmmmmmlli1WQ@#&";
  const widths = {};
  for (const family of families) {
    const row = {};
    for (const fallback of ["monospace", "serif", "sans-serif"]) {
      context.font = `72px "${family}", ${fallback}`;
      row[fallback] = context.measureText(text).width;
    }
    widths[family] = row;
  }
  return widths;
}

function visibleFonts(families) {
  const base = fontWidths(["__apostate_no_such_font__"])["__apostate_no_such_font__"];
  const widths = fontWidths(families);
  return families.filter((family) => ["monospace", "serif", "sans-serif"].some((fallback) => widths[family][fallback] !== base[fallback]));
}

function genericWidths() {
  const context = document.createElement("canvas").getContext("2d");
  const text = "The quick brown fox jumps over the lazy dog 0123456789";
  const out = {};
  for (const family of ["serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"]) {
    context.font = `40px ${family}`;
    out[family] = context.measureText(text).width;
  }
  return out;
}

function namedWidth(family) {
  const context = document.createElement("canvas").getContext("2d");
  context.font = `40px "${family}", __apostate_no_such_font__`;
  return context.measureText("The quick brown fox jumps over the lazy dog 0123456789").width;
}

function voices() {
  return new Promise((resolve) => {
    const read = () => speechSynthesis.getVoices().map((voice) => ({ name: voice.name, lang: voice.lang, local: voice.localService, default: voice.default }));
    const now = read();
    if (now.length) return resolve(now);
    speechSynthesis.addEventListener("voiceschanged", () => resolve(read()), { once: true });
    setTimeout(() => resolve(read()), 3000);
  });
}

// Chrome DevTools Protocol clients that enable the Runtime domain serialise
// console arguments, which reads an error's stack. A page sees that read.
function consoleProbe() {
  let read = false;
  const error = new Error("probe");
  Object.defineProperty(error, "stack", { configurable: true, get() { read = true; return ""; } });
  console.debug(error);
  return new Promise((resolve) => setTimeout(() => resolve(read), 300));
}

const AUTOMATION_GLOBALS = [
  "__playwright__binding__", "__pwInitScripts", "__playwright_builtins__", "playwright", "_playwrightInstance",
  "__puppeteer_evaluation_script__", "puppeteer", "callPhantom", "_phantom", "phantom", "__nightmare",
  "domAutomation", "domAutomationController", "_Selenium_IDE_Recorder", "_selenium", "calledSelenium",
  "__webdriver_evaluate", "__selenium_evaluate", "__webdriver_script_function", "__webdriver_script_func",
  "__webdriver_script_fn", "__fxdriver_evaluate", "__driver_unwrapped", "__webdriver_unwrapped",
  "__driver_evaluate", "__selenium_unwrapped", "__fxdriver_unwrapped", "__lastWatirAlert", "__lastWatirConfirm",
  "__lastWatirPrompt", "_WEBDRIVER_ELEM_CACHE", "webdriver", "Cypress", "__cypress", "__stagehand",
];

async function collectWindow(fonts) {
  const media = window.matchMedia.bind(window);
  const devices = await attempt(async () => (await navigator.mediaDevices.enumerateDevices()).map((device) => device.kind));
  const battery = await attempt(async () => {
    const value = await navigator.getBattery();
    return { charging: value.charging, level: value.level, chargingTime: value.chargingTime, dischargingTime: value.dischargingTime };
  });
  const keyboard = await attempt(async () => {
    const map = await navigator.keyboard.getLayoutMap();
    return { size: map.size, KeyQ: map.get("KeyQ"), KeyZ: map.get("KeyZ"), Semicolon: map.get("Semicolon") };
  });
  const audio = new AudioContext();
  const notification = await attempt(async () => (await navigator.permissions.query({ name: "notifications" })).state);
  return {
    screen: {
      width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight,
      availLeft: screen.availLeft, availTop: screen.availTop, colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
      devicePixelRatio: window.devicePixelRatio,
      outerWidth: window.outerWidth, outerHeight: window.outerHeight, innerWidth: window.innerWidth, innerHeight: window.innerHeight,
      screenX: window.screenX, screenY: window.screenY,
    },
    media: {
      dark: media("(prefers-color-scheme: dark)").matches,
      pointerFine: media("(pointer: fine)").matches,
      hover: media("(hover: hover)").matches,
      gamutP3: media("(color-gamut: p3)").matches,
    },
    audio: { sampleRate: audio.sampleRate, baseLatency: audio.baseLatency, maxChannelCount: audio.destination.maxChannelCount },
    devices,
    battery,
    keyboard,
    voices: await withTimeout(voices(), 4000, "voices"),
    fonts: { visible: visibleFonts(fonts), generic: genericWidths() },
    plugins: [...navigator.plugins].map((plugin) => plugin.name),
    mimeTypes: [...navigator.mimeTypes].map((type) => type.type),
    pdfViewerEnabled: navigator.pdfViewerEnabled,
    chrome: { type: typeof window.chrome, runtime: typeof (window.chrome && window.chrome.runtime) },
    automationGlobals: AUTOMATION_GLOBALS.filter((name) => name !== "webdriver" && name in window),
    windowKeys: Object.getOwnPropertyNames(window).filter((name) => /^(cdc_|\$cdc_|__)/.test(name)),
    consoleStackRead: await consoleProbe(),
    notification: { permission: typeof Notification !== "undefined" ? Notification.permission : null, query: notification },
    canvas: [await canvasHash(), await canvasHash()],
    audioHash: [await attempt(audioHash), await attempt(audioHash)],
  };
}

async function main() {
  const fonts = (params.get("fonts") || "").split("|").filter(Boolean);
  const result = { token };
  result.window = await collectContext();
  result.page = await attempt(() => collectWindow(fonts));
  if (params.get("generic")) {
    result.page.fonts.named = Object.fromEntries(params.get("generic").split("|").map((family) => [family, namedWidth(family)]));
  }
  result.sameOriginFrame = await withTimeout(fromFrame(`frame.html?token=${token}&context=same-origin-frame`), 15000, "same-origin frame");
  if (crossOrigin) result.crossOriginFrame = await withTimeout(fromFrame(`${crossOrigin}/frame.html?token=${token}&context=cross-origin-frame`), 15000, "cross-origin frame");
  result.worker = await withTimeout(fromWorker(), 15000, "worker");
  result.sharedWorker = await withTimeout(fromSharedWorker(), 15000, "shared worker");
  result.serviceWorker = await withTimeout(attempt(fromServiceWorker), 15000, "service worker");
  await fetch(`/result?token=${token}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(result) });
  document.title = "done";
}

main().catch((error) => fetch(`/result?token=${token}`, { method: "POST", body: JSON.stringify({ token, error: String(error && error.stack || error) }) }));

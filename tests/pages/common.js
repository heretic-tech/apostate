// Values every context can read: the window, iframes and all three kinds of
// worker load this file and call collectContext().
"use strict";

self.withTimeout = function (promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((resolve) => setTimeout(() => resolve({ error: `timeout: ${label}` }), ms)),
  ]);
};

self.attempt = async function (fn) {
  try {
    return await fn();
  } catch (error) {
    return { error: String(error && error.message ? error.message : error) };
  }
};

function newCanvas() {
  return typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(16, 16) : document.createElement("canvas");
}

function collectWebGL() {
  const gl1 = newCanvas().getContext("webgl");
  const gl = newCanvas().getContext("webgl2") || gl1;
  if (!gl) return null;
  const extensions = new Set([...(gl.getSupportedExtensions() || []), ...((gl1 && gl1.getSupportedExtensions()) || [])]);
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const limits = {};
  for (const name of [
    "MAX_TEXTURE_SIZE", "MAX_CUBE_MAP_TEXTURE_SIZE", "MAX_RENDERBUFFER_SIZE", "MAX_VERTEX_ATTRIBS",
    "MAX_VERTEX_UNIFORM_VECTORS", "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_VARYING_VECTORS",
    "MAX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "MAX_COMBINED_TEXTURE_IMAGE_UNITS",
    "MAX_3D_TEXTURE_SIZE", "MAX_ARRAY_TEXTURE_LAYERS", "MAX_COLOR_ATTACHMENTS", "MAX_DRAW_BUFFERS",
    "MAX_SAMPLES", "MAX_UNIFORM_BLOCK_SIZE", "MAX_UNIFORM_BUFFER_BINDINGS", "MAX_VERTEX_UNIFORM_COMPONENTS",
    "MAX_FRAGMENT_UNIFORM_COMPONENTS", "MAX_VERTEX_OUTPUT_COMPONENTS", "MAX_FRAGMENT_INPUT_COMPONENTS",
    "UNIFORM_BUFFER_OFFSET_ALIGNMENT",
  ]) {
    if (gl[name] === undefined) continue;
    const value = gl.getParameter(gl[name]);
    limits[name] = typeof value === "number" ? value : value === null ? null : Number(value);
  }
  const viewport = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
  limits.MAX_VIEWPORT_DIMS_WIDTH = viewport[0];
  limits.MAX_VIEWPORT_DIMS_HEIGHT = viewport[1];
  const precisions = {};
  for (const shader of ["VERTEX_SHADER", "FRAGMENT_SHADER"]) {
    for (const kind of ["LOW_FLOAT", "MEDIUM_FLOAT", "HIGH_FLOAT", "LOW_INT", "MEDIUM_INT", "HIGH_INT"]) {
      const format = gl.getShaderPrecisionFormat(gl[shader], gl[kind]);
      precisions[`${shader}.${kind}`] = [format.rangeMin, format.rangeMax, format.precision];
    }
  }
  return {
    version: gl.getParameter(gl.VERSION),
    vendor: info ? gl.getParameter(info.UNMASKED_VENDOR_WEBGL) : null,
    renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
    extensions: [...extensions].sort(),
    limits,
    precisions,
  };
}

async function collectWebGPU() {
  if (!self.navigator.gpu) return { available: false };
  const adapter = await self.navigator.gpu.requestAdapter();
  if (!adapter) return { available: true, adapter: null };
  const info = adapter.info || {};
  const limits = {};
  for (const name in adapter.limits) limits[name] = adapter.limits[name];
  return {
    available: true,
    vendor: info.vendor,
    architecture: info.architecture,
    subgroupMinSize: info.subgroupMinSize,
    subgroupMaxSize: info.subgroupMaxSize,
    features: [...adapter.features].sort(),
    limits,
  };
}

self.collectContext = async function () {
  const nav = self.navigator;
  const zone = Intl.DateTimeFormat().resolvedOptions();
  const out = {
    userAgent: nav.userAgent,
    appVersion: nav.appVersion,
    platform: nav.platform,
    language: nav.language,
    languages: [...nav.languages],
    hardwareConcurrency: nav.hardwareConcurrency,
    deviceMemory: nav.deviceMemory,
    webdriver: nav.webdriver,
    timeZone: zone.timeZone,
    locale: zone.locale,
    offsetJanuary: new Date(2026, 0, 15).getTimezoneOffset(),
    offsetJuly: new Date(2026, 6, 15).getTimezoneOffset(),
    dateString: new Date(Date.UTC(2026, 0, 15, 12)).toLocaleString(),
  };
  if (nav.userAgentData) {
    out.uaData = { brands: nav.userAgentData.brands, mobile: nav.userAgentData.mobile, platform: nav.userAgentData.platform };
    out.uaHigh = await attempt(() => nav.userAgentData.getHighEntropyValues([
      "architecture", "bitness", "formFactors", "fullVersionList", "model", "platformVersion", "uaFullVersion", "wow64",
    ]));
  }
  if (nav.connection) {
    out.connection = { effectiveType: nav.connection.effectiveType, rtt: nav.connection.rtt, downlink: nav.connection.downlink, saveData: nav.connection.saveData };
  }
  out.webgl = await attempt(collectWebGL);
  out.webgpu = await withTimeout(attempt(collectWebGPU), 8000, "webgpu");
  out.headers = await withTimeout(attempt(async () => (await fetch(`/echo?context=${encodeURIComponent(self.contextName || "window")}`)).json()), 5000, "echo");
  return out;
};

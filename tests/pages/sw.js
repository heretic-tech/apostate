importScripts("common.js");
self.contextName = "service-worker";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("message", (event) => {
  const port = event.ports[0];
  event.waitUntil(collectContext().then((values) => port.postMessage(values), (error) => port.postMessage({ error: String(error) })));
});

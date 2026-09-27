importScripts("common.js");
self.contextName = "shared-worker";
self.onconnect = (event) => {
  const port = event.ports[0];
  collectContext().then((values) => port.postMessage(values), (error) => port.postMessage({ error: String(error) }));
};

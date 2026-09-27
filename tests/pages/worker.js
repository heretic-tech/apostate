importScripts("common.js");
self.contextName = "worker";
collectContext().then((values) => postMessage(values), (error) => postMessage({ error: String(error) }));

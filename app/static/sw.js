/* Service worker ExoRenta: caché controlada de estáticos + push notifications. */
const CACHE = "exorenta-v2";
const ASSETS = [
  "/",
  "/assets/styles.css?v=20260904",
  "/assets/app.js?v=20260904",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // la API nunca se cachea

  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp.ok && url.pathname.startsWith("/assets/")) {
          const clon = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clon));
        }
        return resp;
      })
      .catch(() => {
        if (e.request.mode === "navigate") return caches.match("/");
        return caches.match(e.request);
      })
  );
});

self.addEventListener("push", (e) => {
  let datos = { titulo: "ExoRenta", cuerpo: "Tu consulta ha terminado." };
  if (e.data) {
    try { datos = Object.assign(datos, e.data.json()); } catch (_) { /* noop */ }
  }
  e.waitUntil(
    self.registration.showNotification(datos.titulo || "ExoRenta", {
      body: datos.cuerpo || "",
      icon: "/assets/icons/icon-192.png",
      badge: "/assets/icons/icon-192.png",
      tag: datos.tag || "consulta",
      data: { url: datos.url || "/panel" },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const destino = (e.notification.data && e.notification.data.url) || "/panel";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
      for (const w of ws) {
        if ("focus" in w) { w.navigate(destino); return w.focus(); }
      }
      return self.clients.openWindow(destino);
    })
  );
});
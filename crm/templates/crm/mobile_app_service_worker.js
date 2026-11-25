/* Clinical APP: network only. Never store authenticated pages or retry writes. */
var APP_HOME = "{{ app_home|escapejs }}";
var APP_START_TIMEOUT = 20000;

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

function recoveryResponse() {
  // All content is static except a server-generated, escaped canonical path.
  // No patient, session, query, form or notification data enters this page.
  var home = APP_HOME.replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
  var html = '<!doctype html><html lang="es"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">' +
    '<meta name="theme-color" content="#12324a"><title>Recuperar la app</title>' +
    '<style>body{margin:0;padding:48px 22px;background:#f2f7fa;color:#14283b;' +
    'font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}' +
    'main{max-width:440px;margin:0 auto;padding:26px;background:#fff;border-radius:22px;' +
    'border:1px solid #dbe6ed}h1{font-size:25px;margin:0 0 14px}' +
    'a{display:block;background:#185d68;color:#fff;text-align:center;padding:15px;' +
    'border-radius:14px;margin-top:24px;text-decoration:none;font-weight:700}' +
    'small{display:block;margin-top:20px;color:#526775}</style></head><body>' +
    '<main><h1>No se pudo abrir la app</h1><p>La conexión o el servidor no respondieron ' +
    'a tiempo. Revisá tu conexión y volvé a intentar.</p><a href="' + home + '">' +
    'Volver a intentar</a><small>Esta pantalla no repite guardados ni borra datos. ' +
    'Si continúa el problema, abrí la misma dirección en Safari o en el navegador.</small>' +
    '</main></body></html>';
  return new Response(html, {status: 503, headers: {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff"
  }});
}

function loadAppEntry(request) {
  // The deadline is for this GET only; there is no automatic retry, redirect,
  // cache fallback, or interception of forms, API calls, PDFs and other pages.
  return new Promise(function (resolve) {
    var settled = false;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      resolve(recoveryResponse());
      if (controller) controller.abort();
    }, APP_START_TIMEOUT);
    function finish(response) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(response);
    }
    // Request credentials and redirects stay identical to the navigation.
    Promise.resolve().then(function () {
      return controller ? fetch(request, {signal: controller.signal, cache: "no-store"})
        : fetch(request, {cache: "no-store"});
    }).then(function (response) {
      if (settled) return;
      finish(response.status >= 500 || response.status === 204 || response.status === 205
        ? recoveryResponse() : response);
    }, function () { finish(recoveryResponse()); });
  });
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET" || request.mode !== "navigate") return;
  var url;
  try { url = new URL(request.url); } catch (error) { return; }
  if (url.origin !== self.location.origin || url.pathname !== APP_HOME) return;
  event.respondWith(loadAppEntry(request));
});

// Keep the existing reminder operations independent of startup recovery.
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var data = event.notification.data || {};
  var target = data.url || APP_HOME;
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (clients) {
    var visible = clients.find(function (client) { return "focus" in client; });
    if (visible) { visible.navigate(target); return visible.focus(); }
    return self.clients.openWindow(target);
  }));
});
self.addEventListener("message", function (event) {
  if (!event.data || event.data.type !== "SHOW_APPOINTMENT") return;
  var payload = event.data.payload || {};
  event.waitUntil(self.registration.showNotification(payload.title || "Próxima cita", {
    body: payload.body || "",
    icon: payload.icon || "/static/crm/hospital-mia-app.svg",
    badge: "/static/crm/hospital-mia-app.svg",
    tag: "cita-" + (payload.id || "agenda"),
    data: {url: payload.url || APP_HOME}
  }));
});

/* Service worker de Herramientas — versión __VERSION__ (cambia con cada archivo de la interfaz).
 *
 * Qué hace:
 *  - guarda en caché la interfaz con versión (JS, CSS, íconos) y las dos páginas sin datos de cuenta
 *    (/capturar/ y /instalar/) para trabajar sin conexión;
 *  - NUNCA guarda páginas con datos de una cuenta, respuestas de /sesion/ ni /subidas/;
 *  - sin red, cualquier navegación abre /capturar/ (así la app instalada arranca en modo avión);
 *  - no se activa solo a mitad de una captura: espera a que la persona toque «Actualizar»;
 *  - en Android (Background Sync) sube la cola aunque la app esté cerrada.
 */
"use strict";
const VERSION = "__VERSION__";
const CACHE = "itt-herramientas-" + VERSION;
const PAGINAS = ["/capturar/", "/instalar/"];
const ACTIVOS = __ACTIVOS__;

importScripts(...__SCRIPTS_SW__);

self.addEventListener("install", (evento) => {
  evento.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // addAll es atómico: si algo falla (sin señal al instalar), la instalación se reintenta después.
    await cache.addAll([...PAGINAS, ...ACTIVOS].map((u) => new Request(u, { cache: "reload", credentials: "same-origin" })));
  })());
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil((async () => {
    for (const nombre of await caches.keys()) if (nombre.startsWith("itt-herramientas-") && nombre !== CACHE) await caches.delete(nombre);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (evento) => {
  if (evento.data && evento.data.tipo === "SALTAR_ESPERA") self.skipWaiting();
});

self.addEventListener("fetch", (evento) => {
  const peticion = evento.request;
  if (peticion.method !== "GET") return;
  const url = new URL(peticion.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/subidas/") || url.pathname === "/sesion/" || url.pathname === "/sw.js") return;   // siempre a la red
  if (peticion.mode === "navigate") {
    evento.respondWith(navegar(peticion, url));
    return;
  }
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    evento.respondWith(cacheLuegoRed(peticion));
  }
});

async function navegar(peticion, url) {
  const cache = await caches.open(CACHE);
  if (PAGINAS.includes(url.pathname)) {                       // páginas sin datos de cuenta: red primero, caché si no hay red
    try {
      const respuesta = await fetch(peticion);
      if (respuesta.ok) cache.put(url.pathname, respuesta.clone());
      return respuesta;
    } catch (_) {
      return (await cache.match(url.pathname)) || Response.error();
    }
  }
  try {
    return await fetch(peticion);
  } catch (_) {
    return (await cache.match("/capturar/")) || new Response("Sin conexión", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
}

async function cacheLuegoRed(peticion) {
  const cache = await caches.open(CACHE);
  const guardada = await cache.match(peticion);
  if (guardada) return guardada;
  const respuesta = await fetch(peticion);
  if (respuesta.ok && new URL(peticion.url).pathname.startsWith("/static/")) cache.put(peticion, respuesta.clone());
  return respuesta;
}

// Android/Chrome: Background Sync. iOS Safari no lo tiene: allí se sube mientras la app está abierta.
self.addEventListener("sync", (evento) => {
  if (evento.tag !== "itt-subir") return;
  evento.waitUntil((async () => {
    const abiertas = await self.clients.matchAll({ type: "window" });
    if (abiertas.length) { abiertas.forEach((c) => c.postMessage({ tipo: "subir" })); return; }
    const resumen = await self.ITT.subidor.procesar({});
    if (resumen && resumen.pausada === "red") throw new Error("sin red: reintentar");      // el navegador vuelve a disparar el evento
  })());
});

/* Service worker de Herramientas — versión __VERSION__ (cambia con cada archivo de la interfaz).
 *
 * Qué hace:
 *  - guarda en caché la interfaz con versión (JS, CSS, íconos) y las páginas sin datos de cuenta
 *    (/capturar/, /instalar/ y /cotizaciones/) para trabajar sin conexión;
 *  - NUNCA guarda páginas con datos de una cuenta, respuestas de /sesion/ ni /subidas/ (sí el manifiesto y /perfil.css, que solo
 *    llevan la marca y los colores del perfil, para que la app sin señal salga con los suyos);
 *  - sin red, cualquier navegación abre /capturar/ (así la app instalada arranca en modo avión);
 *  - no se activa solo a mitad de una captura: espera a que la persona toque «Actualizar»;
 *  - en Android (Background Sync) sube la cola aunque la app esté cerrada.
 */
"use strict";
const VERSION = "__VERSION__";
const CACHE = "itt-herramientas-" + VERSION;
const PAGINAS = ["/capturar/", "/instalar/", "/cotizaciones/", "/reportes/", "/croquis/"];
const ACTIVOS = __ACTIVOS__;
const OPCIONALES = ["/manifest.webmanifest", "/manifest.webmanifest?p=tresvizo", "/perfil.css"];

importScripts(...__SCRIPTS_SW__);

self.addEventListener("install", (evento) => {
  evento.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // addAll es atómico: si algo falla (sin señal al instalar), la instalación se reintenta después.
    await cache.addAll([...PAGINAS, ...ACTIVOS].map((u) => new Request(u, { cache: "reload", credentials: "same-origin" })));
    // La marca y los colores (ver redLuegoCache): se guardan ya, para que la primera vez sin señal no salgan sin ellos. Sin sesión,
    // /perfil.css no se puede pedir: eso no impide instalar.
    await Promise.allSettled(OPCIONALES.map(async (u) => {
      const r = await fetch(new Request(u, { cache: "reload", credentials: "same-origin" }));
      if (r.ok) await cache.put(u, r);
    }));
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
  if (url.pathname.startsWith("/static/")) {
    evento.respondWith(cacheLuegoRed(peticion));
    return;
  }
  // El manifiesto (cambia con el perfil: ?p=) y los colores del perfil: red primero y, sin red, lo último que se guardó. Antes el
  // manifiesto nunca se guardaba y /perfil.css no pasaba por aquí: en modo avión cada página fallaba al pedirlos y salía sin los colores
  // de su perfil (WebKit además registraba el error del service worker en cada navegación).
  if (url.pathname === "/manifest.webmanifest" || url.pathname === "/perfil.css") {
    evento.respondWith(redLuegoCache(peticion));
  }
});

async function redLuegoCache(peticion) {
  const cache = await caches.open(CACHE);
  try {
    const respuesta = await fetch(peticion);
    if (respuesta.ok) cache.put(peticion, respuesta.clone());
    return respuesta;
  } catch (_) {
    const guardada = (await cache.match(peticion)) || (await cache.match(peticion, { ignoreSearch: true }));
    if (guardada) return guardada;
    // Nunca guardada (otra cuenta en este teléfono, o se instaló sin sesión): la paleta de fábrica, sin error.
    if (new URL(peticion.url).pathname === "/perfil.css") return new Response("/* sin señal: paleta de fábrica */\n", { headers: { "Content-Type": "text/css; charset=utf-8" } });
    return Response.error();
  }
}

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

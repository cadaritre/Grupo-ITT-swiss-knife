/* CAD · el editor guardado en este equipo (sw.py lo agrega siempre al service worker, antes del manejador general de `fetch`; lo que
 * atiende aquí no llega a los demás: stopImmediatePropagation). docs/cad.md, «El editor guardado en el equipo».
 *
 *  - /cad/editor/<versión>/…: si esa versión está guardada en este equipo (static/herramientas/cad/instalador.js: en la computadora al
 *    abrir un plano, en el teléfono a pedido), sale de su caché; si no, de la red, SIN guardarla (las páginas que no usan CAD nunca bajan
 *    el editor, y el service worker nunca llena estas cachés: las llena la página después de comprobar cada archivo).
 *  - Cada archivo guardado lleva su tamaño (X-TV-Bytes, el del MANIFIESTO.json) y su SHA-256 (X-TV-SHA256), comprobado al guardarlo. Si lo
 *    guardado no mide lo que debe (un disco o un navegador que lo dañó), se sirve de la red y esa versión se borra entera: la página
 *    vuelve a guardarla la próxima vez. Nunca se sirve un archivo a medias. Lo que el teléfono guardó antes de estas cabeceras se sirve
 *    como antes (se comprobó por tamaño al bajarlo) hasta la próxima versión.
 *  - La versión la pide la página (su dirección lleva la versión fijada en el servidor): una página nueva nunca recibe un editor viejo.
 */
const CAD_EDITOR = "itt-cad-editor-";

self.addEventListener("fetch", (evento) => {
  const peticion = evento.request;
  if (peticion.method !== "GET") return;
  const url = new URL(peticion.url);
  if (url.origin !== self.location.origin) return;
  const ed = url.pathname.match(/^\/cad\/editor\/([^/]+)\/(.+)$/);
  if (!ed) return;
  evento.stopImmediatePropagation();
  evento.respondWith(editorGuardado(peticion, ed[1], url));
});

async function editorGuardado(peticion, version, url) {
  const nombre = CAD_EDITOR + version;
  try {
    if (await caches.has(nombre)) {
      // La página del editor se pide con ?embed=1&automation=1: se busca sin la consulta y sin mirar Vary (WebKit lo aplica estricto).
      const guardada = await (await caches.open(nombre)).match(url.pathname, { ignoreVary: true, ignoreSearch: true });
      if (guardada) {
        // Lo guardado antes del 18-11-2026 (CAD del teléfono) no lleva estas cabeceras: se comprobó por tamaño al bajarlo y se sirve igual.
        if (!guardada.headers.has("X-TV-Bytes")) return guardada;
        const cuerpo = await guardada.blob();
        if (cuerpo.size === Number(guardada.headers.get("X-TV-Bytes"))) return new Response(cuerpo, { status: 200, headers: guardada.headers });
        await caches.delete(nombre);                     // dañada: fuera entera (sin su caché la página no la vuelve a usar)
      }
    }
  } catch (_) { /* sin caché: de la red */ }
  return fetch(peticion);
}

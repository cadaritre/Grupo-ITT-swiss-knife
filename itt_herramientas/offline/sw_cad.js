/* CAD sin conexión (solo con TV_CAD_TACTIL encendido; sw.py lo agrega al service worker). Va ANTES del manejador general de `fetch`:
 * lo que atiende aquí no llega al general (stopImmediatePropagation).
 *
 *  - /cad/editor/<versión>/…: si esa versión está descargada en el teléfono («Descargar CAD en este teléfono», static/herramientas/cad/
 *    campo.js), sale de su caché; si no, de la red, SIN guardarla (las páginas que no usan CAD nunca bajan el editor).
 *  - Navegar a /cad/… sin red: la página de CAD sin conexión, si está guardada (lista los planos de este teléfono y los abre).
 *  - /static/…: primero la caché de la página sin conexión (sus scripts y estilos), luego lo de siempre.
 *  - /cad/panel-civil/?v=…: la barra y las hojas de Civil (sin datos de nadie), de su caché si la página las guardó (CAD descargado).
 *  - Nunca guarda planos ni páginas con datos de una cuenta: los planos viven en IndexedDB, de la cuenta, y los maneja la página.
 */
const CAD_EDITOR = "itt-cad-editor-", CAD_PAGINA = "itt-cad-pagina", CAD_CIVIL = "itt-cad-pagina-civil";
const CAD_PLANO = /^\/cad\/([0-9a-f-]{36})\/$/;
// Las respuestas de Django llevan «Vary: Cookie»: lo guardado se busca sin mirar Vary (WebKit lo aplica estricto).

self.addEventListener("fetch", (evento) => {
  const peticion = evento.request;
  if (peticion.method !== "GET") return;
  const url = new URL(peticion.url);
  if (url.origin !== self.location.origin) return;
  const ruta = url.pathname;
  const ed = ruta.match(/^\/cad\/editor\/([^/]+)\/(.+)$/);
  if (ed) {
    evento.stopImmediatePropagation();
    evento.respondWith(editorCad(peticion, ed[1], url));
    return;
  }
  if (peticion.mode === "navigate" && (ruta === "/cad/" || ruta === "/cad/sin-conexion/" || CAD_PLANO.test(ruta))) {
    evento.stopImmediatePropagation();
    evento.respondWith(navegarCad(peticion));
    return;
  }
  if (ruta === "/cad/panel-civil/" && peticion.mode !== "navigate") {
    evento.stopImmediatePropagation();
    evento.respondWith(panelCivil(peticion, url));
    return;
  }
  if (ruta.startsWith("/static/") || (ruta.startsWith("/cad/fuentes/") && peticion.mode !== "navigate")) {
    evento.stopImmediatePropagation();
    evento.respondWith((async () => {
      const guardada = await deCache(CAD_PAGINA, peticion);
      if (guardada) return guardada;
      if (ruta.startsWith("/static/")) return cacheLuegoRed(peticion);
      return fetch(peticion);
    })());
  }
});

// De una caché por nombre, sin crearla si nunca se usó CAD y sin mirar Vary.
async function deCache(nombre, peticion) {
  if (!(await caches.has(nombre))) return undefined;
  return (await caches.open(nombre)).match(peticion, { ignoreVary: true, ignoreSearch: typeof peticion === "string" && peticion.includes("/cad/editor/") });
}

async function editorCad(peticion, version, url) {
  const guardada = await deCache(CAD_EDITOR + version, url.pathname);     // la página se pide con ?embed=1&automation=1
  if (guardada) return guardada;
  return fetch(peticion);
}

// La barra y las hojas de Civil (iguales para todas las cuentas; la dirección lleva su huella): en un teléfono con el CAD descargado la
// página las deja en su propia caché (civil-panel.js) y de ahí salen hasta que cambien.
async function panelCivil(peticion, url) {
  return (await deCache(CAD_CIVIL, url.pathname + url.search)) || fetch(peticion);
}

async function navegarCad(peticion) {
  try {
    return await fetch(peticion);
  } catch (_) {
    const pagina = await deCache(CAD_PAGINA, "/cad/sin-conexion/");
    if (pagina) return pagina;
    return (await (await caches.open(CACHE)).match("/capturar/"))
      || new Response("Sin conexión", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
}

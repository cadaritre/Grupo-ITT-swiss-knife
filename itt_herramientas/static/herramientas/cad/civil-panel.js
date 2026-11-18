/* Civil: su barra y sus hojas (templates/herramientas/cad/civil_panel.html) no vienen en el HTML de cada página del plano. Son iguales para
 * todas las cuentas y todos los planos (~16 KB comprimidas, más de la mitad de la página): llegan aparte, con su huella en la dirección
 * (`/cad/panel-civil/?v=<huella>`, se guardan un año y, en el teléfono con el CAD descargado, salen de su caché), se ponen en la página y
 * después se carga civil.js, que las usa como siempre (docs/cad.md, «Datos móviles»).
 */
(function () {
  "use strict";
  const lugar = document.getElementById("civil-panel");
  if (!lugar) return;
  let intentos = 0;
  // En un teléfono con el CAD descargado, la versión vigente del panel queda en su caché (la lee el service worker, offline/sw_cad.js).
  async function guardar(url, respuesta) {
    try {
      if (!("caches" in window) || !(await caches.has("itt-cad-pagina"))) return;
      const cache = await caches.open("itt-cad-pagina-civil");
      if (await cache.match(url)) return;
      for (const vieja of await cache.keys()) await cache.delete(vieja);
      await cache.put(url, respuesta);
    } catch (_) { /* sin caché: la próxima vez llega de la red o del navegador */ }
  }
  async function cargar() {
    try {
      const r = await fetch(lugar.dataset.url, { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      const copia = r.clone();
      const html = await r.text();
      const t = document.createElement("template");
      t.innerHTML = html;
      lugar.replaceWith(t.content);
      const s = document.createElement("script");
      s.src = lugar.dataset.js;
      document.body.append(s);
      guardar(lugar.dataset.url, copia);
    } catch (_) {
      // Sin red un momento: se vuelve a intentar unas veces; sin Civil el editor sigue igual.
      if (++intentos < 4) setTimeout(cargar, 3000 * intentos);
    }
  }
  cargar();
})();

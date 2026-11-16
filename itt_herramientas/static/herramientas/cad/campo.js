/* CAD en el campo (detrás de TV_CAD_TACTIL): el editor descargado una vez en el teléfono y los planos «Disponibles sin conexión».
 *
 * EL EDITOR EN ESTE TELÉFONO
 * - Solo a pedido («Descargar CAD en este teléfono», ~39 MB) y solo en la app instalada (en iOS el almacenamiento de un sitio que no está
 *   instalado se borra a los 7 días sin uso). Se pide `navigator.storage.persist()`.
 * - Cache Storage, una caché por versión fijada del editor (`itt-cad-editor-<versión>`), que sirve el service worker de Herramientas.
 *   Al cambiar la versión fijada se baja solo lo que cambió: lo que tiene el mismo SHA-256 que en la versión anterior se copia de allí.
 *   La versión instalada se lee de la propia caché (marca `__tv_completo__.json`), así que también se sabe sin conexión.
 * - Junto al editor se guarda la página de CAD sin conexión (`/cad/sin-conexion/`) con sus estilos y scripts (`itt-cad-pagina`).
 *
 * PLANOS SIN CONEXIÓN (base `tv-cad-campo`, ver sesion.js)
 * - Llevar un plano NO toma el bloqueo de edición (el bloqueo dura 90 s sin latido; no puede durar días).
 * - Lo editado sin conexión vive en el teléfono como «cambios» y nunca es una versión hasta volver a tener conexión.
 * - Al volver: «Guardar como versión nueva» solo si la versión del servidor sigue siendo la base y nadie tiene el bloqueo; si no, se guarda
 *   como copia («<nombre> (copia)»). Nunca se pisa nada (el servidor además lo exige: bloqueo de esta página y versión base vigente).
 *
 * API: window.TVCadCampo = { instalada(), estadoEditor(), descargarEditor(alProgreso), quitarEditor(), versionInstalada(),
 *      llevar(id), quitar(id), plano(id), planos(), cambios(id), guardarCambios(datos), descartarCambios(id), subir(id), hoja(ui), uso() }
 */
(function () {
  "use strict";
  if (window.TVCadCampo) return;
  const $ = (id) => document.getElementById(id);
  const PREFIJO = "itt-cad-editor-", PAGINA = "itt-cad-pagina", MARCA = "__tv_completo__.json";
  const configEl = $("cad-config");
  const C = configEl ? JSON.parse(configEl.textContent || "{}") : {};
  const T = C.tactil || {};
  // La página sin conexión no lleva el token en su HTML (es igual para todas las cuentas): lo pone sin-conexion.js desde /sesion/.
  let tokenCsrf = "";
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : tokenCsrf; };
  const MB = (n) => `${(n / 1048576).toFixed(n >= 10485760 ? 0 : 1)} MB`;

  function instalada() {
    try { return matchMedia("(display-mode: standalone)").matches || matchMedia("(display-mode: fullscreen)").matches || navigator.standalone === true; }
    catch (_) { return false; }
  }

  // ------------------------------------------------------------------------------------------------------------- el editor
  async function marcaDe(nombre) {
    try {
      const c = await caches.open(nombre);
      const v = nombre.slice(PREFIJO.length);
      const r = await c.match(`/cad/editor/${v}/${MARCA}`, { ignoreVary: true });
      return r ? await r.json() : null;
    } catch (_) { return null; }
  }
  async function versionesGuardadas() {
    if (!("caches" in window)) return [];
    const nombres = (await caches.keys()).filter((n) => n.startsWith(PREFIJO));
    const res = [];
    for (const n of nombres) res.push({ nombre: n, version: n.slice(PREFIJO.length), marca: await marcaDe(n) });
    return res;
  }
  async function versionInstalada() {
    const completas = (await versionesGuardadas()).filter((x) => x.marca);
    completas.sort((a, b) => String(b.marca.fecha).localeCompare(String(a.marca.fecha)));
    return completas[0] ? completas[0].marca : null;
  }
  async function estadoEditor() {
    const instal = await versionInstalada();
    return { instalada: instal, fijada: T.version || null, alDia: !!(instal && T.version && instal.version === T.version), descarga: T.descarga || 0 };
  }
  let bajando = null;
  // Baja el editor fijado (o lo que cambió desde la versión instalada). alProgreso({hechos, total, bytes, totalBytes}).
  function descargarEditor(alProgreso) {
    if (bajando) return bajando;
    bajando = (async () => {
      if (!("caches" in window)) throw new Error("Este navegador no puede guardar el CAD.");
      if (navigator.storage && navigator.storage.persist) { try { await navigator.storage.persist(); } catch (_) { /* se intenta igual */ } }
      // El service worker de Herramientas es quien sirve el editor sin conexión: se registra aquí si esta app aún no lo tenía.
      if ("serviceWorker" in navigator) { try { await navigator.serviceWorker.register("/sw.js", { scope: "/" }); } catch (_) { /* sin él, solo con conexión */ } }
      const r = await fetch(T.urls.paquete, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo saber qué descargar. Inténtalo de nuevo.");
      const paquete = await r.json();
      const nombre = PREFIJO + paquete.version;
      const cache = await caches.open(nombre);
      const anteriores = (await versionesGuardadas()).filter((x) => x.nombre !== nombre && x.marca);
      const total = paquete.archivos.length, totalBytes = paquete.descarga;
      let hechos = 0, bytes = 0;
      const avanzar = (a) => { hechos += 1; bytes += a.descarga; if (alProgreso) alProgreso({ hechos, total, bytes, totalBytes }); };
      for (const a of paquete.archivos) {
        const url = `${paquete.base}${a.ruta}`;
        if (await cache.match(url, { ignoreVary: true })) { avanzar(a); continue; }
        // Mismo archivo (mismo SHA-256) en la versión anterior: se copia, no se baja.
        let copiado = false;
        for (const ant of anteriores) {
          if (ant.marca.archivos && ant.marca.archivos[a.ruta] === a.sha256) {
            const vieja = await (await caches.open(ant.nombre)).match(`/cad/editor/${ant.version}/${a.ruta}`, { ignoreVary: true });
            if (vieja) { await cache.put(url, vieja); copiado = true; break; }
          }
        }
        if (!copiado) {
          const resp = await fetch(url, { credentials: "same-origin", cache: "reload" });
          if (!resp.ok) throw new Error("La descarga se interrumpió. Vuelve a intentarlo: lo ya bajado no se repite.");
          // Se cuenta lo que llega (sin cargar el archivo entero en memoria): un archivo cortado no se da por bueno.
          let llegaron = 0;
          const contar = new TransformStream({ transform(trozo, ctl) { llegaron += trozo.byteLength; ctl.enqueue(trozo); } });
          const cabeceras = new Headers(resp.headers);
          cabeceras.delete("content-encoding"); cabeceras.delete("content-length");
          await cache.put(url, new Response(resp.body.pipeThrough(contar), { status: 200, headers: cabeceras }));
          if (llegaron !== a.bytes) { await cache.delete(url); throw new Error("Un archivo llegó incompleto. Vuelve a intentarlo."); }
        }
        avanzar(a);
      }
      await guardarPagina();
      const marca = { version: paquete.version, fecha: new Date().toISOString(), bytes: paquete.bytes, descarga: paquete.descarga,
        archivos: Object.fromEntries(paquete.archivos.map((a) => [a.ruta, a.sha256])) };
      await cache.put(`/cad/editor/${paquete.version}/${MARCA}`, new Response(JSON.stringify(marca), { headers: { "Content-Type": "application/json" } }));
      for (const ant of await versionesGuardadas()) if (ant.nombre !== nombre) await caches.delete(ant.nombre);
      return marca;
    })().finally(() => { bajando = null; });
    return bajando;
  }
  // La página sin conexión y todo lo que carga, en su propia caché (se rehace en cada visita con conexión si cambió).
  async function guardarPagina() {
    const url = T.urls && T.urls.sin_conexion;
    if (!url) return;
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) return;
    const html = await r.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const recursos = [...doc.querySelectorAll("link[rel=stylesheet][href], script[src], link[rel=manifest][href], link[rel=icon][href]")]
      .map((e) => e.getAttribute("href") || e.getAttribute("src")).filter((u) => u && u.startsWith("/"));
    // Se rehace entera: primero se bajan todas las piezas y después se escriben (si algo falla, queda la de antes).
    const piezas = [[url, new Response(html, { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } })]];
    for (const u of recursos) {
      try { const x = await fetch(u, { credentials: "same-origin" }); if (x.ok) piezas.push([u, await copiaDe(x)]); } catch (_) { /* el que falte lo sirve el service worker de su caché */ }
    }
    // Fuentes SHX de la empresa: el editor sin conexión las registra igual que la página del plano.
    for (const f of C.fuentes || []) {
      try { const x = await fetch(f.url, { credentials: "same-origin" }); if (x.ok) piezas.push([f.url, await copiaDe(x)]); } catch (_) { /* sin esa fuente */ }
    }
    piezas.push(["/cad/sin-conexion/__fuentes__.json", new Response(JSON.stringify((C.fuentes || []).map((f) => ({ nombre: f.nombre, url: f.url }))),
      { headers: { "Content-Type": "application/json" } })]);
    // Se escribe encima y se quitan las piezas que ya no usa (borrar la caché y volver a abrirla en seguida pierde lo escrito en WebKit).
    const cache = await caches.open(PAGINA);
    for (const [u, resp] of piezas) await cache.put(u, resp);
    const vigentes = new Set(piezas.map(([u]) => new URL(u, location.href).href));
    for (const req of await cache.keys()) if (!vigentes.has(req.url)) await cache.delete(req);
  }
  // Una copia sin las cabeceras de la red (compresión, Vary, Set-Cookie): así WebKit la guarda y la sirve igual que Chromium.
  async function copiaDe(resp) {
    const tipo = resp.headers.get("Content-Type") || "application/octet-stream";
    return new Response(await resp.blob(), { status: 200, headers: { "Content-Type": tipo } });
  }
  async function quitarEditor() {
    for (const n of await caches.keys()) if (n.startsWith(PREFIJO) || n.startsWith(PAGINA)) await caches.delete(n);
  }
  async function usoEditor() {
    let total = 0;
    for (const v of await versionesGuardadas()) if (v.marca) total += v.marca.bytes || 0;
    return total;
  }

  // ------------------------------------------------------------------------------------------------------------- planos en el teléfono
  const abrirDB = () => (window.TVCadSesion ? window.TVCadSesion.abrir() : Promise.resolve(null));
  function req(r) { return new Promise((ok, mal) => { r.onsuccess = () => ok(r.result); r.onerror = () => mal(r.error); }); }
  async function con(tiendas, modo, fn) {
    const db = await abrirDB();
    if (!db) throw new Error("Este navegador no puede guardar planos.");
    try {
      const t = db.transaction(tiendas, modo);
      const fin = new Promise((ok, mal) => { t.oncomplete = ok; t.onerror = () => mal(t.error); t.onabort = () => mal(t.error); });
      const r = await fn(...[].concat(tiendas).map((n) => t.objectStore(n)));
      await fin;
      return r;
    } finally { db.close(); }
  }
  async function cuentaActual() {
    if (T.cuenta) return T.cuenta;
    const s = await con("estado", "readonly", (e) => req(e.get("sesion"))).catch(() => null);
    return s && !s.cerrada ? s.cuenta : null;
  }
  async function recordarSesion(cuenta) {
    await con("estado", "readwrite", (e) => req(e.put({ clave: "sesion", cuenta, cerrada: false }))).catch(() => {});
  }
  const deCuenta = async (lista) => { const c = await cuentaActual(); return lista.filter((x) => c && x.cuenta === c); };
  const plano = async (id) => { const p = await con("planos", "readonly", (s) => req(s.get(id))).catch(() => null); return p && p.cuenta === (await cuentaActual()) ? p : null; };
  const planos = async () => deCuenta(await con("planos", "readonly", (s) => req(s.getAll())).catch(() => []));
  const cambios = async (id) => { const c = await con("cambios", "readonly", (s) => req(s.get(id))).catch(() => null); return c && c.cuenta === (await cuentaActual()) ? c : null; };
  const todosLosCambios = async () => deCuenta(await con("cambios", "readonly", (s) => req(s.getAll())).catch(() => []));

  async function llevar(id) {
    const urls = C.urls && C.id === id ? C.urls : null;
    const base = urls ? urls : { archivo: `/cad/${id}/archivo/`, estado: `/cad/${id}/estado/` };
    const [ra, re] = await Promise.all([fetch(base.archivo, { credentials: "same-origin" }), fetch(base.estado, { credentials: "same-origin", headers: { Accept: "application/json" } })]);
    if (!ra.ok || !re.ok) throw new Error(ra.status === 401 || re.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo llevar el plano al teléfono.");
    const bytes = await ra.arrayBuffer(), est = await re.json();
    const revision = Number(ra.headers.get("X-TV-Revision") || est.revision);
    const cuenta = await cuentaActual();
    if (!cuenta) throw new Error("Vuelve a entrar para llevar planos al teléfono.");
    await recordarSesion(cuenta);
    const registro = { id, cuenta, nombre: est.nombre, proyecto: est.proyecto || "", formato: est.formato, revision, bytes, tamano: bytes.byteLength,
      modificado_por: est.modificado_por || "", guardado: new Date().toISOString() };
    await con("planos", "readwrite", (s) => req(s.put(registro)));
    return registro;
  }
  async function quitar(id) { await con("planos", "readwrite", (s) => req(s.delete(id))); }
  async function guardarCambios({ id, nombre, formato, base, bytes }) {
    const cuenta = await cuentaActual();
    if (!cuenta) throw new Error("No hay una sesión en este teléfono.");
    await con("cambios", "readwrite", (s) => req(s.put({ id, cuenta, nombre, formato, base, bytes, tamano: bytes.byteLength, actualizado: new Date().toISOString() })));
  }
  async function descartarCambios(id) { await con("cambios", "readwrite", (s) => req(s.delete(id))); }

  // Sube lo hecho sin conexión: versión nueva solo si la base sigue vigente y nadie edita; si no, una copia. `sesion`: la de la página que
  // ya tiene el bloqueo (la del plano), o ninguna (se toma uno corto con una sesión propia y se suelta al terminar).
  function sesionNueva() { const b = new Uint8Array(18); crypto.getRandomValues(b); return btoa(String.fromCharCode(...b)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c])); }
  async function post(url, cuerpo, cabeceras) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), Accept: "application/json", ...cabeceras }, body: cuerpo });
    if (r.status === 401) throw new Error("Tu sesión terminó. Vuelve a entrar para subir tus cambios.");
    let d = {}; try { d = await r.json(); } catch (_) { /* sin cuerpo */ }
    return { r, d };
  }
  async function decidir(id, sesion = "") {
    const c = await cambios(id);
    if (!c) return null;
    const r = await fetch(`/cad/${id}/estado/?sesion=${encodeURIComponent(sesion)}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status === 404) return { cambio: c, comoVersion: false, motivo: "El plano ya no está en la biblioteca: tus cambios se guardan como un plano nuevo." };
    if (!r.ok) throw new Error(r.status === 401 ? "Tu sesión terminó. Vuelve a entrar para subir tus cambios." : "No se pudo revisar el plano.");
    const est = await r.json();
    const libre = est.bloqueo && (est.bloqueo.libre || est.bloqueo.mio);
    if (est.revision !== c.base) return { cambio: c, comoVersion: false, motivo: `${est.modificado_por || "Alguien"} guardó una versión nueva mientras no tenías conexión: tus cambios se guardan como copia.` };
    if (!libre) return { cambio: c, comoVersion: false, motivo: `${est.bloqueo.quien || "Otra persona"} está editando el plano: tus cambios se guardan como copia.` };
    return { cambio: c, comoVersion: true, motivo: "Nadie cambió el plano: tus cambios se guardan como versión nueva." };
  }
  async function subir(id, { sesion, comoCopia = false } = {}) {
    const dec = await decidir(id, sesion || "");
    if (!dec) return null;
    const { cambio: c } = dec;
    if (dec.comoVersion && !comoCopia) {
      const propia = !sesion;
      const s = sesion || sesionNueva();
      let mio = !propia;
      if (propia) {
        const b = await post(`/cad/${id}/bloqueo/`, JSON.stringify({ accion: "tomar", sesion: s }), { "Content-Type": "application/json" });
        mio = b.r.ok && b.d.mio;
      }
      if (mio) {
        try {
          const g = await post(`/cad/${id}/guardar/`, c.bytes, { "Content-Type": "application/octet-stream", "X-TV-Sesion": s, "X-TV-Revision": String(c.base) });
          if (g.r.ok) {
            await descartarCambios(id);
            const p = await plano(id);
            if (p) await con("planos", "readwrite", (st) => req(st.put({ ...p, bytes: c.bytes, tamano: c.tamano, revision: g.d.revision, formato: g.d.formato, guardado: new Date().toISOString() })));
            return { tipo: "version", revision: g.d.revision };
          }
          if (g.r.status !== 409) throw new Error(g.d.error || "No se pudo guardar. Inténtalo de nuevo.");
        } finally {
          if (propia) post(`/cad/${id}/bloqueo/`, JSON.stringify({ accion: "soltar", sesion: s }), { "Content-Type": "application/json" }).catch(() => {});
        }
      }
    }
    const k = await post(`/cad/${id}/copia/`, c.bytes, { "Content-Type": "application/octet-stream" });
    if (!k.r.ok) throw new Error(k.d.error || "No se pudo guardar la copia. Inténtalo de nuevo.");
    await descartarCambios(id);
    return { tipo: "copia", nombre: k.d.nombre, url: k.d.url, id: k.d.id };
  }

  async function uso() {
    const est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate().catch(() => ({})) : {};
    const persistente = navigator.storage && navigator.storage.persisted ? await navigator.storage.persisted().catch(() => false) : false;
    return { editor: await usoEditor(), planos: (await planos()).reduce((s, p) => s + (p.tamano || 0), 0),
      cambios: (await todosLosCambios()).reduce((s, p) => s + (p.tamano || 0), 0), usado: est.usage || 0, cuota: est.quota || 0, persistente };
  }

  // ------------------------------------------------------------------------------------------------------------- hoja «Sin conexión»
  // `ui` son las piezas de tactil.js (abrirHoja, nodo, boton, interruptor, aviso, icono, cerrarHojas).
  async function hoja(ui) {
    const { abrirHoja, nodo, boton, interruptor, aviso } = ui;
    const cont = nodo("div");
    const ed = await estadoEditor();
    const u = await uso();
    cont.append(nodo("h3", "", "CAD en este teléfono"));
    if (ed.alDia) {
      cont.append(nodo("p", "tt-nota-hoja", `Descargado (${MB(ed.instalada.bytes || u.editor)} en el teléfono). Funciona sin conexión; se actualiza solo cuando cambie la versión.`));
      cont.append(boton("Quitar el CAD de este teléfono", "cloud-off", async () => {
        if (u.cambios) { aviso("Primero sube tus cambios hechos sin conexión.", "error"); return; }
        await quitarEditor(); aviso("El CAD se quitó de este teléfono."); hoja(ui);
      }, { clase: "tt-boton tt-boton--secundario" }));
    } else if (!instalada()) {
      cont.append(nodo("p", "tt-nota-hoja", "Para usar CAD sin conexión, instala la app en la pantalla de inicio y ábrela desde ahí: así el teléfono no borra lo descargado."));
      const a = nodo("a", "tt-boton tt-boton--secundario", "Cómo instalar la app"); a.href = T.urls.instalar; cont.append(a);
    } else {
      cont.append(nodo("p", "tt-nota-hoja", ed.instalada ? `Hay una versión nueva del CAD. Se baja solo lo que cambió.`
        : `Se descarga una sola vez (unos ${MB(ed.descarga)}). Mejor con wifi.`));
      const barra = nodo("div", "tt-barra-uso"); const lleno = nodo("span"); barra.append(lleno); barra.hidden = true;
      const estado = nodo("p", "tt-nota-hoja", "");
      const b = boton(ed.instalada ? "Actualizar el CAD" : "Descargar CAD en este teléfono", "cloud-download", async () => {
        b.disabled = true; barra.hidden = false;
        try {
          await descargarEditor(({ bytes, totalBytes }) => { const f = totalBytes ? bytes / totalBytes : 0; lleno.style.width = `${Math.round(f * 100)}%`; estado.textContent = `${MB(bytes)} de ${MB(totalBytes)}`; });
          aviso("Listo: el CAD funciona sin conexión en este teléfono."); hoja(ui);
        } catch (err) { b.disabled = false; aviso(err.message || "No se pudo descargar.", "error"); }
      }, { clase: "tt-boton" });
      cont.append(b, barra, estado);
    }
    if (C.id && !document.documentElement.dataset.cadCampo) {
      cont.append(nodo("h3", "", "Este plano"));
      const llevado = await plano(C.id);
      cont.append(interruptor("Disponible sin conexión", !!llevado, async (si) => {
        try {
          if (si) {
            if (!ed.alDia) { aviso("Primero descarga el CAD en este teléfono."); hoja(ui); return; }
            await llevar(C.id); aviso("El plano quedó en este teléfono. Lo abres sin conexión desde CAD.");
          } else {
            if (await cambios(C.id)) { aviso("Este plano tiene cambios sin subir: súbelos antes de quitarlo.", "error"); hoja(ui); return; }
            await quitar(C.id); aviso("El plano se quitó de este teléfono.");
          }
        } catch (err) { aviso(err.message, "error"); }
        hoja(ui);
      }));
      cont.append(nodo("p", "tt-nota-hoja", "No aparta el plano: otras personas pueden seguir editándolo. Lo que cambies sin conexión se guarda en el teléfono y, al volver la conexión, se sube como versión nueva o como copia si alguien lo cambió."));
    }
    const lista = await planos();
    if (lista.length) {
      cont.append(nodo("h3", "", "Planos en este teléfono"));
      const ul = nodo("ul", "tt-lista");
      for (const p of lista) {
        const c = await cambios(p.id);
        const li = nodo("li", "tt-fila");
        const t = nodo("div", "tt-fila__texto"); t.append(nodo("strong", "", p.nombre), nodo("span", "", `${c ? "Con cambios sin subir · " : ""}Versión ${p.revision} · ${MB(p.tamano || 0)}`));
        li.append(t); ul.append(li);
      }
      cont.append(ul);
    }
    cont.append(nodo("p", "tt-nota-hoja", `En el teléfono: CAD ${MB(u.editor)} · planos ${MB(u.planos + u.cambios)}${u.persistente ? "" : " · el teléfono podría borrarlo si se queda sin espacio"}.`));
    abrirHoja("Sin conexión", cont);
  }

  // ------------------------------------------------------------------------------------------------------------- en la página del plano
  // Con conexión: (1) cambios hechos sin conexión de este plano → aviso con las salidas; (2) la copia del teléfono se pone al día;
  // (3) el editor del teléfono se actualiza si cambió la versión fijada.
  async function enLaPagina() {
    if (!C.id || document.documentElement.dataset.cadCampo || !navigator.onLine) return;
    if (T.cuenta) await recordarSesion(T.cuenta);
    const c = await cambios(C.id);
    if (c) avisoCambios(c);
    document.addEventListener("tvcad:guardado", () => ponerAlDia(true));
    ponerAlDia(false);
    const ed = await estadoEditor();
    if (ed.instalada && !ed.alDia && instalada()) descargarEditor().catch(() => { /* se reintenta en la siguiente visita */ });
    else if (ed.alDia) guardarPagina().catch(() => {});
  }
  async function ponerAlDia(forzar) {
    const p = await plano(C.id);
    if (!p || (await cambios(C.id))) return;
    const r = await fetch(C.urls.estado, { credentials: "same-origin", headers: { Accept: "application/json" } }).catch(() => null);
    if (!r || !r.ok) return;
    const est = await r.json();
    if (forzar || est.revision !== p.revision) await llevar(C.id).catch(() => {});
  }
  async function avisoCambios(c) {
    const cont = $("cad-avisos");
    if (!cont) return;
    const caja = document.createElement("div"); caja.className = "cad-aviso"; caja.dataset.tipo = "aviso"; caja.id = "cad-aviso-campo";
    const p = document.createElement("p");
    const cuando = new Date(c.actualizado).toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    // La página toma el bloqueo al abrir el editor: se espera a eso para decidir con su sesión (si no, el bloqueo propio parecería ajeno).
    await Promise.race([window.TVCad ? window.TVCad.listo : Promise.resolve(), new Promise((r) => setTimeout(r, 60000))]);
    const sesionPagina = () => (window.TVCad && window.TVCad.estado().editable && window.TVCadPagina ? window.TVCadPagina.sesion() : undefined);
    let dec = null;
    try { dec = await decidir(C.id, sesionPagina()); } catch (_) { /* se decide al tocar */ }
    p.textContent = `Tienes cambios hechos sin conexión en este teléfono (${cuando}). ${dec ? dec.motivo : ""}`.trim();
    const acciones = document.createElement("div"); acciones.className = "cad-aviso__acciones";
    const subirB = document.createElement("button"); subirB.type = "button"; subirB.className = "tv-boton"; subirB.id = "cad-campo-subir";
    subirB.textContent = dec && dec.comoVersion ? "Guardar como versión nueva" : "Guardar como copia";
    const bajar = document.createElement("button"); bajar.type = "button"; bajar.className = "tv-boton tv-boton--secundario"; bajar.textContent = "Descargar";
    const descartar = document.createElement("button"); descartar.type = "button"; descartar.className = "tv-boton tv-boton--fantasma"; descartar.textContent = "Descartar";
    acciones.append(subirB, bajar, descartar);
    caja.append(p, acciones); cont.prepend(caja);
    const avisar = (t, tipo) => { if (window.tvAviso) window.tvAviso(t, tipo); };
    subirB.addEventListener("click", async () => {
      subirB.setAttribute("aria-busy", "true");
      try {
        const r = await subir(C.id, { sesion: sesionPagina() });
        caja.remove();
        if (r && r.tipo === "version") { avisar(`Tus cambios del teléfono son la versión ${r.revision}`); if (window.TVCadPagina) window.TVCadPagina.reabrir(); else location.reload(); }
        else if (r) { avisar(`Tus cambios quedaron en «${r.nombre}»`); }
      } catch (err) { avisar(err.message, "error"); }
      finally { subirB.removeAttribute("aria-busy"); }
    });
    bajar.addEventListener("click", () => window.TVCadSesion && window.TVCadSesion.descargar(c));
    descartar.addEventListener("click", async () => {
      if (!(await confirmar("¿Descartar los cambios hechos sin conexión?", "No se pueden recuperar.", "Descartar"))) return;
      await descartarCambios(C.id); caja.remove(); avisar("Cambios descartados");
    });
  }
  // Confirmación con la hoja de la plataforma (nunca el diálogo del navegador).
  function confirmar(titulo, texto, si) {
    return new Promise((ok) => {
      const d = document.createElement("dialog"); d.className = "tv-hoja tv-hoja--confirmar";
      const c = document.createElement("div"); c.className = "tv-hoja__cuerpo";
      const h = document.createElement("h2"); h.textContent = titulo; const p = document.createElement("p"); p.textContent = texto;
      const a = document.createElement("div"); a.className = "tv-hoja__acciones";
      const no = document.createElement("button"); no.type = "button"; no.className = "tv-boton tv-boton--secundario"; no.textContent = "Cancelar";
      const sb = document.createElement("button"); sb.type = "button"; sb.className = "tv-boton tv-boton--peligro"; sb.textContent = si;
      a.append(no, sb); c.append(h, p, a); d.append(c); document.body.append(d);
      no.onclick = () => d.close("no"); sb.onclick = () => d.close("si");
      d.addEventListener("close", () => { d.remove(); ok(d.returnValue === "si"); }, { once: true });
      d.showModal();
    });
  }

  window.TVCadCampo = Object.freeze({ instalada, estadoEditor, descargarEditor, quitarEditor, versionInstalada, llevar, quitar, plano, planos,
    cambios, todosLosCambios, guardarCambios, descartarCambios, subir, decidir, hoja, uso, recordarSesion, cuentaActual, confirmar,
    ponerCsrf: (t) => { tokenCsrf = String(t || ""); } });
  // En la lista de Planos: cuántos planos hay en este teléfono y cuántos con cambios sin subir (lleva a la página sin conexión).
  async function enLaLista() {
    const t = $("cad-campo-tarjeta");
    if (!t) return;
    const [ps, cs] = [await planos(), await todosLosCambios()];
    if (!ps.length && !cs.length) return;
    $("cad-campo-tarjeta-texto").textContent = `${ps.length === 1 ? "1 plano" : `${ps.length} planos`} en este teléfono${cs.length ? ` · ${cs.length === 1 ? "1 con cambios sin subir" : `${cs.length} con cambios sin subir`}` : ""}.`;
    t.hidden = false;
  }
  const arrancar = () => { enLaPagina(); enLaLista(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", arrancar); else arrancar();
})();

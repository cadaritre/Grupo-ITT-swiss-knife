/* CAD en el campo (detrás de TV_CAD_TACTIL): el editor descargado una vez en el teléfono y los planos «Disponibles sin conexión».
 *
 * EL EDITOR EN ESTE TELÉFONO
 * - Solo a pedido («Descargar CAD en este teléfono», ~39 MB) y solo en la app instalada (en iOS el almacenamiento de un sitio que no está
 *   instalado se borra a los 7 días sin uso). Se pide `navigator.storage.persist()`.
 * - Lo guarda instalador.js, el mismo código que en la computadora (Cache Storage `itt-cad-editor-<versión>`, servido por el service
 *   worker; al cambiar la versión fijada solo lo que cambió, con parches; cada archivo comprobado con el SHA-256 del manifiesto). La
 *   versión instalada se lee de la base del teléfono, así que también se sabe sin conexión.
 * - Junto al editor se guarda la página de CAD sin conexión (`/cad/sin-conexion/`) con sus estilos y scripts (`itt-cad-pagina`).
 *
 * PLANOS SIN CONEXIÓN (base `tv-cad-campo`, ver sesion.js)
 * - Llevar un plano NO toma el bloqueo de edición (el bloqueo dura 150 s sin latido; no puede durar días).
 * - Lo editado sin conexión vive en el teléfono como «cambios» y nunca es una versión hasta volver a tener conexión.
 * - Al volver: «Guardar como versión nueva» solo si la versión del servidor sigue siendo la base y nadie tiene el bloqueo; si no, se guarda
 *   como copia («<nombre> (copia)»). Nunca se pisa nada (el servidor además lo exige: bloqueo de esta página y versión base vigente).
 *
 * CON CONEXIÓN, SIN GASTAR DATOS (docs/cad.md, «Datos móviles»)
 * - Abrir un plano que está en el teléfono no lo baja: editor.js pregunta si la copia sigue siendo la vigente (If-None-Match con su
 *   SHA-256, `copiaVigente`) y abre la del teléfono; si hay una versión nueva, llega una vez y reemplaza la copia (`actualizarCopia`).
 * - Guardar una versión deja esos mismos bytes como la copia (`alGuardarVersion`): no se baja nada después.
 * - Con cambios, la página deja una copia de ellos en el teléfono (`guardarCambios` con `origen: "pagina"`): la recupera la página del
 *   plano (como su borrador) o la abre la página sin conexión.
 * - El editor nuevo se baja ANTES de abrir el marco y solo lo que cambió (`prepararEditor`); la página sin conexión se vuelve a guardar
 *   solo cuando cambia (huella `tactil.pagina` de la página del plano).
 *
 * API: window.TVCadCampo = { instalada(), estadoEditor(), descargarEditor(alProgreso), prepararEditor(decir), quitarEditor(), versionInstalada(),
 *      llevar(id), quitar(id), plano(id), planos(), copiaVigente(id), actualizarCopia(id, datos), alGuardarVersion(id, datos), cambios(id),
 *      guardarCambios(datos), actualizarCambios(id, campos), descartarCambios(id), descartarCambiosDePagina(id), subir(id), hoja(ui), uso() }
 */
(function () {
  "use strict";
  if (window.TVCadCampo) return;
  const $ = (id) => document.getElementById(id);
  const PAGINA = "itt-cad-pagina";
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
  // Guardarlo, ponerlo al día (solo lo que cambió, con parches) y quitarlo es lo mismo que en la computadora: instalador.js
  // (window.TVCadEditor). Aquí solo lo propio del teléfono: a pedido, en la app instalada, y con la página sin conexión al lado.
  const E = () => window.TVCadEditor;
  const versionInstalada = () => (E() ? E().versionInstalada() : Promise.resolve(null));
  async function estadoEditor() {
    const ed = E() ? await E().estado() : { instalada: null, alDia: false };
    return { instalada: ed.instalada, fijada: T.version || null, alDia: !!(ed.instalada && T.version && ed.instalada.version === T.version), descarga: T.descarga || 0, ocupa: T.bytes || 0 };
  }
  // Antes de abrir el editor en la página del plano: si el CAD de este teléfono es de otra versión, se baja ya lo que cambió (solo eso)
  // para que el marco lo abra desde el teléfono. Sin la app instalada, sin red o si falla, el editor se abre de la red como siempre.
  async function prepararEditor(decir) {
    if (!T.version || !instalada() || !E()) return "red";
    const antes = await estadoEditor();
    const r = await E().preparar({ decir, automatico: false });
    // Con el editor nuevo, la página sin conexión (que nombra su versión) se vuelve a guardar ya, no en la próxima apertura.
    if (r === "equipo" && antes.instalada && !antes.alDia) await guardarPagina().catch(() => {});
    return r;
  }
  // Baja el editor fijado (o lo que cambió desde la versión instalada) y la página sin conexión. alProgreso({hechos, total, bytes, totalBytes}).
  async function descargarEditor(alProgreso) {
    if (!E()) throw new Error("Este navegador no puede guardar el CAD.");
    const marca = await E().descargar(alProgreso);
    await guardarPagina();
    return marca;
  }
  // La página sin conexión y todo lo que carga, en su propia caché (se rehace en cada visita con conexión si cambió).
  async function guardarPagina() {
    const url = T.urls && T.urls.sin_conexion;
    if (!url) return;
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) return;
    const r2 = r.clone();
    const html = await r.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const recursos = [...doc.querySelectorAll("link[rel=stylesheet][href], script[src], link[rel=manifest][href], link[rel=icon][href]")]
      .map((e) => e.getAttribute("href") || e.getAttribute("src")).filter((u) => u && u.startsWith("/"));
    // Se rehace entera: primero se bajan todas las piezas y después se escriben (si algo falla, queda la de antes).
    const piezas = [[url, copiaDe(r2)]];
    for (const u of recursos) {
      try { const x = await fetch(u, { credentials: "same-origin" }); if (x.ok) piezas.push([u, copiaDe(x)]); } catch (_) { /* el que falte lo sirve el service worker de su caché */ }
    }
    // Fuentes SHX de la empresa: el editor sin conexión las registra igual que la página del plano.
    for (const f of C.fuentes || []) {
      try { const x = await fetch(f.url, { credentials: "same-origin" }); if (x.ok) piezas.push([f.url, copiaDe(x)]); } catch (_) { /* sin esa fuente */ }
    }
    // La lista de fuentes va en la base del teléfono, no como respuesta inventada en la caché.
    await con("estado", "readwrite", (e) => req(e.put({ clave: "fuentes", lista: (C.fuentes || []).map((f) => ({ nombre: f.nombre, url: f.url })) }))).catch(() => {});
    // Se escribe encima y se quitan las piezas que ya no usa (borrar la caché y volver a abrirla en seguida pierde lo escrito en WebKit).
    const cache = await caches.open(PAGINA);
    for (const [u, resp] of piezas) await cache.put(u, resp);
    const vigentes = new Set(piezas.map(([u]) => new URL(u, location.href).href));
    for (const req of await cache.keys()) if (!vigentes.has(req.url)) await cache.delete(req);
    // Se lee de vuelta cada pieza: en WebKit una escritura que no terminó se pierde si la persona cambia de página en seguida.
    for (const [u] of piezas) { const x = await cache.match(u, { ignoreVary: true }); if (!x) throw new Error("No se pudo guardar la página sin conexión."); await x.arrayBuffer(); }
    if (T.pagina) await con("estado", "readwrite", (e) => req(e.put({ clave: "pagina", version: T.pagina }))).catch(() => {});
  }
  // Una copia sin las cabeceras de la red (compresión, Vary, Set-Cookie), con el cuerpo de la red tal cual llega (en WebKit, una
  // respuesta guardada con un cuerpo hecho en la página se pierde al cambiar de página).
  function copiaDe(resp) {
    const tipo = resp.headers.get("Content-Type") || "application/octet-stream";
    return new Response(resp.body, { status: 200, headers: { "Content-Type": tipo } });
  }
  async function quitarEditor() {
    if (E()) await E().quitar();
    for (const n of await caches.keys()) if (n.startsWith(PAGINA)) await caches.delete(n);
  }
  const usoEditor = () => (E() ? E().uso() : Promise.resolve(0));

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

  async function sha256(bytes) {
    const h = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return Array.from(h, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Lleva el plano al teléfono. En su página, con la versión vigente recién abierta, usa esos bytes (no lo baja otra vez).
  async function llevar(id) {
    const cuenta = await cuentaActual();
    if (!cuenta) throw new Error("Vuelve a entrar para llevar planos al teléfono.");
    const abiertos = C.id === id && window.TVCadPagina && window.TVCadPagina.abiertos ? window.TVCadPagina.abiertos() : null;
    let bytes, est, revision;
    if (abiertos) {
      bytes = abiertos.bytes; revision = abiertos.revision;
      est = { nombre: C.nombre, proyecto: C.proyecto, formato: C.formato, modificado_por: C.modificado_por };
    } else {
      const base = C.urls && C.id === id ? C.urls : { archivo: `/cad/${id}/archivo/`, estado: `/cad/${id}/estado/` };
      const [ra, re] = await Promise.all([fetch(base.archivo, { credentials: "same-origin", cache: "no-store" }),
        fetch(`${base.estado}?ligero=1`, { credentials: "same-origin", headers: { Accept: "application/json" } })]);
      if (!ra.ok || !re.ok) throw new Error(ra.status === 401 || re.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo llevar el plano al teléfono.");
      bytes = await ra.arrayBuffer(); est = await re.json();
      revision = Number(ra.headers.get("X-TV-Revision") || est.revision);
    }
    await recordarSesion(cuenta);
    const registro = { id, cuenta, nombre: est.nombre, proyecto: est.proyecto || "", formato: est.formato, revision, bytes, tamano: bytes.byteLength,
      sha256: await sha256(bytes), modificado_por: est.modificado_por || "", guardado: new Date().toISOString() };
    await con("planos", "readwrite", (s) => req(s.put(registro)));
    return registro;
  }
  // La copia del teléfono con su SHA-256 (lo calcula y lo guarda si es de antes de tenerlo), o null.
  async function copiaVigente(id) {
    const p = await plano(id);
    if (!p || !p.bytes) return null;
    if (!p.sha256) {
      p.sha256 = await sha256(p.bytes);
      await con("planos", "readwrite", (s) => req(s.put(p))).catch(() => {});
    }
    return p;
  }
  // Cambia la copia del teléfono (de esta cuenta): bytes nuevos (otra versión) o solo su número.
  async function actualizarCopia(id, { bytes, revision, formato } = {}) {
    const p = await plano(id);
    if (!p) return null;
    const nuevo = { ...p, revision: revision || p.revision, formato: formato || p.formato };
    if (bytes) Object.assign(nuevo, { bytes, tamano: bytes.byteLength, sha256: await sha256(bytes), guardado: new Date().toISOString() });
    await con("planos", "readwrite", (s) => req(s.put(nuevo)));
    return nuevo;
  }
  // Se guardó una versión en la página del plano: esos bytes son la copia del teléfono, y la copia de sus cambios ya no hace falta.
  async function alGuardarVersion(id, datos) {
    await descartarCambiosDePagina(id);
    if (await plano(id)) await actualizarCopia(id, { bytes: datos.bytes.slice(0), revision: datos.revision, formato: datos.formato });
  }
  async function quitar(id) { await con("planos", "readwrite", (s) => req(s.delete(id))); }
  // `origen: "pagina"`: la copia que deja la página del plano con conexión (ver arriba); sin él, lo hecho en la página sin conexión.
  // `borrador`: la fecha del borrador del servidor que tiene estos mismos cambios.
  async function guardarCambios({ id, nombre, formato, base, bytes, origen, borrador }) {
    const cuenta = await cuentaActual();
    if (!cuenta) throw new Error("No hay una sesión en este teléfono.");
    const registro = { id, cuenta, nombre, formato, base, bytes, tamano: bytes.byteLength, actualizado: new Date().toISOString() };
    if (origen) registro.origen = origen;
    if (borrador) registro.borrador = borrador;
    await con("cambios", "readwrite", (s) => req(s.put(registro)));
  }
  async function actualizarCambios(id, campos) {
    const c = await cambios(id);
    if (c) await con("cambios", "readwrite", (s) => req(s.put({ ...c, ...campos })));
  }
  async function descartarCambios(id) { await con("cambios", "readwrite", (s) => req(s.delete(id))); }
  async function descartarCambiosDePagina(id) {
    const c = await cambios(id);
    if (c && c.origen === "pagina") await descartarCambios(id);
  }

  // Sube lo hecho sin conexión: versión nueva solo si la base sigue vigente y nadie edita; si no, una copia. `sesion`: la de la página que
  // ya tiene el bloqueo (la del plano), o ninguna (se toma uno corto con una sesión propia y se suelta al terminar).
  function sesionNueva() { const b = new Uint8Array(18); crypto.getRandomValues(b); return btoa(String.fromCharCode(...b)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c])); }
  const comprimir = (b) => (window.TVCadSesion && window.TVCadSesion.comprimir ? window.TVCadSesion.comprimir(b) : Promise.resolve({ cuerpo: b, cabeceras: {} }));
  async function post(url, cuerpo, cabeceras) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), Accept: "application/json", ...cabeceras }, body: cuerpo });
    if (r.status === 401) throw new Error("Tu sesión terminó. Vuelve a entrar para subir tus cambios.");
    let d = {}; try { d = await r.json(); } catch (_) { /* sin cuerpo */ }
    return { r, d };
  }
  async function decidir(id, sesion = "") {
    const c = await cambios(id);
    if (!c) return null;
    const r = await fetch(`/cad/${id}/estado/?sesion=${encodeURIComponent(sesion)}&ligero=1`, { credentials: "same-origin", headers: { Accept: "application/json" } });
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
          const z = await comprimir(c.bytes);
          const g = await post(`/cad/${id}/guardar/`, z.cuerpo, { "Content-Type": "application/octet-stream", "X-TV-Sesion": s, "X-TV-Revision": String(c.base), ...z.cabeceras });
          if (g.r.ok) {
            await descartarCambios(id);
            await actualizarCopia(id, { bytes: c.bytes, revision: g.d.revision, formato: g.d.formato });
            return { tipo: "version", revision: g.d.revision };
          }
          if (g.r.status !== 409) throw new Error(g.d.error || "No se pudo guardar. Inténtalo de nuevo.");
        } finally {
          if (propia) post(`/cad/${id}/bloqueo/`, JSON.stringify({ accion: "soltar", sesion: s }), { "Content-Type": "application/json" }).catch(() => {});
        }
      }
    }
    const z = await comprimir(c.bytes);
    const k = await post(`/cad/${id}/copia/`, z.cuerpo, { "Content-Type": "application/octet-stream", ...z.cabeceras });
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
        : `Se descarga una sola vez (unos ${MB(ed.descarga)}; ocupa unos ${MB(ed.ocupa)} en el teléfono). Mejor con wifi.`));
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
    cont.append(nodo("p", "tt-nota-hoja", `En el teléfono: CAD ${MB(u.editor)} · planos ${MB(u.planos + u.cambios)}.`));
    cont.append(nodo("p", "tt-nota-hoja", u.persistente ? "El teléfono lo conserva aunque se quede con poco espacio." : "El teléfono podría borrarlo si se queda sin espacio. Abre la app desde la pantalla de inicio para que lo conserve."));
    abrirHoja("Sin conexión", cont);
  }

  // ------------------------------------------------------------------------------------------------------------- en la página del plano
  // Con conexión: (1) cambios hechos sin conexión de este plano → aviso con las salidas (los que dejó la página del plano los ofrece ella,
  // como su borrador); (2) la página sin conexión se vuelve a guardar solo si cambió. La copia del plano la pone al día editor.js al abrir
  // (una pregunta de unos bytes) y al guardar, y el editor del teléfono se actualiza antes de abrirlo (`prepararEditor`).
  async function enLaPagina() {
    if (!C.id || document.documentElement.dataset.cadCampo || !navigator.onLine) return;
    if (T.cuenta) await recordarSesion(T.cuenta);
    const c = await cambios(C.id);
    if (c && c.origen !== "pagina") avisoCambios(c);
    const ed = await estadoEditor();
    if (ed.alDia && T.pagina) {
      const guardada = await con("estado", "readonly", (e) => req(e.get("pagina"))).catch(() => null);
      if (!guardada || guardada.version !== T.pagina) guardarPagina().catch(() => {});
    }
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

  window.TVCadCampo = Object.freeze({ instalada, estadoEditor, descargarEditor, prepararEditor, quitarEditor, versionInstalada, llevar, quitar, plano, planos,
    copiaVigente, actualizarCopia, alGuardarVersion, cambios, todosLosCambios, guardarCambios, actualizarCambios, descartarCambios, descartarCambiosDePagina,
    subir, decidir, hoja, uso, recordarSesion, cuentaActual, confirmar,
    ponerCsrf: (t) => { tokenCsrf = String(t || ""); }, guardarPagina,
    fuentes: async () => { const x = await con("estado", "readonly", (e) => req(e.get("fuentes"))).catch(() => null); return (x && x.lista) || []; } });
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

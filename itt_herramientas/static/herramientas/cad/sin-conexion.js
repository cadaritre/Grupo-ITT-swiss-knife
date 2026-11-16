/* CAD sin conexión: la página que abre el service worker sin señal (templates/herramientas/cad/sin_conexion.html).
 *
 * - Lista los planos llevados a este teléfono por la cuenta que entró por última vez (si la sesión se cerró, no muestra nada).
 * - Abre uno en el editor descargado y en modo táctil (tactil.js). Hace aquí el papel de editor.js: abre los bytes, mira si hay cambios y
 *   «guardar» los deja EN EL TELÉFONO como cambios sin conexión (nunca una versión). Se guardan solos cada 30 s con cambios y al pasar la
 *   app a segundo plano.
 * - Con conexión ofrece subir los cambios (versión nueva o copia, ver campo.js) o abrir el plano normal.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const C = JSON.parse($("cad-config").textContent);
  const Campo = window.TVCadCampo;
  const frame = $("cad-editor");
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const avisar = (t, tipo) => { if (window.tvAviso) window.tvAviso(t, tipo); };
  const anunciar = (n, d) => document.dispatchEvent(new CustomEvent(`tvcad:${n}`, { detail: d }));
  const MB = (n) => `${(n / 1048576).toFixed(1)} MB`;
  try { const t = localStorage.getItem("tv.cad.tema"); if (t) document.documentElement.dataset.tvTema = t; } catch (_) { /* claro */ }

  function mensaje(t) { $("sc-mensaje").hidden = !t; $("sc-mensaje-texto").textContent = t || ""; }
  function red() { $("sc-red").textContent = navigator.onLine ? "Con conexión" : "Sin conexión: lo que cambies se guarda en este teléfono"; }
  addEventListener("online", () => { red(); if (!abierto) lista(); });
  addEventListener("offline", red);

  // Con conexión se confirma quién está: otra cuenta o sesión cerrada → no se muestra nada de nadie.
  async function sesion() {
    if (!navigator.onLine) return Campo.cuentaActual();
    try {
      const r = await fetch(C.urls.sesion, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
      if (r.status === 401) {
        const db = await TVCadSesion.abrir();
        if (db) { await new Promise((ok) => { const t = db.transaction("estado", "readwrite"); const s = t.objectStore("estado"); const g = s.get("sesion");
          g.onsuccess = () => s.put({ ...(g.result || {}), clave: "sesion", cerrada: true }); t.oncomplete = ok; t.onerror = ok; }); db.close(); }
        return null;
      }
      if (r.ok) {
        const d = await r.json();
        await Campo.recordarSesion(d.cuenta);
        Campo.ponerCsrf(d.csrf);
        try { localStorage.setItem("tv.cad.tema", d.tema === "oscuro" ? "oscuro" : "claro"); document.documentElement.dataset.tvTema = d.tema === "oscuro" ? "oscuro" : "claro"; } catch (_) { /* nada */ }
        return d.cuenta;
      }
    } catch (_) { /* sin red de verdad */ }
    return Campo.cuentaActual();
  }

  async function lista() {
    red();
    const cuenta = await sesion();
    const ul = $("sc-planos"); ul.replaceChildren();
    if (!cuenta) { mensaje("Inicia sesión con conexión para ver los planos de este teléfono."); $("sc-vacio").hidden = true; return; }
    mensaje("");
    const planos = await Campo.planos();
    const sinEditor = !(await Campo.versionInstalada());
    if (sinEditor) mensaje("El CAD no está descargado en este teléfono. Con conexión, abre un plano y en «Más» › «Sin conexión» descárgalo.");
    $("sc-vacio").hidden = planos.length > 0;
    for (const p of planos.sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))) {
      const c = await Campo.cambios(p.id);
      const li = document.createElement("li"); li.className = "cad-campo__plano";
      const t = document.createElement("div"); t.className = "cad-campo__texto";
      const n = document.createElement("strong"); n.textContent = p.nombre;
      const m = document.createElement("span"); m.className = "tv-tenue";
      m.textContent = [p.proyecto, `Versión ${p.revision}`, MB(p.tamano || 0), c ? "Con cambios sin subir" : ""].filter(Boolean).join(" · ");
      t.append(n, m);
      const acc = document.createElement("div"); acc.className = "cad-campo__acciones";
      const abrirB = document.createElement("a"); abrirB.className = "tv-boton"; abrirB.textContent = "Abrir";
      abrirB.href = navigator.onLine && !c ? `/cad/${p.id}/` : `${C.urls.sin_conexion}?plano=${p.id}`;
      if (sinEditor && !navigator.onLine) abrirB.setAttribute("aria-disabled", "true");
      acc.append(abrirB);
      if (c && navigator.onLine) {
        const s = document.createElement("button"); s.type = "button"; s.className = "tv-boton tv-boton--secundario"; s.textContent = "Subir cambios";
        s.addEventListener("click", async () => {
          s.setAttribute("aria-busy", "true");
          try {
            const r = await Campo.subir(p.id);
            avisar(r.tipo === "version" ? `Tus cambios son la versión ${r.revision} de «${p.nombre}».` : `Alguien cambió el plano mientras tanto: tus cambios quedaron en «${r.nombre}».`);
            lista();
          } catch (err) { avisar(err.message, "error"); s.removeAttribute("aria-busy"); }
        });
        acc.append(s);
      }
      li.append(t, acc); ul.append(li);
    }
  }

  // ------------------------------------------------------------------------------------------------------------- un plano
  let abierto = null, control = null, documento = null, revEditor = 0, revGuardada = 0, sucio = false, guardando = false;
  function pintarEstado(estado, texto) {
    const chip = $("cad-estado"); chip.dataset.estado = estado; $("cad-estado-texto").textContent = texto;
    $("cad-guardar").disabled = !abierto || guardando || !sucio;
  }
  function base64(buffer) {
    const b = new Uint8Array(buffer); let t = "";
    for (let i = 0; i < b.length; i += 0x8000) t += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return btoa(t);
  }
  function deBase64(t) { const c = atob(t); const b = new Uint8Array(c.length); for (let i = 0; i < c.length; i++) b[i] = c.charCodeAt(i); return b.buffer; }
  let n = 0;
  async function canal(q, ms = 120000) { n += 1; const r = await control.request({ protocol: 1, request_id: `sc-${n}-${Date.now()}`, ...q }, { timeoutMs: ms }); return r || {}; }

  async function abrirPlano(id) {
    const cuenta = await sesion();
    const p = cuenta ? await Campo.plano(id) : null;
    if (!p) { mensaje(cuenta ? "Este plano no está en este teléfono." : "Inicia sesión con conexión para ver los planos de este teléfono."); return lista(); }
    const ed = await Campo.versionInstalada();
    if (!ed) { mensaje("El CAD no está descargado en este teléfono."); return lista(); }
    const c = await Campo.cambios(id);
    abierto = { ...p, base: c ? c.base : p.revision };
    $("sc-lista").hidden = true; $("cad-lienzo").hidden = false;
    $("cad-nombre").textContent = p.nombre;
    pintarEstado("cargando", "Abriendo…");
    const cargado = new Promise((r) => frame.addEventListener("load", r, { once: true }));
    frame.src = `/cad/editor/${ed.version}/index.html?embed=1&automation=1&canvas=1`;
    await cargado;
    for (let i = 0; i < 600 && !control; i++) { try { control = frame.contentWindow.ocsControl; } catch (_) { control = null; } if (!control) await espera(100); }
    if (!control) { pintarEstado("error", "No se pudo abrir el editor"); return; }
    await control.ready;
    try { const st = await canal({ op: "state" }); if (st.modal) await canal({ op: "action", name: "close_modal", document_id: st.document_id }); } catch (_) { /* nada */ }
    await fuentes();
    const bytes = (c || p).bytes;
    const nombre = `${p.nombre}.${(c || p).formato}`;
    canal({ op: "open", name: nombre, data_base64: base64(bytes) }).catch(() => {});
    for (let i = 0; i < 1200 && !documento; i++) {
      const st = await canal({ op: "state" }, 10000).catch(() => ({}));
      const d = (st.documents || []).find((x) => !x.start);
      if (d) { documento = d.id; revEditor = revGuardada = d.revision || 0; break; }
      await espera(100);
    }
    if (!documento) { pintarEstado("error", "No se pudo abrir el plano"); return; }
    $("cad-carga").hidden = true;
    pintarEstado("campo", c ? "Con cambios en el teléfono" : "Sin conexión");
    anunciar("cargado", { editable: true, documento, plano: { id, nombre: p.nombre, formato: (c || p).formato, revision: p.revision } });
    setInterval(vigilar, 1000);
    setInterval(() => { if (sucio && document.visibilityState === "visible") guardarLocal(); }, 30000);
  }
  async function fuentes() {
    try {
      const r = await caches.match("/cad/sin-conexion/__fuentes__.json", { ignoreVary: true });
      if (!r) return;
      for (const f of await r.json()) {
        const x = await caches.match(f.url, { ignoreVary: true });
        if (x) await canal({ op: "font_register", file_name: f.nombre, data_base64: base64(await x.arrayBuffer()) }, 15000).catch(() => {});
      }
    } catch (_) { /* con la fuente de reemplazo */ }
  }
  let consultando = false;
  async function vigilar() {
    if (consultando || !documento || document.visibilityState === "hidden") return;
    consultando = true;
    try {
      const st = await canal({ op: "state" }, 5000);
      const d = (st.documents || []).find((x) => x.id === documento);
      if (!d) return;
      revEditor = d.revision || 0;
      const s = revEditor !== revGuardada;
      if (s !== sucio) { sucio = s; anunciar("cambio", { sucio }); pintarEstado(sucio ? "sucio" : "campo", sucio ? "Sin guardar en el teléfono" : "Guardado en el teléfono"); }
    } catch (_) { /* el editor está ocupado */ } finally { consultando = false; }
  }
  // Lo que hay en el editor queda en el teléfono como «cambios sin conexión». No es una versión: se sube al volver la conexión.
  async function guardarLocal() {
    if (!abierto || !documento || guardando || (window.TVCad && TVCad.ocupado && TVCad.ocupado())) return false;
    guardando = true; pintarEstado("guardando", "Guardando en el teléfono…");
    try {
      const st = await canal({ op: "state" }, 5000).catch(() => ({}));
      const d = (st.documents || []).find((x) => x.id === documento);
      const rev = d ? d.revision || 0 : revEditor;
      const r = await canal({ op: "save", document_id: documento, name: `${abierto.nombre}.${abierto.formato}`, target_format: abierto.formato });
      if (r.ok === false || !r.result || !r.result.data_base64) throw new Error("El editor no pudo preparar el archivo.");
      await Campo.guardarCambios({ id: abierto.id, nombre: abierto.nombre, formato: abierto.formato, base: abierto.base, bytes: deBase64(r.result.data_base64) });
      revGuardada = rev; sucio = revEditor !== revGuardada;
      guardando = false; pintarEstado(sucio ? "sucio" : "campo", "Guardado en el teléfono");
      anunciar("guardado", { revision: abierto.revision, formato: abierto.formato });
      return true;
    } catch (err) {
      guardando = false; pintarEstado("error", "No se pudo guardar en el teléfono");
      avisar(err && err.name === "QuotaExceededError" ? "El teléfono no tiene espacio para guardar los cambios." : "No se pudo guardar en el teléfono. Inténtalo de nuevo.", "error");
      return false;
    }
  }
  $("cad-guardar").addEventListener("click", () => guardarLocal().then((ok) => { if (ok) avisar("Guardado en este teléfono. Se sube cuando vuelvas a tener conexión."); }));
  $("cad-volver").addEventListener("click", async (ev) => {
    ev.preventDefault();
    if (sucio && !(await guardarLocal())) return;
    location.assign(C.urls.sin_conexion);
  });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && sucio) guardarLocal(); });
  addEventListener("pagehide", () => { if (window.TVCadSesion) TVCadSesion.limpiarEditor(); });

  // Después de los scripts diferidos (sesion.js, la base de este teléfono).
  function inicio() {
    const id = new URLSearchParams(location.search).get("plano") || (location.pathname.match(/^\/cad\/([0-9a-f-]{36})\/$/) || [])[1];
    if (id) abrirPlano(id); else lista();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", inicio); else inicio();
})();

/* CAD: el editor (Open CAD Studio web) en un marco del mismo origen, unido a la biblioteca por su canal de control (?automation=1).
 *
 * - Abre la versión vigente (o una anterior, o el borrador de recuperación) por el canal (`op: open`).
 * - Guardar, Ctrl/⌘+S y «Guardar» dentro del editor se guardan en el servidor como versión nueva. Si el plano cambió o se perdió el
 *   bloqueo, nada se pisa: se ofrece guardar como copia.
 * - Bloqueo de edición con latido; quien no lo tiene ve el plano en solo lectura y quién lo edita.
 * - Mientras hay cambios sin guardar, cada 2 minutos se guarda un borrador de recuperación (no es una versión).
 * - Sin el aviso del navegador «¿Abandonar el sitio?»: al salir por la página se pregunta con una hoja propia; lo demás lo cubre el borrador.
 * - Varios planos en la misma ventana (en la computadora; docs/cad.md, «Varios planos en una ventana»): una pestaña por plano, cada una
 *   un plano completo del servidor con su bloqueo, versiones, borradores, «Sin guardar» y solo lectura. «Guardar» es de la pestaña
 *   activa; «Guardar todo» (Ctrl/⌘+Mayús+S) guarda cada una como su versión. Todas viven en el mismo editor (copiar y pegar entre
 *   ellas funciona) y al recargar se vuelven a abrir las mismas, recordadas en este dispositivo.
 * - Esc es del editor (cancelar y deseleccionar, como en AutoCAD) donde esté el foco en la página (docs/cad.md, «Esc»).
 */
(function () {
  "use strict";
  const C = JSON.parse(document.getElementById("cad-config").textContent);
  const $ = (id) => document.getElementById(id);
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const csrf = () => { const x = document.querySelector("input[name=csrfmiddlewaretoken]"); return x ? x.value : ""; };
  const avisar = (texto, tipo) => { if (window.tvAviso) window.tvAviso(texto, tipo); };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const frame = $("cad-editor");
  const nuevaSesion = () => { const b = new Uint8Array(18); crypto.getRandomValues(b); return btoa(String.fromCharCode(...b)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c])); };
  // Sin el modo táctil (ajuste TV_CAD_TACTIL apagado), en el teléfono los planos solo se ven. Con él, el teléfono edita en modo táctil.
  const TELEFONO = !C.tactil && matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
  // Varias pestañas: en la computadora, con la versión vigente. El modo táctil (teléfono, tableta) sigue con un plano por ventana.
  const P = C.pestanas && frame && !TELEFONO ? C.pestanas : null;
  const enTactil = () => document.documentElement.classList.contains("cad-tactil");

  // -- Pestañas: un plano cada una --------------------------------------------------------------------------------------------
  const CAMPOS = ["id", "nombre", "formato", "revision", "proyecto", "carpeta", "ubicacion", "version", "version_formato", "puede_editar", "puede_eliminar",
    "rol_lectura", "borrador", "urls", "modificado_por", "actualizado", "autor", "creado", "bytes"];
  const pestanas = [];
  let activa = null;
  function nuevaPestana(cfg, versiones) {
    const t = {
      cfg, sesion: nuevaSesion(), versiones: versiones || null, ajena: !!cfg.ajena,
      e: { revision: cfg.revision, base: cfg.revision, formato: cfg.version_formato || cfg.formato, nombre: cfg.nombre,
        listo: false, cargado: false, editable: false, sucio: false, sucioDesde: 0, ultimoBorrador: 0,
        guardando: false, bufferPendiente: null, lecturaMotivo: "", perdido: false, salirDespues: null, recuperado: false },
      documento: null, revEditor: 0, revGuardada: 0, revBorrador: null, revTelefono: null, borradorServidor: null,
      latido: null, vigia: null, borradorEnCurso: false, cerrando: false,
      aviso: { lectura: null, error: null, borrador: "" },
    };
    pestanas.push(t);
    return t;
  }
  const t0 = nuevaPestana(Object.fromEntries(CAMPOS.map((k) => [k, C[k]])), C.versiones || null);
  activa = t0;
  const sucia = (t) => !!(t.e.sucio || t.e.recuperado);
  // Lo que «Guardar todo» intenta: cada pestaña con cambios que esta cuenta podría guardar (si otra persona tiene el bloqueo, se dice).
  const guardable = (t) => sucia(t) && t.e.cargado && (t.e.editable || t.e.perdido || t.ajena || !!t.cfg.puede_editar);

  // Avisos para las extensiones de la página (control.js, `window.TVCad`): siempre de la pestaña activa.
  const anunciar = (nombre, detalle) => document.dispatchEvent(new CustomEvent(`tvcad:${nombre}`, { detail: detalle }));
  const anunciarDe = (t, nombre, detalle) => { if (t === activa) anunciar(nombre, detalle); };
  const datosCargado = (t) => ({ editable: t.e.editable, documento: t.documento, plano: { id: t.cfg.id, nombre: t.e.nombre, formato: t.e.formato, revision: t.e.revision } });

  // -- Estado visible ---------------------------------------------------------------------------------------------------------
  const chip = $("cad-estado"), chipTexto = $("cad-estado-texto"), botonGuardar = $("cad-guardar");
  function estadoDe(t) {
    if (!t.e.cargado) return ["cargando", "Abriendo…"];
    if (t.e.guardando) return ["guardando", "Guardando…"];
    if (t.ajena) return [sucia(t) ? "sucio" : "lectura", "Fuera de la biblioteca"];
    if (!t.e.editable) return ["lectura", "Solo lectura"];
    if (sucia(t)) return ["sucio", "Sin guardar"];
    return ["guardado", "Guardado"];
  }
  function refrescarEstado(t = activa) {
    pintarPestana(t);
    pintarGuardarTodo();
    if (t !== activa || !chip) return;
    const [estado, texto] = estadoDe(t);
    chip.dataset.estado = estado;
    // El plano se abrió con la copia de este teléfono (la vigente, confirmada por el servidor): no se bajó.
    chipTexto.textContent = t.e.desdeTelefono ? `${texto} · Desde el teléfono` : texto;
    const punto = chip.querySelector(".tv-punto");
    punto.className = "tv-punto " + ({ guardado: "tv-punto--exito", sucio: "tv-punto--aviso", error: "tv-punto--error" }[estado] || "tv-punto--neutro");
    if (botonGuardar) {
      botonGuardar.disabled = t.ajena ? !(t.e.cargado && C.puede_editar) || t.e.guardando
        : !((t.e.editable || t.e.perdido) && t.e.cargado) || t.e.guardando || !sucia(t);
      botonGuardar.querySelector("span").textContent = t.ajena ? "Guardar en la biblioteca" : t.e.perdido ? "Guardar como copia" : "Guardar";
    }
  }
  // `descargar`: el error es de guardar; se ofrece bajar los cambios a este equipo (auditoría M-3: con la biblioteca llena, un plano
  // demasiado grande, el servidor caído o sin red, la persona no tenía otra forma de conservar su trabajo).
  function error(t, texto, { descargar = false } = {}) {
    t.aviso.error = texto ? { texto, descargar } : null;
    if (t === activa) pintarError(t);
  }
  function pintarError(t) {
    const caja = $("cad-aviso-error"), a = t.aviso.error;
    caja.hidden = !a;
    $("cad-aviso-error-texto").textContent = a ? a.texto : "";
    const acciones = $("cad-aviso-error-acciones");
    if (acciones) acciones.hidden = !(a && a.descargar && t.e.cargado);
  }
  function lectura(t, texto, { tomar = false, reintentar = false } = {}) {
    t.aviso.lectura = texto ? { texto, tomar, reintentar } : null;
    if (t === activa) pintarLectura(t);
  }
  function pintarLectura(t) {
    const a = t.aviso.lectura;
    $("cad-aviso-lectura").hidden = !a;
    $("cad-aviso-lectura-texto").textContent = a ? a.texto : "";
    $("cad-tomar").hidden = !(a && a.tomar);
    $("cad-reintentar").hidden = !(a && a.reintentar);
  }
  function carga(texto) {
    const c = $("cad-carga");
    if (!c) return;
    c.hidden = !texto;
    if (texto) $("cad-carga-texto").textContent = texto;
  }
  const cargaDe = (t, texto) => { if (t === activa) carga(texto); };
  // «Abriendo el plano… 60 %»: bajar el archivo es lo primero (hasta 35 %), luego el editor lo lee (35–95 %, lo que dice su `open_progress`)
  // y arma la primera vista. Nunca dice 100 % antes de que el plano se vea.
  const avance = (t, fraccion) => cargaDe(t, `Abriendo el plano… ${Math.max(0, Math.min(99, Math.round(fraccion * 100)))} %`);
  // El cuerpo de una respuesta, contando lo que llega (Content-Length; sin él, el avance espera al final).
  async function leerConAvance(r, alAvance) {
    const total = Number(r.headers.get("Content-Length")) || 0;
    if (!total || !r.body || !r.body.getReader) return r.arrayBuffer();
    const lector = r.body.getReader(), bytes = new Uint8Array(total);
    let n = 0;
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;
      if (n + value.length > total) return new Blob([bytes.subarray(0, n), value, ...(await restoDe(lector))]).arrayBuffer();
      bytes.set(value, n); n += value.length;
      alAvance(n / total);
    }
    return n === total ? bytes.buffer : bytes.slice(0, n).buffer;
  }
  async function restoDe(lector) { const partes = []; for (;;) { const { done, value } = await lector.read(); if (done) return partes; partes.push(value); } }

  // -- Peticiones -------------------------------------------------------------------------------------------------------------
  let sinSesion = false;
  async function pedir(url, { metodo = "GET", json, cuerpo, cabeceras = {}, keepalive = false, cache } = {}) {
    const opciones = { method: metodo, credentials: "same-origin", keepalive, headers: { Accept: "application/json", ...cabeceras } };
    if (cache) opciones.cache = cache;
    if (metodo !== "GET") opciones.headers["X-CSRFToken"] = csrf();
    if (json !== undefined) { opciones.headers["Content-Type"] = "application/json"; opciones.body = JSON.stringify(json); }
    else if (cuerpo !== undefined) opciones.body = cuerpo;
    const r = await fetch(url, opciones);
    if (r.status === 401) { sesionTerminada(); throw new Error("Tu sesión terminó. Entra de nuevo en otra pestaña y vuelve aquí para guardar."); }
    return r;
  }

  // -- Sesión terminada (auditoría M-4) -----------------------------------------------------------------------------------------
  // Nunca se recarga la página: se perdería lo que no alcanzó a entrar al borrador. Se avisa con una franja fija, se pausan los latidos y
  // se ofrece entrar en otra pestaña y descargar los cambios; al volver a esta pestaña con la sesión de nuevo, todo sigue como estaba.
  function sesionTerminada() {
    if (sinSesion) return;
    sinSesion = true;
    for (const t of pestanas) clearInterval(t.latido);
    const caja = $("cad-aviso-sesion");
    if (caja) {
      caja.hidden = false;
      const b = caja.querySelector("[data-cad-descargar-cambios]");
      if (b) b.hidden = !sucia(activa);
    }
  }
  async function comprobarSesion() {
    if (!sinSesion || activa.ajena) return;
    let r;
    try { r = await fetch(`${activa.cfg.urls.estado}?sesion=${activa.sesion}&ligero=1`, { credentials: "same-origin", headers: { Accept: "application/json" } }); }
    catch (_) { return; }                                                  // sin red: se vuelve a intentar al volver a la pestaña
    if (r.status === 401 || !r.ok) return;
    sinSesion = false;
    const caja = $("cad-aviso-sesion"); if (caja) caja.hidden = true;
    for (const t of pestanas) {
      if (!t.e.editable || t.ajena) continue;
      latir(t);
      try { const d = await bloqueo(t, "latido"); if (d.perdido && d.motivo === "cambio") cambioAjeno(t, d.quien); else if (d.perdido) perderBloqueo(t, d); } catch (_) { /* el latido sigue */ }
    }
    avisar("Sesión recuperada: ya puedes guardar.");
  }
  addEventListener("focus", comprobarSesion);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") comprobarSesion(); });
  async function datos(r) { try { return await r.json(); } catch (_) { return {}; } }
  // El plano que se sube (guardar, copia, borrador), con gzip si así pesa menos (cad/sesion.js; el servidor lo acepta: Content-Encoding).
  const comprimir = (b) => (window.TVCadSesion && window.TVCadSesion.comprimir ? window.TVCadSesion.comprimir(b) : Promise.resolve({ cuerpo: b, cabeceras: {} }));

  // -- El editor: canal de control oficial (`?automation=1`) -----------------------------------------------------------------
  // Con el canal se abre cada plano, se lee su estado (cambios sin guardar) y se piden sus bytes para guardar o para el borrador. Lo
  // que la persona guarda DENTRO del editor (Ctrl+S, Guardar, el aviso al cerrar) llega por `openCadStudioSaveFile`, con el documento
  // (desde tv-v2026.45-12), y termina en `recibirGuardado` de su pestaña.
  let control = null, nPeticion = 0;
  const SERIE = nuevaSesion().slice(0, 8);

  function alEditor(tipo, campos = {}, transferir) {
    if (!frame.contentWindow) return;
    frame.contentWindow.postMessage({ source: "opencadstudio-host", type: tipo, ...campos }, location.origin, transferir || []);
  }
  async function canal(peticion, tiempoMs = 120000) {
    if (!control) throw new Error("El editor no está listo.");
    nPeticion += 1;
    const r = await control.request({ protocol: 1, request_id: `tv-${SERIE}-${nPeticion}-${Date.now()}`, ...peticion }, { timeoutMs: tiempoMs });
    return r || {};
  }
  // Abrir, cambiar de pestaña y cerrar no se pueden con una orden de la persona a medias (`command_busy`): como AutoCAD al cambiar de
  // dibujo, la orden se cancela (un Esc) y se vuelve a pedir una vez.
  async function canalLibre(peticion, tiempoMs) {
    let r = await canal(peticion, tiempoMs);
    if (r && r.ok === false && r.code === "command_busy") {
      await canal({ op: "cancel", document_id: r.document_id }, 10000).catch(() => {});
      r = await canal(peticion, tiempoMs);
    }
    return r;
  }
  // Lo que cambia los documentos del editor (abrir, cerrar, activar) va de uno en uno: el editor abre un plano a la vez.
  let cadena = Promise.resolve(), enCurso = 0;
  function enCola(fn) {
    enCurso += 1;
    const r = cadena.then(() => fn());
    cadena = r.catch(() => {}).finally(() => { enCurso -= 1; });
    return r;
  }
  function base64(buffer) {
    const bytes = new Uint8Array(buffer); let texto = "";
    for (let i = 0; i < bytes.length; i += 0x8000) texto += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(texto);
  }
  function deBase64(texto) {
    const crudo = atob(texto); const bytes = new Uint8Array(crudo.length);
    for (let i = 0; i < crudo.length; i++) bytes[i] = crudo.charCodeAt(i);
    return bytes.buffer;
  }
  async function esperarCanal() {
    for (let i = 0; i < 600; i++) {
      let c = null;
      try { c = frame.contentWindow && frame.contentWindow.ocsControl; } catch (_) { c = null; }
      if (c) { await c.ready; return c; }
      await espera(200);
    }
    throw new Error("El editor no terminó de abrir. Recarga la página.");
  }
  function engancharGuardadoDelEditor() {
    try {
      // Lo que la persona guarda en el editor llega aquí, sin diálogo de descarga. `documento`: de cuál pestaña es (tv-v2026.45-12).
      frame.contentWindow.openCadStudioSaveFile = (nombre, bytes, documento) => {
        const ext = String(nombre || "").split(".").pop().toLowerCase();
        const copia = bytes.slice().buffer;
        const t = pestanas.find((x) => documento !== undefined && x.documento === Number(documento)) || activa;
        if (ext === "dwg" || ext === "dxf") { recibirGuardado(t, { buffer: copia, formato: ext, origen: "editor" }); return; }
        const a = document.createElement("a");                       // otras exportaciones (PDF, PNG, SVG…): descarga normal
        a.href = URL.createObjectURL(new Blob([copia])); a.download = nombre || "exportacion"; document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      };
    } catch (_) { /* marco de otro origen: no puede pasar (mismo origen por diseño) */ }
  }
  async function cargarMarco() {
    control = null;
    const listo = new Promise((r) => frame.addEventListener("load", r, { once: true }));
    frame.src = C.urls.editor;
    await listo;
    control = await esperarCanal();
    engancharGuardadoDelEditor();
    try {
      const est = await canal({ op: "state" });
      if (est.modal) await canal({ op: "action", name: "close_modal", document_id: est.document_id });
    } catch (_) { /* sin diálogo que cerrar */ }
    // Con varias pestañas, las de la página son las únicas: las del editor no se muestran (solo en esta sesión del editor).
    if (P) await canal({ op: "file_tabs", on: false }, 10000).catch(() => {});
    await registrarFuentes();
  }

  // -- Fuentes SHX de la empresa (las sube quien administra en «Fuentes»): el editor las guarda solo en memoria, así que se registran
  // cada vez que se abre, antes del plano. Una que falla no detiene la apertura; quien administra ve un aviso discreto.
  const bytesFuente = new Map();                                          // por dirección (lleva la huella): un solo viaje por página
  async function registrarFuentes() {
    const lista = C.fuentes || [];
    if (!lista.length) return;
    const fallas = [];
    await Promise.all(lista.map(async (f) => {
      if (bytesFuente.has(f.url)) return;
      try { const r = await fetch(f.url, { credentials: "same-origin" }); if (r.ok) bytesFuente.set(f.url, base64(await r.arrayBuffer())); } catch (_) { /* abajo cuenta como falla */ }
    }));
    for (const f of lista) {
      const d = bytesFuente.get(f.url);
      let res = null;
      if (d) { try { res = await canal({ op: "font_register", file_name: f.nombre, data_base64: d }, 15000); } catch (_) { res = null; } }
      const ok = !!(res && res.ok !== false && res.result);
      if (!ok) fallas.push(f.nombre);
      const glifos = ok ? res.result.shapes : null;
      if (ok ? (glifos !== f.glifos || f.error) : !f.error) {
        fetch(f.informe, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          // El motivo es un código de la lista cerrada del servidor (auditoría B-9 y B-2): el texto del editor viene en inglés y la página
          // de Fuentes lo mostraba tal cual; el servidor guarda el texto en español que corresponde al código.
          body: JSON.stringify({ ok: ok && glifos !== 0, glifos, codigo: ok ? (glifos === 0 ? "sin_letras" : "") : "rechazada" }) }).catch(() => {});
      }
    }
    const aviso = $("cad-aviso-fuentes");
    if (aviso && C.es_admin && fallas.length) {
      $("cad-aviso-fuentes-texto").textContent = `${fallas.length === 1 ? "Una fuente no se pudo usar" : `${fallas.length} fuentes no se pudieron usar`} (${fallas.join(", ")}): sus textos se ven con la de reemplazo.`;
      aviso.hidden = false;
    }
  }

  async function bytesDe(url, alAvance) {
    const r = await pedir(url);
    if (!r.ok) throw new Error(r.status === 404 ? "El plano ya no existe." : "No se pudo descargar el plano. Recarga la página.");
    return alAvance ? leerConAvance(r, alAvance) : r.arrayBuffer();
  }

  // -- La copia del teléfono («Disponible sin conexión», campo.js; solo el modo táctil, una pestaña) ----------------------------
  // Con la copia del plano en este teléfono, abrirlo con conexión no lo baja otra vez: se pregunta al servidor si sigue siendo la versión
  // vigente (If-None-Match con su SHA-256; 304 sin cuerpo) y se abre la del teléfono. Si hay una versión nueva, llega una sola vez y
  // reemplaza la copia. Nunca se abre una versión vieja sin decirlo: la respuesta del servidor manda. En la computadora, sin copia, la
  // pregunta lleva el ETag de la versión que el navegador ya tiene (304 sin cuerpo cuando no cambió).
  const Campo = (t) => (C.tactil && t === t0 && window.TVCadCampo ? window.TVCadCampo : null);
  async function copiaDelTelefono(t) {
    const campo = Campo(t);
    if (!campo || t.cfg.version) return null;
    try { return await campo.copiaVigente(t.cfg.id); } catch (_) { return null; }
  }
  const COPIA_MAX = 25 * 1048576;
  async function bytesVigente(t) {
    const copia = await copiaDelTelefono(t);
    const r = await pedir(t.cfg.urls.archivo, { cabeceras: copia ? { "If-None-Match": `"${copia.sha256}"` } : {}, cache: copia ? "no-store" : "no-cache" });
    const revision = Number(r.headers.get("X-TV-Revision")) || t.cfg.revision;
    if (r.status === 304 && copia) {
      t.e.desdeTelefono = true;
      if (revision !== copia.revision) Campo(t).actualizarCopia(t.cfg.id, { revision }).catch(() => {});     // mismo archivo, otro número (restaurada)
      return copia.bytes;
    }
    if (!r.ok) throw new Error(r.status === 404 ? "El plano ya no existe." : "No se pudo descargar el plano. Recarga la página.");
    const buffer = await leerConAvance(r, (f) => avance(t, 0.35 * f));
    t.e.desdeTelefono = false;
    if (copia) {
      await Campo(t).actualizarCopia(t.cfg.id, { bytes: buffer.slice(0), revision, formato: t.e.formato }).catch(() => {});
      avisar(`Había una versión nueva (la ${revision}): se descargó y quedó en el teléfono.`);
    } else if (C.tactil && t === t0 && buffer.byteLength <= COPIA_MAX) {
      t.e.abiertos = { revision, bytes: buffer.slice(0) };                // si la persona lo deja «Disponible sin conexión», no se baja otra vez
    }
    return buffer;
  }
  async function bytesDeLaCopia(t) {
    const c = Campo(t) && (await Campo(t).cambios(t.cfg.id));
    if (!c) throw new Error("Los cambios de este teléfono ya no están.");
    return c.bytes;
  }

  // El nombre del plano dentro del editor; dos pestañas con el mismo nombre llevan un número (el editor avisaría que «comparten copia»).
  function nombreEnEditor(t, formato) {
    const base = t.e.nombre, iguales = pestanas.filter((x) => x !== t && x.e.nombre === base && x.documento).length;
    return iguales ? `${base} (${iguales + 1}).${formato}` : `${base}.${formato}`;
  }
  async function documentosDelEditor() {
    const est = await canal({ op: "state" }, 10000).catch(() => ({}));
    return est.documents || [];
  }

  // Abre los bytes del plano en el editor, en un documento nuevo; si la pestaña ya tenía uno (recuperar el borrador, «Editar ahora»,
  // restaurar una versión), ese se cierra después: el editor no se vuelve a cargar y las demás pestañas siguen abiertas.
  async function abrir(t, fuente, soloLectura) {
    t.e.cargado = false; t.e.sucio = false;
    refrescarEstado(t);
    avance(t, 0);
    const url = t.cfg.urls.archivo, bajando = (f) => avance(t, 0.35 * f);
    let formato = t.e.formato, buffer;
    if (fuente === "borrador") {
      formato = (t.cfg.borrador && t.cfg.borrador.formato) || t.e.formato;
      buffer = t.cfg.borrador && t.cfg.borrador.local ? await bytesDeLaCopia(t) : await bytesDe(`${url}?borrador=1`, bajando);
    } else if (t.cfg.version) buffer = await bytesDe(`${url}?version=${t.cfg.version}`, bajando);
    else buffer = await bytesVigente(t);
    avance(t, 0.35);
    // Un plano grande tarda más en abrir en el editor (medido: docs/cad.md, «Planos grandes»): los topes crecen con su tamaño.
    const tope = 120000 + 4000 * Math.ceil(buffer.byteLength / 1048576);
    t.e.editable = !soloLectura;
    return enCola(async () => {
      if (t.cerrando) return;
      const anterior = t.documento;
      const antes = new Set((await documentosDelEditor()).map((d) => d.id));
      // El plano cuenta como abierto cuando aparece su documento. La respuesta de «open» puede tardar o venir con un error que no es del
      // plano (WebKit: el editor no logra guardar su lista de recientes en el almacenamiento del navegador), así que no se espera solo a ella.
      let respuesta = null;
      const pedido = canalLibre({ op: "open", name: nombreEnEditor(t, formato), data_base64: base64(buffer) }, tope).then((r) => { respuesta = r; }, () => { respuesta = { ok: false }; });
      buffer = null;                                                       // lo que queda es la copia del editor
      let documento = null;
      for (const hasta = Date.now() + tope, desde = Date.now(); Date.now() < hasta && !documento;) {
        const est = await canal({ op: "state" }, 10000).catch(() => ({}));
        const doc = (est.documents || []).find((d) => !d.start && !antes.has(d.id));
        if (doc) { documento = doc.id; break; }
        if (Number.isFinite(est.open_progress)) avance(t, 0.35 + 0.6 * est.open_progress / 100);
        if (respuesta && respuesta.ok === false && Date.now() - desde > 2000) break;
        await espera(150);
      }
      if (!documento) {
        await Promise.race([pedido, espera(1000)]);
        cargaDe(t, ""); error(t, "No se pudo abrir el plano en el editor. Descárgalo y revísalo en tu programa de dibujo.");
        return;
      }
      t.documento = documento;
      // La pestaña del plano aparece antes de que el editor termine de abrirlo. La revisión de partida («nada cambiado») se toma cuando
      // terminó: con la respuesta de «open» y la revisión quieta. Antes se tomaba al ver la pestaña y, si el editor la movía mientras
      // terminaba de abrir un plano grande, el plano quedaba «Sin guardar» sin que nadie lo tocara; y la capa táctil empezaba a pedirle
      // cosas al editor a media apertura.
      avance(t, 0.97);
      await Promise.race([pedido, espera(tope)]);
      if (anterior && anterior !== documento) await canal({ op: "close", document_id: anterior, discard: true }, 30000).catch(() => {});
      // Abrir deja el plano nuevo al frente: si es otra pestaña la que se ve (se abre detrás, al recuperar las pestañas), vuelve la vista.
      if (activa !== t && activa.documento) await canalLibre({ op: "activate", document_id: activa.documento }, 30000).catch(() => {});
      t.revEditor = t.revGuardada = await revisionQuieta(t);
      if (window.TVCad && window.TVCad.anotar) window.TVCad.anotar({ op: "abierto", ok: !(respuesta && respuesta.ok === false), revision: t.revGuardada });
      alCargar(t);
    });
  }
  // La revisión del plano cuando deja de moverse (dos lecturas iguales seguidas; a lo más unos segundos).
  async function revisionQuieta(t) {
    let antes = null;
    for (let i = 0; i < 20; i++) {
      const doc = (await documentosDelEditor()).find((d) => d.id === t.documento);
      const rev = doc ? doc.revision || 0 : 0;
      if (rev === antes) return rev;
      antes = rev;
      await espera(150);
    }
    return antes || 0;
  }
  function alCargar(t) {
    t.e.cargado = true; t.e.sucio = false;
    cargaDe(t, "");
    refrescarEstado(t);
    // Cada pestaña, también las de atrás (la carpeta del proyecto de su plano, local/local.js); luego la activa como siempre.
    anunciar("pestana", { accion: "abierta", plano: { id: t.cfg.id, nombre: t.e.nombre }, documento: t.documento });
    if (t === activa) { pintarError(t); anunciar("cargado", datosCargado(t)); }
  }

  window.addEventListener("message", (ev) => {
    if (!frame || ev.origin !== location.origin || ev.source !== frame.contentWindow) return;
    const m = ev.data || {};
    if (m.source === "tresvizo-cad-editor") {
      // Atajos de la página con el foco en el editor (cad/editor/arranque.js los reenvía).
      if (m.type === "guardar") { if (C.puede_editar) guardarActiva(); return; }
      if (m.type === "guardar-todo") { if (C.puede_editar) guardarTodo(); return; }
      return;
    }
    if (m.source !== "opencadstudio-embed") return;
    // Compilaciones con el puente `?embed=1`: lo que se guarda dentro del editor llega así (sin documento: es el que se ve).
    if (m.type === "ocs:save") { recibirGuardado(activa, { buffer: m.buffer, formato: m.format, origen: "editor", saveId: m.saveId }); return; }
    if (m.type === "ocs:error") alError(m);
  });
  function alError(m) {
    if (m.code === "host_save_failed") return;                           // lo provocamos nosotros al rechazar un guardado
    if (m.code === "read_only") { avisar(activa.e.lecturaMotivo || "Este plano está abierto en solo lectura.", "error"); return; }
    if (m.code === "open_failed") { carga(""); error(activa, "No se pudo abrir el plano en el editor. Descárgalo y revísalo en tu programa de dibujo."); return; }
    if (m.code === "save_failed") { activa.e.guardando = false; refrescarEstado(); error(activa, "El editor no pudo preparar el archivo para guardarlo. Inténtalo de nuevo."); }
  }

  // Cambios sin guardar: la revisión de cada dibujo en el editor contra la que tenía al abrirlo o al guardarlo en la biblioteca. Una sola
  // lectura del estado por segundo para todas las pestañas.
  let consultando = false;
  setInterval(async () => {
    if (consultando || !control || document.visibilityState === "hidden" || !pestanas.some((t) => t.e.cargado && t.documento)) return;
    consultando = true;                                                    // nunca dos consultas a la vez: en un equipo lento se apilarían
    try {
      const est = await canal({ op: "state" }, 5000);
      const docs = est.documents || [];
      for (const t of pestanas.slice()) {
        if (!t.e.cargado || !t.documento || t.cerrando) continue;
        const doc = docs.find((d) => d.id === t.documento);
        if (!doc) { if (enCurso === 0) documentoCerrado(t); continue; }
        if (t.ajena) { const s = !!doc.dirty; if (s !== t.e.sucio) { t.e.sucio = s; refrescarEstado(t); } continue; }
        alRevisar(t, doc.revision || 0);
      }
      if (P && enCurso === 0) seguirAlEditor(est, docs);
    } catch (_) { /* el editor está ocupado: la próxima vuelta */ }
    finally { consultando = false; }
  }, 1000);
  function alRevisar(t, revision) {
    t.revEditor = revision;
    const sucio = t.revEditor !== t.revGuardada;
    if ((sucio || t.e.recuperado) && t.revEditor !== t.revTelefono) programarCopia(t);
    if (sucio === t.e.sucio) return;
    t.e.sucio = sucio;
    if (sucio) t.e.sucioDesde = Date.now();
    if (t === activa) {
      // Para diagnosticar (consola del navegador, `TVCad.registro()`): qué le pidió la página al editor justo antes.
      if (window.TVCad && window.TVCad.anotar) window.TVCad.anotar({ sucio, revision: t.revEditor, base: t.revGuardada });
      // La primera vez en la sesión se guarda qué pasó antes, y va con el siguiente borrador (cad/registro.py).
      if (sucio && !t.e.registroSucio && !t.e.registroEnviado) t.e.registroSucio = registroParaServidor();
    }
    if (sucio && t.e.borradorPendiente) elegirBorrador(t);
    anunciarDe(t, "cambio", { sucio });
    refrescarEstado(t);
  }
  // El editor es la verdad de qué documento se ve: si cambió solo (se cerró uno desde una orden suya, o la persona hizo uno nuevo con
  // NUEVO o abrió un archivo de este equipo con ABRIR), la página lo sigue. Un documento que no es de la biblioteca sale como pestaña
  // «Fuera de la biblioteca», que se puede guardar en ella.
  // Un documento ajeno se adopta solo si sigue ahí unos segundos y ninguna extensión trabaja con el editor: Civil abre un dibujo temporal
  // con su resultado, lo copia al plano y lo cierra (insertarDxf); ese nunca es una pestaña, ni la vista se va a él.
  const vistos = new Map();
  function seguirAlEditor(est, docs) {
    if (extensionOcupada()) { vistos.clear(); return; }
    for (const id of [...vistos.keys()]) if (!docs.some((d) => d.id === id)) vistos.delete(id);
    for (const d of docs) {
      if (d.start || pestanas.some((t) => t.documento === d.id)) continue;
      vistos.set(d.id, (vistos.get(d.id) || 0) + 1);
      if (vistos.get(d.id) < 5 || pestanas.length >= P.max) continue;
      vistos.delete(d.id);
      const nombre = String(d.title || "Dibujo").replace(/\.(dwg|dxf)$/i, "");
      const ext = (String(d.title || "").match(/\.(dwg|dxf)$/i) || [, "dwg"])[1].toLowerCase();
      const t = nuevaPestana({ ajena: true, id: `editor-${d.id}`, nombre, formato: ext, revision: 0, proyecto: "", puede_editar: false,
        urls: {}, borrador: null }, []);
      t.documento = d.id; t.e.cargado = true; t.e.editable = false; t.e.sucio = !!d.dirty;
      pintarPestanas();
    }
    const visible = pestanas.find((t) => t.documento === est.document_id);
    if (visible && visible !== activa) mostrar(visible);
  }
  // El documento ya no está en el editor (CLOSE dentro del editor; si tenía cambios, el editor preguntó y lo guardado llegó por
  // `openCadStudioSaveFile`): la pestaña se quita.
  function documentoCerrado(t) {
    t.documento = null;
    quitar(t, { editor: false });
  }

  // -- Guardar ------------------------------------------------------------------------------------------------------------------
  // Bytes del plano tal como está en el editor (no cambia lo que la persona ve). `formato`: el del plano.
  async function bytesDelEditor(t) {
    const r = await canal({ op: "save", document_id: t.documento, name: `${t.e.nombre}.${t.e.formato}`, target_format: t.e.formato });
    if (r.ok === false && r.code === "command_busy") throw new Error("Termina o cancela (Esc) la orden que estás usando y vuelve a guardar.");
    if (r.ok === false || !r.result || !r.result.data_base64) throw new Error("El editor no pudo preparar el archivo. Inténtalo de nuevo.");
    return deBase64(r.result.data_base64);
  }

  // Los cambios tal como están en el editor, a un archivo de este equipo (no cambia nada en la biblioteca).
  async function descargarCambios(boton) {
    const t = activa;
    if (!t.e.cargado || !t.documento) return;
    if (boton) boton.setAttribute("aria-busy", "true");
    try {
      const buffer = await bytesDelEditor(t);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([buffer], { type: "application/octet-stream" }));
      a.download = `${t.e.nombre} (cambios sin guardar).${t.e.formato}`;
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      avisar("Tus cambios se descargaron a este equipo.");
    } catch (err) { avisar(err.message || "No se pudieron descargar los cambios.", "error"); }
    finally { if (boton) boton.removeAttribute("aria-busy"); }
  }
  document.addEventListener("click", (ev) => { const b = ev.target.closest("[data-cad-descargar-cambios]"); if (b) descargarCambios(b); });

  const extensionOcupada = () => !!(window.TVCad && window.TVCad.ocupado && window.TVCad.ocupado());
  // Resultado: {ok, revision?, motivo?}. `silencioso` (Guardar todo): un conflicto no abre su hoja, queda para cuando se vea la pestaña.
  async function pedirGuardado(t, destino, { silencioso = false } = {}) {
    if (!t.e.cargado || t.e.guardando) return { ok: false, motivo: "Se está guardando." };
    if (t.ajena) return destino === "borrador" ? { ok: false } : guardarAjena(t);
    if (destino !== "borrador" && !t.e.editable && !t.e.perdido) {
      const motivo = t.e.lecturaMotivo || "Este plano está abierto en solo lectura.";
      if (!silencioso) avisar(motivo, "error");
      return { ok: false, motivo };
    }
    if (destino !== "borrador" && extensionOcupada()) {
      const motivo = "Espera a que termine lo que estás haciendo en Civil y vuelve a guardar.";
      if (!silencioso) avisar(motivo, "error");
      return { ok: false, motivo };
    }
    if (destino !== "borrador") { t.e.guardando = true; refrescarEstado(t); }
    // La revisión que se guarda es la de este momento, no la de la última consulta (cada segundo): un cambio hecho por el canal justo antes
    // de «Guardar» (p. ej. Civil) dejaba el plano en «Sin guardar» aunque sí se guardó.
    let revision = t.revEditor;
    try { const doc = (await documentosDelEditor()).find((d) => d.id === t.documento); if (doc) revision = t.revEditor = doc.revision || 0; }
    catch (_) { /* se usa la última conocida */ }
    try {
      const buffer = await bytesDelEditor(t);
      if (destino === "borrador") { await guardarBorrador(t, buffer, revision); return { ok: true }; }
      return await recibirGuardado(t, { buffer, formato: t.e.formato, origen: "pagina", destino, revision, silencioso });
    } catch (err) {
      t.e.guardando = false; refrescarEstado(t);
      if (destino !== "borrador") { t.e.salirDespues = null; if (!silencioso) error(t, err.message); }
      return { ok: false, motivo: err.message };
    }
  }
  const guardarActiva = () => pedirGuardado(activa, activa.e.perdido ? "copia" : "plano");

  async function recibirGuardado(t, { buffer, formato, saveId, destino, revision, silencioso = false }) {
    const e = t.e;
    const responder = (ok, mensaje, nombre) => { if (saveId) alEditor("ocs:saved", { ok, saveId, error: mensaje, name: nombre }); };
    formato = formato === "dxf" ? "dxf" : "dwg";
    if (t.ajena) return guardarAjena(t, buffer);
    if (revision === undefined) {                                         // guardado hecho en el editor: su revisión es la de ahora
      try { const doc = (await documentosDelEditor()).find((d) => d.id === t.documento); revision = doc ? doc.revision : t.revEditor; }
      catch (_) { revision = t.revEditor; }
    }
    if (!e.editable && !e.perdido) {
      responder(false, "Solo lectura: este plano no se puede guardar aquí.");
      avisar(e.lecturaMotivo || "Este plano está abierto en solo lectura: no se guardó.", "error");
      return { ok: false, motivo: e.lecturaMotivo || "Solo lectura." };
    }
    e.guardando = true; refrescarEstado(t); error(t, "");
    if (destino === "copia" || e.perdido) {
      if (silencioso) { e.guardando = false; refrescarEstado(t); return { ok: false, motivo: `${e.lecturaMotivo} Guárdalo como copia desde su pestaña.`.trim() }; }
      return guardarCopia(t, buffer, saveId);
    }
    try {
      const { cuerpo, cabeceras: codificacion } = await comprimir(buffer);
      const r = await pedir(t.cfg.urls.guardar, { metodo: "POST", cuerpo, cabeceras: { "Content-Type": "application/octet-stream", "X-TV-Sesion": t.sesion, "X-TV-Revision": String(e.base), ...codificacion } });
      const d = await datos(r);
      if (r.ok) {
        e.revision = e.base = d.revision; e.formato = d.formato; e.recuperado = false;
        t.revGuardada = revision; e.sucio = t.revEditor !== t.revGuardada;
        t.cfg.proyecto = d.proyecto; t.cfg.modificado_por = d.modificado_por; t.cfg.actualizado = new Date().toISOString();
        t.versiones = d.versiones || t.versiones;
        // La copia del teléfono pasa a ser esta versión (con los bytes que se acaban de subir: no se baja nada) y sus cambios ya no hacen falta.
        if (Campo(t)) Campo(t).alGuardarVersion(t.cfg.id, { bytes: buffer, revision: d.revision, formato: d.formato }).catch(() => {});
        t.revTelefono = t.revBorrador = null;
        responder(true, undefined, `${e.nombre}.${d.formato}`);
        ocultarBorrador(t);
        if (t === activa) pintarCabecera(t);
        if (!silencioso) avisar(pestanas.length > 1 ? `«${e.nombre}» guardado · versión ${d.revision}` : `Guardado · versión ${d.revision}`);
        anunciarDe(t, "guardado", { revision: d.revision, formato: d.formato });
        e.guardando = false; refrescarEstado(t);
        if (e.salirDespues) { const destinoSalida = e.salirDespues; e.salirDespues = null; salir(destinoSalida); }
        return { ok: true, revision: d.revision };
      }
      responder(false, d.error || "No se guardó.");
      e.guardando = false; e.salirDespues = null; refrescarEstado(t);
      if (r.status === 409) { e.bufferPendiente = buffer; if (!silencioso) conflicto(t, d.error); else e.conflicto = d.error; return { ok: false, motivo: d.error || "El plano cambió mientras lo editabas.", conflicto: true }; }
      error(t, d.error || "No se pudo guardar. Inténtalo de nuevo.", { descargar: true });
      return { ok: false, motivo: d.error || "No se pudo guardar." };
    } catch (err) {
      responder(false, "Sin conexión: no se guardó.");
      e.guardando = false; e.salirDespues = null; refrescarEstado(t);
      const motivo = "No se pudo guardar: revisa tu conexión e inténtalo de nuevo. Tus cambios siguen en el editor.";
      error(t, motivo, { descargar: true });
      return { ok: false, motivo };
    }
  }

  let conflictoDe = null;
  function conflicto(t, texto) {
    conflictoDe = t;
    $("cad-conflicto-texto").textContent = texto || "El plano cambió mientras lo editabas.";
    const d = $("cad-conflicto");
    if (!d.open) d.showModal();
  }

  async function guardarCopia(t, buffer, saveId) {
    const e = t.e;
    try {
      const { cuerpo, cabeceras: codificacion } = await comprimir(buffer);
      const r = await pedir(t.cfg.urls.copia, { metodo: "POST", cuerpo, cabeceras: { "Content-Type": "application/octet-stream", ...codificacion } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo guardar la copia.");
      if (Campo(t)) await Campo(t).descartarCambiosDePagina(t.cfg.id).catch(() => {});
      if (saveId) alEditor("ocs:saved", { ok: true, saveId, name: `${d.nombre}.${e.formato}` });
      e.sucio = false; e.recuperado = false; e.guardando = false;
      soltar(t);
      avisar(`Se guardó «${d.nombre}»`);
      // Con pestañas, la misma pestaña pasa a ser la copia (el dibujo sigue en el editor, sin abrirlo otra vez); si no, se va a ella.
      if (P && !enTactil()) { await convertirEn(t, d.id, revisionActual(t)); return { ok: true }; }
      ir(d.url);
      return { ok: true };
    } catch (err) {
      if (saveId) alEditor("ocs:saved", { ok: false, saveId, error: err.message });
      e.guardando = false; e.salirDespues = null; refrescarEstado(t);
      error(t, err instanceof TypeError ? "No se pudo guardar la copia: revisa tu conexión e inténtalo de nuevo. Tus cambios siguen en el editor." : err.message, { descargar: true });
      return { ok: false, motivo: err.message };
    }
  }
  const revisionActual = (t) => t.revEditor;
  // La pestaña pasa a ser el plano `id` de la biblioteca (una copia recién guardada, o un dibujo del editor que se guardó en ella), con
  // su bloqueo: lo que se ve en el editor ya es esa versión.
  async function convertirEn(t, id, revision) {
    const r = await pedir(P.pestana.replace(MARCA, id));
    const d = await datos(r);
    if (!r.ok) throw new Error(d.error || "No se pudo abrir el plano nuevo.");
    clearInterval(t.latido); clearInterval(t.vigia);
    t.cfg = d.plano; t.versiones = d.versiones; t.ajena = false; t.sesion = nuevaSesion();
    Object.assign(t.e, { revision: d.plano.revision, base: d.plano.revision, formato: d.plano.formato, nombre: d.plano.nombre, perdido: false,
      sucio: false, recuperado: false, lecturaMotivo: "", borradorPendiente: false, bufferPendiente: null, guardando: false });
    t.revGuardada = revision; t.revBorrador = null; t.aviso = { lectura: null, error: null, borrador: "" };
    const b = await bloqueo(t, "tomar").catch(() => ({}));
    t.e.editable = !!b.mio;
    if (b.mio) latir(t);
    if (t === activa) { pintarCabecera(t); anunciar("cargado", datosCargado(t)); anunciar("editable", { editable: t.e.editable }); }
    refrescarEstado(t);
    guardarPestanas();
  }
  // Un dibujo del editor que no es de la biblioteca: «Guardar en la biblioteca» lo deja como plano nuevo.
  async function guardarAjena(t, buffer) {
    if (!C.puede_editar || !P) return { ok: false, motivo: "No es un plano de la biblioteca." };
    t.e.guardando = true; refrescarEstado(t);
    try {
      const revision = (await documentosDelEditor()).find((d) => d.id === t.documento);
      const bytes = buffer || (await bytesDelEditor(t));
      const { cuerpo, cabeceras: codificacion } = await comprimir(bytes);
      // Queda en la carpeta del plano con que se abrió la página (lo normal: un dibujo nuevo del mismo proyecto).
      const r = await pedir(P.desde_editor, { metodo: "POST", cuerpo, cabeceras: { "Content-Type": "application/octet-stream", "X-TV-Nombre": encodeURIComponent(t.e.nombre),
        ...(C.carpeta ? { "X-TV-Carpeta": C.carpeta } : {}), ...codificacion } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo guardar en la biblioteca.");
      await convertirEn(t, d.id, revision ? revision.revision || 0 : 0);
      avisar(`«${d.nombre}» quedó en la biblioteca.`);
      return { ok: true, revision: 1 };
    } catch (err) {
      t.e.guardando = false; refrescarEstado(t);
      error(t, err instanceof TypeError ? "No se pudo guardar: revisa tu conexión." : err.message);
      return { ok: false, motivo: err.message };
    }
  }

  $("cad-guardar-copia").addEventListener("click", async () => {
    $("cad-conflicto").close();
    const t = conflictoDe || activa;
    if (t.e.bufferPendiente) { const b = t.e.bufferPendiente; t.e.bufferPendiente = null; t.e.guardando = true; refrescarEstado(t); return guardarCopia(t, b, null); }
    pedirGuardado(t, "copia");
  });

  if (botonGuardar) botonGuardar.addEventListener("click", () => { guardarActiva(); enfocarEditor(); });

  // -- Guardar todo ------------------------------------------------------------------------------------------------------------
  // Cada pestaña con cambios se guarda como su versión, una tras otra. Si todas se guardan, un aviso; si alguna no (otra persona tiene el
  // bloqueo, el plano cambió, sin red), una hoja dice cuáles sí y cuáles no, y por qué. Nada se guarda como copia sin que se pida.
  async function guardarTodo() {
    const cuales = pestanas.filter(guardable);
    if (!cuales.length) { avisar("No hay cambios sin guardar."); return { ok: true, filas: [] }; }
    const filas = [];
    for (const t of cuales) filas.push({ t, ...(await (t.ajena ? guardarAjena(t) : pedirGuardado(t, "plano", { silencioso: true }))) });
    const bien = filas.filter((f) => f.ok), mal = filas.filter((f) => !f.ok);
    if (!mal.length) {
      avisar(bien.length === 1 ? `Se guardó «${bien[0].t.e.nombre}» · versión ${bien[0].revision}` : `Se guardaron ${bien.length} planos.`);
    } else mostrarGuardarTodo(filas);
    return { ok: !mal.length, filas };
  }
  function mostrarGuardarTodo(filas) {
    const d = $("cad-guardar-todo");
    if (!d) return;
    const bien = filas.filter((f) => f.ok).length;
    $("cad-guardar-todo-titulo").textContent = bien ? `Se guardaron ${bien} de ${filas.length} planos` : (filas.length === 1 ? "No se guardó el plano" : "No se guardó ningún plano");
    $("cad-guardar-todo-lista").replaceChildren(...filas.map((f) => {
      const li = document.createElement("li"); li.dataset.estado = f.ok ? "guardado" : "no";
      li.append(icono(f.ok ? "circle-check" : "triangle-alert"));
      const texto = document.createElement("div"); texto.className = "cad-guardar-todo__texto";
      const fuerte = document.createElement("strong"); fuerte.textContent = f.t.e.nombre;
      const tenue = document.createElement("span"); tenue.className = "tv-tenue";
      tenue.textContent = f.ok ? `Guardado · versión ${f.revision}` : `No se guardó: ${f.motivo || "inténtalo de nuevo."}`;
      texto.append(fuerte, tenue); li.append(texto);
      if (!f.ok && pestanas.includes(f.t)) {
        const b = document.createElement("button"); b.type = "button"; b.className = "tv-boton tv-boton--secundario"; b.textContent = "Ver";
        b.addEventListener("click", () => { d.close(); activar(f.t).then(() => { if (f.t.e.bufferPendiente) conflicto(f.t, f.t.e.conflicto); }); });
        li.append(b);
      }
      return li;
    }));
    if (!d.open) d.showModal();
  }
  function pintarGuardarTodo() {
    const b = $("cad-guardar-todo-boton");
    if (!b) return;
    const n = pestanas.filter(guardable).length;
    b.disabled = !n;
    $("cad-guardar-todo-texto").textContent = n > 1 ? `Guardar todo (${n})` : "Guardar todo";
  }
  if ($("cad-guardar-todo-boton")) $("cad-guardar-todo-boton").addEventListener("click", () => { $("cad-menu").open = false; guardarTodo(); });

  document.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && !ev.altKey && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      if (!C.puede_editar) return;
      if (ev.shiftKey) { if (P) guardarTodo(); return; }
      guardarActiva();
    }
  });

  // -- Borrador de recuperación ---------------------------------------------------------------------------------------------
  // El borrador lleva la sesión de esta pestaña y, tras recuperar o guardar como copia el de la última vez, cuál era ese
  // (`X-TV-Borrador-Previo`): el servidor nunca deja que otra página pise en silencio un borrador que nadie ha decidido (auditoría
  // M-6). Si lo hay (409 `borrador_pendiente`), se pide elegir.
  // Solo nombres de operaciones, tiempos, si se aceptaron y revisiones: nada del dibujo (ni coordenadas, ni capas, ni órdenes).
  function registroParaServidor() {
    const filas = (window.TVCad && window.TVCad.registro ? window.TVCad.registro() : []).slice(-40);
    const num = (v) => (Number.isFinite(v) ? Math.round(v) : null);
    const r = filas.filter((x) => x.op && /^[a-z_-]{1,24}$/.test(x.op)).map((x) => [num(x.t), x.op, x.ok === false ? 0 : 1, num(x.revision)]);
    const s = filas.filter((x) => x.sucio !== undefined).map((x) => [num(x.t), num(x.revision), num(x.base)]);
    let texto = JSON.stringify({ v: 1, m: enTactil() ? "t" : "c", r, s });
    while (texto.length > 2000 && r.length) { r.shift(); texto = JSON.stringify({ v: 1, m: "?", r, s }); }
    return texto;
  }
  // Lo que viaja: el plano entero, comprimido si así pesa menos (un DXF baja a una décima parte; un DWG ya viene comprimido).
  async function guardarBorrador(t, buffer, revision) {
    const e = t.e;
    try {
      const { cuerpo, cabeceras: codificacion } = await comprimir(buffer);
      const cabeceras = { "Content-Type": "application/octet-stream", "X-TV-Revision": String(e.base), "X-TV-Sesion": t.sesion, ...codificacion };
      if (e.borradorVisto) cabeceras["X-TV-Borrador-Previo"] = e.borradorVisto;
      if (e.registroSucio) cabeceras["X-TV-Registro"] = e.registroSucio;
      const r = await pedir(t.cfg.urls.borrador, { metodo: "POST", cuerpo, cabeceras });
      const d = await datos(r);
      if (r.ok) {
        e.ultimoBorrador = Date.now(); t.revBorrador = revision; t.borradorServidor = d.actualizado;
        if (e.registroSucio) { e.registroSucio = null; e.registroEnviado = true; }
        // La copia del teléfono es la misma que el borrador del servidor: al volver se recupera de la del teléfono, sin bajarla.
        if (Campo(t) && revision === t.revTelefono) Campo(t).actualizarCambios(t.cfg.id, { borrador: d.actualizado }).catch(() => {});
        return;
      }
      if (r.status === 409 && d.motivo === "borrador_pendiente") await borradorAjeno(t, d.actualizado);
    } catch (_) { /* sin red: se intenta en la siguiente vuelta */ }
  }
  async function borradorAjeno(t, actualizado) {
    try {
      const r = await pedir(`${t.cfg.urls.estado}?sesion=${t.sesion}&ligero=1`);
      const d = await datos(r);
      t.cfg.borrador = d.borrador ? { ...(t.cfg.borrador || {}), ...d.borrador } : { ...(t.cfg.borrador || {}), actualizado };
    } catch (_) { t.cfg.borrador = { ...(t.cfg.borrador || {}), actualizado }; }
    if (!t.cfg.borrador.revision_base) t.cfg.borrador.revision_base = t.e.base;
    mostrarBorrador(t);
    elegirBorrador(t);
  }
  async function borrador(t) {
    // Una extensión (Civil) trabajando con el editor: el editor rechazaría guardar a media operación; se intenta en la siguiente vuelta.
    // Con el aviso «Tienes cambios sin guardar de la última vez» pendiente, un borrador nuevo pisaría ese (auditoría M-6): primero se elige.
    const e = t.e;
    if (t.ajena || t.cerrando || e.borradorPendiente || extensionOcupada() || t.borradorEnCurso || !sucia(t) || !e.cargado || e.guardando || !(e.editable || e.perdido)) return;
    t.borradorEnCurso = true;
    try { await pedirGuardado(t, "borrador"); } finally { t.borradorEnCurso = false; }
  }
  // El borrador del servidor sale solo si algo cambió desde el anterior: a lo más uno cada 2 minutos mientras hay cambios (el primero, 2
  // minutos después del primer cambio) y al pasar la app a segundo plano o cerrarla. Mirar el plano nunca manda borradores.
  // En un plano grande el borrador es caro: el editor reescribe el archivo entero (un DWG de 48.7 MB tarda segundos y mucha memoria) y sube
  // todo (con gzip un DWG baja ~14 %). Su intervalo crece con el tamaño: 2 minutos hasta 10 MB, luego 2 minutos por cada 10 MB (un plano de
  // 48 MB, cada ~10 minutos), a lo más 15. Así lo que se sube queda en ~5 MB por minuto de cambios como mucho, y lo que se pierde en el peor
  // caso (el navegador se cierra) son esos minutos. Al pasar la app a segundo plano sale el borrador si el anterior ya tiene la mitad del
  // intervalo (docs/cad.md, «Planos grandes»).
  const cambioSinBorrador = (t) => t.revEditor !== t.revBorrador;
  const intervaloBorrador = (t) => {
    const mb = (Number(t.cfg.bytes) || 0) / 1048576;
    return C.autoguardado_s * 1000 * Math.min(7.5, Math.max(1, mb / 10));
  };
  setInterval(() => {
    for (const t of pestanas) {
      const desde = Math.max(t.e.sucioDesde, t.e.ultimoBorrador);
      if (Date.now() - desde >= intervaloBorrador(t) && cambioSinBorrador(t)) borrador(t);
    }
  }, Math.min(5000, C.autoguardado_s * 500));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "hidden") return;
    copiarAlTelefono(t0, true);
    for (const t of pestanas) if (cambioSinBorrador(t) && Date.now() - t.e.ultimoBorrador >= intervaloBorrador(t) / 2) borrador(t);
  });

  // -- La copia de los cambios en el teléfono (planos «Disponibles sin conexión», solo el modo táctil) ---------------------------
  // Con cambios, lo que hay en el editor queda en el teléfono en cuanto la persona deja de dibujar (2 s; a lo más una vez cada 10 s): si
  // se va la señal, se cierra la app o se apaga el teléfono, nada se pierde, y la página sin conexión lo abre tal cual. No viaja por la
  // red. Al volver, se ofrece recuperarla (sin bajar nada); al guardar una versión o una copia, se borra.
  let copiaTimer = null, copiando = false, ultimaCopia = 0;
  function programarCopia(t) {
    if (!Campo(t) || copiaTimer) return;
    copiaTimer = setTimeout(() => copiarAlTelefono(t, false), Math.max(2000, 10000 - (Date.now() - ultimaCopia)));
  }
  async function copiarAlTelefono(t, ya) {
    clearTimeout(copiaTimer); copiaTimer = null;
    const e = t.e;
    if (!Campo(t) || copiando || !e.cargado || !t.documento || e.guardando || extensionOcupada() || !sucia(t) || !(e.editable || e.perdido)) return;
    if (t.revEditor === t.revTelefono || !(await Campo(t).plano(t.cfg.id))) return;          // solo los planos que están en el teléfono
    copiando = true;
    try {
      const doc = (await documentosDelEditor()).find((d) => d.id === t.documento);
      const rev = doc ? doc.revision || 0 : t.revEditor;
      const bytes = await bytesDelEditor(t);
      await Campo(t).guardarCambios({ id: t.cfg.id, nombre: e.nombre, formato: e.formato, base: e.base, bytes, origen: "pagina",
        borrador: rev === t.revBorrador ? t.borradorServidor : null });
      t.revTelefono = rev; ultimaCopia = Date.now();
    } catch (_) { /* sin espacio o el editor ocupado: la próxima vez */ }
    finally {
      copiando = false;
      if (!ya && t.revEditor !== t.revTelefono && sucia(t)) programarCopia(t);
    }
  }

  // Cambios de una visita anterior que quedaron en el teléfono (la copia de arriba). Si son al menos tan nuevos como el borrador del
  // servidor, son esos los que se ofrecen («Tienes cambios sin guardar del …»), y recuperarlos no baja nada.
  async function borradorDelTelefono(t) {
    const c = Campo(t) && (await Campo(t).cambios(t.cfg.id).catch(() => null));
    if (!c || c.origen !== "pagina") return;
    const servidor = t.cfg.borrador;
    const fecha = (x) => Date.parse(x) || 0;
    if (servidor && c.borrador !== servidor.actualizado && fecha(c.actualizado) < fecha(servidor.actualizado)) return;     // el del servidor es más nuevo
    t.cfg.borrador = { actualizado: c.actualizado, revision_base: c.base, formato: c.formato, bytes: c.tamano, local: true,
      servidor: servidor ? servidor.actualizado : null, borrador: c.borrador || null };
  }
  function ocultarBorrador(t) { t.aviso.borrador = ""; t.e.borradorPendiente = false; if (t === activa) $("cad-aviso-borrador").hidden = true; }
  function mostrarBorrador(t) {
    if (!t.cfg.borrador || !t.e.editable) return;
    const cuando = new Date(t.cfg.borrador.actualizado);
    const fecha = cuando.toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    t.aviso.borrador = t.cfg.borrador.revision_base === t.e.revision
      ? `Tienes cambios sin guardar del ${fecha}.`
      : `Tienes cambios sin guardar del ${fecha}, hechos sobre una versión anterior. Si los recuperas, se guardarán como copia.`;
    t.e.borradorPendiente = true;
    if (t === activa) pintarBorrador(t);
  }
  function pintarBorrador(t) {
    $("cad-aviso-borrador").hidden = !t.aviso.borrador;
    if (t.aviso.borrador) $("cad-aviso-borrador-texto").textContent = t.aviso.borrador;
  }
  // Al primer cambio con el borrador anterior sin decidir, se pide elegir; la hoja no se cierra sin una respuesta. Si el cambio es de
  // otra pestaña, primero se muestra esa.
  let elegirDe = null;
  async function elegirBorrador(t) {
    const d = $("cad-borrador-elegir");
    if (!d || d.open || !t.e.borradorPendiente) return;
    if (t !== activa) await activar(t);
    elegirDe = t;
    d.showModal();
  }
  if ($("cad-borrador-elegir")) {
    $("cad-borrador-elegir").addEventListener("cancel", (ev) => ev.preventDefault());
    $("cad-borrador-elegir-recuperar").addEventListener("click", () => { $("cad-borrador-elegir").close(); recuperar(elegirDe || activa); });
    $("cad-borrador-elegir-descartar").addEventListener("click", () => { $("cad-borrador-elegir").close(); descartarBorrador(elegirDe || activa); });
    $("cad-borrador-elegir-copia").addEventListener("click", async (ev) => {
      const t = elegirDe || activa;
      const b = ev.currentTarget; b.setAttribute("aria-busy", "true");
      try {
        const bytes = t.cfg.borrador && t.cfg.borrador.local ? await bytesDeLaCopia(t) : await bytesDe(`${t.cfg.urls.archivo}?borrador=1`);
        const { cuerpo, cabeceras: codificacion } = await comprimir(bytes);
        const r = await pedir(t.cfg.urls.copia, { metodo: "POST", cuerpo, cabeceras: { "Content-Type": "application/octet-stream", ...codificacion } });
        const d = await datos(r);
        if (!r.ok) throw new Error(d.error || "No se pudo guardar la copia.");
        await pedir(t.cfg.urls.borrador_descartar, { metodo: "POST" });
        if (Campo(t)) await Campo(t).descartarCambiosDePagina(t.cfg.id).catch(() => {});
        t.e.borradorVisto = t.cfg.borrador && (t.cfg.borrador.local ? t.cfg.borrador.servidor : t.cfg.borrador.actualizado);
        ocultarBorrador(t);
        $("cad-borrador-elegir").close();
        avisar(`Tus cambios de la última vez quedaron en «${d.nombre}».`);
      } catch (err) { avisar(err instanceof TypeError ? "No se pudo guardar la copia: revisa tu conexión." : err.message, "error"); }
      finally { b.removeAttribute("aria-busy"); }
    });
  }
  async function recuperar(t) {
    ocultarBorrador(t);
    try {
      t.e.base = t.cfg.borrador.revision_base;
      t.e.borradorVisto = t.cfg.borrador.local ? t.cfg.borrador.servidor : t.cfg.borrador.actualizado;
      await abrir(t, "borrador", false);
      t.e.recuperado = true; t.e.sucioDesde = Date.now();
      // Lo recuperado del servidor ya está allá; lo del teléfono puede ser más nuevo que el borrador del servidor: ese sí se manda.
      t.revBorrador = t.cfg.borrador.local && t.cfg.borrador.borrador !== t.cfg.borrador.servidor ? null : t.revEditor;
      if (!t.cfg.borrador.local && Campo(t)) Campo(t).descartarCambiosDePagina(t.cfg.id).catch(() => {});        // la del teléfono era más vieja
      anunciarDe(t, "cambio", { sucio: true });
      refrescarEstado(t);
      if (t.e.base !== t.e.revision) { t.e.perdido = true; lectura(t, "Estos cambios se hicieron sobre una versión anterior: al guardar se crea una copia."); }
    } catch (err) { error(t, err.message); }
  }
  async function descartarBorrador(t) {
    ocultarBorrador(t);
    await pedir(t.cfg.urls.borrador_descartar, { metodo: "POST" }).catch(() => {});
    if (Campo(t)) await Campo(t).descartarCambiosDePagina(t.cfg.id).catch(() => {});
    avisar("Cambios descartados");
  }
  $("cad-recuperar").addEventListener("click", () => recuperar(activa));
  $("cad-descartar-borrador").addEventListener("click", () => descartarBorrador(activa));

  // -- Bloqueo (uno por pestaña: cada una es una página del editor para el servidor) ---------------------------------------------
  async function bloqueo(t, accion) {
    // El latido lleva la versión sobre la que se edita: si el bloqueo caducó y otra persona guardó mientras tanto, el servidor no lo
    // retoma y responde `perdido` con `motivo: "cambio"` (auditoría M-7).
    const cuerpo = accion === "latido" ? { accion, sesion: t.sesion, revision: t.e.base } : { accion, sesion: t.sesion };
    const r = await pedir(t.cfg.urls.bloqueo, { metodo: "POST", json: cuerpo });
    const d = await datos(r);
    if (r.status === 410) { error(t, "Este plano se eliminó."); throw new Error("eliminado"); }
    if (!r.ok) throw new Error(d.error || "No se pudo comprobar quién edita el plano.");
    return d;
  }
  function textoDeQuien(d) {
    return d.otra_ventana ? "Este plano está abierto para editar en otra ventana tuya." : `${d.quien} está editando este plano. Lo ves en solo lectura.`;
  }

  async function alListo(t) {
    if (t.e.listo) return;
    t.e.listo = true;
    cargaDe(t, "Abriendo el plano…");
    const e = t.e;
    try {
      if (t.cfg.version) { e.lecturaMotivo = "Es una versión anterior: solo lectura."; return await abrir(t, "vigente", true); }
      if (!t.cfg.puede_editar) { e.lecturaMotivo = "Tienes acceso de solo lectura."; lectura(t, t.cfg.rol_lectura ? "Tienes acceso de solo lectura." : ""); return await abrir(t, "vigente", true); }
      if (TELEFONO) {
        e.lecturaMotivo = "En el teléfono los planos se abren para ver.";
        lectura(t, "En el teléfono los planos se abren para ver. Para editar, usa una computadora o tableta.");
        return await abrir(t, "vigente", true);
      }
      const d = await bloqueo(t, "tomar");
      if (d.mio) {
        e.revision = e.base = d.revision;
        await abrir(t, "vigente", false);
        await borradorDelTelefono(t);
        mostrarBorrador(t);
        latir(t);
      } else {
        e.lecturaMotivo = textoDeQuien(d);
        lectura(t, e.lecturaMotivo, { tomar: C.es_admin || d.otra_ventana });
        await abrir(t, "vigente", true);
        vigilarLiberacion(t);
      }
    } catch (err) {
      if (err.message !== "eliminado") error(t, err.message);
      cargaDe(t, "");
    }
  }

  function latir(t) {
    clearInterval(t.latido);
    t.latido = setInterval(async () => {
      if (!t.e.editable || sinSesion || t.cerrando) return;
      try {
        const d = await bloqueo(t, "latido");
        if (d.perdido && d.motivo === "cambio") cambioAjeno(t, d.quien);
        else if (d.perdido) perderBloqueo(t, d);
        else if (!t.e.guardando && Number.isInteger(d.revision) && d.revision > t.e.base) cambioAjeno(t);
      } catch (_) { /* sin red un momento: el bloqueo dura 150 s */ }
    }, C.latido_s * 1000);
  }
  function perderBloqueo(t, d) {
    clearInterval(t.latido);
    t.e.editable = false; t.e.perdido = true;
    anunciarDe(t, "editable", { editable: false });
    t.e.lecturaMotivo = d.otra_ventana ? "Abriste este plano para editar en otra ventana." : `${d.quien || "Otra persona"} tomó el control de este plano.`;
    lectura(t, `${t.e.lecturaMotivo} ${t.e.sucio ? "Guarda tus cambios como copia para no perderlos." : ""}`.trim());
    refrescarEstado(t);
  }

  // El bloqueo caducó (el equipo durmió más de 150 s), otra persona guardó una versión nueva y el latido lo volvió a tomar (auditoría M-7):
  // seguir sería dibujar sobre una versión vieja. Se suelta el bloqueo, se dice quién guardó y lo de aquí se guarda como copia.
  async function cambioAjeno(t, nombre) {
    if (!t.e.editable) return;
    let quien = nombre || "";
    if (!quien) { try { const r = await pedir(`${t.cfg.urls.estado}?sesion=${t.sesion}&ligero=1`); if (r.ok) quien = (await datos(r)).modificado_por || ""; } catch (_) { /* sin nombre */ } }
    if (!t.e.editable) return;
    soltar(t);
    t.e.perdido = true;
    anunciarDe(t, "editable", { editable: false });
    t.e.lecturaMotivo = `${quien || "Otra persona"} guardó una versión nueva de este plano mientras no estabas.`;
    lectura(t, `${t.e.lecturaMotivo} ${sucia(t) ? "Guarda tus cambios como copia para no perderlos." : "Toca «Editar ahora» para abrir la versión nueva."}`.trim(), { reintentar: !sucia(t) });
    refrescarEstado(t);
  }

  function vigilarLiberacion(t) {
    clearInterval(t.vigia);
    t.vigia = setInterval(async () => {
      if (t.cerrando) return clearInterval(t.vigia);
      try {
        const r = await pedir(`${t.cfg.urls.estado}?sesion=${t.sesion}&ligero=1`);
        const d = await datos(r);
        if (r.ok && d.bloqueo && d.bloqueo.libre) {
          clearInterval(t.vigia);
          lectura(t, "Ya nadie está editando este plano.", { reintentar: true });
        }
      } catch (_) { /* se vuelve a intentar */ }
    }, C.latido_s * 1000);
  }
  async function editarAhora(t, forzar) {
    try {
      const d = await bloqueo(t, forzar ? "forzar" : "tomar");
      if (!d.mio) { t.e.lecturaMotivo = textoDeQuien(d); lectura(t, t.e.lecturaMotivo, { tomar: C.es_admin || d.otra_ventana }); return; }
      clearInterval(t.vigia);
      t.e.revision = t.e.base = d.revision; t.e.perdido = false;
      lectura(t, "");
      await abrir(t, "vigente", false);
      latir(t);
      anunciarDe(t, "editable", { editable: true });
      if (forzar && d.tomado_de) avisar(`Tomaste el control (lo editaba ${d.tomado_de})`);
    } catch (err) { if (err.message !== "eliminado") error(t, err.message); }
  }
  $("cad-tomar").addEventListener("click", () => editarAhora(activa, true));
  $("cad-reintentar").addEventListener("click", () => editarAhora(activa, false));

  function soltar(t) {
    if (!t.e.editable || t.ajena) return;
    t.e.editable = false;
    clearInterval(t.latido);
    try { fetch(t.cfg.urls.bloqueo, { method: "POST", keepalive: true, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ accion: "soltar", sesion: t.sesion }) }); } catch (_) { /* nada */ }
  }
  addEventListener("pagehide", () => {
    for (const t of pestanas) soltar(t);
    // Las copias de planos que el editor deja en el navegador no se quedan al salir (auditoría A-1; también se borran al arrancar
    // el editor y al cerrar sesión, ver cad/sesion.js).
    if (window.TVCadSesion) window.TVCadSesion.limpiarEditor();
  });

  // -- Salir de la página con cambios ----------------------------------------------------------------------------------------
  // En modo táctil la página tiene una entrada de guarda en el historial (tactil.js): `ir` la reemplaza en lugar de dejarla detrás.
  const ir = (url) => (window.TVCadTactil ? window.TVCadTactil.ir(url) : location.assign(url));
  function salir(url) { for (const t of pestanas) { t.e.sucio = false; t.e.recuperado = false; soltar(t); } ir(url); }
  let salirA = null;
  const conCambios = () => pestanas.filter((t) => sucia(t) && (t.e.editable || t.e.perdido || t.ajena));
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[href]");
    if (!a || ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.shiftKey || a.target === "_blank" || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname === location.pathname || url.pathname.startsWith("/cad/") && url.searchParams.get("descargar") === "1") return;
    const sucias = conCambios();
    if (!sucias.length) return;
    ev.preventDefault();
    salirA = url.href;
    // Una sola pregunta, con la lista de los planos con cambios.
    const lista = $("cad-salir-lista");
    if (lista) {
      lista.hidden = pestanas.length < 2;
      lista.replaceChildren(...sucias.map((t) => { const li = document.createElement("li"); li.textContent = t.e.nombre; return li; }));
      $("cad-salir-titulo").textContent = sucias.length > 1 ? `Tienes cambios sin guardar en ${sucias.length} planos` : pestanas.length > 1 ? `«${sucias[0].e.nombre}» tiene cambios sin guardar` : "Tienes cambios sin guardar";
      $("cad-salir-texto").textContent = sucias.length > 1 ? "Guárdalos como versiones nuevas antes de salir." : "Guárdalos como versión nueva antes de salir.";
    }
    $("cad-salir").showModal();
  }, true);
  $("cad-salir-sin").addEventListener("click", () => { $("cad-salir").close(); salir(salirA); });
  // «Guardar y salir»: solo se sale si se guardó todo. Si algo falla, la salida no queda programada para un guardado posterior (B-6).
  $("cad-salir-guardar").addEventListener("click", async () => {
    $("cad-salir").close();
    const sucias = conCambios();
    if (sucias.length === 1 && sucias[0] === activa) {
      activa.e.salirDespues = salirA;
      const r = await guardarActiva();
      if (!r.ok) activa.e.salirDespues = null;
      return;
    }
    const r = await guardarTodo();
    if (r.ok) salir(salirA);
  });

  // -- Versiones ---------------------------------------------------------------------------------------------------------------
  function icono(nombre) {
    const plantilla = document.querySelector(`#cad-iconos [data-cad-icono="${nombre}"] svg`);
    return plantilla ? plantilla.cloneNode(true) : document.createTextNode("");
  }
  function pintarVersiones(t) {
    const lista = $("cad-versiones-lista");
    if (!lista || !t.versiones) return;
    lista.replaceChildren(...t.versiones.map((x) => {
      const li = document.createElement("li");
      li.dataset.numero = x.numero;
      const texto = document.createElement("div"); texto.className = "cad-versiones__texto";
      const fuerte = document.createElement("strong"); fuerte.textContent = `Versión ${x.numero} `;
      if (x.vigente) { const i = document.createElement("span"); i.className = "tv-insignia tv-insignia--exito"; i.textContent = "Vigente"; fuerte.append(i); }
      if (x.disponible === false) { const i = document.createElement("span"); i.className = "tv-insignia tv-insignia--error"; i.textContent = "Archivo no disponible"; fuerte.append(" ", i); li.dataset.sinArchivo = "1"; }
      const tenue = document.createElement("span"); tenue.className = "tv-tenue";
      const cuando = x.creada ? fechaLarga(x.creada) : "";
      tenue.textContent = [`Guardada por ${x.autor || "—"}`, cuando, x.nota, x.formato.toUpperCase(), tamano(x.bytes)].filter(Boolean).join(" · ");
      texto.append(fuerte, tenue);
      const acciones = document.createElement("div"); acciones.className = "cad-versiones__acciones";
      const hay = x.disponible !== false;
      if (hay && !x.vigente) acciones.append(boton("a", `${t.cfg.urls.plano}?version=${x.numero}`, "eye", "Abrir (solo lectura)"));
      if (hay) acciones.append(boton("a", `${t.cfg.urls.archivo}?descargar=1&version=${x.numero}`, "download", "Descargar"));
      if (hay && t.cfg.puede_editar && !x.vigente) { const b = boton("button", null, "rotate-ccw", "Restaurar"); b.dataset.cadRestaurar = x.numero; acciones.append(b); }
      li.append(texto, acciones);
      return li;
    }));
  }
  const fechaLarga = (iso) => new Date(iso).toLocaleString("es-MX", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  function pintarSubtitulo(t) {
    const sub = $("cad-subtitulo");
    if (!sub || t.cfg.version) return;
    sub.textContent = t.ajena ? "Dibujo hecho en el editor: no está en la biblioteca." : [t.cfg.proyecto, `Versión ${t.e.revision}`, t.cfg.modificado_por].filter(Boolean).join(" · ");
  }
  function boton(tag, href, nombreIcono, etiqueta) {
    const b = document.createElement(tag);
    b.className = "tv-boton tv-boton--secundario tv-boton--icono";
    if (href) b.href = href; else b.type = "button";
    b.setAttribute("aria-label", etiqueta); b.dataset.tvTooltip = etiqueta;
    b.append(icono(nombreIcono));
    return b;
  }
  function tamano(n) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-cad-restaurar]");
    if (!b) return;
    const t = activa;
    if (sucia(t)) { avisar("Guarda o descarta tus cambios antes de restaurar una versión.", "error"); return; }
    b.setAttribute("aria-busy", "true");
    try {
      const r = await pedir(t.cfg.urls.restaurar_version.replace("{n}", b.dataset.cadRestaurar), { metodo: "POST", json: { sesion: t.sesion } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo restaurar la versión.");
      t.e.revision = t.e.base = d.revision;
      t.versiones = d.versiones; t.cfg.proyecto = d.proyecto; t.cfg.modificado_por = d.modificado_por;
      pintarVersiones(t);
      pintarSubtitulo(t);
      $("cad-versiones").close();
      avisar(`Versión ${b.dataset.cadRestaurar} restaurada como versión ${d.revision}`);
      if (t.e.editable) await abrir(t, "vigente", false);
    } catch (err) { avisar(err.message, "error"); }
    finally { b.removeAttribute("aria-busy"); }
  });

  // -- La cabecera es de la pestaña activa ---------------------------------------------------------------------------------------
  // Nombre, formato, subtítulo, autoría, estado, Guardar, Versiones, el menú y los avisos de esa pestaña.
  function pintarCabecera(t) {
    const u = t.cfg.urls || {}, ajena = t.ajena;
    $("cad-nombre").textContent = t.e.nombre;
    const f = $("cad-formato"); if (f) { f.textContent = t.e.formato.toUpperCase(); f.dataset.formato = t.e.formato; }
    pintarSubtitulo(t);
    const autoria = $("cad-autoria");
    if (autoria && !ajena && t.cfg.actualizado) {
      autoria.textContent = `Guardado ${fechaLarga(t.cfg.actualizado)}${t.cfg.modificado_por ? ` por ${t.cfg.modificado_por}` : ""}${t.cfg.autor ? ` · creado por ${t.cfg.autor} el ${new Date(t.cfg.creado).toLocaleDateString("es-MX", { day: "numeric", month: "short", year: "numeric" })}` : ""}`;
    } else if (autoria) autoria.textContent = "";
    document.title = document.title.replace(/^[^·|–-]*?(\s[·|–-]\s)/, `${t.e.nombre}$1`);
    const v = $("cad-boton-versiones"); if (v) v.disabled = ajena;
    pintarVersiones(t);
    // El menú «Más acciones»
    const descargar = $("cad-descargar");
    if (descargar) { descargar.hidden = ajena; if (!ajena) { descargar.href = `${u.archivo}?descargar=1`; descargar.querySelector("span").textContent = `Descargar ${t.e.formato.toUpperCase()}`; } }
    const conv = $("cad-convertir");
    if (conv) { const otro = t.e.formato === "dwg" ? "dxf" : "dwg"; conv.dataset.formato = otro; conv.hidden = ajena; conv.querySelector("span").textContent = `Descargar como ${otro.toUpperCase()}`; }
    const drive = $("cad-drive"); if (drive) drive.hidden = ajena;
    const ren = $("cad-form-renombrar");
    if (ren) {
      ren.action = u.renombrar || ""; $("cad-ren-nombre").value = t.e.nombre;
      const donde = $("cad-ren-ubicacion"); if (donde) donde.textContent = `En ${t.cfg.ubicacion || "Planos"}`;
    }
    for (const b of document.querySelectorAll('[data-tv-abrir="cad-renombrar"]')) b.hidden = ajena || !t.cfg.puede_editar;
    const dup = $("cad-form-duplicar"); if (dup) { dup.action = u.duplicar || ""; dup.hidden = ajena; }
    const eli = $("cad-form-eliminar"); if (eli) { eli.action = u.eliminar || ""; $("cad-eliminar-titulo").textContent = `¿Eliminar «${t.e.nombre}»?`; }
    for (const b of document.querySelectorAll('[data-tv-abrir="cad-eliminar"]')) b.hidden = ajena || !t.cfg.puede_eliminar;
    const imp = $("cad-boton-imprimir"); if (imp && C.nativo) imp.hidden = ajena;
    pintarLectura(t); pintarError(t); pintarBorrador(t);
    refrescarEstado(t);
    if (P && !ajena && u.plano && location.pathname !== u.plano) history.replaceState(history.state, "", u.plano);
  }

  // -- Tareas del servidor: imprimir y convertir ----------------------------------------------------------------------------
  // La espera termina siempre (auditoría B-7): con dos respuestas seguidas que no son la tarea (404, 500, una página de error), con tres
  // fallos de red seguidos o a los 10 minutos.
  async function esperarTarea(inicial, decir) {
    let t = inicial, malas = 0, sinRed = 0;
    const limite = Date.now() + 10 * 60 * 1000;
    while (!t.terminada) {
      if (Date.now() > limite) throw new Error("Tardó demasiado. Inténtalo de nuevo más tarde.");
      await espera(1200);
      let r;
      try { r = await pedir(`${C.urls.tarea.replace(MARCA, t.tarea || t.id)}?formato=json`); }
      catch (err) {
        if (sinSesion) throw err;
        if (++sinRed >= 3) throw new Error("Se perdió la conexión mientras se preparaba. Inténtalo de nuevo.");
        continue;
      }
      sinRed = 0;
      const d = r.ok ? await datos(r) : {};
      if (!r.ok || typeof d.terminada !== "boolean") {
        if (++malas >= 2) throw new Error("No se pudo saber cómo va. Inténtalo de nuevo.");
        continue;
      }
      malas = 0;
      t = { ...d, tarea: t.tarea || t.id };
      if (!t.terminada && decir) decir(`${t.mensaje || "Preparando…"} ${Math.round((t.fraccion || 0) * 100)} %`);
    }
    if (t.estado !== "completada") throw new Error(t.error || "No se pudo completar. Inténtalo de nuevo.");
    return t.resultado || {};
  }
  function bajar(url) {
    const a = document.createElement("a");
    a.href = url; a.download = ""; document.body.append(a); a.click(); a.remove();
  }

  const formImprimir = $("cad-form-imprimir");
  if (formImprimir) {
    const LLAVE = "tv-cad-impresion";
    try {
      const previo = JSON.parse(localStorage.getItem(LLAVE) || "{}");
      for (const [k, v] of Object.entries(previo)) {
        const campo = formImprimir.elements[k];
        if (!campo) continue;
        if (campo instanceof RadioNodeList) { for (const r of campo) r.checked = r.value === v; }
        else if (campo.type === "checkbox") campo.checked = v === "1";
        else if ([...(campo.options || [])].some((o) => o.value === v) || campo.tagName === "INPUT") campo.value = v;
      }
    } catch (_) { /* sin almacenamiento */ }
    const escala = $("cad-escala"), otra = $("cad-escala-otra-campo");
    const verOtra = () => { otra.hidden = escala.value !== "otra"; };
    escala.addEventListener("change", verOtra); verOtra();
    formImprimir.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const boton_ = $("cad-imprimir-ir"), estadoTexto = $("cad-imprimir-estado");
      const decir = (x) => { estadoTexto.hidden = !x; estadoTexto.textContent = x || ""; };
      const fd = new FormData(formImprimir);
      if (!fd.get("grosores")) fd.set("grosores", "0");
      try { localStorage.setItem(LLAVE, JSON.stringify(Object.fromEntries([...fd.entries()].filter(([k]) => k !== "version")))); } catch (_) { /* nada */ }
      boton_.setAttribute("aria-busy", "true"); boton_.disabled = true; decir("Preparando el PDF…");
      try {
        const r = await pedir(activa.cfg.urls.imprimir, { metodo: "POST", cuerpo: fd });
        const d = await datos(r);
        if (!r.ok && r.status !== 202) throw new Error(d.error || "No se pudo imprimir.");
        const res = d.terminada ? d.resultado : await esperarTarea(d, decir);
        if (!res || !res.url) throw new Error(d.error || "No se pudo imprimir.");
        bajar(res.url);
        decir(""); $("cad-imprimir").close(); avisar("PDF listo");
      } catch (err) { decir(""); avisar(err.message, "error"); }
      finally { boton_.removeAttribute("aria-busy"); boton_.disabled = false; }
    });
  }

  async function convertir(permitir) {
    const formato = $("cad-convertir").dataset.formato;
    const fd = new FormData();
    fd.set("formato", formato);
    if (activa.cfg.version) fd.set("version", activa.cfg.version);
    if (permitir) fd.set("permitir_perdida", "1");
    avisar(`Preparando el ${formato.toUpperCase()}…`);
    try {
      const r = await pedir(activa.cfg.urls.convertir, { metodo: "POST", cuerpo: fd });
      const d = await datos(r);
      if (!r.ok && r.status !== 202) throw new Error(d.error || "No se pudo convertir.");
      const res = d.terminada ? d.resultado : await esperarTarea(d);
      if (res && res.perdida) { $("cad-perdida-texto").textContent = res.mensaje; $("cad-perdida").showModal(); return; }
      if (!res || !res.url) throw new Error("No se pudo convertir.");
      bajar(res.url);
      avisar(res.mensaje || "Listo");
    } catch (err) { avisar(err.message, "error"); }
  }
  if ($("cad-convertir")) $("cad-convertir").addEventListener("click", () => { $("cad-menu").open = false; convertir(false); });
  $("cad-perdida-seguir").addEventListener("click", () => { $("cad-perdida").close(); convertir(true); });

  if ($("cad-drive")) $("cad-drive").addEventListener("click", async () => {
    $("cad-menu").open = false;
    try {
      const r = await pedir(activa.cfg.urls.drive, { metodo: "POST" });
      const d = await datos(r);
      avisar(d.mensaje || d.error || "Listo", r.ok ? undefined : "error");
    } catch (_) { avisar("No se pudo enviar. Revisa tu conexión.", "error"); }
  });

  // Con varias pestañas, renombrar, duplicar y eliminar no salen de la página (se perderían las demás pestañas abiertas).
  if (P) {
    const enviar = async (form, fn) => {
      try {
        const r = await pedir(form.action, { metodo: "POST", cuerpo: new FormData(form) });
        const d = await datos(r);
        if (!r.ok) throw new Error(d.error || "No se pudo completar.");
        await fn(d);
      } catch (err) { avisar(err instanceof TypeError ? "Sin conexión: inténtalo de nuevo." : err.message, "error"); }
    };
    const ren = $("cad-form-renombrar");
    if (ren) ren.addEventListener("submit", (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const t = activa;
      enviar(ren, (d) => { t.e.nombre = t.cfg.nombre = d.nombre; t.cfg.proyecto = d.proyecto; $("cad-renombrar").close(); pintarCabecera(t); avisar("Cambios guardados."); });
    }, true);
    const dup = $("cad-form-duplicar");
    if (dup) dup.addEventListener("submit", (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation(); $("cad-menu").open = false;
      enviar(dup, (d) => { avisar(`Se creó «${d.nombre}».`); return abrirPestana(d.id); });
    }, true);
    const eli = $("cad-form-eliminar");
    if (eli) eli.addEventListener("submit", (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const t = activa;
      enviar(eli, async (d) => { $("cad-eliminar").close(); t.e.sucio = t.e.recuperado = false; await quitar(t); avisar(d.mensaje); });
    }, true);
  }

  // -- Menú «Más acciones» ------------------------------------------------------------------------------------------------------
  const menu = $("cad-menu");
  document.addEventListener("click", (ev) => { if (menu.open && !menu.contains(ev.target)) menu.open = false; });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && menu.open) { ev.preventDefault(); menu.open = false; menu.querySelector("summary").focus(); } });
  menu.addEventListener("click", (ev) => { if (ev.target.closest("[data-tv-abrir]")) menu.open = false; });

  // Sugerencias de proyecto o cliente al renombrar.
  const proyectos = JSON.parse(($("cad-proyectos") || {}).textContent || "[]");
  if (window.ITT && ITT.combobox) document.querySelectorAll("[data-cad-proyectos]").forEach((i) => ITT.combobox(i, () => proyectos));

  // -- Esc es del editor (docs/cad.md, «Esc») ----------------------------------------------------------------------------------
  // El editor solo oye el teclado con el foco en su lienzo. Con el foco en nuestra página (después de un clic en la cabecera, en Guardar,
  // en «Pantalla completa», al cerrar una hoja), Esc no llegaba a ningún lado: la orden seguía y la selección también. Ahora un Esc en la
  // página hace lo que un Esc en el editor: con una orden en curso, la cancela (la orden `cancel`, que el canal acepta aunque la orden sea
  // de la persona); sin orden, cierra lo que esté abierto y deselecciona (`command_escape`). Y el foco vuelve al dibujo. Antes van las
  // hojas de la página (las cierra el navegador) y el menú «Más acciones»; los campos de texto de la página se quedan su Esc.
  function lienzo() { try { return frame.contentDocument.querySelector("canvas"); } catch (_) { return null; } }
  function enfocarEditor() {
    if (!frame || enTactil() || document.querySelector("dialog[open]")) return;
    const c = lienzo();
    try { frame.contentWindow.focus(); if (c) c.focus({ preventScroll: true }); } catch (_) { /* nada */ }
  }
  async function escAlEditor() {
    enfocarEditor();
    if (!control) return;
    const est = await canal({ op: "state" }, 3000).catch(() => null);
    if (!est || !est.document_id) return;
    if (est.command || est.operation) await canal({ op: "cancel", document_id: est.document_id }, 5000).catch(() => {});
    else await canal({ op: "action", name: "command_escape", document_id: est.document_id }, 5000).catch(() => {});
  }
  const CAMPO_DE_TEXTO = "input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable=''], [contenteditable=true], [role=listbox], [role=combobox]";
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || ev.defaultPrevented || ev.isComposing || !frame) return;
    if (document.querySelector("dialog[open]") || (menu && menu.open)) return;
    const objetivo = ev.target instanceof Element ? ev.target : null;
    if (objetivo && objetivo.closest(CAMPO_DE_TEXTO)) return;
    if (objetivo && objetivo.closest("details[open]")) return;
    ev.preventDefault();
    escAlEditor();
  });
  // Un clic en la página que no lleva a escribir ni abre una hoja devuelve el foco al dibujo (como en AutoCAD, el teclado es del dibujo).
  for (const id of ["cad-completa", "cad-estado", "cad-nombre"]) { const x = $(id); if (x) x.addEventListener("click", () => setTimeout(enfocarEditor, 0)); }
  for (const d of document.querySelectorAll("dialog")) d.addEventListener("close", () => setTimeout(enfocarEditor, 0));

  // -- Pestañas ---------------------------------------------------------------------------------------------------------------
  const tira = $("cad-pestanas"), listaTira = $("cad-pestanas-lista");
  function pintarPestanas() {
    if (!tira) return;
    tira.hidden = !P || enTactil();
    const vistas = new Map([...listaTira.children].map((x) => [x._t, x]));
    listaTira.replaceChildren(...pestanas.map((t) => vistas.get(t) || nuevaVista(t)));
    for (const t of pestanas) pintarPestana(t);
  }
  function nuevaVista(t) {
    const x = document.createElement("div");
    x._t = t;
    x.className = "cad-pestana"; x.setAttribute("role", "tab"); x.tabIndex = -1;
    const nombre = document.createElement("span"); nombre.className = "cad-pestana__nombre";
    const marca = document.createElement("span"); marca.className = "cad-pestana__marca"; marca.setAttribute("aria-hidden", "true");
    const cerrarB = document.createElement("button"); cerrarB.type = "button"; cerrarB.className = "cad-pestana__cerrar";
    cerrarB.append(icono("x"));
    cerrarB.addEventListener("click", (ev) => { ev.stopPropagation(); cerrar(t); });
    x.append(marca, nombre, cerrarB);
    x.addEventListener("click", () => activar(t));
    x.addEventListener("auxclick", (ev) => { if (ev.button === 1) { ev.preventDefault(); cerrar(t); } });
    x.addEventListener("keydown", (ev) => {
      const i = pestanas.indexOf(t);
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") { ev.preventDefault(); const o = pestanas[(i + (ev.key === "ArrowRight" ? 1 : -1) + pestanas.length) % pestanas.length]; o._vista && o._vista.focus(); activar(o, { foco: false }); }
      if (ev.key === "Delete") { ev.preventDefault(); cerrar(t); }
    });
    t._vista = x;
    return x;
  }
  function pintarPestana(t) {
    const x = t._vista;
    if (!x) return;
    const si = t === activa;
    x.setAttribute("aria-selected", String(si)); x.tabIndex = si ? 0 : -1;
    const [estado, texto] = estadoDe(t);
    x.dataset.estado = estado;
    x.querySelector(".cad-pestana__nombre").textContent = t.e.nombre;
    x.title = [t.e.nombre + "." + t.e.formato, t.cfg.proyecto, texto].filter(Boolean).join(" · ");
    x.setAttribute("aria-label", `${t.e.nombre}, ${texto}`);
    const m = x.querySelector(".cad-pestana__marca");
    m.replaceChildren(estado === "lectura" ? icono(t.ajena ? "triangle-alert" : "lock") : "");
    x.querySelector(".cad-pestana__cerrar").setAttribute("aria-label", `Cerrar «${t.e.nombre}»`);
  }
  // Solo la vista de la página (la cabecera); el editor ya muestra ese documento.
  function mostrar(t) {
    activa = t;
    pintarCabecera(t);
    pintarPestanas();
    carga(t.e.cargado ? "" : "Abriendo el plano…");
    if (t.e.cargado) anunciar("cargado", datosCargado(t));
    guardarPestanas();
  }
  async function activar(t, { foco = true } = {}) {
    if (!pestanas.includes(t)) return;
    if (activa !== t) {
      if (extensionOcupada()) { avisar("Espera a que termine lo que estás haciendo en Civil.", "error"); return; }
      mostrar(t);
      if (t.documento) await enCola(() => canalLibre({ op: "activate", document_id: t.documento }, 30000)).catch(() => {});
    }
    if (foco) enfocarEditor();
  }

  // Abrir otro plano en una pestaña nueva (o ir a la suya si ya está abierto).
  async function abrirPestana(id, { detras = false } = {}) {
    if (!P) return null;
    const ya = pestanas.find((t) => t.cfg.id === id);
    if (ya) { if (!detras) await activar(ya); return ya; }
    if (pestanas.length >= P.max) {
      avisar(`Ya tienes ${P.max} planos abiertos, el máximo en una ventana. Cierra uno para abrir otro.`, "error");
      return null;
    }
    const r = await pedir(P.pestana.replace(MARCA, id)).catch(() => null);
    const d = r ? await datos(r) : {};
    if (!r || !r.ok) { avisar(d.error || "No se pudo abrir el plano.", "error"); return null; }
    // La memoria del editor en el navegador (4 GB, que no se devuelve): entre todas las pestañas, a lo más P.bytes de planos.
    const abiertos = pestanas.reduce((n, t) => n + (t.cfg.bytes || 0), 0);
    if (P.bytes && pestanas.length && abiertos + (d.plano.bytes || 0) > P.bytes) {
      avisar(`Los planos abiertos ya ocupan casi toda la memoria del editor en el navegador. Cierra uno para abrir «${d.plano.nombre}».`, "error");
      return null;
    }
    const t = nuevaPestana(d.plano, d.versiones);
    pintarPestanas();
    if (!detras) mostrar(t);
    guardarPestanas();
    await alListo(t);
    if (!detras) enfocarEditor();
    return t;
  }
  async function cerrar(t) {
    if (t.e.guardando || t.cerrando) return;
    if (sucia(t) && (t.e.editable || t.e.perdido || t.ajena)) {
      if (t !== activa) await activar(t, { foco: false });
      const d = $("cad-cerrar");
      cerrarDe = t;
      $("cad-cerrar-titulo").textContent = `¿Guardar los cambios de «${t.e.nombre}»?`;
      $("cad-cerrar-guardar").querySelector("span").textContent = t.ajena ? "Guardar en la biblioteca y cerrar" : t.e.perdido ? "Guardar como copia y cerrar" : "Guardar y cerrar";
      d.showModal();
      return;
    }
    await quitar(t);
  }
  let cerrarDe = null;
  if ($("cad-cerrar")) {
    $("cad-cerrar-sin").addEventListener("click", () => { $("cad-cerrar").close(); const t = cerrarDe; cerrarDe = null; if (t) quitar(t); });
    $("cad-cerrar-guardar").addEventListener("click", async () => {
      $("cad-cerrar").close();
      const t = cerrarDe; cerrarDe = null;
      if (!t) return;
      const r = await pedirGuardado(t, t.e.perdido ? "copia" : "plano");
      if (r.ok) await quitar(t);
    });
  }
  async function quitar(t, { editor = true } = {}) {
    if (t.cerrando) return;
    t.cerrando = true;
    soltar(t); clearInterval(t.latido); clearInterval(t.vigia);
    const i = pestanas.indexOf(t);
    if (editor && t.documento) await enCola(() => canalLibre({ op: "close", document_id: t.documento, discard: true }, 30000)).catch(() => {});
    pestanas.splice(i, 1);
    if (t._vista) t._vista.remove();
    anunciar("pestana", { accion: "cerrada", plano: { id: t.cfg.id, nombre: t.e.nombre }, documento: t.documento });
    if (!pestanas.length) { guardarPestanas(); ir(C.urls.lista); return; }
    if (activa === t) await activar(pestanas[Math.min(i, pestanas.length - 1)]);
    else pintarPestanas();
    guardarPestanas();
  }

  // Las pestañas abiertas se recuerdan en este dispositivo, por ventana (una ventana del navegador no pisa la lista de otra): al recargar
  // vuelven las mismas, cada una con su recuperación de borrador como siempre.
  const LLAVE_PESTANAS = "tv.cad.pestanas";
  const ventana = (() => { try { let v = sessionStorage.getItem("tv.cad.ventana"); if (!v) { v = nuevaSesion().slice(0, 12); sessionStorage.setItem("tv.cad.ventana", v); } return v; } catch (_) { return "una"; } })();
  const leerPestanas = () => { try { return JSON.parse(localStorage.getItem(LLAVE_PESTANAS) || "{}") || {}; } catch (_) { return {}; } };
  function guardarPestanas() {
    if (!P) return;
    try {
      const todas = leerPestanas();
      const ids = pestanas.filter((t) => !t.ajena).map((t) => t.cfg.id);
      if (ids.length) todas[ventana] = { ids, activa: activa.ajena ? ids[0] : activa.cfg.id, t: Date.now() };
      else delete todas[ventana];
      // A lo más 6 ventanas y una semana: lo demás sobra.
      const vivas = Object.entries(todas).filter(([, x]) => x && Date.now() - x.t < 7 * 864e5).sort((a, b) => b[1].t - a[1].t).slice(0, 6);
      localStorage.setItem(LLAVE_PESTANAS, JSON.stringify(Object.fromEntries(vivas)));
    } catch (_) { /* sin almacenamiento: no se recuerdan */ }
  }
  function pestanasRecordadas() {
    const todas = leerPestanas();
    const mia = todas[ventana];
    if (mia && mia.ids && mia.ids.includes(t0.cfg.id)) return mia;
    // Ventana nueva (otra sesión del navegador): la lista más reciente que tenga este plano.
    return Object.values(todas).filter((x) => x && x.ids && x.ids.includes(t0.cfg.id) && Date.now() - x.t < 12 * 36e5).sort((a, b) => b.t - a.t)[0] || null;
  }
  async function restaurarPestanas() {
    const r = pestanasRecordadas();
    if (!r || r.ids.length < 2) { guardarPestanas(); return; }
    // El plano de la página primero (ya está abierto); los demás en su orden, detrás.
    const orden = r.ids.slice(0, P.max);
    for (const id of orden) if (id !== t0.cfg.id) await abrirPestana(id, { detras: true });
    // El orden de la tira es el de antes.
    pestanas.sort((a, b) => orden.indexOf(a.cfg.id) - orden.indexOf(b.cfg.id));
    pintarPestanas();
    const quiere = pestanas.find((t) => t.cfg.id === r.activa);
    if (quiere && quiere !== activa) await activar(quiere);
    guardarPestanas();
  }

  // -- El árbol de la biblioteca (arbol.js), solo cuando se usa: la página del plano no lo carga al abrir -----------------------
  let arbolJs = null;
  window.TVCadArbolCargar = () => {
    if (window.TVCadArbol) return Promise.resolve(window.TVCadArbol);
    if (!arbolJs) arbolJs = new Promise((ok, mal) => {
      const s = document.createElement("script"); s.src = C.carpetas.arbol_js;
      s.onload = () => ok(window.TVCadArbol); s.onerror = () => { arbolJs = null; mal(new Error("No se pudo cargar la biblioteca. Revisa la conexión.")); };
      document.head.append(s);
    });
    return arbolJs;
  };

  // -- «Mover a…» desde la hoja Renombrar: el plano activo a otra carpeta de la biblioteca (su bloqueo y su pestaña siguen igual) --
  const moverB = $("cad-ren-mover");
  if (moverB && C.carpetas) moverB.addEventListener("click", async () => {
    const t = activa;
    try {
      const A = await window.TVCadArbolCargar();
      $("cad-renombrar").close();
      const destino = await A.moverA({ titulo: `Mover «${t.e.nombre}» a…`, actual: t.cfg.carpeta || "", url: C.carpetas.lista, nueva: C.carpetas.nueva });
      if (destino === undefined) return;
      const r = await pedir(C.carpetas.mover, { metodo: "POST", json: { planos: [t.cfg.id], carpetas: [], destino } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo mover el plano.");
      t.cfg.carpeta = d.destino.id; t.cfg.ubicacion = d.destino.ubicacion; t.cfg.proyecto = d.destino.proyecto;
      pintarCabecera(t);
      avisar(d.mensaje);
    } catch (err) { avisar(err instanceof TypeError ? "Sin conexión: inténtalo de nuevo." : err.message, "error"); }
  });

  // -- «Abrir otro plano»: la biblioteca en una hoja, carpeta por carpeta (las mismas migas y el mismo árbol que «Planos») -------
  const hojaElegir = $("cad-elegir");
  let explorador = null;
  function filaElegir(x) {
    const li = document.createElement("li");
    const b = document.createElement("button"); b.type = "button"; b.className = "cad-elegir__plano"; b.dataset.id = x.id;
    b.disabled = !x.disponible;
    const fmt = document.createElement("span"); fmt.className = "itt-formato cad-elegir__formato"; fmt.dataset.formato = x.formato; fmt.textContent = x.formato.toUpperCase();
    const texto = document.createElement("span"); texto.className = "cad-elegir__texto";
    const fuerte = document.createElement("strong"); fuerte.textContent = x.nombre;
    const meta = document.createElement("span"); meta.className = "tv-tenue";
    meta.textContent = [x.ubicacion ? `En ${x.ubicacion}` : "", `Modificado ${fechaLarga(x.actualizado)}${x.modificado_por ? ` por ${x.modificado_por}` : ""}`, tamano(x.bytes)].filter(Boolean).join(" · ");
    texto.append(fuerte, meta);
    b.append(fmt, texto);
    const abierto = pestanas.some((t) => t.cfg.id === x.id);
    const insignia = abierto ? ["Abierto", "tv-insignia--exito"] : !x.disponible ? ["Archivo no disponible", "tv-insignia--error"] : x.en_edicion_por ? [x.en_edicion_por, "tv-insignia--aviso"] : null;
    if (insignia) { const i = document.createElement("span"); i.className = `tv-insignia ${insignia[1]}`; i.textContent = insignia[0]; b.append(i); }
    b.addEventListener("click", async () => { hojaElegir.close(); await abrirPestana(x.id); });
    li.append(b);
    return li;
  }
  async function abrirElegir() {
    if (!hojaElegir) return;
    if (menu) menu.open = false;
    if (pestanas.length >= P.max) { avisar(`Ya tienes ${P.max} planos abiertos, el máximo en una ventana. Cierra uno para abrir otro.`, "error"); return; }
    let A;
    try { A = await window.TVCadArbolCargar(); } catch (err) { avisar(err.message, "error"); return; }
    if (!explorador) explorador = A.explorador({ url: P.elegir, urlCarpetas: C.carpetas.lista, arbol: $("cad-elegir-arbol"), migas: $("cad-elegir-migas"),
      lista: $("cad-elegir-lista"), mas: $("cad-elegir-mas"), q: $("cad-elegir-q"), alcance: $("cad-elegir-en"), clave: "elegir",
      decir: (t) => { $("cad-elegir-estado").textContent = t; }, filaPlano: filaElegir });
    hojaElegir.showModal();
    $("cad-elegir-q").focus();
    // Se empieza en la carpeta del plano a la vista: lo normal es abrir otro plano del mismo proyecto.
    explorador.abrir((activa && !activa.ajena && activa.cfg.carpeta) || "");
  }
  if (P && hojaElegir) {
    if ($("cad-abrir-otro")) $("cad-abrir-otro").addEventListener("click", abrirElegir);
    if ($("cad-menu-abrir-otro")) $("cad-menu-abrir-otro").addEventListener("click", abrirElegir);
  }
  document.addEventListener("tvcad:pantalla", () => pintarPestanas());

  // Para el modo táctil y CAD sin conexión (campo.js): la sesión de esta página (dueña del bloqueo) y volver a abrir la versión vigente.
  // `abiertos()`: la versión vigente que se acaba de bajar (para dejarla «Disponible sin conexión» sin bajarla otra vez).
  if (C.tactil) window.TVCadPagina = Object.freeze({ sesion: () => activa.sesion, reabrir: () => (activa.e.editable ? editarAhora(activa, false) : location.reload()),
    abiertos: () => (activa.e.abiertos && activa.e.abiertos.revision === activa.e.revision ? { revision: activa.e.abiertos.revision, bytes: activa.e.abiertos.bytes.slice(0) } : null),
    desdeTelefono: () => !!activa.e.desdeTelefono });
  // Para las pruebas y otras partes de la página: las pestañas, en solo lectura.
  window.TVCadPestanas = Object.freeze({
    lista: () => pestanas.map((t) => ({ id: t.cfg.id, nombre: t.e.nombre, documento: t.documento, activa: t === activa, cargado: t.e.cargado,
      editable: t.e.editable, sucio: sucia(t), estado: estadoDe(t)[0], ajena: t.ajena, revision: t.e.revision })),
    abrir: (id) => abrirPestana(id).then((t) => (t ? t.cfg.id : null)), activar: (id) => { const t = pestanas.find((x) => x.cfg.id === id); return t ? activar(t) : null; },
    guardarTodo: () => guardarTodo().then((r) => ({ ok: r.ok, filas: r.filas.map((f) => ({ id: f.t.cfg.id, ok: f.ok, revision: f.revision, motivo: f.motivo })) })),
    maximo: () => (P ? P.max : 1),
  });

  // -- Planos grandes en un teléfono o una tableta ---------------------------------------------------------------------------------
  // Un teléfono deja a una pestaña mucha menos memoria que una computadora, y si el editor pasa de ella el navegador cierra la página sin
  // aviso. Antes de bajar el editor y el plano se estima lo que el editor necesitará y, si no cabe, se dice claro, con «Descargar el plano»
  // y, por si acaso, «Intentar abrirlo aquí». El modelo y el tope están medidos (docs/cad.md, «Planos grandes»).
  // Medido el 18-11-2026 (WebKit, la memoria del editor tras abrir y dibujar): el editor solo ~270 MB; un DWG denso de 25.5 MB, 1 153 MB
  // (35 MB por MB); un DXF de 22 MB, 371 MB (5 por MB). Se toma un poco más, para quedar del lado seguro.
  const MEMORIA_EDITOR = Object.freeze({ base: 300, porMB: { dwg: 40, dxf: 10 } });                         // MB del editor: base + MB × archivo
  // Lo que el editor puede usar en un iPhone o iPad sin que Safari cierre la página: ~1 GB en los de 4 GB de memoria (iOS deja a una
  // pestaña algo más de 1.5 GB y el navegador, la página y la GPU toman lo suyo). No se puede medir aquí (sin teléfonos ni simuladores):
  // es el tope conservador de WebKit en iOS, y con él caben los DWG de hasta ~17 MB y los DXF de hasta ~70 MB.
  const TOPE_TELEFONO_MB = 1000;
  const tactilPrimero = () => matchMedia("(pointer: coarse)").matches;                                    // el puntero principal es el dedo
  function memoriaNecesariaMB(bytes, formato) {
    return MEMORIA_EDITOR.base + (MEMORIA_EDITOR.porMB[formato] || MEMORIA_EDITOR.porMB.dwg) * (bytes / 1048576);
  }
  // Android dice su memoria (navigator.deviceMemory, en GB, a lo más 8); iPhone y iPad no: se toma el tope medido.
  function topeDelEquipoMB() {
    const gb = Math.min(Number(navigator.deviceMemory) || 0, 8);                                         // la especificación tapa en 8
    return gb > 0 ? Math.min(TOPE_TELEFONO_MB * Math.max(1, gb / 4), 3072) : TOPE_TELEFONO_MB;
  }
  function cabeEnEsteEquipo(t) {
    const bytes = Number(t.cfg.bytes) || 0, formato = t.e.formato;
    if (!tactilPrimero() || !bytes || memoriaNecesariaMB(bytes, formato) <= topeDelEquipoMB()) return Promise.resolve(true);
    const panel = $("cad-grande");
    if (!panel) return Promise.resolve(true);
    carga("");
    const mb = (bytes / 1048576).toLocaleString("es-MX", { maximumFractionDigits: 1 });
    $("cad-grande-texto").textContent = `Pesa ${mb} MB y abrirlo necesita más memoria de la que el navegador de este equipo deja usar: se cerraría sin avisar. Ábrelo en una computadora o descárgalo.`;
    panel.hidden = false;
    anunciar("grande", { bytes, necesaria: Math.round(memoriaNecesariaMB(bytes, formato)), tope: Math.round(topeDelEquipoMB()) });
    return new Promise((resolver) => {
      $("cad-grande-intentar").addEventListener("click", () => { panel.hidden = true; carga("Abriendo el editor…"); resolver(true); }, { once: true });
    });
  }
  window.TVCadMemoria = Object.freeze({ necesariaMB: memoriaNecesariaMB, topeMB: topeDelEquipoMB });

  // -- Arranque -------------------------------------------------------------------------------------------------------------------
  // Después de los demás scripts de la página (campo.js): si el CAD de este teléfono tiene una versión vieja, primero se baja lo que
  // cambió (solo eso) y luego se abre el editor desde el teléfono; si no, el marco bajaría la versión nueva entera de la red.
  refrescarEstado(t0);
  pintarPestanas();
  // El editor guardado en este equipo (instalador.js, docs/cad.md «El editor guardado en el equipo»): en la computadora se guarda solo
  // la primera vez y cada versión nueva baja solo lo que cambió; en el teléfono, solo si se descargó (modo táctil). Si el editor no arranca
  // desde lo guardado, esa versión se borra y se abre de la red.
  const ENEL_TELEFONO = matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
  async function arrancar() {
    await cabeEnEsteEquipo(t0);
    const campo = Campo(t0);
    let desde = "red";
    try {
      if (ENEL_TELEFONO) { if (campo && campo.prepararEditor) desde = await campo.prepararEditor((x) => carga(x)); }
      else if (C.guardado && window.TVCadEditor) desde = await window.TVCadEditor.preparar({ decir: (x) => carga(x), automatico: true });
    } catch (_) { desde = "red"; /* se abre desde la red, como sin el CAD guardado */ }
    if (window.TVCadEditor && window.TVCadEditor.pintarMenu) window.TVCadEditor.pintarMenu();
    try {
      await cargarMarco();
    } catch (err) {
      if (desde !== "equipo" || !window.TVCadEditor) throw err;
      await window.TVCadEditor.fallo(C.guardado ? C.guardado.version : (C.tactil || {}).version);
      await cargarMarco();
    }
    await alListo(t0);
    if (P && !enTactil() && !(C.tactil && matchMedia("(pointer: coarse)").matches)) await restaurarPestanas();
  }
  if (frame) {
    const empezar = () => arrancar().catch((err) => { carga(""); error(t0, err.message); });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", empezar); else setTimeout(empezar, 0);
  } else carga("");
})();

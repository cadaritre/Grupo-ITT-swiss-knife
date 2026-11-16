/* CAD: el editor (Open CAD Studio web) en un marco del mismo origen, unido a la biblioteca por su puente oficial (?embed=1, postMessage).
 *
 * - Abre la versión vigente (o una anterior, o el borrador de recuperación) con `ocs:open`.
 * - Guardar, Ctrl/⌘+S y «Guardar» al cerrar dentro del editor llegan como `ocs:save`: se guardan en el servidor como versión nueva y se
 *   responde `ocs:saved`. Si el plano cambió o se perdió el bloqueo, nada se pisa: se ofrece guardar como copia.
 * - Bloqueo de edición con latido; quien no lo tiene ve el plano en solo lectura y quién lo edita.
 * - Mientras hay cambios sin guardar, cada 2 minutos se guarda un borrador de recuperación (no es una versión).
 * - Sin el aviso del navegador «¿Abandonar el sitio?»: al salir por la página se pregunta con una hoja propia; lo demás lo cubre el borrador.
 */
(function () {
  "use strict";
  const C = JSON.parse(document.getElementById("cad-config").textContent);
  const $ = (id) => document.getElementById(id);
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : ""; };
  const avisar = (texto, tipo) => { if (window.tvAviso) window.tvAviso(texto, tipo); };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const frame = $("cad-editor");
  const SESION = (() => { const b = new Uint8Array(18); crypto.getRandomValues(b); return btoa(String.fromCharCode(...b)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c])); })();
  const TELEFONO = matchMedia("(max-width: 767px) and (pointer: coarse)").matches;

  const e = {
    revision: C.revision, base: C.revision, formato: C.version_formato || C.formato, nombre: C.nombre,
    listo: false, cargado: false, editable: false, sucio: false, sucioDesde: 0, ultimoBorrador: 0,
    guardando: false, bufferPendiente: null, lecturaMotivo: "", perdido: false, salirDespues: null, recuperado: false,
  };

  // Avisos para las extensiones de la página (control.js, `window.TVCad`).
  const anunciar = (nombre, detalle) => document.dispatchEvent(new CustomEvent(`tvcad:${nombre}`, { detail: detalle }));

  // -- Estado visible ---------------------------------------------------------------------------------------------------------
  const chip = $("cad-estado"), chipTexto = $("cad-estado-texto"), botonGuardar = $("cad-guardar");
  function pintarEstado(estado, texto) {
    if (!chip) return;
    chip.dataset.estado = estado;
    chipTexto.textContent = texto;
    const punto = chip.querySelector(".tv-punto");
    punto.className = "tv-punto " + ({ guardado: "tv-punto--exito", sucio: "tv-punto--aviso", error: "tv-punto--error" }[estado] || "tv-punto--neutro");
    if (botonGuardar) botonGuardar.disabled = !((e.editable || e.perdido) && e.cargado) || e.guardando || !(e.sucio || e.recuperado);
  }
  function refrescarEstado() {
    if (!e.cargado) return pintarEstado("cargando", "Abriendo…");
    if (e.guardando) return pintarEstado("guardando", "Guardando…");
    if (!e.editable) return pintarEstado("lectura", "Solo lectura");
    if (e.sucio || e.recuperado) return pintarEstado("sucio", "Sin guardar");
    pintarEstado("guardado", "Guardado");
  }
  // `descargar`: el error es de guardar; se ofrece bajar los cambios a este equipo (auditoría M-3: con la biblioteca llena, un plano
  // demasiado grande, el servidor caído o sin red, la persona no tenía otra forma de conservar su trabajo).
  function error(texto, { descargar = false } = {}) {
    const caja = $("cad-aviso-error");
    caja.hidden = !texto;
    $("cad-aviso-error-texto").textContent = texto || "";
    const acciones = $("cad-aviso-error-acciones");
    if (acciones) acciones.hidden = !(texto && descargar && e.cargado);
  }
  function lectura(texto, { tomar = false, reintentar = false } = {}) {
    const caja = $("cad-aviso-lectura");
    caja.hidden = !texto;
    $("cad-aviso-lectura-texto").textContent = texto || "";
    $("cad-tomar").hidden = !tomar;
    $("cad-reintentar").hidden = !reintentar;
  }
  function carga(texto) {
    const c = $("cad-carga");
    if (!c) return;
    c.hidden = !texto;
    if (texto) $("cad-carga-texto").textContent = texto;
  }

  // -- Peticiones -------------------------------------------------------------------------------------------------------------
  async function pedir(url, { metodo = "GET", json, cuerpo, cabeceras = {}, keepalive = false } = {}) {
    const opciones = { method: metodo, credentials: "same-origin", keepalive, headers: { Accept: "application/json", ...cabeceras } };
    if (metodo !== "GET") opciones.headers["X-CSRFToken"] = csrf();
    if (json !== undefined) { opciones.headers["Content-Type"] = "application/json"; opciones.body = JSON.stringify(json); }
    else if (cuerpo !== undefined) opciones.body = cuerpo;
    const r = await fetch(url, opciones);
    if (r.status === 401) { sesionTerminada(); throw new Error("Tu sesión terminó. Entra de nuevo en otra pestaña y vuelve aquí para guardar."); }
    return r;
  }

  // -- Sesión terminada (auditoría M-4) -----------------------------------------------------------------------------------------
  // Nunca se recarga la página: se perdería lo que no alcanzó a entrar al borrador. Se avisa con una franja fija, se pausa el latido y
  // se ofrece entrar en otra pestaña y descargar los cambios; al volver a esta pestaña con la sesión de nuevo, todo sigue como estaba.
  function sesionTerminada() {
    if (e.sinSesion) return;
    e.sinSesion = true;
    clearInterval(latido);
    const caja = $("cad-aviso-sesion");
    if (caja) {
      caja.hidden = false;
      const b = caja.querySelector("[data-cad-descargar-cambios]");
      if (b) b.hidden = !(e.sucio || e.recuperado);
    }
  }
  async function comprobarSesion() {
    if (!e.sinSesion) return;
    let r;
    try { r = await fetch(`${C.urls.estado}?sesion=${SESION}`, { credentials: "same-origin", headers: { Accept: "application/json" } }); }
    catch (_) { return; }                                                  // sin red: se vuelve a intentar al volver a la pestaña
    if (r.status === 401 || !r.ok) return;
    e.sinSesion = false;
    const caja = $("cad-aviso-sesion"); if (caja) caja.hidden = true;
    if (e.editable) {
      latir();
      try { const d = await bloqueo("latido"); if (d.perdido) perderBloqueo(d); } catch (_) { /* el latido sigue */ }
    }
    avisar("Sesión recuperada: ya puedes guardar.");
  }
  addEventListener("focus", comprobarSesion);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") comprobarSesion(); });
  async function datos(r) { try { return await r.json(); } catch (_) { return {}; } }

  // -- El editor: canal de control oficial (`?automation=1`) y, si la versión fijada lo trae, el puente `?embed=1` ---------------------
  // Con el canal se abre el plano, se lee su estado (cambios sin guardar) y se piden los bytes para guardar o para el borrador.
  // Lo que la persona guarda DENTRO del editor (Ctrl/⌘+S, Guardar) llega por `openCadStudioSaveFile` (versiones sin puente) o como
  // `ocs:save` (versiones con puente, que además abre en solo lectura). Las dos vías terminan en `recibirGuardado`.
  let control = null, nPeticion = 0, documento = null, revEditor = 0, revGuardada = 0, abrioVez = false;

  function alEditor(tipo, campos = {}, transferir) {
    if (!frame.contentWindow) return;
    frame.contentWindow.postMessage({ source: "opencadstudio-host", type: tipo, ...campos }, location.origin, transferir || []);
  }
  async function canal(peticion, tiempoMs = 120000) {
    if (!control) throw new Error("El editor no está listo.");
    nPeticion += 1;
    const r = await control.request({ protocol: 1, request_id: `tv-${SESION.slice(0, 8)}-${nPeticion}-${Date.now()}`, ...peticion }, { timeoutMs: tiempoMs });
    return r || {};
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
      // Versiones sin puente: lo que la persona guarda en el editor llega aquí, sin diálogo de descarga.
      frame.contentWindow.openCadStudioSaveFile = (nombre, bytes) => {
        const ext = String(nombre || "").split(".").pop().toLowerCase();
        const copia = bytes.slice().buffer;
        if (ext === "dwg" || ext === "dxf") { recibirGuardado({ buffer: copia, formato: ext, origen: "editor" }); return; }
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
      const datos = bytesFuente.get(f.url);
      let res = null;
      if (datos) { try { res = await canal({ op: "font_register", file_name: f.nombre, data_base64: datos }, 15000); } catch (_) { res = null; } }
      const ok = !!(res && res.ok !== false && res.result);
      if (!ok) fallas.push(f.nombre);
      const glifos = ok ? res.result.shapes : null;
      if (ok ? (glifos !== f.glifos || f.error) : !f.error) {
        fetch(f.informe, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          // El motivo va en español y fijo (auditoría B-9): el texto del editor viene en inglés y la página de Fuentes lo mostraba tal cual.
          body: JSON.stringify({ ok, glifos, error: ok ? "" : !datos ? "No se pudo bajar el archivo de la fuente." : !res ? "El editor no respondió al cargarla."
            : "El editor no pudo leer este archivo de fuente." }) }).catch(() => {});
      }
    }
    const aviso = $("cad-aviso-fuentes");
    if (aviso && C.es_admin && fallas.length) {
      $("cad-aviso-fuentes-texto").textContent = `${fallas.length === 1 ? "Una fuente no se pudo usar" : `${fallas.length} fuentes no se pudieron usar`} (${fallas.join(", ")}): sus textos se ven con la de reemplazo.`;
      aviso.hidden = false;
    }
  }

  async function bytesDe(url) {
    const r = await pedir(url);
    if (!r.ok) throw new Error(r.status === 404 ? "El plano ya no existe." : "No se pudo descargar el plano. Recarga la página.");
    return r.arrayBuffer();
  }

  async function abrir(fuente, soloLectura) {
    e.cargado = false; e.sucio = false;
    refrescarEstado();
    carga("Abriendo el plano…");
    let url = C.urls.archivo, formato = e.formato;
    if (fuente === "borrador") { url += "?borrador=1"; formato = (C.borrador && C.borrador.formato) || e.formato; }
    else if (C.version) url += `?version=${C.version}`;
    const buffer = await bytesDe(url);
    if (abrioVez) await cargarMarco();                                    // un editor limpio por plano: sin pestañas viejas
    abrioVez = true; documento = null;
    e.editable = !soloLectura;
    const nombre = `${e.nombre}.${formato}`;
    if (C.puente) {
      alEditor("ocs:open", { name: nombre, buffer, readOnly: !!soloLectura }, [buffer]);
      return;                                                             // termina en `ocs:loaded`
    }
    // El plano cuenta como abierto cuando aparece su pestaña. La respuesta de «open» puede tardar o venir con un error que no es del plano
    // (WebKit: el editor no logra guardar su lista de recientes en el almacenamiento del navegador), así que no se espera solo a ella.
    let respuesta = null;
    const pedido = canal({ op: "open", name: nombre, data_base64: base64(buffer) }).then((r) => { respuesta = r; }, () => { respuesta = { ok: false }; });
    for (let i = 0; i < 1200 && !documento; i++) {
      const est = await canal({ op: "state" }, 10000).catch(() => ({}));
      const doc = (est.documents || []).find((d) => !d.start);
      if (doc) { documento = doc.id; revEditor = revGuardada = doc.revision || 0; break; }
      if (respuesta && respuesta.ok === false && i > 20) break;
      await espera(100);
    }
    if (!documento) {
      await Promise.race([pedido, espera(1000)]);
      carga(""); error("No se pudo abrir el plano en el editor. Descárgalo y revísalo en tu programa de dibujo.");
      return;
    }
    alCargar();
  }
  function alCargar() {
    e.cargado = true; carga(""); e.sucio = false; refrescarEstado();
    anunciar("cargado", { editable: e.editable, documento, plano: { id: C.id, nombre: e.nombre, formato: e.formato, revision: e.revision } });
  }

  window.addEventListener("message", (ev) => {
    if (ev.origin !== location.origin || ev.source !== frame.contentWindow) return;
    const m = ev.data || {};
    if (m.source === "tresvizo-cad-editor" && m.type === "guardar") { if (C.puede_editar) pedirGuardado(e.perdido ? "copia" : "plano"); return; }
    if (m.source !== "opencadstudio-embed") return;
    if (m.type === "ocs:loaded") {
      canal({ op: "state" }).then((est) => {
        const doc = (est.documents || []).find((d) => !d.start);
        if (doc) { documento = doc.id; revEditor = revGuardada = doc.revision || 0; }
      }).catch(() => {}).finally(alCargar);
      return;
    }
    if (m.type === "ocs:save") return recibirGuardado({ buffer: m.buffer, formato: m.format, origen: "editor", saveId: m.saveId });
    if (m.type === "ocs:error") return alError(m);
  });
  function alError(m) {
    if (m.code === "host_save_failed") return;                           // lo provocamos nosotros al rechazar un guardado
    if (m.code === "read_only") { avisar(e.lecturaMotivo || "Este plano está abierto en solo lectura.", "error"); return; }
    if (m.code === "open_failed") { carga(""); error("No se pudo abrir el plano en el editor. Descárgalo y revísalo en tu programa de dibujo."); return; }
    if (m.code === "save_failed") { e.guardando = false; refrescarEstado(); error("El editor no pudo preparar el archivo para guardarlo. Inténtalo de nuevo."); }
  }

  // Cambios sin guardar: la revisión del dibujo en el editor contra la que tenía al abrirlo o al guardarlo en la biblioteca.
  let consultando = false;
  setInterval(async () => {
    if (consultando || !e.cargado || !documento || !control || document.visibilityState === "hidden") return;
    consultando = true;                                                    // nunca dos consultas a la vez: en un equipo lento se apilarían
    try {
      const est = await canal({ op: "state" }, 5000);
      const doc = (est.documents || []).find((d) => d.id === documento);
      if (!doc) return;
      revEditor = doc.revision || 0;
      const sucio = revEditor !== revGuardada;
      if (sucio !== e.sucio) {
        e.sucio = sucio;
        if (sucio) e.sucioDesde = Date.now();
        if (sucio && e.borradorPendiente) elegirBorrador();
        anunciar("cambio", { sucio });
        refrescarEstado();
      }
    } catch (_) { /* el editor está ocupado: la próxima vuelta */ }
    finally { consultando = false; }
  }, 1000);

  // -- Guardar ------------------------------------------------------------------------------------------------------------------
  // Bytes del plano tal como está en el editor (no cambia lo que la persona ve). `formato`: el del plano.
  async function bytesDelEditor() {
    const r = await canal({ op: "save", document_id: documento, name: `${e.nombre}.${e.formato}`, target_format: e.formato });
    if (r.ok === false || !r.result || !r.result.data_base64) throw new Error("El editor no pudo preparar el archivo. Inténtalo de nuevo.");
    return deBase64(r.result.data_base64);
  }

  // Los cambios tal como están en el editor, a un archivo de este equipo (no cambia nada en la biblioteca).
  async function descargarCambios(boton) {
    if (!e.cargado || !documento) return;
    if (boton) boton.setAttribute("aria-busy", "true");
    try {
      const buffer = await bytesDelEditor();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([buffer], { type: "application/octet-stream" }));
      a.download = `${e.nombre} (cambios sin guardar).${e.formato}`;
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      avisar("Tus cambios se descargaron a este equipo.");
    } catch (err) { avisar(err.message || "No se pudieron descargar los cambios.", "error"); }
    finally { if (boton) boton.removeAttribute("aria-busy"); }
  }
  document.addEventListener("click", (ev) => { const b = ev.target.closest("[data-cad-descargar-cambios]"); if (b) descargarCambios(b); });

  async function pedirGuardado(destino) {
    if (!e.cargado || e.guardando) return false;
    if (destino !== "borrador" && !e.editable && !e.perdido) { avisar(e.lecturaMotivo || "Este plano está abierto en solo lectura.", "error"); return false; }
    if (destino !== "borrador" && extensionOcupada()) { avisar("Espera a que termine lo que estás haciendo en Civil y vuelve a guardar.", "error"); return false; }
    if (destino !== "borrador") { e.guardando = true; refrescarEstado(); }
    // La revisión que se guarda es la de este momento, no la de la última consulta (cada segundo): un cambio hecho por el canal justo antes
    // de «Guardar» (p. ej. Civil) dejaba el plano en «Sin guardar» aunque sí se guardó.
    let revision = revEditor;
    try { const est = await canal({ op: "state" }, 5000); const doc = (est.documents || []).find((d) => d.id === documento); if (doc) revision = revEditor = doc.revision || 0; }
    catch (_) { /* se usa la última conocida */ }
    try {
      const buffer = await bytesDelEditor();
      if (destino === "borrador") await guardarBorrador(buffer);
      else await recibirGuardado({ buffer, formato: e.formato, origen: "pagina", destino, revision });
    } catch (err) {
      e.guardando = false; refrescarEstado();
      if (destino !== "borrador") error(err.message);
      return false;
    }
    return true;
  }

  async function recibirGuardado({ buffer, formato, origen, saveId, destino, revision }) {
    const responder = (ok, mensaje, nombre) => { if (saveId) alEditor("ocs:saved", { ok, saveId, error: mensaje, name: nombre }); };
    formato = formato === "dxf" ? "dxf" : "dwg";
    if (revision === undefined) {                                         // guardado hecho en el editor: su revisión es la de ahora
      try { const est = await canal({ op: "state" }, 5000); const doc = (est.documents || []).find((d) => d.id === documento); revision = doc ? doc.revision : revEditor; }
      catch (_) { revision = revEditor; }
    }
    if (!e.editable && !e.perdido) {
      responder(false, "Solo lectura: este plano no se puede guardar aquí.");
      avisar(e.lecturaMotivo || "Este plano está abierto en solo lectura: no se guardó.", "error");
      return;
    }
    e.guardando = true; refrescarEstado(); error("");
    if (destino === "copia" || e.perdido) return guardarCopia(buffer, saveId);
    try {
      const r = await pedir(C.urls.guardar, { metodo: "POST", cuerpo: buffer, cabeceras: { "Content-Type": "application/octet-stream", "X-TV-Sesion": SESION, "X-TV-Revision": String(e.base) } });
      const d = await datos(r);
      if (r.ok) {
        e.revision = e.base = d.revision; e.formato = d.formato; e.recuperado = false;
        revGuardada = revision; e.sucio = revEditor !== revGuardada;
        responder(true, undefined, `${e.nombre}.${d.formato}`);
        $("cad-formato").textContent = d.formato.toUpperCase(); $("cad-formato").dataset.formato = d.formato;
        pintarVersiones(d.versiones);
        pintarSubtitulo(d);
        ocultarBorrador();
        avisar(`Guardado · versión ${d.revision}`);
        anunciar("guardado", { revision: d.revision, formato: d.formato });
        e.guardando = false; refrescarEstado();
        if (e.salirDespues) { const destinoSalida = e.salirDespues; e.salirDespues = null; salir(destinoSalida); }
        return;
      }
      responder(false, d.error || "No se guardó.");
      e.guardando = false; refrescarEstado();
      if (r.status === 409) { e.bufferPendiente = buffer; return conflicto(d.error); }
      error(d.error || "No se pudo guardar. Inténtalo de nuevo.", { descargar: true });
    } catch (err) {
      responder(false, "Sin conexión: no se guardó.");
      e.guardando = false; refrescarEstado();
      error("No se pudo guardar: revisa tu conexión e inténtalo de nuevo. Tus cambios siguen en el editor.", { descargar: true });
    }
  }

  function conflicto(texto) {
    $("cad-conflicto-texto").textContent = texto || "El plano cambió mientras lo editabas.";
    const d = $("cad-conflicto");
    if (!d.open) d.showModal();
  }

  async function guardarCopia(buffer, saveId) {
    try {
      const r = await pedir(C.urls.copia, { metodo: "POST", cuerpo: buffer, cabeceras: { "Content-Type": "application/octet-stream" } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo guardar la copia.");
      if (saveId) alEditor("ocs:saved", { ok: true, saveId, name: `${d.nombre}.${e.formato}` });
      e.sucio = false; e.recuperado = false; e.guardando = false;
      soltar();
      avisar(`Se guardó «${d.nombre}»`);
      location.assign(d.url);
    } catch (err) {
      if (saveId) alEditor("ocs:saved", { ok: false, saveId, error: err.message });
      e.guardando = false; refrescarEstado();
      error(err instanceof TypeError ? "No se pudo guardar la copia: revisa tu conexión e inténtalo de nuevo. Tus cambios siguen en el editor." : err.message, { descargar: true });
    }
  }

  $("cad-guardar-copia").addEventListener("click", async () => {
    $("cad-conflicto").close();
    if (e.bufferPendiente) { const b = e.bufferPendiente; e.bufferPendiente = null; e.guardando = true; refrescarEstado(); return guardarCopia(b, null); }
    pedirGuardado("copia");
  });

  if (botonGuardar) botonGuardar.addEventListener("click", () => pedirGuardado(e.perdido ? "copia" : "plano"));
  document.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && !ev.altKey && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      if (C.puede_editar) pedirGuardado(e.perdido ? "copia" : "plano");
    }
  });

  // -- Borrador de recuperación ---------------------------------------------------------------------------------------------
  let borradorEnCurso = false;
  async function guardarBorrador(buffer) {
    try {
      const r = await pedir(C.urls.borrador, { metodo: "POST", cuerpo: buffer, cabeceras: { "Content-Type": "application/octet-stream", "X-TV-Revision": String(e.base) } });
      if (r.ok) e.ultimoBorrador = Date.now();
    } catch (_) { /* sin red: se intenta en la siguiente vuelta */ }
  }
  const extensionOcupada = () => !!(window.TVCad && window.TVCad.ocupado && window.TVCad.ocupado());
  async function borrador() {
    // Una extensión (Civil) trabajando con el editor: el editor rechazaría guardar a media operación; se intenta en la siguiente vuelta.
    // Con el aviso «Tienes cambios sin guardar de la última vez» pendiente, un borrador nuevo pisaría ese (auditoría M-6): primero se elige.
    if (e.borradorPendiente || extensionOcupada() || borradorEnCurso || !(e.sucio || e.recuperado) || !e.cargado || e.guardando || !(e.editable || e.perdido)) return;
    borradorEnCurso = true;
    try { await pedirGuardado("borrador"); } finally { borradorEnCurso = false; }
  }
  setInterval(() => {
    const desde = Math.max(e.sucioDesde, e.ultimoBorrador);
    if (Date.now() - desde >= C.autoguardado_s * 1000) borrador();
  }, Math.min(5000, C.autoguardado_s * 500));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && Date.now() - e.ultimoBorrador > 20000) borrador();
  });

  function ocultarBorrador() { $("cad-aviso-borrador").hidden = true; e.borradorPendiente = false; }
  function mostrarBorrador() {
    if (!C.borrador || !e.editable) return;
    const cuando = new Date(C.borrador.actualizado);
    const fecha = cuando.toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    $("cad-aviso-borrador-texto").textContent = C.borrador.revision_base === e.revision
      ? `Tienes cambios sin guardar del ${fecha}.`
      : `Tienes cambios sin guardar del ${fecha}, hechos sobre una versión anterior. Si los recuperas, se guardarán como copia.`;
    $("cad-aviso-borrador").hidden = false;
    e.borradorPendiente = true;
  }
  // Al primer cambio con el borrador anterior sin decidir, se pide elegir; la hoja no se cierra sin una respuesta.
  function elegirBorrador() {
    const d = $("cad-borrador-elegir");
    if (!d || d.open || !e.borradorPendiente) return;
    d.showModal();
  }
  if ($("cad-borrador-elegir")) {
    $("cad-borrador-elegir").addEventListener("cancel", (ev) => ev.preventDefault());
    $("cad-borrador-elegir-recuperar").addEventListener("click", () => { $("cad-borrador-elegir").close(); $("cad-recuperar").click(); });
    $("cad-borrador-elegir-descartar").addEventListener("click", () => { $("cad-borrador-elegir").close(); $("cad-descartar-borrador").click(); });
    $("cad-borrador-elegir-copia").addEventListener("click", async (ev) => {
      const b = ev.currentTarget; b.setAttribute("aria-busy", "true");
      try {
        const bytes = await bytesDe(`${C.urls.archivo}?borrador=1`);
        const r = await pedir(C.urls.copia, { metodo: "POST", cuerpo: bytes, cabeceras: { "Content-Type": "application/octet-stream" } });
        const d = await datos(r);
        if (!r.ok) throw new Error(d.error || "No se pudo guardar la copia.");
        await pedir(C.urls.borrador_descartar, { metodo: "POST" });
        ocultarBorrador();
        $("cad-borrador-elegir").close();
        avisar(`Tus cambios de la última vez quedaron en «${d.nombre}».`);
      } catch (err) { avisar(err instanceof TypeError ? "No se pudo guardar la copia: revisa tu conexión." : err.message, "error"); }
      finally { b.removeAttribute("aria-busy"); }
    });
  }
  $("cad-recuperar").addEventListener("click", async () => {
    ocultarBorrador();
    try {
      e.base = C.borrador.revision_base;
      await abrir("borrador", false);
      e.recuperado = true; e.sucioDesde = Date.now();
      anunciar("cambio", { sucio: true });
      refrescarEstado();
      if (e.base !== e.revision) { e.perdido = true; lectura("Estos cambios se hicieron sobre una versión anterior: al guardar se crea una copia."); }
    } catch (err) { error(err.message); }
  });
  $("cad-descartar-borrador").addEventListener("click", async () => {
    ocultarBorrador();
    await pedir(C.urls.borrador_descartar, { metodo: "POST" }).catch(() => {});
    avisar("Cambios descartados");
  });

  // -- Bloqueo --------------------------------------------------------------------------------------------------------------------
  async function bloqueo(accion) {
    const r = await pedir(C.urls.bloqueo, { metodo: "POST", json: { accion, sesion: SESION } });
    const d = await datos(r);
    if (r.status === 410) { error("Este plano se eliminó."); throw new Error("eliminado"); }
    if (!r.ok) throw new Error(d.error || "No se pudo comprobar quién edita el plano.");
    return d;
  }
  function textoDeQuien(d) {
    return d.otra_ventana ? "Este plano está abierto para editar en otra ventana tuya." : `${d.quien} está editando este plano. Lo ves en solo lectura.`;
  }

  async function alListo() {
    if (e.listo) return;
    e.listo = true;
    carga("Abriendo el plano…");
    try {
      if (C.version) { e.lecturaMotivo = "Es una versión anterior: solo lectura."; return await abrir("vigente", true); }
      if (!C.puede_editar) { e.lecturaMotivo = "Tienes acceso de solo lectura."; lectura(C.rol_lectura ? "Tienes acceso de solo lectura." : ""); return await abrir("vigente", true); }
      if (TELEFONO) {
        e.lecturaMotivo = "En el teléfono los planos se abren para ver.";
        lectura("En el teléfono los planos se abren para ver. Para editar, usa una computadora o tableta.");
        return await abrir("vigente", true);
      }
      const d = await bloqueo("tomar");
      if (d.mio) {
        e.revision = e.base = d.revision;
        await abrir("vigente", false);
        mostrarBorrador();
        latir();
      } else {
        e.lecturaMotivo = textoDeQuien(d);
        lectura(e.lecturaMotivo, { tomar: C.es_admin || d.otra_ventana });
        await abrir("vigente", true);
        vigilarLiberacion();
      }
    } catch (err) {
      if (err.message !== "eliminado") error(err.message);
      carga("");
    }
  }

  let latido = null;
  function latir() {
    clearInterval(latido);
    latido = setInterval(async () => {
      if (!e.editable || e.sinSesion) return;
      try {
        const d = await bloqueo("latido");
        if (d.perdido) perderBloqueo(d);
        else if (!e.guardando && Number.isInteger(d.revision) && d.revision > e.base) cambioAjeno();
      } catch (_) { /* sin red un momento: el bloqueo dura 90 s */ }
    }, C.latido_s * 1000);
  }
  function perderBloqueo(d) {
    clearInterval(latido);
    e.editable = false; e.perdido = true;
    anunciar("editable", { editable: false });
    e.lecturaMotivo = d.otra_ventana ? "Abriste este plano para editar en otra ventana." : `${d.quien || "Otra persona"} tomó el control de este plano.`;
    lectura(`${e.lecturaMotivo} ${e.sucio ? "Guarda tus cambios como copia para no perderlos." : ""}`.trim());
    if (botonGuardar) { botonGuardar.querySelector("span").textContent = "Guardar como copia"; }
    refrescarEstado();
  }

  // El bloqueo caducó (el equipo durmió más de 90 s), otra persona guardó una versión nueva y el latido lo volvió a tomar (auditoría M-7):
  // seguir sería dibujar sobre una versión vieja. Se suelta el bloqueo, se dice quién guardó y lo de aquí se guarda como copia.
  async function cambioAjeno() {
    if (!e.editable) return;
    let quien = "";
    try { const r = await pedir(`${C.urls.estado}?sesion=${SESION}`); if (r.ok) quien = (await datos(r)).modificado_por || ""; } catch (_) { /* sin nombre */ }
    if (!e.editable) return;
    soltar();
    e.perdido = true;
    anunciar("editable", { editable: false });
    e.lecturaMotivo = `${quien || "Otra persona"} guardó una versión nueva de este plano mientras no estabas.`;
    lectura(`${e.lecturaMotivo} ${e.sucio || e.recuperado ? "Guarda tus cambios como copia para no perderlos." : "Toca «Editar ahora» para abrir la versión nueva."}`.trim(), { reintentar: !(e.sucio || e.recuperado) });
    if (botonGuardar) { botonGuardar.querySelector("span").textContent = "Guardar como copia"; }
    refrescarEstado();
  }

  let vigia = null;
  function vigilarLiberacion() {
    clearInterval(vigia);
    vigia = setInterval(async () => {
      try {
        const r = await pedir(`${C.urls.estado}?sesion=${SESION}`);
        const d = await datos(r);
        if (r.ok && d.bloqueo && d.bloqueo.libre) {
          clearInterval(vigia);
          lectura("Ya nadie está editando este plano.", { reintentar: true });
        }
      } catch (_) { /* se vuelve a intentar */ }
    }, 30000);
  }
  async function editarAhora(forzar) {
    try {
      const d = await bloqueo(forzar ? "forzar" : "tomar");
      if (!d.mio) { e.lecturaMotivo = textoDeQuien(d); lectura(e.lecturaMotivo, { tomar: C.es_admin || d.otra_ventana }); return; }
      clearInterval(vigia);
      e.revision = e.base = d.revision; e.perdido = false;
      lectura("");
      await abrir("vigente", false);
      latir();
      if (forzar && d.tomado_de) avisar(`Tomaste el control (lo editaba ${d.tomado_de})`);
    } catch (err) { if (err.message !== "eliminado") error(err.message); }
  }
  $("cad-tomar").addEventListener("click", () => editarAhora(true));
  $("cad-reintentar").addEventListener("click", () => editarAhora(false));

  function soltar() {
    if (!e.editable) return;
    e.editable = false;
    clearInterval(latido);
    try { fetch(C.urls.bloqueo, { method: "POST", keepalive: true, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ accion: "soltar", sesion: SESION }) }); } catch (_) { /* nada */ }
  }
  addEventListener("pagehide", () => {
    soltar();
    // Las copias de planos que el editor deja en el navegador no se quedan al salir (auditoría A-1; también se borran al arrancar
    // el editor y al cerrar sesión, ver cad/sesion.js).
    if (window.TVCadSesion) window.TVCadSesion.limpiarEditor();
  });

  // -- Salir de la página con cambios ----------------------------------------------------------------------------------------
  function salir(url) { e.sucio = false; e.recuperado = false; soltar(); location.assign(url); }
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[href]");
    if (!a || ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.shiftKey || a.target === "_blank" || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname === location.pathname || url.pathname.startsWith("/cad/") && url.searchParams.get("descargar") === "1") return;
    if (!(e.sucio || e.recuperado) || !(e.editable || e.perdido)) return;
    ev.preventDefault();
    e.salirA = url.href;
    $("cad-salir").showModal();
  }, true);
  $("cad-salir-sin").addEventListener("click", () => { $("cad-salir").close(); salir(e.salirA); });
  $("cad-salir-guardar").addEventListener("click", () => { $("cad-salir").close(); e.salirDespues = e.salirA; if (!pedirGuardado(e.perdido ? "copia" : "plano")) e.salirDespues = null; });

  // -- Versiones ---------------------------------------------------------------------------------------------------------------
  function icono(nombre) {
    const plantilla = document.querySelector(`#cad-iconos [data-cad-icono="${nombre}"] svg`);
    return plantilla ? plantilla.cloneNode(true) : document.createTextNode("");
  }
  function pintarVersiones(versiones) {
    const lista = $("cad-versiones-lista");
    if (!lista || !versiones) return;
    lista.replaceChildren(...versiones.map((x) => {
      const li = document.createElement("li");
      li.dataset.numero = x.numero;
      const texto = document.createElement("div"); texto.className = "cad-versiones__texto";
      const fuerte = document.createElement("strong"); fuerte.textContent = `Versión ${x.numero} `;
      if (x.vigente) { const i = document.createElement("span"); i.className = "tv-insignia tv-insignia--exito"; i.textContent = "Vigente"; fuerte.append(i); }
      if (x.disponible === false) { const i = document.createElement("span"); i.className = "tv-insignia tv-insignia--error"; i.textContent = "Archivo no disponible"; fuerte.append(" ", i); li.dataset.sinArchivo = "1"; }
      const tenue = document.createElement("span"); tenue.className = "tv-tenue";
      const cuando = x.creada ? new Date(x.creada).toLocaleString("es-MX", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
      tenue.textContent = [`Guardada por ${x.autor || "—"}`, cuando, x.nota, x.formato.toUpperCase(), tamano(x.bytes)].filter(Boolean).join(" · ");
      texto.append(fuerte, tenue);
      const acciones = document.createElement("div"); acciones.className = "cad-versiones__acciones";
      const base = `${C.urls.plano}`;
      const hay = x.disponible !== false;
      if (hay && !x.vigente) acciones.append(boton("a", `${base}?version=${x.numero}`, "eye", "Abrir (solo lectura)"));
      if (hay) acciones.append(boton("a", `${C.urls.archivo}?descargar=1&version=${x.numero}`, "download", "Descargar"));
      if (hay && C.puede_editar && !x.vigente) { const b = boton("button", null, "rotate-ccw", "Restaurar"); b.dataset.cadRestaurar = x.numero; acciones.append(b); }
      li.append(texto, acciones);
      return li;
    }));
  }
  function pintarSubtitulo(d) {
    const sub = $("cad-subtitulo");
    if (!sub || C.version) return;
    sub.textContent = [d.proyecto, `Versión ${d.revision}`, d.modificado_por].filter(Boolean).join(" · ");
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
    if (e.sucio || e.recuperado) { avisar("Guarda o descarta tus cambios antes de restaurar una versión.", "error"); return; }
    b.setAttribute("aria-busy", "true");
    try {
      const r = await pedir(C.urls.restaurar_version.replace("{n}", b.dataset.cadRestaurar), { metodo: "POST", json: { sesion: SESION } });
      const d = await datos(r);
      if (!r.ok) throw new Error(d.error || "No se pudo restaurar la versión.");
      e.revision = e.base = d.revision;
      pintarVersiones(d.versiones);
      pintarSubtitulo(d);
      $("cad-versiones").close();
      avisar(`Versión ${b.dataset.cadRestaurar} restaurada como versión ${d.revision}`);
      if (e.editable) await abrir("vigente", false);
    } catch (err) { avisar(err.message, "error"); }
    finally { b.removeAttribute("aria-busy"); }
  });

  // -- Tareas del servidor: imprimir y convertir ----------------------------------------------------------------------------
  async function esperarTarea(inicial, decir) {
    let t = inicial;
    while (!t.terminada) {
      await espera(1200);
      const r = await pedir(`${C.urls.tarea.replace(MARCA, t.tarea || t.id)}?formato=json`);
      t = { ...(await datos(r)), tarea: t.tarea || t.id };
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
      const ir = $("cad-imprimir-ir"), estadoTexto = $("cad-imprimir-estado");
      const decir = (t) => { estadoTexto.hidden = !t; estadoTexto.textContent = t || ""; };
      const fd = new FormData(formImprimir);
      if (!fd.get("grosores")) fd.set("grosores", "0");
      try { localStorage.setItem(LLAVE, JSON.stringify(Object.fromEntries([...fd.entries()].filter(([k]) => k !== "version")))); } catch (_) { /* nada */ }
      ir.setAttribute("aria-busy", "true"); ir.disabled = true; decir("Preparando el PDF…");
      try {
        const r = await pedir(C.urls.imprimir, { metodo: "POST", cuerpo: fd });
        const d = await datos(r);
        if (!r.ok && r.status !== 202) throw new Error(d.error || "No se pudo imprimir.");
        const res = d.terminada ? d.resultado : await esperarTarea(d, decir);
        if (!res || !res.url) throw new Error(d.error || "No se pudo imprimir.");
        bajar(res.url);
        decir(""); $("cad-imprimir").close(); avisar("PDF listo");
      } catch (err) { decir(""); avisar(err.message, "error"); }
      finally { ir.removeAttribute("aria-busy"); ir.disabled = false; }
    });
  }

  async function convertir(permitir) {
    const boton = $("cad-convertir");
    const formato = boton.dataset.formato;
    const fd = new FormData();
    fd.set("formato", formato);
    if (C.version) fd.set("version", C.version);
    if (permitir) fd.set("permitir_perdida", "1");
    avisar(`Preparando el ${formato.toUpperCase()}…`);
    try {
      const r = await pedir(C.urls.convertir, { metodo: "POST", cuerpo: fd });
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
      const r = await pedir(C.urls.drive, { metodo: "POST" });
      const d = await datos(r);
      avisar(d.mensaje || d.error || "Listo", r.ok ? undefined : "error");
    } catch (_) { avisar("No se pudo enviar. Revisa tu conexión.", "error"); }
  });

  // -- Menú «Más acciones» ------------------------------------------------------------------------------------------------------
  const menu = $("cad-menu");
  document.addEventListener("click", (ev) => { if (menu.open && !menu.contains(ev.target)) menu.open = false; });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
  menu.addEventListener("click", (ev) => { if (ev.target.closest("[data-tv-abrir]")) menu.open = false; });

  // Sugerencias de proyecto o cliente al renombrar.
  const proyectos = JSON.parse(($("cad-proyectos") || {}).textContent || "[]");
  if (window.ITT && ITT.combobox) document.querySelectorAll("[data-cad-proyectos]").forEach((i) => ITT.combobox(i, () => proyectos));

  // -- Arranque -------------------------------------------------------------------------------------------------------------------
  refrescarEstado();
  if (frame) cargarMarco().then(alListo).catch((err) => { carga(""); error(err.message); });
  else carga("");
})();

/* Capa de datos de las cotizaciones en el teléfono (arquitectura §18.3, herramientas-web.md §4.1).
 *
 * Cada cotización vive primero en IndexedDB (almacén `borradores` de la cola): se puede crear, editar, duplicar y borrar sin
 * conexión. Un borrador es «sucio» mientras el servidor no tenga su última versión; ese contador entra en «N pendientes» y
 * en el aviso antes de cerrar. La sincronización es idempotente (llave `id_cliente`) y NUNCA pisa: si el servidor cambió
 * desde la revisión en que se basó el teléfono, la versión del teléfono se guarda como copia.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const cola = () => ITT.cola;
  let urls = {};
  const aviso = (detalle) => document.dispatchEvent(new CustomEvent("itt:cot", { detail: detalle }));

  const marcaId = "00000000-0000-0000-0000-000000000000";
  const url = (nombre, id) => (urls[nombre] || "").replace(marcaId, id || "").replace("SHA", id || "");
  const dos = (n) => String(n).padStart(2, "0");
  const hoy = () => { const d = new Date(); return `${dos(d.getDate())}/${dos(d.getMonth() + 1)}/${d.getFullYear()}`; };
  const copiar = (x) => JSON.parse(JSON.stringify(x));
  const iguales = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  async function csrf() { return cola().ajuste("csrf", ""); }

  /** Petición JSON a la API. Lanza {clase:"red"|"sesion"|"rechazo"|"servidor", mensaje}. */
  async function pedir(ruta, opciones) {
    const o = Object.assign({ metodo: "GET" }, opciones);
    const cabeceras = { Accept: "application/json" };
    if (o.metodo !== "GET") cabeceras["X-CSRFToken"] = await csrf();
    let cuerpo;
    if (o.json !== undefined) { cabeceras["Content-Type"] = "application/json"; cuerpo = JSON.stringify(o.json); }
    else if (o.formulario) cuerpo = o.formulario;
    let r;
    try { r = await fetch(ruta, { method: o.metodo, headers: cabeceras, body: cuerpo, credentials: "same-origin", redirect: "manual", cache: "no-store" }); }
    catch (e) { throw { clase: "red", mensaje: "Sin conexión" }; }
    if (r.type === "opaqueredirect" || r.status === 401) throw { clase: "sesion", mensaje: "Vuelve a iniciar sesión" };
    if (r.status === 403) throw { clase: "sesion", mensaje: "Sin permiso o sesión caducada. Vuelve a iniciar sesión." };
    if (o.crudo) { if (!r.ok) throw await error(r); return r; }
    if (!r.ok) throw await error(r);
    return r.json();
  }
  async function error(r) {
    let mensaje = `Error ${r.status}`;
    try { const d = await r.json(); if (d && d.error) mensaje = d.error; } catch (_) { /* no era JSON */ }
    return { clase: r.status >= 500 || r.status === 404 ? "servidor" : "rechazo", mensaje, estado: r.status };
  }

  // -- Contexto (machotes, valores del perfil, siguiente folio): se guarda para trabajar sin conexión -----------------------
  async function contexto() {
    const cuenta = await cola().ajuste("cuenta", null);
    try {
      const c = await pedir(urls.contexto);
      await cola().guardarAjuste("cot_contexto", c);
      return c;
    } catch (e) {
      const guardado = await cola().ajuste("cot_contexto", null);
      if (guardado && (cuenta == null || guardado.cuenta === cuenta)) return Object.assign({ sin_conexion: true }, guardado);
      throw e;
    }
  }

  function folioSiguiente(ctx, locales) {
    const ano = new Date().getFullYear();
    const patron = new RegExp(`^COT-${ano}-(\\d{1,6})$`);
    let mayor = 0;
    const m0 = patron.exec(ctx.folio || "");
    if (m0) mayor = Number(m0[1]) - 1;
    for (const b of locales) { const m = patron.exec((b.datos && b.datos.quote_number) || ""); if (m) mayor = Math.max(mayor, Number(m[1])); }
    return `COT-${ano}-${String(mayor + 1).padStart(4, "0")}`;
  }

  // -- Registros --------------------------------------------------------------------------------------------------------------
  async function listar() {
    const cuenta = await cola().ajuste("cuenta", null);
    const todos = await cola().borradores();
    return todos.filter((b) => !b.borrada && (b.cuenta == null || cuenta == null || b.cuenta === cuenta))
      .sort((a, b) => b.actualizada - a.actualizada);
  }

  async function nuevo(ctx, base) {
    const cuenta = await cola().ajuste("cuenta", null);
    const locales = await listar();
    const datos = Object.assign(copiar(ctx.base), base || {}, { items: [], images: [], spelling_checked: false });
    if (!base || !base.quote_number) datos.quote_number = folioSiguiente(ctx, locales);
    if (!base || !base.quote_date) datos.quote_date = hoy();
    const ahora = Date.now();
    const registro = { id: cola().uuid(), cuenta, servidor_id: null, revision_base: null, datos, sucio: true, borrada: false, creada: ahora, actualizada: ahora, miniaturas: {}, aviso: "" };
    await cola().guardarBorrador(registro);
    return registro;
  }

  /** Guarda cambios del editor. Lee-modifica-escribe en una transacción: dos teclas seguidas no se pisan. */
  async function guardar(id, datos, extra) {
    return cola().actualizarBorrador(id, (b) => {
      b.datos = copiar(datos);
      b.sucio = true;
      b.actualizada = Date.now();
      return Object.assign(b, extra || {});
    });
  }

  async function duplicar(ctx, origen) {
    const cuenta = await cola().ajuste("cuenta", null);
    const locales = await listar();
    const datos = copiar(origen.datos);
    datos.quote_number = folioSiguiente(ctx, locales);
    datos.quote_date = hoy();
    datos.spelling_checked = false;
    const ahora = Date.now();
    const registro = { id: cola().uuid(), cuenta, servidor_id: null, revision_base: null, datos, sucio: true, borrada: false, creada: ahora, actualizada: ahora, miniaturas: copiar(origen.miniaturas || {}), aviso: "" };
    await cola().guardarBorrador(registro);
    return registro;
  }

  /** Borra: si el servidor ya la tiene, queda «por borrar» hasta confirmar; si no, se quita al instante. */
  async function borrar(id) {
    await cola().actualizarBorrador(id, (b) => (b.servidor_id ? Object.assign(b, { borrada: true, sucio: true, actualizada: Date.now() }) : null));
    ITT.refrescarCola && ITT.refrescarCola();
  }

  // -- Fotos de referencia -----------------------------------------------------------------------------------------------------
  async function miniatura(blob) {
    const mapa = await createImageBitmap(blob);
    const escala = Math.min(1, 160 / Math.max(mapa.width, mapa.height));
    const lienzo = document.createElement("canvas");
    lienzo.width = Math.max(1, Math.round(mapa.width * escala));
    lienzo.height = Math.max(1, Math.round(mapa.height * escala));
    lienzo.getContext("2d").drawImage(mapa, 0, 0, lienzo.width, lienzo.height);
    return lienzo.toDataURL("image/jpeg", 0.7);
  }

  /** Reduce la foto, la pone en la cola de subida (con la misma garantía que Capturar) y devuelve {sha256, miniatura}. */
  async function agregarFoto(id, archivo) {
    const r = await ITT.reducirImagen(archivo);
    const cuenta = await cola().ajuste("cuenta", null);
    const ahoraLocal = () => { const d = new Date(); return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}T${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`; };
    const ya = (await cola().listar()).find((i) => i.sha256 === r.sha256);
    if (!ya) {
      await cola().agregar({
        destino: "herramientas.captura", blob: r.blob, nombre: (archivo.name || "foto").replace(/\.[^.]+$/, "") + ".jpg", tipo: "image/jpeg",
        sha256: r.sha256, ancho: r.ancho, alto: r.alto, cuenta, meta: { tomada: r.exif.tomada || ahoraLocal(), referencia: `cotizacion:${id}`.slice(0, 100), origen: "galeria", sin_ubicacion: true, orden: 0 },
      });
    }
    return { sha256: r.sha256, miniatura: await miniatura(r.blob) };
  }

  // -- Sincronización ---------------------------------------------------------------------------------------------------------
  let sincronizando = false;

  function aRegistro(fila, datos, cuenta, previo) {
    return Object.assign({ miniaturas: {}, aviso: "", creada: Date.parse(fila.actualizada) || Date.now() }, previo || {}, {
      id: previo ? previo.id : fila.id, cuenta, servidor_id: fila.id, revision_base: fila.revision, datos, sucio: false, borrada: false,
      actualizada: previo ? previo.actualizada : Date.parse(fila.actualizada) || Date.now(),
    });
  }

  /** Trae del servidor lo que el teléfono no tiene o tiene desactualizado (y no tiene cambios propios sin subir). */
  async function descargar(cuenta) {
    const { cotizaciones } = await pedir(urls.lista);
    const locales = await cola().borradores();
    const porServidor = new Map(locales.filter((b) => b.servidor_id).map((b) => [b.servidor_id, b]));
    const vigentes = new Set(cotizaciones.map((c) => c.id));
    for (const fila of cotizaciones) {
      const local = porServidor.get(fila.id);
      if (local && (local.sucio || local.revision_base >= fila.revision)) continue;
      const detalle = await pedir(url("detalle", fila.id));
      await cola().guardarBorrador(aRegistro(fila, detalle.datos, cuenta, local));
    }
    // Lo que se borró en otro dispositivo se quita aquí, salvo que tenga cambios sin subir (esos se vuelven a crear al sincronizar).
    for (const b of locales) {
      if (b.servidor_id && !vigentes.has(b.servidor_id) && !b.sucio) await cola().quitarBorrador(b.id);
      else if (b.servidor_id && !vigentes.has(b.servidor_id) && b.sucio && !b.borrada) await cola().actualizarBorrador(b.id, (x) => Object.assign(x, { servidor_id: null, revision_base: null }));
    }
  }

  async function subirUno(b, cuenta) {
    if (b.borrada) {
      try { await pedir(url("detalle", b.servidor_id), { metodo: "DELETE" }); }
      catch (e) { if (e.estado !== 404) throw e; }
      await cola().actualizarBorrador(b.id, (x) => (x.borrada ? null : x));
      return;
    }
    const enviado = b.actualizada;
    const datosEnviados = copiar(b.datos);
    const r = await pedir(urls.sincronizar, { metodo: "POST", json: { id_cliente: b.id, servidor_id: b.servidor_id, revision_base: b.revision_base, datos: datosEnviados } });
    if (r.resultado === "copia") {
      // El servidor cambió desde la revisión del teléfono: la versión del teléfono quedó como copia (nada se perdió ni se pisó).
      const original = await pedir(url("detalle", r.copia_de));
      const servidorCopia = await pedir(url("detalle", r.servidor_id));
      const actual = await cola().borrador(b.id);
      const cambioDespues = actual && actual.actualizada !== enviado;
      const copia = { id: r.servidor_id, cuenta, servidor_id: r.servidor_id, revision_base: r.revision, datos: cambioDespues ? actual.datos : servidorCopia.datos,
        sucio: !!cambioDespues, borrada: false, creada: Date.now(), actualizada: Date.now(), miniaturas: actual ? actual.miniaturas : {},
        aviso: "Se guardó como copia porque esta cotización cambió en otro dispositivo mientras la editabas aquí." };
      await cola().guardarBorrador(copia);
      await cola().guardarBorrador(aRegistro(original, original.datos, cuenta, actual ? { id: actual.id, miniaturas: actual.miniaturas } : { id: b.id }));
      aviso({ tipo: "copia", id: b.id, copia: copia.id });
      return;
    }
    await cola().actualizarBorrador(b.id, (x) => {
      x.servidor_id = r.servidor_id; x.revision_base = r.revision;
      if (x.actualizada === enviado) x.sucio = false;       // si se editó mientras se subía, sigue pendiente
      return x;
    });
  }

  /** Ciclo de sincronización: sube los borradores sucios y baja lo nuevo. Lo llama ui.js con cada disparador de subida. */
  async function sincronizar() {
    if (sincronizando || !navigator.onLine || !urls.sincronizar) return;
    if (await cola().ajuste("sesion_caducada", false)) return;
    sincronizando = true;
    try {
      const cuenta = await cola().ajuste("cuenta", null);
      for (const b of await cola().borradores()) {
        if (!b.sucio || (b.cuenta != null && cuenta != null && b.cuenta !== cuenta)) continue;
        try { await subirUno(b, cuenta); }
        catch (e) {
          if (e.clase === "red" || e.clase === "sesion") break;
          if (e.clase === "rechazo") await cola().actualizarBorrador(b.id, (x) => Object.assign(x, { error: e.mensaje }));   // sigue pendiente; se muestra en la lista
        }
      }
      try { await descargar(cuenta); } catch (e) { /* sin red o sesión: se conserva lo local */ }
    } finally { sincronizando = false; }
    ITT.refrescarCola && ITT.refrescarCola();
    aviso({ tipo: "sincronizado" });
  }

  function iniciar(direcciones) {
    urls = direcciones;
    (ITT.sincronizadores = ITT.sincronizadores || []).push(sincronizar);
  }

  ITT.cot = { iniciar, pedir, url, contexto, listar, nuevo, guardar, duplicar, borrar, agregarFoto, sincronizar, folioSiguiente, copiar, iguales, hoy, get urls() { return urls; } };
})(typeof self !== "undefined" ? self : this);

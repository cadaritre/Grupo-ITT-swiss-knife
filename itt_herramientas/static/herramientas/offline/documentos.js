/* Documentos del teléfono (cotizaciones, reportes…): borradores en IndexedDB que se sincronizan con el servidor
 * (arquitectura §18.3). Un documento vive primero en el teléfono y se puede crear, editar y borrar sin conexión.
 *
 * Es «sucio» mientras el servidor no tenga su última versión; ese contador entra en «N pendientes» y en el aviso antes de
 * cerrar. La sincronización es idempotente (llave `id_cliente`) y NUNCA pisa: si el servidor cambió desde la revisión en que
 * se basó el teléfono, la versión del teléfono se guarda como copia.
 *
 *   const docs = ITT.crearDocumentos({ tipo: "reporte", urls: {sincronizar, lista, detalle, …}, aviso: "…" });
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const cola = () => ITT.cola;
  const MARCA_ID = "00000000-0000-0000-0000-000000000000";
  const copiar = (x) => JSON.parse(JSON.stringify(x));

  async function error(r) {
    let mensaje = r.status >= 500 ? "Algo falló de nuestro lado. Inténtalo de nuevo en un momento." : r.status === 404 ? "Ya no existe. Recarga la página." : "No se pudo completar. Revisa los datos e inténtalo de nuevo.";
    let extra = {};
    try { const d = await r.json(); if (d && d.error) mensaje = d.error; extra = d || {}; } catch (_) { /* no era JSON */ }
    return Object.assign({}, extra, { clase: r.status >= 500 || r.status === 404 ? "servidor" : "rechazo", mensaje, estado: r.status });
  }

  /** Petición a la API. Lanza {clase:"red"|"sesion"|"rechazo"|"servidor", mensaje, estado}. */
  async function pedir(ruta, opciones) {
    const o = Object.assign({ metodo: "GET" }, opciones);
    const cabeceras = { Accept: "application/json" };
    if (o.metodo !== "GET") {
      let csrf = await cola().ajuste("csrf", "");
      if (!csrf && navigator.onLine) {                          // primera petición antes de que ui.js termine de pedir la sesión: se pide aquí
        try {
          const s = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
          if (s.ok) { const d = await s.json(); csrf = d.csrf; await cola().guardarAjuste("csrf", d.csrf); await cola().guardarAjuste("cuenta", d.cuenta); }
        } catch (_) { /* sin red: la petición fallará con su propio mensaje */ }
      }
      cabeceras["X-CSRFToken"] = csrf;
    }
    let cuerpo;
    if (o.json !== undefined) { cabeceras["Content-Type"] = "application/json"; cuerpo = JSON.stringify(o.json); }
    else if (o.formulario) cuerpo = o.formulario;
    let r;
    try { r = await fetch(ruta, { method: o.metodo, headers: cabeceras, body: cuerpo, credentials: "same-origin", redirect: "manual", cache: "no-store" }); }
    catch (e) { throw { clase: "red", mensaje: "Sin conexión" }; }
    if (r.type === "opaqueredirect" || r.status === 401) throw { clase: "sesion", mensaje: "Vuelve a iniciar sesión" };
    if (r.status === 403) throw { clase: "sesion", mensaje: "Sin permiso o sesión caducada. Vuelve a iniciar sesión." };
    if (!r.ok) throw await error(r);
    return o.crudo ? r : r.json();
  }

  function crearDocumentos(config) {
    const tipo = config.tipo;
    let urls = config.urls || {};
    const esMio = (b) => (b.tipo || "cotizacion") === tipo;
    const url = (nombre, id) => (urls[nombre] || "").replace(MARCA_ID, id || "").replace("SHA", id || "");
    const avisar = (detalle) => document.dispatchEvent(new CustomEvent("itt:doc", { detail: Object.assign({ tipo }, detalle) }));
    let sincronizando = false;
    let repetir = false;

    async function listar() {
      const cuenta = await cola().ajuste("cuenta", null);
      const todos = await cola().borradores();
      return todos.filter((b) => esMio(b) && !b.borrada && (b.cuenta == null || cuenta == null || b.cuenta === cuenta)).sort((a, b) => b.actualizada - a.actualizada);
    }

    /** Registro nuevo en el teléfono. `datos` es el documento; se guarda sucio y sin servidor. */
    async function crear(datos, extra) {
      const cuenta = await cola().ajuste("cuenta", null);
      const ahora = Date.now();
      const registro = Object.assign({ id: cola().uuid(), tipo, cuenta, servidor_id: null, revision_base: null, datos, sucio: true, borrada: false, creada: ahora, actualizada: ahora, miniaturas: {}, aviso: "" }, extra || {});
      await cola().guardarBorrador(registro);
      return registro;
    }

    /** Guarda cambios del editor. Lee-modifica-escribe en una transacción: dos guardados seguidos no se pisan. */
    async function guardar(id, datos, extra) {
      return cola().actualizarBorrador(id, (b) => {
        b.datos = copiar(datos);
        b.sucio = true;
        b.actualizada = Date.now();
        return Object.assign(b, extra || {});
      });
    }

    /** Borra: si el servidor ya lo tiene, queda «por borrar» hasta confirmar; si no, se quita al instante. */
    async function borrar(id) {
      await cola().actualizarBorrador(id, (b) => (b.servidor_id ? Object.assign(b, { borrada: true, sucio: true, actualizada: Date.now() }) : null));
      ITT.refrescarCola && ITT.refrescarCola();
    }

    function aRegistro(fila, datos, cuenta, previo) {
      return Object.assign({ tipo, miniaturas: {}, aviso: "", creada: Date.parse(fila.actualizada) || Date.now() }, previo || {}, {
        id: previo ? previo.id : fila.id, cuenta, servidor_id: fila.id, revision_base: fila.revision, datos, sucio: false, borrada: false,
        autor: fila.autor || (previo && previo.autor) || null,
        actualizada: previo ? previo.actualizada : Date.parse(fila.actualizada) || Date.now(),
      });
    }

    /** Trae del servidor lo que el teléfono no tiene o tiene desactualizado (y no tiene cambios propios sin subir). */
    async function descargar(cuenta) {
      const { [config.lista || "cotizaciones"]: filas } = await pedir(urls.lista);
      const locales = (await cola().borradores()).filter(esMio);
      const porServidor = new Map(locales.filter((b) => b.servidor_id).map((b) => [b.servidor_id, b]));
      const vigentes = new Set(filas.map((c) => c.id));
      for (const fila of filas) {
        const local = porServidor.get(fila.id);
        if (local && (local.sucio || local.revision_base >= fila.revision)) {
          // Ya está al día (o con cambios propios): solo se completa quién lo hizo, que el servidor fija al crearlo.
          if (fila.autor && JSON.stringify(local.autor || null) !== JSON.stringify(fila.autor)) await cola().actualizarBorrador(local.id, (x) => Object.assign(x, { autor: fila.autor }));
          continue;
        }
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
        const copia = { id: r.servidor_id, tipo, cuenta, servidor_id: r.servidor_id, revision_base: r.revision, datos: cambioDespues ? actual.datos : servidorCopia.datos, autor: servidorCopia.autor || r.autor || null,
          sucio: !!cambioDespues, borrada: false, creada: Date.now(), actualizada: Date.now(), miniaturas: actual ? actual.miniaturas : {},
          aviso: config.avisoCopia || "Se guardó como copia porque este documento cambió en otro dispositivo mientras lo editabas aquí." };
        await cola().guardarBorrador(copia);
        await cola().guardarBorrador(aRegistro(original, original.datos, cuenta, actual ? { id: actual.id, miniaturas: actual.miniaturas } : { id: b.id }));
        avisar({ tipo_evento: "copia", id: b.id, copia: copia.id });
        return;
      }
      await cola().actualizarBorrador(b.id, (x) => {
        x.servidor_id = r.servidor_id; x.revision_base = r.revision; x.error = "";
        if (r.autor) x.autor = r.autor;
        if (x.actualizada === enviado) x.sucio = false;       // si se editó mientras se subía, sigue pendiente
        return x;
      });
    }

    /** Ciclo de sincronización: sube los borradores sucios y baja lo nuevo. Lo llama ui.js con cada disparador de subida. */
    async function sincronizar() {
      if (sincronizando) { repetir = true; return; }                // hubo cambios durante el ciclo: se repite al terminar
      if (!navigator.onLine || !urls.sincronizar) return;
      if (await cola().ajuste("sesion_caducada", false)) return;
      sincronizando = true;
      repetir = false;
      try {
        const cuenta = await cola().ajuste("cuenta", null);
        for (const b of (await cola().borradores()).filter(esMio)) {
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
      avisar({ tipo_evento: "sincronizado" });
      if (repetir) { repetir = false; return sincronizar(); }
    }

    function iniciar(direcciones) {
      urls = direcciones;
      (ITT.sincronizadores = ITT.sincronizadores || []).push(sincronizar);
    }

    /** Id local del documento que en el servidor es `servidorId` (enlaces de la búsqueda). Si aún no está en el teléfono, sincroniza una vez. */
    async function localDe(servidorId) {
      const buscar = async () => ((await listar()).find((b) => b.servidor_id === servidorId) || {}).id || null;
      let id = await buscar();
      if (!id && navigator.onLine) { try { await sincronizar(); } catch (_) { /* sin red: se queda en la lista */ } id = await buscar(); }
      return id;
    }

    return { tipo, iniciar, pedir, url, listar, crear, guardar, borrar, sincronizar, copiar, localDe, get urls() { return urls; } };
  }

  ITT.crearDocumentos = crearDocumentos;
  ITT.pedirApi = pedir;
})(typeof self !== "undefined" ? self : this);

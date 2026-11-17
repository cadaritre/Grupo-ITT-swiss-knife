/* Documentos del teléfono (cotizaciones, reportes…): borradores en IndexedDB que se sincronizan con el servidor
 * (arquitectura §18.3). Un documento vive primero en el teléfono y se puede crear, editar y borrar sin conexión.
 *
 * Es «sucio» mientras el servidor no tenga su última versión; ese contador entra en «N pendientes» y en el aviso antes de
 * cerrar. La sincronización es idempotente (llave `id_cliente`) y NUNCA pisa: si el servidor cambió desde la revisión en que
 * se basó el teléfono, se fusiona si no choca («fusionada») y si choca queda como copia en conflicto. Los documentos son del equipo:
 * si esta página editaba con el bloqueo y otra persona tomó el control, lo que no se había subido queda en el servidor como borrador
 * de recuperación («recuperacion») y aquí se vuelve a la versión del servidor.
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

  /** Token CSRF que corresponde a la cookie de ahora (lo da `/sesion/`) y se guarda para la cola. "" sin red o sin sesión. */
  async function tokenVigente() {
    try {
      const s = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
      if (!s.ok) return "";
      const d = await s.json();
      await cola().guardarAjuste("csrf", d.csrf);
      await cola().guardarAjuste("cuenta", d.cuenta);
      return d.csrf || "";
    } catch (_) { return ""; }                                    // sin red: la petición fallará con su propio mensaje
  }

  /** Petición a la API. Lanza {clase:"red"|"sesion"|"rechazo"|"servidor", mensaje, estado}.
   *
   * El token CSRF guardado puede no ser el de la cookie: al abrir la página sin cookie, ui.js y la herramienta piden `/sesion/` a
   * la vez y cada respuesta trae un secreto distinto (gana la última cookie, pero se pudo guardar el token de la otra); también
   * cambia al volver a iniciar sesión. Si el servidor rechaza el token (403 de CSRF), se pide el vigente y se repite UNA vez:
   * el rechazo ocurre antes de la vista, así que repetir no duplica nada.
   */
  async function pedir(ruta, opciones) {
    const o = Object.assign({ metodo: "GET" }, opciones);
    const cabeceras = { Accept: "application/json" };
    let cuerpo;
    if (o.json !== undefined) { cabeceras["Content-Type"] = "application/json"; cuerpo = JSON.stringify(o.json); }
    else if (o.formulario) cuerpo = o.formulario;
    const enviar = async (csrf) => {
      if (o.metodo !== "GET") cabeceras["X-CSRFToken"] = csrf;
      try { return await fetch(ruta, { method: o.metodo, headers: cabeceras, body: cuerpo, credentials: "same-origin", redirect: "manual", cache: "no-store" }); }
      catch (e) { throw { clase: "red", mensaje: "Sin conexión" }; }
    };
    let csrf = "";
    if (o.metodo !== "GET") {
      csrf = await cola().ajuste("csrf", "");
      if (!csrf && navigator.onLine) csrf = await tokenVigente();          // primera petición antes de que ui.js termine de pedir la sesión
    }
    let r = await enviar(csrf);
    if (r.status === 403 && o.metodo !== "GET" && /CSRF/i.test(await r.clone().text().catch(() => ""))) {
      const vigente = await tokenVigente();
      if (vigente) r = await enviar(vigente);
    }
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
    let esperando = [];                                          // quienes esperan a que termine el ciclo en curso (p. ej. `localDe`)

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

    /** Lo que fija el servidor de cada documento: autor, «modificada por», bloqueo, copia en conflicto, permisos y PDF. */
    function delServidor(fila, previo) {
      return { autor: fila.autor || (previo && previo.autor) || null, pdf_revision: fila.pdf_revision == null ? null : fila.pdf_revision,
        modificado: fila.modificado || null, bloqueo: fila.bloqueo || null, conflicto: fila.conflicto || "", puede_borrar: fila.puede_borrar !== false };
    }

    function aRegistro(fila, datos, cuenta, previo) {
      return Object.assign({ tipo, miniaturas: {}, aviso: "", creada: Date.parse(fila.actualizada) || Date.now() }, previo || {}, {
        id: previo ? previo.id : fila.id, cuenta, servidor_id: fila.id, revision_base: fila.revision, datos, sucio: false, borrada: false, error: "", eliminado_en_servidor: false,
        actualizada: previo ? previo.actualizada : Date.parse(fila.actualizada) || Date.now(),
      }, delServidor(fila, previo));
    }

    /** Vuelve a traer un documento del servidor (si aquí no tiene cambios sin subir). Devuelve el registro, actualizado o no. */
    async function refrescar(id) {
      const local = await cola().borrador(id);
      if (!local || !local.servidor_id || local.sucio) return local;
      const detalle = await pedir(url("detalle", local.servidor_id));
      const cuenta = await cola().ajuste("cuenta", null);
      const nuevo = aRegistro(detalle, detalle.datos, cuenta, { id: local.id, miniaturas: local.miniaturas, aviso: local.aviso, creada: local.creada });
      nuevo.recuperaciones = detalle.recuperaciones || [];
      await cola().guardarBorrador(nuevo);
      return nuevo;
    }

    /** Guarda en el teléfono lo que respondió el servidor tras restaurar o recuperar (la fila con sus datos). */
    async function guardarDelServidor(id, fila) {
      const local = await cola().borrador(id);
      const cuenta = await cola().ajuste("cuenta", null);
      await cola().guardarBorrador(aRegistro(fila, fila.datos, cuenta, local ? { id: local.id, miniaturas: local.miniaturas, creada: local.creada } : { id }));
    }

    /** Trae del servidor lo que el teléfono no tiene o tiene desactualizado (y no tiene cambios propios sin subir). */
    /** Todas las páginas de la lista del servidor (los documentos son del equipo: pueden ser muchos). */
    async function listaCompleta() {
      const filas = [];
      let pagina = 1;
      for (let vueltas = 0; pagina && vueltas < 500; vueltas++) {
        const r = await pedir(pagina === 1 ? urls.lista : `${urls.lista}${urls.lista.includes("?") ? "&" : "?"}pagina=${pagina}`);
        filas.push(...(r[config.lista || "cotizaciones"] || []));
        pagina = r.siguiente || null;
      }
      return filas;
    }

    async function descargar(cuenta) {
      const filas = await listaCompleta();
      const locales = (await cola().borradores()).filter(esMio);
      const porServidor = new Map(locales.filter((b) => b.servidor_id).map((b) => [b.servidor_id, b]));
      const vigentes = new Set(filas.map((c) => c.id));
      for (const fila of filas) {
        const local = porServidor.get(fila.id);
        if (local && (local.sucio || local.revision_base >= fila.revision)) {
          // Ya está al día (o con cambios propios): solo se completa lo que fija el servidor (autor, quién lo modificó, bloqueo, PDF).
          const extra = delServidor(fila, local);
          const antes = { autor: local.autor || null, pdf_revision: local.pdf_revision == null ? null : local.pdf_revision, modificado: local.modificado || null,
            bloqueo: local.bloqueo || null, conflicto: local.conflicto || "", puede_borrar: local.puede_borrar !== false };
          if (JSON.stringify(antes) !== JSON.stringify(extra)) await cola().actualizarBorrador(local.id, (x) => Object.assign(x, extra));
          continue;
        }
        const detalle = await pedir(url("detalle", fila.id));
        await cola().guardarBorrador(aRegistro(fila, detalle.datos, cuenta, local));
      }
      // Lo que no vino en la lista NO se da por borrado: se pregunta al servidor. Si está en la papelera (410) o ya no existe (404) y aquí
      // no tiene cambios, se quita del teléfono; con cambios se queda tal cual, con su `servidor_id`: al subir, el servidor guarda lo de
      // aquí como copia en conflicto. Nunca se olvida el `servidor_id` (eso creaba un documento nuevo, con otro folio y sin marca).
      for (const b of locales) {
        if (!b.servidor_id || vigentes.has(b.servidor_id) || b.borrada) continue;
        try {
          const detalle = await pedir(url("detalle", b.servidor_id));
          if (!b.sucio && detalle.revision !== b.revision_base) await cola().guardarBorrador(aRegistro(detalle, detalle.datos, cuenta, b));
        } catch (e) {
          if ((e.estado === 410 || e.estado === 404) && !b.sucio) await cola().quitarBorrador(b.id);
          else if (e.estado === 410 || e.estado === 404) await cola().actualizarBorrador(b.id, (x) => Object.assign(x, { eliminado_en_servidor: true }));
          else if (e.clase === "red" || e.clase === "sesion") throw e;
        }
      }
    }

    async function subirUno(b, cuenta) {
      if (b.borrada) {
        try { await pedir(url("detalle", b.servidor_id), { metodo: "DELETE" }); }
        catch (e) {
          if (e.estado === 409) {                                     // sin permiso o alguien lo edita: vuelve a la lista, con el motivo
            await cola().actualizarBorrador(b.id, (x) => Object.assign(x, { borrada: false, sucio: false, error: e.mensaje }));
            avisar({ tipo_evento: "no_borrado", id: b.id, mensaje: e.mensaje });
            return;
          }
          if (e.estado !== 404) throw e;
        }
        await cola().actualizarBorrador(b.id, (x) => (x.borrada ? null : x));
        return;
      }
      const enviado = b.actualizada;
      const datosEnviados = copiar(b.datos);
      const r = await pedir(urls.sincronizar, { metodo: "POST", json: { id_cliente: b.id, servidor_id: b.servidor_id, revision_base: b.revision_base, datos: datosEnviados, sesion: b.sesion || "" } });
      if (r.resultado === "recuperacion") {
        // Otra persona tomó el control: lo de aquí quedó en el servidor como borrador de recuperación; el teléfono vuelve a la versión vigente.
        // Si aquí se siguió escribiendo mientras viajaba la petición, eso también se sube (va al mismo borrador de recuperación de esta
        // página): el teléfono no vuelve a la versión vigente hasta que lo último escrito llegó al servidor.
        const actual = await cola().borrador(b.id);
        if (actual && actual.actualizada !== enviado) { repetir = true; avisar({ tipo_evento: "recuperacion", id: b.id, quien: r.quien }); return; }
        const detalle = await pedir(url("detalle", r.servidor_id));
        const nuevo = aRegistro(detalle, detalle.datos, cuenta, actual ? { id: actual.id, miniaturas: actual.miniaturas, creada: actual.creada } : { id: b.id });
        nuevo.recuperaciones = detalle.recuperaciones || [];
        await cola().guardarBorrador(nuevo);
        avisar({ tipo_evento: "recuperacion", id: b.id, quien: r.quien });
        return;
      }
      if (r.resultado === "fusionada") {
        // El servidor cambió y no chocaba: guardó la fusión. Si aquí no se editó mientras tanto, se toma la fusión y su revisión. Si se
        // editó, lo de aquí NO incluye lo del equipo: se conserva la revisión base anterior para que el servidor vuelva a fusionar (si se
        // tomara la nueva, el siguiente guardado pisaría lo del equipo).
        await cola().actualizarBorrador(b.id, (x) => {
          x.servidor_id = r.servidor_id; x.error = ""; x.sesion = "";
          if (r.modificado) x.modificado = r.modificado;
          if (x.actualizada === enviado) { x.datos = r.datos; x.sucio = false; x.revision_base = r.revision; }
          return x;
        });
        if ((await cola().borrador(b.id) || {}).sucio) repetir = true;
        avisar({ tipo_evento: "fusionada", id: b.id });
        return;
      }
      if (r.resultado === "copia") {
        // El servidor cambió desde la revisión del teléfono: la versión del teléfono quedó como copia (nada se perdió ni se pisó).
        const original = await pedir(url("detalle", r.copia_de));
        const servidorCopia = await pedir(url("detalle", r.servidor_id));
        const actual = await cola().borrador(b.id);
        const cambioDespues = actual && actual.actualizada !== enviado;
        const copia = Object.assign({ id: r.servidor_id, tipo, cuenta, servidor_id: r.servidor_id, revision_base: r.revision, datos: cambioDespues ? actual.datos : servidorCopia.datos,
          sucio: !!cambioDespues, borrada: false, creada: Date.now(), actualizada: Date.now(), miniaturas: actual ? actual.miniaturas : {},
          aviso: `${r.conflicto || "Copia en conflicto"}. ${config.avisoCopia || "Este documento cambió mientras lo editabas sin conexión: tu versión quedó aparte, nada se perdió."}` },
        delServidor(servidorCopia));
        await cola().guardarBorrador(copia);
        await cola().guardarBorrador(aRegistro(original, original.datos, cuenta, actual ? { id: actual.id, miniaturas: actual.miniaturas } : { id: b.id }));
        avisar({ tipo_evento: "copia", id: b.id, copia: copia.id });
        return;
      }
      await cola().actualizarBorrador(b.id, (x) => {
        x.servidor_id = r.servidor_id; x.revision_base = r.revision; x.error = "";
        if (r.autor) x.autor = r.autor;
        if (r.modificado) x.modificado = r.modificado;
        if (r.folio && x.datos && tipo === "cotizacion" && x.datos.quote_number !== r.folio) x.datos.quote_number = r.folio;     // el folio lo asigna el servidor
        if (x.actualizada === enviado) x.sucio = false;       // si se editó mientras se subía, sigue pendiente
        return x;
      });
    }

    /** Ciclo de sincronización: sube los borradores sucios y baja lo nuevo. Lo llama ui.js con cada disparador de subida.
     * Si ya hay un ciclo en curso, se repite al terminar y la promesa se cumple cuando acaba (así `localDe` encuentra lo que se bajó). */
    async function sincronizar() {
      if (sincronizando) { repetir = true; return new Promise((listo) => esperando.push(listo)); }
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
      if (repetir) { repetir = false; await sincronizar(); }
      const listos = esperando;
      esperando = [];
      for (const listo of listos) listo();
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

    return { tipo, iniciar, pedir, url, listar, crear, guardar, borrar, sincronizar, copiar, localDe, refrescar, guardarDelServidor, get urls() { return urls; } };
  }

  ITT.crearDocumentos = crearDocumentos;
  ITT.pedirApi = pedir;
})(typeof self !== "undefined" ? self : this);

/* Documentos del teléfono (cotizaciones, reportes…): borradores en IndexedDB que se sincronizan con el servidor
 * (arquitectura §18.3). Un documento vive primero en el teléfono y se puede crear, editar y borrar sin conexión.
 *
 * Es «sucio» mientras el servidor no tenga su última versión; ese contador entra en «N pendientes» y en el aviso antes de
 * cerrar. La sincronización es idempotente (llave `id_cliente`) y NUNCA pisa: si el servidor cambió desde la revisión en que
 * se basó el teléfono, se fusiona si no choca («fusionada») y si choca queda como copia en conflicto. Los documentos son del equipo:
 * si esta página editaba con el bloqueo y otra persona tomó el control, lo que no se había subido queda en el servidor como borrador
 * de recuperación («recuperacion») y aquí se vuelve a la versión del servidor.
 *
 * Cada perfil corporativo (Grupo ITT, TresVizo) es un espacio de documentos aparte: cada registro dice de qué perfil es (`perfil`, fijo
 * desde que se creó), la lista muestra solo los del perfil activo de la cuenta (ajuste `perfil`, lo da el servidor) y cada perfil tiene su
 * propio cursor de sincronización. Lo pendiente sube siempre, sea del perfil que sea: el servidor sabe de qué perfil es cada documento.
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

  /** Perfil corporativo activo de la cuenta tal como lo dijo el servidor la última vez (null si aún no se sabe). */
  const perfilActivo = () => cola().ajuste("perfil", null);
  /** Perfil de un registro: el suyo o, en uno guardado antes de separar los perfiles, el de su resumen del servidor. */
  const perfilDe = (b) => b.perfil || (b.resumen && b.resumen.perfil) || null;
  /** ¿Es del perfil `perfil`? Un borrador que nunca ha subido y no sabe su perfil es del activo (subirá con él). */
  const delPerfil = (b, perfil) => perfil == null || (perfilDe(b) || perfil) === perfil;

  /** Recuerda el perfil activo que dijo el servidor. Si cambió (se cambió aquí o en otro dispositivo), lo avisa: la página vuelve a pintar
   * la lista con los documentos del otro perfil. */
  async function fijarPerfil(perfil) {
    if (!perfil) return false;
    const antes = await perfilActivo();
    if (antes === perfil) return false;
    await cola().guardarAjuste("perfil", perfil);
    if (antes != null) document.dispatchEvent(new CustomEvent("itt:perfil", { detail: { perfil, antes } }));
    return antes != null;
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

    /** Los documentos de la cuenta en el perfil activo (los del otro perfil se quedan guardados aparte, sin mezclarse). */
    async function listar() {
      const cuenta = await cola().ajuste("cuenta", null);
      const perfil = await perfilActivo();
      const todos = await cola().borradores();
      return todos.filter((b) => esMio(b) && !b.borrada && (b.cuenta == null || cuenta == null || b.cuenta === cuenta) && delPerfil(b, perfil))
        .sort((a, b) => b.actualizada - a.actualizada);
    }

    /** Registro nuevo en el teléfono. `datos` es el documento; se guarda sucio y sin servidor. */
    async function crear(datos, extra) {
      const cuenta = await cola().ajuste("cuenta", null);
      const perfil = await perfilActivo();
      const ahora = Date.now();
      const registro = Object.assign({ id: cola().uuid(), tipo, cuenta, perfil, servidor_id: null, revision_base: null, datos, sucio: true, borrada: false, creada: ahora, actualizada: ahora, miniaturas: {}, aviso: "" }, extra || {});
      await cola().guardarBorrador(registro);
      return registro;
    }

    /** Guarda cambios del editor. Lee-modifica-escribe en una transacción: dos guardados seguidos no se pisan. */
    async function guardar(id, datos, extra) {
      return cola().actualizarBorrador(id, (b) => {
        // Un documento del equipo que aquí solo se conoce por su resumen no tiene sus datos: guardarlo pisaría lo del servidor.
        if (b.parcial) throw new Error("Este documento aún no está en el teléfono: ábrelo con señal para editarlo.");
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
        id: previo ? previo.id : fila.id, cuenta, perfil: fila.perfil || (previo && previo.perfil) || null, servidor_id: fila.id, revision_base: fila.revision, datos, sucio: false, borrada: false, error: "", eliminado_en_servidor: false,
        parcial: false, resumen: Object.assign({}, fila, { datos: undefined, recuperaciones: undefined }),
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

    /** Registro de un documento del equipo que el teléfono solo conoce por su resumen (no se abrió aquí): sin datos para editar. */
    function parcial(fila, cuenta, previo) {
      return Object.assign({ tipo, miniaturas: {}, aviso: "", creada: Date.parse(fila.actualizada) || Date.now() }, previo || {}, {
        id: previo ? previo.id : fila.id, cuenta, perfil: fila.perfil || (previo && previo.perfil) || null, servidor_id: fila.id, revision_base: fila.revision, parcial: true, resumen: fila,
        datos: config.sintetico ? config.sintetico(fila) : {}, sucio: false, borrada: false, error: "", eliminado_en_servidor: false,
        actualizada: Date.parse(fila.actualizada) || Date.now(),
      }, delServidor(fila, previo));
    }

    /** Sincronización incremental (`?desde=<cursor>`): solo lo que cambió desde la última vez, con lo que se fue a la papelera.
     *
     * Política de lo que se guarda en el teléfono: los documentos que se crearon o se abrieron en él (con sus datos completos, para
     * trabajar sin conexión) se mantienen al día; de los demás solo se guarda el resumen que pinta la lista, y sus datos se piden al
     * abrirlos. Lo que se fue a la papelera (o se borró para siempre) se quita del teléfono si no tiene cambios; con cambios se queda, con
     * su `servidor_id`, y al subir el servidor lo guarda como copia en conflicto.
     *
     * Solo baja el perfil activo, con su propio cursor: los documentos del otro perfil se quedan como estaban y siguen al día cuando ese
     * perfil vuelve a estar activo. Si el servidor dice que el perfil activo cambió (otro dispositivo), empieza otra vez con el del nuevo. */
    async function descargar(cuenta) {
      let perfil = await perfilActivo();
      const llave = () => `cursor:${tipo}:${cuenta}:${perfil || "?"}`;
      let cursor = await cola().ajuste(llave(), 0);
      for (let vueltas = 0; vueltas < 1000; vueltas++) {
        const r = await pedir(`${urls.lista}${urls.lista.includes("?") ? "&" : "?"}desde=${encodeURIComponent(cursor || 0)}`);
        if (r.perfil && r.perfil !== perfil) {                         // el perfil activo cambió: se baja el espacio del nuevo con SU cursor
          await fijarPerfil(r.perfil);
          perfil = r.perfil;
          cursor = await cola().ajuste(llave(), 0);
          continue;
        }
        const locales = (await cola().borradores()).filter((b) => esMio(b) && delPerfil(b, perfil));
        const porServidor = new Map(locales.filter((b) => b.servidor_id).map((b) => [b.servidor_id, b]));
        for (const fila of r[config.lista || "cotizaciones"] || []) {
          const local = porServidor.get(fila.id);
          if (!local) { await cola().guardarBorrador(parcial(fila, cuenta, null)); continue; }
          if (local.parcial) { await cola().guardarBorrador(parcial(fila, cuenta, local)); continue; }
          if (!local.sucio && !local.borrada && (local.revision_base || 0) < fila.revision) {     // guardado en el teléfono: se trae lo nuevo
            const detalle = await pedir(url("detalle", fila.id));
            await cola().guardarBorrador(aRegistro(detalle, detalle.datos, cuenta, local));
            continue;
          }
          await cola().actualizarBorrador(local.id, (x) => Object.assign(x, delServidor(fila, x), { resumen: fila }));
        }
        const fuera = async (b) => {
          if (!b || b.borrada) return;
          if (!b.sucio) await cola().quitarBorrador(b.id);
          else await cola().actualizarBorrador(b.id, (x) => Object.assign(x, { eliminado_en_servidor: true }));
        };
        for (const id of r.eliminados || []) await fuera(porServidor.get(id));
        if (r.vigentes) {                                              // se borraron documentos para siempre desde el cursor anterior
          const siguen = new Set(r.vigentes);
          for (const b of locales) if (b.servidor_id && !siguen.has(b.servidor_id)) await fuera(b);
        }
        cursor = r.cursor;
        await cola().guardarAjuste(llave(), cursor);
        if (!r.mas) break;
      }
    }

    /** Abre un documento que el teléfono solo conoce por su resumen: trae sus datos y desde ahí se guarda en el teléfono. */
    async function abrir(id) {
      const local = await cola().borrador(id);
      if (!local || !local.parcial) return local;
      const detalle = await pedir(url("detalle", local.servidor_id));
      const cuenta = await cola().ajuste("cuenta", null);
      const nuevo = aRegistro(detalle, detalle.datos, cuenta, { id: local.id, creada: local.creada });
      nuevo.recuperaciones = detalle.recuperaciones || [];
      await cola().guardarBorrador(nuevo);
      return nuevo;
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
      const perfilEnviado = perfilDe(b) || (await perfilActivo()) || "";
      const r = await pedir(urls.sincronizar, { metodo: "POST", json: { id_cliente: b.id, servidor_id: b.servidor_id, revision_base: b.revision_base, datos: datosEnviados, sesion: b.sesion || "", perfil: perfilEnviado } });
      if (r.ajeno) {
        // Es de un perfil que esta cuenta ya no puede usar (lo editó antes de que se separaran los perfiles o de perder el permiso): quedó
        // guardado en el servidor, en el espacio de su perfil; aquí ya no se muestra.
        await cola().quitarBorrador(b.id);
        avisar({ tipo_evento: "ajeno", id: b.id });
        return;
      }
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
          x.servidor_id = r.servidor_id; x.error = "";
          if (r.perfil) x.perfil = r.perfil;
          if (r.modificado) x.modificado = r.modificado;
          if (x.actualizada === enviado) { x.datos = r.datos; x.sucio = false; x.revision_base = r.revision; x.sesion = ""; return x; }
          // Se siguió escribiendo durante la petición: lo nuevo de aquí se pone encima de la fusión del servidor, con la misma regla que
          // el servidor (offline/fusion.js). Así la siguiente subida parte de la revisión fusionada y no choca consigo misma. Solo si eso
          // choca se conserva la base anterior, para que el servidor vuelva a fusionar (o guarde copia); nunca se pisa nada.
          // La base es lo enviado tal como lo guardó el servidor (`r.enviados`, normalizado: sin espacios de más, etc.), igual que `r.datos`.
          try { x.datos = ITT.fusionar(r.enviados || datosEnviados, r.datos, x.datos, config.claves || {}); x.revision_base = r.revision; }
          catch (e) { if (!(ITT.ChoqueDeFusion && e instanceof ITT.ChoqueDeFusion)) throw e; }
          return x;
        });
        if ((await cola().borrador(b.id) || {}).sucio) repetir = true;
        avisar({ tipo_evento: "fusionada", id: b.id });
        return;
      }
      if (r.resultado === "copia") {
        // El servidor cambió desde la revisión del teléfono: la versión del teléfono quedó como copia (nada se perdió ni se pisó). Si el
        // original se borró o está en la papelera, el teléfono se queda solo con la copia.
        let original = null;
        if (r.copia_de) {
          try { original = await pedir(url("detalle", r.copia_de)); }
          catch (e) { if (e.estado !== 410 && e.estado !== 404) throw e; }
        }
        const servidorCopia = await pedir(url("detalle", r.servidor_id));
        const actual = await cola().borrador(b.id);
        const cambioDespues = actual && actual.actualizada !== enviado;
        const copia = Object.assign({ id: r.servidor_id, tipo, cuenta, perfil: r.perfil || perfilEnviado || null, servidor_id: r.servidor_id, revision_base: r.revision, datos: cambioDespues ? actual.datos : servidorCopia.datos,
          sucio: !!cambioDespues, borrada: false, creada: Date.now(), actualizada: Date.now(), miniaturas: actual ? actual.miniaturas : {},
          aviso: `${r.conflicto || "Copia en conflicto"}. ${config.avisoCopia || "Este documento cambió mientras lo editabas sin conexión: tu versión quedó aparte, nada se perdió."}` },
        delServidor(servidorCopia));
        await cola().guardarBorrador(copia);
        if (original) await cola().guardarBorrador(aRegistro(original, original.datos, cuenta, actual ? { id: actual.id, miniaturas: actual.miniaturas } : { id: b.id }));
        else if (copia.id !== b.id) await cola().quitarBorrador(b.id);          // el original ya no está: queda solo la copia
        avisar({ tipo_evento: "copia", id: b.id, copia: copia.id });
        return;
      }
      await cola().actualizarBorrador(b.id, (x) => {
        x.servidor_id = r.servidor_id; x.revision_base = r.revision; x.error = "";
        if (r.perfil) x.perfil = r.perfil;
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

    /** Si `servidorId` es un documento de OTRO perfil que la cuenta puede usar: `{clave, nombre}` de ese perfil (para decirlo y ofrecer
     * cambiar). null si es del perfil activo, si no existe para la cuenta o sin señal. */
    async function otroPerfilDe(servidorId) {
      if (!navigator.onLine) return null;
      try { return (await pedir(url("detalle", servidorId))).otro_perfil || null; } catch (_) { return null; }
    }

    /** «Copiar a <otro perfil>»: el servidor crea el documento en el espacio del otro perfil (una cotización, con el folio de su serie). */
    async function copiarAPerfil(id, perfil) {
      const local = await cola().borrador(id);
      if (!local || !local.servidor_id) throw { clase: "rechazo", mensaje: "Primero sincroniza este documento." };
      return pedir(url("copiar-perfil", local.servidor_id), { metodo: "POST", json: { perfil } });
    }

    return { tipo, iniciar, pedir, url, listar, crear, guardar, borrar, sincronizar, copiar, localDe, otroPerfilDe, copiarAPerfil, refrescar, guardarDelServidor,
      abrir, get urls() { return urls; } };
  }

  /** Cambia el perfil activo de la cuenta (el servidor lo recuerda) y vuelve a abrir la página: todo se ve en el espacio del otro perfil.
   * Lo pendiente del perfil anterior se queda en el teléfono y sube igual. Necesita señal. */
  async function cambiarPerfil(urlCambio, clave) {
    const formulario = new FormData();
    formulario.append("perfil", clave);
    const r = await pedir(urlCambio, { metodo: "POST", formulario });
    await fijarPerfil(r.perfil);
    return r;
  }

  ITT.crearDocumentos = crearDocumentos;
  ITT.perfiles = { fijar: fijarPerfil, activo: perfilActivo, de: perfilDe, cambiar: cambiarPerfil };
  ITT.pedirApi = pedir;
})(typeof self !== "undefined" ? self : this);

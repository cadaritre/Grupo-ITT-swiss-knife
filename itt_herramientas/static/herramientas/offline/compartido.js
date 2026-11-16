/* Documentos compartidos con el equipo (cotizaciones, reportes, croquis): lo común a los tres editores.
 *
 *   const comp = ITT.compartido.crear({ tipo: "cotizacion", prefijo: "cot", docs, articulo: "la", nombre: "cotización",
 *     alRecargar: (reg) => …, alCambiarModo: (soloLectura) => …, resumen: (datos) => [nodos], pdfVersion: (id, vid) => url|null });
 *
 * - Bloqueo de edición: al abrir un documento que ya está en el servidor, la página toma el bloqueo y late cada `ctx.latido` s. Si otra
 *   persona lo edita, el documento se abre en solo lectura con «La está editando X desde las hh:mm» y «Tomar el control». Si a esta
 *   página le quitan el control, lo dice y lo que no se había subido llega al servidor como borrador de recuperación (nunca se pierde).
 *   Sin conexión se edita en el teléfono; al sincronizar se fusiona o queda como copia en conflicto (lo decide el servidor).
 * - Historial de versiones con vista de solo lectura y «Restaurar esta versión».
 * - Borrador de recuperación: «Ver», «Usar mis cambios» y «Descartar».
 * - Avisos del servidor (tomaron el control, copia en conflicto) en la lista.
 * Las zonas que se editan llevan `data-editable` en la plantilla: en solo lectura quedan `inert`.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});

  function nuevaSesion() {
    const a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return "p" + Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  const hora = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }); };
  const fechaHora = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : `${ITT.ui.fechaCorta(iso)}, ${hora(iso)}`; };

  function crear(config) {
    const { h, limpiar, icono, boton, confirmar } = ITT.ui;
    const $ = (id) => document.getElementById(id);
    const p = config.prefijo;
    const docs = config.docs;
    const sesion = nuevaSesion();
    const Art = config.articulo === "lo" ? "Lo" : "La";
    let reg = null;
    let mio = false;                       // esta página tiene el bloqueo
    let lectura = false;                   // solo lectura (otra persona edita, o se eliminó)
    let temporizador = null;
    let latido = 15;
    let ultimo = null;                     // último estado del bloqueo
    let vioOtro = false;                   // se mostró «La está editando X»: al quedar libre se avisa que ya se puede editar

    const enLinea = () => navigator.onLine;
    const url = (nombre, id, vid) => (docs.urls[nombre] || "").replace("00000000-0000-0000-0000-000000000000", id || "").replace("999999999", vid == null ? "" : String(vid));

    // -- Modo de edición -----------------------------------------------------------------------------------------------------------
    function ponerLectura(si) {
      lectura = !!si;
      for (const zona of document.querySelectorAll(`#${p}-vista-editor [data-editable]`)) {
        if (lectura) zona.setAttribute("inert", ""); else zona.removeAttribute("inert");
      }
      const vista = $(`${p}-vista-editor`);
      if (vista) vista.dataset.soloLectura = lectura ? "1" : "";
      document.documentElement.dataset.ittSoloLectura = lectura ? "1" : "";
      config.alCambiarModo && config.alCambiarModo(lectura);
    }

    function banda(estado) {
      const e = $(`${p}-bloqueo`);
      if (!e) return;
      limpiar(e);
      e.hidden = !estado;
      if (!estado) return;
      e.dataset.tipo = estado.tipo;
      if (estado.tipo === "otro") {
        e.append(icono("lock"), h("span", {}, estado.otraVentana ? `${Art} tienes abierta en otra ventana.` : `${Art} está editando ${estado.quien} desde las ${hora(estado.desde)}.`),
          boton({ onclick: tomarControl, class: "tv-boton tv-boton--secundario", id: `${p}-tomar-control`, "data-siempre": "1" }, "hand", estado.otraVentana ? "Editar aquí" : "Tomar el control"));
      } else if (estado.tipo === "eliminado") {
        e.append(icono("trash-2"), h("span", {}, `Alguien mandó ${config.articulo} ${config.nombre} a la papelera.`),
          docs.urls.papelera ? h("a", { class: "tv-boton tv-boton--secundario", href: docs.urls.papelera }, "Ver la papelera") : null);
      }
    }

    // -- Bloqueo -----------------------------------------------------------------------------------------------------------------
    async function pedirBloqueo(accion) {
      return docs.pedir(url("bloqueo", reg.servidor_id), { metodo: "POST", json: { accion, sesion } });
    }

    async function refrescar() {
      if (!reg || !reg.servidor_id) return;
      const nuevo = await docs.refrescar(reg.id);
      if (nuevo) { reg = nuevo; config.alRecargar && (await config.alRecargar(nuevo)); }
    }

    async function latir() {
      clearTimeout(temporizador);
      temporizador = null;
      if (!reg) return;
      try {
        if (enLinea() && reg.servidor_id) {
          const r = await pedirBloqueo(mio ? "latido" : "tomar");
          if (!reg) return;
          ultimo = r;
          if (r.eliminado) { mio = false; banda({ tipo: "eliminado" }); ponerLectura(true); }
          else if (r.mio) {
            if (!mio) {                                                  // recién tomado (al abrir, o porque la otra persona terminó)
              mio = true;
              const local = await ITT.cola.borrador(reg.id);
              if (local && !local.sucio && r.revision > (local.revision_base || 0)) await refrescar();
              if (vioOtro && config.alMensaje) config.alMensaje(`Ya puedes editar ${config.articulo} ${config.nombre}.`, "info");
              vioOtro = false;
              banda(null); ponerLectura(false);
            }
          } else {
            const perdio = mio;
            mio = false;
            vioOtro = true;
            banda({ tipo: "otro", quien: r.quien, desde: r.desde, otraVentana: r.otra_ventana });
            ponerLectura(true);
            if (perdio) await perdido(r);
            else {
              const local = await ITT.cola.borrador(reg.id);
              if (local && !local.sucio && r.revision > (local.revision_base || 0)) await refrescar();
            }
          }
        } else if (!reg.servidor_id || !enLinea()) {
          if (!enLinea()) mio = false;
          banda(null); ponerLectura(false);                                // sin conexión (o aún no subido): se edita en el teléfono
        }
      } catch (e) {
        if (e && e.clase === "red") { mio = false; banda(null); ponerLectura(false); }
      }
      if (reg) temporizador = setTimeout(latir, latido * 1000);
    }

    async function perdido(r) {
      // Lo que no se había subido sale con la sesión de esta página: el servidor lo guarda como borrador de recuperación.
      const local = await ITT.cola.borrador(reg.id);
      if (local && local.sucio && docs.sincronizar) { try { await docs.sincronizar(); } catch (_) { /* se reintenta con la cola */ } }
      await refrescar();
      cargarRecuperacion();
      await confirmar(`${r.otra_ventana ? "Abriste esta página en otra ventana" : `${r.quien} tomó el control`}. Lo que no se había guardado quedó en un borrador de recuperación.`, { aceptar: "Entendido", soloAviso: true });
    }

    async function tomarControl() {
      if (!reg || !enLinea()) return;
      const quien = ultimo && !ultimo.otra_ventana ? ultimo.quien : "";
      if (quien && !(await confirmar(`${quien} dejará de editar${config.articulo}. Lo que no haya guardado quedará en un borrador de recuperación.`, { aceptar: "Tomar el control" }))) return;
      try {
        const r = await pedirBloqueo("forzar");
        if (r.mio) { mio = true; vioOtro = false; await refrescar(); banda(null); ponerLectura(false); }
      } catch (e) { config.alMensaje && config.alMensaje(e.mensaje || "No se pudo tomar el control. Inténtalo de nuevo.", "error"); }
    }

    function soltar() {
      if (!reg || !mio || !reg.servidor_id) return;
      mio = false;
      const destino = url("bloqueo", reg.servidor_id);
      ITT.cola.ajuste("csrf", "").then((csrf) => {
        try { fetch(destino, { method: "POST", keepalive: true, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ accion: "soltar", sesion }) }); }
        catch (_) { /* caduca solo */ }
      });
    }

    // -- Abrir y cerrar ---------------------------------------------------------------------------------------------------------------
    async function abrir(r, opciones) {
      if (reg && reg.id !== r.id) cerrar();
      reg = r;
      latido = (opciones && opciones.latido) || latido;
      banda(null);
      pintarRecuperacion(r.recuperacion || null);
      if (r.servidor_id && enLinea()) {
        ponerLectura(true);                                              // hasta saber si se puede editar
        await latir();
        cargarRecuperacion();
      } else { ponerLectura(false); temporizador = setTimeout(latir, latido * 1000); }
    }
    function cerrar() {
      clearTimeout(temporizador);
      temporizador = null;
      soltar();
      reg = null; mio = false; ultimo = null; vioOtro = false;
      banda(null); pintarRecuperacion(null); ponerLectura(false);
    }
    addEventListener("pagehide", soltar);
    document.addEventListener("itt:doc", (e) => {                          // lo que no se había subido llegó como borrador de recuperación
      const d = e.detail || {};
      if (d.tipo === config.tipo && d.tipo_evento === "recuperacion" && reg && d.id === reg.id) cargarRecuperacion();
    });
    addEventListener("online", () => { if (reg) latir(); });

    // -- Borrador de recuperación -------------------------------------------------------------------------------------------------
    async function cargarRecuperacion() {
      if (!reg || !reg.servidor_id || !enLinea()) return;
      try { const r = await docs.pedir(url("recuperacion", reg.servidor_id)); pintarRecuperacion(r.recuperacion); } catch (_) { /* sin red */ }
    }
    function pintarRecuperacion(rec) {
      const e = $(`${p}-recuperacion`);
      if (!e) return;
      limpiar(e);
      e.hidden = !rec;
      if (!rec) return;
      e.append(h("span", {}, icono("history"), ` Tienes cambios sin guardar del ${fechaHora(rec.creado)}${rec.quien ? `, de cuando ${rec.quien} tomó el control` : ""}.`),
        h("span", { class: "itt-recuperacion__acciones" },
          boton({ onclick: () => verDatos("Tus cambios sin guardar", rec.datos, null), class: "tv-boton tv-boton--secundario", "data-siempre": "1" }, "eye", "Ver"),
          boton({ onclick: usarRecuperacion, class: "tv-boton tv-boton--secundario", id: `${p}-usar-recuperacion` }, "rotate-ccw", "Usar mis cambios"),
          boton({ onclick: descartarRecuperacion, class: "tv-boton tv-boton--fantasma" }, "x", "Descartar")));
    }
    async function usarRecuperacion() {
      if (!mio) return config.alMensaje && config.alMensaje(`Para usar tus cambios, primero toma el control de ${config.articulo} ${config.nombre}.`, "error");
      if (!(await confirmar("Tus cambios pasarán a ser la versión vigente. La de ahora queda en el historial.", { aceptar: "Usar mis cambios" }))) return;
      try {
        const r = await docs.pedir(url("recuperacion", reg.servidor_id), { metodo: "POST", json: { sesion } });
        await docs.guardarDelServidor(reg.id, r);
        pintarRecuperacion(null);
        await refrescar();
        config.alMensaje && config.alMensaje("Se usaron tus cambios.", "info");
      } catch (e) { config.alMensaje && config.alMensaje(e.mensaje || "No se pudo. Inténtalo de nuevo.", "error"); }
    }
    async function descartarRecuperacion() {
      if (!(await confirmar("¿Descartar tus cambios sin guardar? No se pueden recuperar.", { aceptar: "Descartar", peligro: true }))) return;
      try { await docs.pedir(url("recuperacion", reg.servidor_id), { metodo: "DELETE" }); pintarRecuperacion(null); }
      catch (e) { config.alMensaje && config.alMensaje(e.mensaje || "No se pudo. Inténtalo de nuevo.", "error"); }
    }

    // -- Historial ----------------------------------------------------------------------------------------------------------------
    function dialogo() {
      let d = $("dlg-historial");
      if (d) return d;
      d = h("dialog", { id: "dlg-historial", class: "tv-hoja itt-historial", "aria-labelledby": "dlg-historial-titulo" },
        h("div", { class: "tv-hoja__cuerpo" },
          h("div", { class: "tv-hoja__asa", "aria-hidden": "true" }),
          h("h2", { id: "dlg-historial-titulo" }, "Historial de versiones"),
          h("p", { class: "tv-tenue itt-historial__nota", id: "historial-nota" }),
          h("div", { id: "historial-cuerpo", class: "itt-historial__cuerpo" }),
          h("div", { class: "tv-hoja__acciones" }, h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => d.close() }, "Cerrar"))));
      document.body.append(d);
      return d;
    }
    async function historial() {
      if (!reg || !reg.servidor_id) return config.alMensaje && config.alMensaje("El historial empieza cuando el documento se sube al servidor.", "info");
      if (!enLinea()) return config.alMensaje && config.alMensaje("Sin conexión. El historial necesita señal.", "error");
      const d = dialogo();
      const cuerpo = $("historial-cuerpo");
      limpiar(cuerpo);
      cuerpo.append(h("p", { class: "tv-tenue" }, "Cargando…"));
      if (!d.open) d.showModal();
      try {
        const r = await docs.pedir(url("versiones", reg.servidor_id));
        $("historial-nota").textContent = `Los guardados seguidos de una misma persona (${r.retencion.agrupar_minutos} min) cuentan como una versión. Se conservan las ${r.retencion.visibles} más recientes.`;
        limpiar(cuerpo);
        const ul = h("ul", { class: "itt-historial__lista" });
        for (const v of r.versiones) {
          ul.append(h("li", { class: "itt-historial__fila", "data-version": String(v.revision) },
            h("div", { class: "itt-historial__texto" },
              h("strong", {}, `Versión ${v.revision}`, v.vigente ? h("span", { class: "cot-etiqueta" }, "Vigente") : null),
              h("span", { class: "tv-tenue" }, `${v.nombre} · ${fechaHora(v.fecha)}`),
              v.nota ? h("span", { class: "tv-tenue" }, v.nota) : null),
            boton({ onclick: () => verVersion(v), class: "tv-boton tv-boton--secundario" }, "eye", "Ver")));
        }
        cuerpo.append(ul);
      } catch (e) { limpiar(cuerpo); cuerpo.append(h("p", {}, e.mensaje || "No se pudo cargar el historial.")); }
    }
    async function verVersion(v) {
      try {
        const r = await docs.pedir(url("version", reg.servidor_id, v.id));
        verDatos(`Versión ${v.revision} · ${v.nombre}`, r.datos, v);
      } catch (e) { config.alMensaje && config.alMensaje(e.mensaje || "No se pudo abrir la versión.", "error"); }
    }
    function verDatos(titulo, datos, v) {
      const d = dialogo();
      const cuerpo = $("historial-cuerpo");
      limpiar(cuerpo);
      $("historial-nota").textContent = v ? `${fechaHora(v.fecha)}${v.nota ? ` · ${v.nota}` : ""}. Solo lectura.` : "Solo lectura.";
      const pdf = v && config.pdfVersion ? config.pdfVersion(reg.servidor_id, v.id) : null;
      cuerpo.append(h("h3", { class: "itt-historial__titulo" }, titulo), h("div", { class: "itt-historial__vista" }, config.resumen(datos)),
        h("div", { class: "itt-historial__acciones" },
          v ? boton({ onclick: historial, class: "tv-boton tv-boton--fantasma" }, "arrow-left", "Todas las versiones") : null,
          pdf ? h("a", { class: "tv-boton tv-boton--secundario", href: pdf, target: "_blank", rel: "noopener" }, icono("file-text"), h("span", {}, "PDF de esta versión")) : null,
          v && !v.vigente ? boton({ onclick: () => restaurar(v), class: "tv-boton", id: "historial-restaurar" }, "rotate-ccw", "Restaurar esta versión") : null));
      if (!d.open) d.showModal();
    }
    async function restaurar(v) {
      if (!mio) return config.alMensaje && config.alMensaje(`Para restaurar, primero toma el control de ${config.articulo} ${config.nombre}.`, "error");
      if (!(await confirmar(`La versión ${v.revision} pasará a ser la vigente. La de ahora queda en el historial.`, { aceptar: "Restaurar" }))) return;
      try {
        const r = await docs.pedir(url("restaurar-version", reg.servidor_id, v.id), { metodo: "POST", json: { sesion } });
        await docs.guardarDelServidor(reg.id, r);
        $("dlg-historial").close();
        await refrescar();
        config.alMensaje && config.alMensaje(`Se restauró la versión ${v.revision}.`, "info");
      } catch (e) { config.alMensaje && config.alMensaje(e.mensaje || "No se pudo restaurar.", "error"); }
    }

    // -- Avisos (lista) -------------------------------------------------------------------------------------------------------------
    async function avisos() {
      const e = $(`${p}-avisos`);
      if (!e || !enLinea() || !docs.urls.avisos) return;
      let lista = [];
      try { lista = (await docs.pedir(docs.urls.avisos)).avisos || []; } catch (_) { return; }
      limpiar(e);
      e.hidden = !lista.length;
      for (const a of lista) {
        e.append(h("li", { class: "tv-alerta tv-alerta--aviso itt-aviso-doc" }, h("span", {}, a.texto),
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: async (ev) => {
            ev.target.closest("li").remove();
            e.hidden = !e.children.length;
            try { await docs.pedir(docs.urls.avisos, { metodo: "POST", json: { vistos: [a.id] } }); } catch (_) { /* se vuelve a mostrar */ }
          } }, "Entendido")));
      }
    }

    return {
      abrir, cerrar, historial, avisos, tomarControl,
      get sesion() { return mio ? sesion : ""; },
      get soloLectura() { return lectura; },
      get mio() { return mio; },
    };
  }

  ITT.compartido = { crear, hora, fechaHora };
})(typeof self !== "undefined" ? self : this);

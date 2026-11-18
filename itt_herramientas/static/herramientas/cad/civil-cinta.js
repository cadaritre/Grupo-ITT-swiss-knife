/* Civil en la cinta del editor: la pestaña «Civil» de Open CAD Studio, con sus grupos, botones y comandos (docs/civil.md, «En la cinta»).
 *
 * La pestaña se arma de `civil/pagina.py` `MENUS` (llega en el panel de Civil como JSON, `#civil-cinta`): un grupo por menú, las acciones
 * `grande` como botones grandes, las demás chicas por sección del menú (hasta tres con su nombre; más, un botón dividido con la última
 * usada y su lista), y un menú `como: "desplegable"` como un solo botón grande con su lista. Además, el botón de la escala del plano («1:1000»). Cada acción tiene
 * su comando (CVTRIANGULAR, CVCURVAS…) que también se escribe en la línea de comandos del editor, con autocompletar. Nada de esto conoce
 * las acciones una por una: lo que se agregue a `MENUS` aparece solo.
 *
 * El editor (compilación tv-v2026.45-13 en adelante, parche 70 del fork) dibuja la pestaña (`ribbon_define`), la deja apagada por plano
 * (`ribbon_state` con su `document_id`: sin el control del plano solo quedan las que solo leen) y, al pulsar un botón o escribir un
 * comando, anota un evento `ribbon` y avisa a la página (`ocs-control-event` en su ventana); aquí se lee con `events` y se abre la hoja
 * de siempre (civil.js). Si el editor no tiene la operación (`unknown_operation`), Civil vuelve a su barra encima del editor.
 *
 * En la cinta, las hojas de Civil son paletas del editor: se abren sin bloquear el dibujo, acopladas a la derecha del área de dibujo y
 * nunca sobre la cinta (si la persona vuelve a la cinta mientras una está abierta, la paleta se aparta hasta que regresa al dibujo).
 * Esc cierra primero la hoja abierta, también con el foco en el editor (cad/editor/arranque.js pregunta a `TVCivilCinta.esc()`).
 *
 * API (global `window.TVCivilCinta`):
 *   await montar(civil)   civil: { accion(clave), tiene(clave), ocupado() } (civil.js). true si la pestaña quedó en el editor.
 *   activa()              la pestaña está en el editor (no la barra).
 *   mostrar(hoja)         abre una hoja de Civil: paleta en la cinta, hoja modal fuera de ella.
 *   esc()                 cierra la hoja abierta; true si cerró una.
 *   escala(texto)         el texto del botón de la escala y la zona («1:500 · Zona 14N»).
 *   refrescar()           vuelve a poner qué acciones se pueden usar (p. ej. al registrar una nueva).
 *   mostrarPestana()      pone la pestaña «Civil» al frente.
 *   comandos()            { comando: clave } de la pestaña (diagnóstico y pruebas).
 */
(function () {
  "use strict";
  if (window.TVCivilCinta) return;

  const PESTANA = "civil";
  const SIN_CONTROL = "Para dibujar, toma el control del plano.";
  const NO_DISPONIBLE = "Esta herramienta todavía no está disponible.";
  const raiz = document.documentElement;

  let datos = null, civil = null, activa = false, escalaTexto = "1:1000 · Zona 14N";
  let porComando = new Map();
  let ventana = null, cursor = 0, leyendo = false, otraVez = false;
  let paleta = null, relojPaleta = 0;

  const marco = () => document.getElementById("cad-editor");
  const pedir = (p, ms = 15000) => window.TVCad.pedir(p, { tiempoMs: ms });
  const codigo = (err) => (err && err.respuesta && err.respuesta.code) || (err && err.codigo) || "";
  const tactil = () => raiz.classList.contains("cad-tactil");

  // -- La pestaña -----------------------------------------------------------------------------------------------------------------
  function boton(a, tipo) {
    return { type: tipo, command: a.comando, label: a.nombre, icon: a.icono, ...(a.descripcion ? { description: a.descripcion } : {}) };
  }
  function grupo(g) {
    const acciones = g.acciones || [];
    if (!acciones.length) return null;
    if (g.como === "desplegable") {
      const def = acciones.find((a) => a.grande) || acciones[0];
      return { title: g.nombre, items: [{ type: "split_large", label: g.nombre, icon: def.icono, default: def.comando,
        items: acciones.map((a) => ({ command: a.comando, label: a.nombre, icon: a.icono, ...(a.descripcion ? { description: a.descripcion } : {}) })) }] };
    }
    // Las grandes primero; las chicas por sección del menú: hasta tres, cada una con su nombre; más, un botón dividido que muestra la
    // última que se usó (como «Rectángulo/Polígono» del editor) y su lista.
    const items = acciones.filter((a) => a.grande).map((a) => boton(a, "large"));
    const secciones = new Map();
    for (const a of acciones) if (!a.grande) { if (!secciones.has(a.seccion)) secciones.set(a.seccion, []); secciones.get(a.seccion).push(a); }
    for (const s of secciones.values()) {
      if (s.length <= 3) items.push(...s.map((a) => boton(a, "small")));
      else items.push({ type: "split", items: s.map((a) => ({ command: a.comando, label: a.nombre, icon: a.icono, ...(a.descripcion ? { description: a.descripcion } : {}) })) });
    }
    return { title: g.nombre, items };
  }
  function definicion() {
    const e = datos.escala;
    return {
      id: PESTANA, title: "Civil",
      groups: [{ title: e.nombre, items: [{ ...boton(e, "large"), label: escalaTexto }] }, ...datos.grupos.map(grupo).filter(Boolean)],
    };
  }
  function indexar() {
    porComando = new Map([[datos.escala.comando, datos.escala]]);
    for (const g of datos.grupos) for (const a of g.acciones || []) if (!porComando.has(a.comando)) porComando.set(a.comando, a);
  }
  async function definir(mostrarla = false) {
    for (let i = 0; ; i++) {
      try { await pedir({ op: "ribbon_define", tab: definicion(), ...(mostrarla ? { show: true } : {}) }); return true; }
      catch (err) {
        const c = codigo(err);
        if (c === "unknown_operation") return false;                                  // editor sin pestañas propias: la barra
        if (["command_busy", "busy", "tiempo"].includes(c) && i < 8) { await new Promise((r) => setTimeout(r, 750)); continue; }
        console.warn("Civil: la pestaña de la cinta no se pudo poner", err);
        return false;
      }
    }
  }

  // -- Qué se puede usar, por plano -----------------------------------------------------------------------------------------------
  async function refrescar() {
    if (!activa) return;
    const st = window.TVCad.estado();
    if (!st.documento) return;
    const disabled = {};
    for (const [cmd, a] of porComando) {
      if (!civil.tiene(a.clave)) disabled[cmd] = NO_DISPONIBLE;
      else if (!st.editable && !a.solo_lee) disabled[cmd] = SIN_CONTROL;
    }
    try { await pedir({ op: "ribbon_state", document_id: st.documento, disabled }); }
    catch (err) { if (codigo(err) !== "document_closed") console.warn("Civil: estado de la cinta", err); }
  }

  // -- Lo que la persona pulsa o escribe ------------------------------------------------------------------------------------------
  function escuchar() {
    const m = marco();
    const w = m && m.contentWindow;
    if (!w || w === ventana) return false;
    ventana = w;
    try {
      w.addEventListener("ocs-control-event", leer);
      // La paleta se aparta mientras la persona usa la cinta (sus listas caen sobre el dibujo) y vuelve al regresar al dibujo.
      w.addEventListener("pointerdown", alPuntero, { capture: true, passive: true });
      w.addEventListener("pointermove", alPuntero, { capture: true, passive: true });
    } catch (_) { ventana = null; return false; }
    return true;
  }
  async function leer() {
    if (leyendo) { otraVez = true; return; }
    leyendo = true;
    try {
      do {
        otraVez = false;
        const r = await pedir({ op: "events", after: cursor });
        cursor = r.cursor || cursor;
        for (const e of r.events || []) if (e.kind === "ribbon" && e.tab === PESTANA) ejecutar(e);
      } while (otraVez);
    } catch (err) { console.warn("Civil: eventos del editor", err); }
    finally { leyendo = false; }
  }
  function ejecutar(e) {
    const a = porComando.get(e.command);
    if (!a || tactil()) return;
    if (civil.ocupado()) { aviso("Espera a que termine lo anterior.", "error"); return; }
    civil.accion(a.clave);
  }
  const aviso = (t, tipo) => (window.tvAviso ? window.tvAviso(t, tipo) : console.log(t));

  // -- Hojas como paletas ---------------------------------------------------------------------------------------------------------
  /** El área de dibujo del editor en px de la página, o null si no se sabe (o es muy angosta para una paleta). */
  async function areaDeDibujo() {
    const m = marco();
    if (!m) return null;
    let st;
    try { st = await pedir({ op: "state" }, 5000); } catch (_) { return null; }
    const r = st.viewport_rect, b = m.getBoundingClientRect();
    if (!Array.isArray(r) || r.length !== 4) return null;
    return { x: b.left + r[0], y: b.top + r[1], w: r[2], h: r[3] };
  }
  async function colocar(h) {
    const a = await areaDeDibujo();
    if (!a || !h.open) return;
    const margen = 8, ancho = Math.min(440, a.w - 2 * margen);
    h.style.setProperty("--civil-paleta-x", `${Math.round(a.x + a.w - ancho - margen)}px`);
    h.style.setProperty("--civil-paleta-y", `${Math.round(a.y + margen)}px`);
    h.style.setProperty("--civil-paleta-w", `${Math.round(ancho)}px`);
    h.style.setProperty("--civil-paleta-h", `${Math.round(Math.max(160, a.h - 2 * margen))}px`);
  }
  function alPuntero(ev) {
    if (!paleta || !paleta.open) return;
    const limite = Number(paleta.dataset.cintaMarco || 0);           // alto de la cinta dentro del marco
    const enCinta = ev.clientY < limite;
    if (ev.type === "pointerdown" && enCinta) paleta.toggleAttribute("data-apartada", true);
    else if (!enCinta && paleta.hasAttribute("data-apartada")) paleta.removeAttribute("data-apartada");
  }
  async function mostrar(h) {
    if (!activa || window.matchMedia("(max-width: 599px)").matches) { h.classList.remove("civil-hoja--paleta"); h.showModal(); return; }
    if (paleta && paleta !== h && paleta.open) paleta.close();
    h.classList.add("civil-hoja--paleta");
    if (!h.dataset.civilPaleta) {
      h.dataset.civilPaleta = "1";
      h.addEventListener("close", () => setTimeout(enfocarEditor, 0));
    }
    h.removeAttribute("data-apartada");
    paleta = h;
    if (!h.open) { h.toggleAttribute("data-colocando", true); h.show(); }
    // lo primero que se puede llenar recibe el foco, como en un diálogo del editor
    const primero = h.querySelector("input:not([type=hidden]):not([disabled]), select, textarea, button:not([data-tv-cerrar])");
    if (primero) try { primero.focus({ preventScroll: true }); } catch (_) { /* nada */ }
    await colocar(h);
    h.removeAttribute("data-colocando");
    try {
      const m = marco(), st = await pedir({ op: "state" }, 5000);
      if (m && Array.isArray(st.viewport_rect)) h.dataset.cintaMarco = String(st.viewport_rect[1]);
    } catch (_) { /* sin estado: la paleta no se aparta */ }
    clearInterval(relojPaleta);
    // la cinta puede minimizarse o la ventana cambiar: la paleta sigue al área de dibujo mientras está abierta
    relojPaleta = setInterval(() => { if (!h.open) { clearInterval(relojPaleta); return; } colocar(h); }, 1500);
  }
  function esc() {
    if (paleta && paleta.open) { paleta.close(); return true; }
    return false;
  }
  /** El teclado vuelve al dibujo cuando se cierra la última paleta (como al cerrar una paleta del editor). */
  function enfocarEditor() {
    if (document.querySelector("dialog[open]") || tactil()) return;
    try { const m = marco(); m.contentWindow.focus(); const c = m.contentDocument.querySelector("canvas"); if (c) c.focus({ preventScroll: true }); }
    catch (_) { /* sin marco */ }
  }
  // Esc con el foco en la página: primero la paleta abierta (la elección en el dibujo, con su aviso, cierra la paleta antes de empezar).
  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !paleta || !paleta.open) return;
    if (document.querySelector("dialog[open]:not(.civil-hoja--paleta)")) return;    // otra hoja modal encima: es suya
    ev.preventDefault(); ev.stopImmediatePropagation();
    esc();
  }, true);
  window.addEventListener("resize", () => { if (paleta && paleta.open) colocar(paleta); });

  // -- Arranque -------------------------------------------------------------------------------------------------------------------
  async function montar(api) {
    civil = api;
    const nodo = document.getElementById("civil-cinta");
    if (!nodo || !window.TVCad) return false;
    try { datos = JSON.parse(nodo.textContent); } catch (_) { return false; }
    indexar();
    if (!(await definir())) return false;
    activa = true;
    raiz.dataset.civil = "cinta";
    escuchar();
    try { cursor = (await pedir({ op: "state" })).event_cursor || 0; } catch (_) { cursor = 0; }
    await refrescar();
    // Otra pestaña, o el plano vuelto a abrir (tomar el control, restaurar): su estado; si el marco del editor es otro, la pestaña otra vez.
    window.TVCad.on("cargado", async () => {
      if (escuchar()) {
        await definir();
        try { cursor = (await pedir({ op: "state" })).event_cursor || 0; } catch (_) { /* sigue */ }
      }
      refrescar();
    });
    window.TVCad.on("editable", () => refrescar());
    return true;
  }

  let reloj = 0;
  function escala(texto) {
    if (!texto || texto === escalaTexto) return;
    escalaTexto = texto;
    if (!activa) return;
    clearTimeout(reloj);
    reloj = setTimeout(() => { definir().then(refrescar); }, 150);
  }

  window.TVCivilCinta = Object.freeze({
    montar, mostrar, esc, escala, refrescar,
    mostrarPestana: () => (activa ? definir(true) : Promise.resolve(false)),
    activa: () => activa,
    comandos: () => Object.fromEntries([...porComando].map(([c, a]) => [c, a.clave])),
  });
})();

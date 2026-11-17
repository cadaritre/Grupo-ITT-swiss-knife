/* TVCad: acceso de las extensiones de la página CAD (p. ej. «Civil») al editor incrustado, por su canal de control oficial.
 *
 * El editor (Open CAD Studio web) vive en un marco del mismo origen abierto con `?embed=1&automation=1`. Este módulo envuelve
 * `window.ocsControl.request` de ese marco (docs/automation/web.md y API-SPEC.md de Open CAD Studio) y da a las extensiones un lugar
 * en la página, la barra `#cad-extensiones`, encima del editor. Solo funciona en la misma página: el navegador no deja que otro
 * origen toque el marco, y la CSP del editor pone `frame-ancestors 'self'`.
 *
 * API (global `window.TVCad`, versión 1):
 *
 *   TVCad.version                      1
 *   TVCad.listo                        Promise<estado>: se cumple cuando el editor abrió el plano y su canal de control respondió.
 *                                      Nunca se rechaza; si el editor no carga, simplemente no se cumple (usa `estaListo()` o `pedir`).
 *   TVCad.estaListo()                  true/false
 *   TVCad.estado()                     { listo, editable, plano: { id, nombre, formato, revision } }
 *                                      `editable` = esta página tiene el bloqueo de edición (si es false, el plano se ve en solo lectura:
 *                                      se puede dibujar en pantalla, pero no se guarda).
 *   await TVCad.pedir(peticion, { tiempoMs = 30000, sondeoMs })
 *                                      Manda una petición del canal (`{op: "state"}`, `{op: "run", cmd: "LINE 0,0 10,10"}`,
 *                                      `{op: "entities_set", …}`) y devuelve la respuesta final. Completa lo que falte del sobre del
 *                                      protocolo: `protocol: 1`, un `request_id` único y el `document_id` del plano abierto.
 *                                      `sondeoMs`: cada cuánto se mira si ya respondió (por omisión el del editor, 50 ms); el modo
 *                                      táctil usa 4 ms para que la referencia siga al dedo.
 *                                      Rechaza con `TVCad.ErrorEditor` (propiedad `codigo`):
 *                                        "no_listo"  el editor aún no abrió el plano (espera `TVCad.listo`);
 *                                        "sin_canal" el editor no tiene canal de control (versión sin él, o la página cambió);
 *                                        "tiempo"    no respondió a tiempo;
 *                                        "rechazada" la respuesta trae `ok: false` (en `error.respuesta` viene completa).
 *   await TVCad.ejecutar("CIRCLE 0,0 5")  atajo de `pedir({op: "run", cmd})`.
 *   TVCad.barra()                      el elemento `#cad-extensiones` (role="toolbar"), ya visible. Pon ahí tus menús y botones con las
 *                                      clases del armazón (`tv-boton`, `tv-boton--secundario`, `{% icono %}` desde la plantilla).
 *   TVCad.ocupar() / soltar()          una extensión avisa que está trabajando con el editor (p. ej. esperando que la persona elija en el
 *                                      dibujo, o insertando un resultado): mientras tanto la página no guarda el borrador de recuperación
 *                                      y «Guardar» pide esperar, porque el editor rechaza guardar a media operación. Se cuentan: cada
 *                                      ocupar() lleva su soltar(). `TVCad.ocupado()` dice si alguna extensión está trabajando.
 *   TVCad.vistaDe(state) / await TVCad.vista()
 *                                      La vista del editor, pantalla ↔ mundo, a partir de su `state` (`camera`, `viewport_size` y, desde
 *                                      tv-v2026.45-8, `viewport_rect` [x, y, w, h] del área de dibujo en px CSS de su documento y
 *                                      `screen_scale`). Sin `viewport_rect` el área de dibujo es el lienzo del editor (exacto con el lienzo
 *                                      solo, `?canvas=1`, como en el modo táctil). Devuelve { cx, cy, k (unidades por px CSS), w, h, ox, oy
 *                                      (esquina del área de dibujo en px CSS del marco), aMarco(x, y) / deMarco(px, py) en px del marco y
 *                                      aPagina(x, y) / dePagina(px, py) en px de la página }. La usan el modo táctil y Civil: nunca difieren.
 *   TVCad.registro()                   las últimas peticiones que cambian algo (op, orden, si se aceptó y la revisión que dejó) y los
 *                                      pasos a «Sin guardar»; para diagnosticar en el teléfono (también sale en la consola).
 *   TVCad.on(evento, fn) / off(...)    eventos: "listo" (estado), "cambio" ({ sucio }), "guardado" ({ revision, formato }),
 *                                      "cargado" (estado; también al reabrir el plano, p. ej. tras tomar el control o restaurar).
 *
 * Guardar sigue siendo del editor y de la página (botón «Guardar», Ctrl/⌘+S): un cambio hecho por el canal marca el plano «Sin guardar»
 * como cualquier otro, y se guarda como versión nueva de la biblioteca. Las extensiones no escriben en la biblioteca por su cuenta.
 */
(function () {
  "use strict";
  if (window.TVCad) return;

  class ErrorEditor extends Error {
    constructor(codigo, mensaje, respuesta) {
      super(mensaje);
      this.name = "ErrorEditor";
      this.codigo = codigo;
      this.respuesta = respuesta;
    }
  }

  const oyentes = new Map();
  let ocupaciones = 0;
  const estado = { listo: false, editable: false, plano: null, documento: null };
  let n = 0;
  let cumplir;
  const listo = new Promise((r) => { cumplir = r; });

  function emitir(evento, datos) {
    for (const fn of oyentes.get(evento) || []) {
      try { fn(datos); } catch (err) { console.error(err); }
    }
  }
  function marco() { return document.getElementById("cad-editor"); }
  function canal() {
    const m = marco();
    try { return m && m.contentWindow && m.contentWindow.ocsControl; } catch (_) { return null; }
  }
  const copia = () => ({ listo: estado.listo, editable: estado.editable, plano: estado.plano && { ...estado.plano } });

  // La página del editor (editor.js) avisa con eventos del documento.
  document.addEventListener("tvcad:cargado", async (ev) => {
    const d = ev.detail || {};
    estado.editable = !!d.editable;
    estado.plano = d.plano || estado.plano;
    estado.documento = d.documento || estado.documento;
    const c = canal();
    if (!c) return;
    try { await c.ready; } catch (_) { return; }
    const primera = !estado.listo;
    estado.listo = true;
    if (primera) { cumplir(copia()); emitir("listo", copia()); }
    emitir("cargado", copia());
  });
  document.addEventListener("tvcad:cambio", (ev) => emitir("cambio", { sucio: !!(ev.detail && ev.detail.sucio) }));
  document.addEventListener("tvcad:guardado", (ev) => {
    const d = ev.detail || {};
    if (estado.plano) { estado.plano.revision = d.revision; estado.plano.formato = d.formato; }
    emitir("guardado", { revision: d.revision, formato: d.formato });
  });
  document.addEventListener("tvcad:editable", (ev) => { estado.editable = !!(ev.detail && ev.detail.editable); });

  // Registro corto de lo que las extensiones y el modo táctil le piden al editor (las últimas 60 peticiones que no son lecturas de estado,
  // con la revisión que devolvió), y de cuándo el plano pasó a «Sin guardar»: para encontrar qué cambió un plano que solo se miró.
  const registro = [];
  const anotar = (x) => { registro.push({ t: Math.round(performance.now()), ...x }); if (registro.length > 80) registro.shift(); if (x.sucio) console.info("CAD: plano con cambios sin guardar", registro.slice(-12)); };
  async function pedir(peticion, { tiempoMs = 30000, sondeoMs } = {}) {
    if (!estado.listo) throw new ErrorEditor("no_listo", "El editor todavía no abre el plano. Espera a TVCad.listo.");
    const c = canal();
    if (!c || typeof c.request !== "function") throw new ErrorEditor("sin_canal", "El editor no tiene canal de control.");
    let respuesta;
    try {
      n += 1;
      const sobre = { protocol: 1, request_id: `tvcad-${Date.now().toString(36)}-${n}`, ...peticion };
      if (sobre.document_id === undefined && estado.documento && !["state", "new", "open", "capabilities"].includes(sobre.op)) sobre.document_id = estado.documento;
      respuesta = await c.request(sobre, sondeoMs ? { timeoutMs: tiempoMs, pollMs: sondeoMs } : { timeoutMs: tiempoMs });
    } catch (err) {
      throw new ErrorEditor("tiempo", "El editor no respondió a tiempo.", undefined);
    }
    if (!["state", "snap", "query", "measure", "layers", "layouts", "extents"].includes(peticion.op)) {
      anotar({ op: peticion.op, cmd: peticion.cmd || peticion.name || peticion.action || "", ok: !(respuesta && respuesta.ok === false), revision: respuesta && respuesta.revision });
    }
    if (respuesta && respuesta.ok === false) throw new ErrorEditor("rechazada", respuesta.error || respuesta.code || "El editor rechazó la petición.", respuesta);
    return respuesta;
  }

  /** Pantalla ↔ mundo del editor (ver la cabecera). Pura: con el mismo `state` y el mismo marco da siempre lo mismo. */
  function vistaDe(st) {
    const c = st.camera, [vw, vh] = st.viewport_size || [0, 0];
    let s = 1, ox = 0, oy = 0, w, h;
    const r = st.viewport_rect;
    const conRect = Array.isArray(r) && r.length === 4 && r.every(Number.isFinite);
    if (conRect) {
      // la fórmula del editor (TRESVIZO.md, «Where the drawing area is»): k por px de viewport_size, screen_scale px CSS por cada uno
      [ox, oy, w, h] = r;
      s = Number.isFinite(st.screen_scale) && st.screen_scale > 0 ? st.screen_scale : (vw > 0 ? w / vw : 1);
    } else {
      try {
        const lienzo = marco().contentDocument.querySelector("canvas");
        const rc = lienzo.getBoundingClientRect();
        if (vw > 0 && rc.width > 0) s = rc.width / vw;
        ox = rc.left; oy = rc.top;
      } catch (_) { /* sin acceso al marco: escala 1 */ }
      w = vw * s; h = vh * s;
    }
    const cx = c.target[0], cy = c.target[1];
    const k = conRect ? (2 * c.distance * Math.tan(c.fov_y / 2)) / Math.max(1, vh) / s : (2 * c.distance * Math.tan(c.fov_y / 2)) / Math.max(1, h);
    const aMarco = (x, y) => [ox + w / 2 + (x - cx) / k, oy + h / 2 - (y - cy) / k];
    const deMarco = (px, py) => [cx + (px - ox - w / 2) * k, cy - (py - oy - h / 2) * k];
    const borde = () => { const m = marco(); const b = m ? m.getBoundingClientRect() : { left: 0, top: 0 }; return [b.left, b.top]; };
    return { cx, cy, k, w, h, ox, oy, s, aMarco, deMarco,
      aPagina: (x, y) => { const [bx, by] = borde(), [px, py] = aMarco(x, y); return [bx + px, by + py]; },
      dePagina: (px, py) => { const [bx, by] = borde(); return deMarco(px - bx, py - by); } };
  }

  function barra() {
    const b = document.getElementById("cad-extensiones");
    if (b) b.hidden = false;
    return b;
  }

  window.TVCad = Object.freeze({
    version: 1, ErrorEditor, listo,
    estaListo: () => estado.listo,
    estado: copia,
    pedir,
    ejecutar: (cmd, opciones) => pedir({ op: "run", cmd: String(cmd) }, opciones),
    barra,
    vistaDe,
    vista: async () => vistaDe(await pedir({ op: "state" })),
    ocupar() { ocupaciones += 1; },
    soltar() { ocupaciones = Math.max(0, ocupaciones - 1); },
    ocupado: () => ocupaciones > 0,
    anotar,
    registro: () => registro.slice(),
    on(evento, fn) { if (!oyentes.has(evento)) oyentes.set(evento, new Set()); oyentes.get(evento).add(fn); },
    off(evento, fn) { (oyentes.get(evento) || new Set()).delete(fn); },
  });
})();

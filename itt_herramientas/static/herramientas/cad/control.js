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
 *   await TVCad.pedir(peticion, { tiempoMs = 30000 })
 *                                      Manda una petición del canal (`{op: "state"}`, `{op: "run", cmd: "LINE 0,0 10,10"}`,
 *                                      `{op: "entities_set", …}`) y devuelve la respuesta final. Completa lo que falte del sobre del
 *                                      protocolo: `protocol: 1`, un `request_id` único y el `document_id` del plano abierto.
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

  async function pedir(peticion, { tiempoMs = 30000 } = {}) {
    if (!estado.listo) throw new ErrorEditor("no_listo", "El editor todavía no abre el plano. Espera a TVCad.listo.");
    const c = canal();
    if (!c || typeof c.request !== "function") throw new ErrorEditor("sin_canal", "El editor no tiene canal de control.");
    let respuesta;
    try {
      n += 1;
      const sobre = { protocol: 1, request_id: `tvcad-${Date.now().toString(36)}-${n}`, ...peticion };
      if (sobre.document_id === undefined && estado.documento && !["state", "new", "open", "capabilities"].includes(sobre.op)) sobre.document_id = estado.documento;
      respuesta = await c.request(sobre, { timeoutMs: tiempoMs });
    } catch (err) {
      throw new ErrorEditor("tiempo", "El editor no respondió a tiempo.", undefined);
    }
    if (respuesta && respuesta.ok === false) throw new ErrorEditor("rechazada", respuesta.error || respuesta.code || "El editor rechazó la petición.", respuesta);
    return respuesta;
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
    ocupar() { ocupaciones += 1; },
    soltar() { ocupaciones = Math.max(0, ocupaciones - 1); },
    ocupado: () => ocupaciones > 0,
    on(evento, fn) { if (!oyentes.has(evento)) oyentes.set(evento, new Set()); oyentes.get(evento).add(fn); },
    off(evento, fn) { (oyentes.get(evento) || new Set()).delete(fn); },
  });
})();

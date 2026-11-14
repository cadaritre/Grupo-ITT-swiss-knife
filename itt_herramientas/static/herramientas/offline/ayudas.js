/* Ayudas de interfaz comunes a las pantallas de Herramientas (cotizaciones, reportes, croquis…):
 *   ITT.ui.h(etiqueta, props, ...hijos)   crea un elemento (sin innerHTML: el texto nunca se interpreta como HTML)
 *   ITT.ui.limpiar(nodo)                  vacía un nodo
 *   ITT.ui.mensaje(id, texto, tipo)       aviso en un <p id> con role=alert
 *   ITT.ui.confirmar(texto, opciones)     cuadro de confirmación (plantilla pwa/_confirmar.html), sustituye a confirm()/prompt()
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const $ = (id) => document.getElementById(id);

  function h(etiqueta, props, ...hijos) {
    const e = document.createElement(etiqueta);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === false || v == null) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (k in e && k !== "list") e[k] = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const hijo of hijos.flat()) if (hijo != null) e.append(hijo);
    return e;
  }
  const limpiar = (n) => { while (n.firstChild) n.firstChild.remove(); };

  function mensaje(id, texto, tipo) {
    const m = $(id);
    m.textContent = texto || "";
    m.hidden = !texto;
    m.dataset.tipo = tipo || "";
    if (texto) m.scrollIntoView({ block: "nearest" });
  }

  /** Resuelve true/false, o el texto escrito si `opciones.entrada` es una cadena (aparece un campo de texto). */
  function confirmar(texto, opciones) {
    const o = Object.assign({ aceptar: "Aceptar", cancelar: "Cancelar", entrada: null, etiqueta: "Nombre", peligro: false, soloAviso: false }, opciones);
    const dlg = $("dlg-confirmar");
    $("confirmar-texto").textContent = texto;
    $("confirmar-campo").hidden = o.entrada === null;
    $("confirmar-etiqueta").textContent = o.etiqueta;
    $("confirmar-entrada").value = o.entrada || "";
    $("confirmar-si").textContent = o.aceptar;
    $("confirmar-si").classList.toggle("tv-boton--peligro", o.peligro);
    $("confirmar-no").textContent = o.cancelar;
    $("confirmar-no").hidden = o.soloAviso;
    $("confirmar-no").onclick = () => dlg.close("no");
    return new Promise((resolver) => {
      dlg.returnValue = "no";
      dlg.addEventListener("close", () => resolver(dlg.returnValue === "si" ? (o.entrada === null ? true : $("confirmar-entrada").value.trim()) : false), { once: true });
      dlg.showModal();
    });
  }

  ITT.ui = { h, limpiar, mensaje, confirmar };
})(typeof self !== "undefined" ? self : this);

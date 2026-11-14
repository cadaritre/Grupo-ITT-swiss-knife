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

  /** Aviso flotante, pegado encima de la barra de acciones de abajo (donde se tocó), no al inicio de la página. Se retira solo. */
  function mensaje(id, texto, tipo) {
    const m = $(id);
    m.textContent = texto || "";
    m.hidden = !texto;
    m.dataset.tipo = tipo || "";
    m.classList.add("itt-aviso--flotante");
    clearTimeout(m._retirar);
    if (!texto) return;
    const barra = [...document.querySelectorAll(".cot-barra, .cq-barra")].find((b) => b.offsetParent !== null && getComputedStyle(b).position !== "static");
    const alto = barra ? Math.max(0, innerHeight - barra.getBoundingClientRect().top) : 0;
    m.style.setProperty("--itt-flota", `${Math.round(alto + 8)}px`);
    m._retirar = setTimeout(() => { m.hidden = true; }, tipo === "error" ? 12000 : 6000);
  }

  /** Resuelve true/false, o el texto escrito si `opciones.entrada` es una cadena (aparece un campo de texto), o "alt" si se eligió `opciones.alternativa` (tercer botón). */
  function confirmar(texto, opciones) {
    const o = Object.assign({ aceptar: "Aceptar", cancelar: "Cancelar", entrada: null, etiqueta: "Nombre", peligro: false, soloAviso: false, alternativa: "" }, opciones);
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
    const alt = $("confirmar-alt");
    if (alt) { alt.hidden = !o.alternativa; alt.textContent = o.alternativa; alt.onclick = () => dlg.close("alt"); }
    return new Promise((resolver) => {
      dlg.returnValue = "no";
      dlg.addEventListener("close", () => resolver(dlg.returnValue === "alt" ? "alt" : dlg.returnValue === "si" ? (o.entrada === null ? true : $("confirmar-entrada").value.trim()) : false), { once: true });
      dlg.showModal();
    });
  }

  /** Las fechas de los documentos se guardan como dd/mm/aaaa (igual que el escritorio); la pantalla usa el selector de fecha del sistema, como los reportes. */
  const fechaAIso = (es) => { const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(es || "").trim()); return m ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}` : ""; };
  const fechaAEs = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || "")); return m ? `${m[3]}/${m[2]}/${m[1]}` : ""; };

  ITT.ui = { h, limpiar, mensaje, confirmar, fechaAIso, fechaAEs };
})(typeof self !== "undefined" ? self : this);

/* Ayudas de interfaz comunes a las pantallas de Herramientas (cotizaciones, reportes, croquis…):
 *   ITT.ui.h(etiqueta, props, ...hijos)   crea un elemento (sin innerHTML: el texto nunca se interpreta como HTML)
 *   ITT.ui.limpiar(nodo)                  vacía un nodo
 *   ITT.ui.mensaje(id, texto, tipo)       aviso en un <p id> con role=alert
 *   ITT.ui.confirmar(texto, opciones)     cuadro de confirmación (plantilla pwa/_confirmar.html), sustituye a confirm()/prompt()
 *   ITT.ui.icono(nombre, etiqueta)        icono de Lucide clonado de <template id="itt-iconos"> (pwa/_iconos.html)
 *   ITT.ui.boton(props, icono, texto)     botón .tv-boton con icono; sin texto lleva `etiqueta` como nombre accesible
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

  /** Icono de Lucide (decorativo salvo que lleve etiqueta). Los SVG vienen de la plantilla, no del JS. */
  function icono(nombre, etiqueta) {
    const molde = document.getElementById("itt-iconos");
    const origen = molde && molde.content.querySelector(`[data-i="${nombre}"] svg`);
    if (!origen) return document.createTextNode("");
    const svg = origen.cloneNode(true);
    if (etiqueta) { svg.removeAttribute("aria-hidden"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", etiqueta); }
    return svg;
  }
  /** Botón con icono. `props.etiqueta` sin texto: botón solo de icono, con nombre accesible y título. */
  function boton(props, nombreIcono, texto) {
    const p = Object.assign({ type: "button" }, props);
    const etiqueta = p.etiqueta; delete p.etiqueta;
    if (!texto && etiqueta) { p["aria-label"] = etiqueta; p.title = etiqueta; p.class = `${p.class || "tv-boton tv-boton--secundario"} itt-boton-icono`; }
    return h("button", p, nombreIcono ? icono(nombreIcono) : null, texto ? h("span", {}, texto) : null);
  }

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
    // Con barra: la distancia ya incluye la banda de pendientes que pudiera haber debajo. Sin barra: el CSS se coloca sobre la banda.
    if (barra) m.style.setProperty("--itt-flota", `${Math.round(Math.max(0, innerHeight - barra.getBoundingClientRect().top) + 8)}px`);
    else m.style.removeProperty("--itt-flota");
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

  /** Fecha corta en es-MX («14 nov 2026»). */
  function fechaCorta(valor) {
    const d = typeof valor === "number" ? new Date(valor) : new Date(String(valor || ""));
    return isNaN(d) ? "" : d.toLocaleDateString("es-MX", { day: "numeric", month: "short", year: "numeric" }).replace(/\./g, "");
  }
  /** Quién hizo el documento: lo que fijó el servidor al crearlo o, si aún no se sube, la cuenta de este teléfono. */
  function autorDe(reg, propio) {
    if (reg && reg.autor) return { nombre: reg.autor.nombre || "", correo: reg.autor.correo || "", creada: reg.autor.creada };
    if (propio) return { nombre: propio.nombre || "", correo: propio.correo || "", creada: reg ? reg.creada : Date.now() };
    return null;
  }
  /** Pinta la línea de autoría (nombre, correo, fecha de creación y «Modificado por») en `nodo`; la oculta si no se sabe. */
  function autoria(nodo, reg, propio) {
    if (!nodo) return;
    limpiar(nodo);
    const a = autorDe(reg, propio);
    nodo.hidden = !a;
    if (!a) return;
    nodo.append(icono("user-round"), h("span", {}, h("strong", {}, a.nombre || a.correo || "Cuenta eliminada"), a.correo && a.nombre ? " " : null,
      a.correo && a.nombre ? h("span", { class: "itt-autoria__correo" }, a.correo) : null),
      h("span", { class: "itt-autoria__fecha" }, `Creado ${fechaCorta(a.creada)}`),
      reg && reg.modificado && reg.modificado.nombre ? h("span", { class: "itt-autoria__fecha", "data-modificado": "1" }, `Modificad${reg.tipo === "cotizacion" ? "a" : "o"} por ${reg.modificado.nombre} · ${fechaCorta(reg.modificado.fecha)}`) : null);
  }
  /** Texto corto de autoría para una tarjeta de la lista: «Ana López · modificado por Beto · 16 nov 2026» (fecha del último cambio). */
  function autoriaCorta(reg, propio) {
    const a = autorDe(reg, propio);
    if (!a) return "";
    const m = reg && reg.modificado;
    const otro = m && m.nombre && m.nombre !== a.nombre ? `modificad${reg.tipo === "cotizacion" ? "a" : "o"} por ${m.nombre}` : "";
    return [a.nombre || a.correo, otro, fechaCorta(m && m.fecha ? m.fecha : a.creada)].filter(Boolean).join(" · ");
  }

  ITT.ui = { h, limpiar, mensaje, confirmar, fechaAIso, fechaAEs, fechaCorta, icono, boton, autoria, autoriaCorta };
})(typeof self !== "undefined" ? self : this);

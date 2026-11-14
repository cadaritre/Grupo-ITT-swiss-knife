/* Editor de cotizaciones (mobile-first, sin conexión). Datos: IndexedDB (borradores.js). Totales: totales.js (misma aritmética que Python).
 * Rutas con hash: #/ lista · #/c/<id> editor · #/machotes.
 */
(function () {
  "use strict";
  const ITT = self.ITT;
  const $ = (id) => document.getElementById(id);
  const raiz = $("cot");
  const urls = {};
  for (const a of raiz.attributes) if (a.name.startsWith("data-url-")) urls[a.name.slice(9)] = a.value;
  const PDFJS = raiz.dataset.pdfjs;
  const PDFJS_WORKER = raiz.dataset.pdfjsWorker;
  const T = ITT.totales;

  let ctx = null;                 // contexto del servidor (o el guardado)
  let reg = null;                 // registro abierto en el editor
  let temporizador = null;        // guardado diferido
  let conceptoAbierto = -1;
  let conceptoSinEditar = false;                     // concepto recién agregado y todavía sin tocar: «Cancelar» lo descarta
  let pdfActual = null;           // {blob, nombre}
  let vistaPrevia = null;

  // -- Utilidades -----------------------------------------------------------------------------------------------------------
  const { h, limpiar, confirmar, icono, boton } = ITT.ui;
  const url = (nombre, id) => ITT.cot.url(nombre, id);
  const enLinea = () => navigator.onLine;

  const mensaje = (texto, tipo) => ITT.ui.mensaje("cot-mensaje", texto, tipo);


  function numero(texto) {
    let t = String(texto).trim().replace(/\s/g, "").replace(/^\$/, "");
    if (t === "") return NaN;
    if (t.includes(",") && t.includes(".")) t = t.replace(/,/g, "");
    else if (t.includes(",")) t = t.replace(",", ".");
    return /^-?\d*\.?\d+$|^-?\d+\.$/.test(t) ? parseFloat(t) : NaN;
  }
  const fmtNum = (n) => (Number.isInteger(n) ? String(n) : String(parseFloat(n.toPrecision(12))));
  const dinero = (v) => T.dinero(v);

  // -- Estado de guardado -------------------------------------------------------------------------------------------------------
  function estadoGuardado() {
    const e = $("cot-estado-guardado");
    limpiar(e);
    if (!reg) return;
    const [ico, texto] = reg.sucio ? (enLinea() ? ["cloud-upload", "Guardando…"] : ["cloud-off", "Guardado en este teléfono"]) : ["cloud-check", "Guardado"];
    e.append(icono(ico), h("span", {}, texto));
    e.dataset.estado = reg.sucio ? "pendiente" : "sincronizado";
  }

  function programarGuardado() {
    reg.sucio = true;
    estadoGuardado();
    clearTimeout(temporizador);
    temporizador = setTimeout(guardarYa, 300);
  }
  async function guardarYa() {
    clearTimeout(temporizador);
    temporizador = null;
    if (!reg) return;
    const snapshot = ITT.cot.copiar(reg.datos);
    const nuevo = await ITT.cot.guardar(reg.id, snapshot, { miniaturas: reg.miniaturas, aviso: reg.aviso });
    if (nuevo && reg && nuevo.id === reg.id) { reg.actualizada = nuevo.actualizada; }
    estadoGuardado();
    ITT.refrescarCola && (await ITT.refrescarCola());
    if (enLinea() && ITT.subirAhora) ITT.subirAhora();
  }
  addEventListener("pagehide", () => { if (temporizador) guardarYa(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && temporizador) guardarYa(); });

  // -- Totales y avisos ---------------------------------------------------------------------------------------------------------
  const CAMPOS_TEXTO = ["client_name", "contact", "location", "project_title", "notes", "delivery_time", "payment_terms"];

  function recalcular() {
    const t = T.totales(reg.datos);
    $("cot-subtotal").textContent = dinero(t.subtotal);
    $("cot-fila-iva").hidden = reg.datos.include_vat === false;
    $("cot-iva").textContent = dinero(t.iva);
    $("cot-total").textContent = dinero(t.total);
    $("cot-total").title = `${reg.datos.currency || ""}`;
    $("resumen-conceptos").textContent = reg.datos.items.length ? `${reg.datos.items.length} · ${dinero(t.subtotal)}` : "";
    $("resumen-imagenes").textContent = reg.datos.images.length ? String(reg.datos.images.length) : "";
    document.documentElement.dataset.cotTotal = String(t.total);
    return t;
  }

  function avisosRecomendados() {
    const d = reg.datos;
    const v = (k) => String(d[k] || "").trim();
    const falta = { quote_number: !v("quote_number"), client_name: !v("client_name"), contact: !v("contact"), location: !v("location"), project_title: !v("project_title") };
    const sinContacto = !v("email") && !v("phone");
    falta.email = sinContacto; falta.phone = sinContacto;
    for (const [campo, pendiente] of Object.entries(falta)) {
      const caja = document.querySelector(`[data-campo="${campo}"]`);
      if (!caja) continue;
      caja.dataset.pendiente = pendiente ? "1" : "";
      const etiqueta = caja.querySelector("label");
      let marca = etiqueta.querySelector(".cot-marca");
      if (!marca) { marca = h("span", { class: "cot-marca" }); etiqueta.append(marca); }
      marca.textContent = pendiente ? (campo === "email" || campo === "phone" ? "Correo o teléfono" : "Recomendado") : "";
      marca.hidden = !pendiente;
    }
    const n = [falta.quote_number, falta.client_name, falta.contact, sinContacto, falta.location, falta.project_title].filter(Boolean).length;
    const s = $("cot-recomendados");
    limpiar(s);
    s.append(icono(n ? "triangle-alert" : "circle-check"), h("span", {}, n ? `Faltan ${n} dato${n === 1 ? "" : "s"} recomendado${n === 1 ? "" : "s"}` : "Datos completos"));
    s.dataset.estado = n ? "pendiente" : "listo";
    $("resumen-datos").textContent = n ? `Faltan ${n}` : [reg.datos.client_name].filter(Boolean).join("");
  }

  function indicadorOrtografia() {
    const b = $("cot-ortografia-estado");
    const ok = !!reg.datos.spelling_checked;
    limpiar(b);
    b.append(icono(ok ? "circle-check" : "spell-check"), h("span", {}, ok ? "Ortografía revisada" : "Revisar ortografía"));
    b.dataset.estado = ok ? "listo" : "pendiente";
  }
  function ortografiaSucia() { if (reg.datos.spelling_checked) { reg.datos.spelling_checked = false; indicadorOrtografia(); } }

  function titulo() {
    const d = reg.datos;
    $("cot-titulo-editor").textContent = [d.quote_number, d.project_title].filter(Boolean).join(" · ") || "Cotización";
  }

  // -- Lista -------------------------------------------------------------------------------------------------------------------
  async function pintarLista() {
    const lista = $("cot-lista");
    const filtro = $("cot-buscar").value.trim().toLowerCase();
    const todos = (await ITT.cot.listar()).sort((a, b) => String(b.actualizada || b.creada || "").localeCompare(String(a.actualizada || a.creada || "")));
    const visibles = todos.filter((b) => !filtro || [b.datos.quote_number, b.datos.client_name, b.datos.project_title].join(" ").toLowerCase().includes(filtro));
    limpiar(lista);
    $("cot-lista-vacia").hidden = todos.length > 0;
    $("cot-buscador").hidden = todos.length === 0;
    $("cot-sin-resultados").hidden = !(todos.length && !visibles.length);
    for (const b of visibles) {
      const t = T.totales(b.datos);
      const folio = b.datos.quote_number || "Sin folio";
      const estado = b.error ? h("span", { class: "itt-estado", "data-estado": "error" }, icono("triangle-alert"), h("span", {}, "No se pudo subir"))
        : b.sucio ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono("cloud-off"), h("span", {}, "Sin subir")) : null;
      lista.append(h("li", { class: "cot-tarjeta", "data-id": b.id, "data-sucio": b.sucio ? "1" : "" },
        h("a", { class: "cot-tarjeta__enlace", href: `#/c/${b.id}` },
          h("span", { class: "cot-tarjeta__folio" }, folio),
          h("strong", { class: "cot-tarjeta__titulo" }, b.datos.project_title || "Sin título"),
          h("span", { class: "cot-tarjeta__cliente" }, b.datos.client_name || "Sin cliente"),
          h("span", { class: "cot-tarjeta__autor" }, ITT.ui.autoriaCorta(b, ctx && ctx.autor))),
        h("div", { class: "cot-tarjeta__pie" },
          h("span", { class: "cot-tarjeta__total" }, dinero(t.total), h("small", {}, ` ${b.datos.currency || ""}`)),
          estado,
          b.aviso ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono("copy"), h("span", {}, "Copia por conflicto")) : null,
          h("span", { class: "cot-tarjeta__espacio" }),
          boton({ onclick: () => duplicarDesde(b), etiqueta: `Duplicar ${folio}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "copy"),
          boton({ onclick: () => borrarRegistro(b), etiqueta: `Eliminar ${folio}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "trash-2"))));
    }
  }

  async function nueva() {
    if (!ctx) return mensaje("Sin conexión. Abre Cotizaciones una vez con señal para poder trabajar sin ella.", "error");
    const r = await ITT.cot.nuevo(ctx);
    location.hash = `#/c/${r.id}`;
  }
  async function duplicarDesde(b) {
    if (!ctx) return;
    const r = await ITT.cot.duplicar(ctx, b);
    await ITT.refrescarCola();
    mensaje(`Copia creada: ${r.datos.quote_number}.`, "info");
    await pintarLista();
    ITT.subirAhora();
  }
  async function borrarRegistro(b) {
    if (!(await confirmarBorrado(b))) return;
    await ITT.cot.borrar(b.id);
    await pintarLista();
    ITT.subirAhora();
  }

  // -- Editor -----------------------------------------------------------------------------------------------------------------------
  function pintarCampos() {
    const d = reg.datos;
    for (const e of raiz.querySelectorAll("[data-dato]")) {
      const k = e.dataset.dato;
      if (e.type === "checkbox") e.checked = d[k] !== false;
      else if (e.type === "date") e.value = ITT.ui.fechaAIso(d[k]);
      else e.value = e.dataset.numero ? fmtNum(Number(d[k] ?? 0)) : (d[k] ?? "");
    }
    $("f-idioma").value = d.language || "es";
    $("f-vat_rate").disabled = d.include_vat === false;
    const aviso = $("cot-aviso-doc");
    limpiar(aviso);
    aviso.hidden = !reg.aviso;
    if (reg.aviso) {
      aviso.append(reg.aviso, " ", h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: async () => { reg.aviso = ""; aviso.hidden = true; await guardarYa(); } }, "Entendido"));
    }
    ITT.ui.autoria($("cot-autor"), reg, ctx && ctx.autor);
    titulo(); avisosRecomendados(); indicadorOrtografia(); pintarConceptos(); pintarImagenes(); recalcular(); estadoGuardado();
    $("mas-editable").setAttribute("download", nombreEditable());
  }

  function alEscribir(e) {
    const k = e.target.dataset.dato;
    if (!k || !reg) return;
    const d = reg.datos;
    if (e.target.type === "checkbox") { d[k] = e.target.checked; $("f-vat_rate").disabled = !e.target.checked; }
    else if (e.target.dataset.numero) {
      const n = numero(e.target.value);
      const limites = { vat_rate: [0, 100], advance_percent: [0, 100], validity_days: [0, 3650] }[k];
      const bueno = Number.isFinite(n) && n >= limites[0] && n <= limites[1];
      e.target.setAttribute("aria-invalid", bueno ? "false" : "true");
      if (!bueno) return;
      d[k] = k === "validity_days" ? Math.round(n) : n;
    } else if (e.target.type === "date") d[k] = ITT.ui.fechaAEs(e.target.value);
    else if (k === "currency") d[k] = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);
    else d[k] = e.target.value;
    if (CAMPOS_TEXTO.includes(k) || k === "prepared_by") ortografiaSucia();
    titulo(); avisosRecomendados(); recalcular(); programarGuardado();
    $("mas-editable").setAttribute("download", nombreEditable());
    programarVistaPrevia();
  }

  function pintarConceptos() {
    const lista = $("cot-conceptos");
    limpiar(lista);
    const items = reg.datos.items;
    $("cot-sin-conceptos").hidden = items.length > 0;
    const t = T.totales(reg.datos);
    items.forEach((it, i) => {
      lista.append(h("li", { class: "cot-concepto", "data-i": String(i) },
        h("button", { type: "button", class: "cot-concepto__abrir", onclick: () => abrirConcepto(i), "aria-label": `Editar concepto ${i + 1}: ${it.title || "Sin título"}` },
          h("span", { class: "cot-concepto__num" }, String(i + 1)),
          h("span", { class: "cot-concepto__texto" },
            h("strong", {}, it.title || "Sin título"),
            h("span", { class: "tv-tenue" }, `${fmtNum(it.quantity)} ${it.unit || ""} × ${dinero(it.unit_price)}`, it.subitems.length ? ` · ${it.subitems.length} alcance${it.subitems.length === 1 ? "" : "s"}` : "")),
          h("span", { class: "cot-concepto__importe" }, dinero(t.importes[i]))),
        h("div", { class: "cot-concepto__mover" },
          boton({ onclick: () => mover(i, -1), disabled: i === 0, etiqueta: "Subir concepto", class: "tv-boton tv-boton--secundario itt-boton-plano" }, "chevron-up"),
          boton({ onclick: () => mover(i, 1), disabled: i === items.length - 1, etiqueta: "Bajar concepto", class: "tv-boton tv-boton--secundario itt-boton-plano" }, "chevron-down"))));
    });
  }
  function mover(i, delta) {
    const items = reg.datos.items;
    const j = i + delta;
    if (j < 0 || j >= items.length) return;
    [items[i], items[j]] = [items[j], items[i]];
    pintarConceptos(); programarGuardado(); programarVistaPrevia();
  }
  function cambioEnConceptos() { ortografiaSucia(); pintarConceptos(); recalcular(); programarGuardado(); programarVistaPrevia(); }

  // Diálogo de concepto: se aplica al escribir («Aplicado automático», como en el escritorio).
  function abrirConcepto(i, esNuevo = false) {
    conceptoAbierto = i;
    conceptoSinEditar = esNuevo;
    $("c-quitar").hidden = esNuevo;                    // un concepto nuevo no tiene qué quitar: se cancela
    $("c-cerrar").textContent = esNuevo ? "Cancelar" : "Cerrar";
    const it = reg.datos.items[i];
    $("dlg-concepto-titulo").textContent = `Concepto ${i + 1}`;
    $("c-title").value = it.title; $("c-description").value = it.description; $("c-subitems").value = it.subitems.join("\n");
    $("c-unit").value = it.unit; $("c-quantity").value = fmtNum(it.quantity); $("c-unit_price").value = fmtNum(it.unit_price);
    $("c-error").hidden = true;
    contarAlcances(); importeConcepto();
    $("dlg-concepto").showModal();
  }
  function contarAlcances() { const n = lineas($("c-subitems").value).length; $("c-subitems-cuenta").textContent = n ? `· ${n}` : ""; }
  const lineas = (texto) => texto.split("\n").map((l) => l.replace(/^\s*(?:[•\-–—]|\d+[.)])\s*/, "").trim()).filter(Boolean);
  function importeConcepto() {
    const q = numero($("c-quantity").value), p = numero($("c-unit_price").value);
    $("c-importe").textContent = Number.isFinite(q) && Number.isFinite(p) ? dinero(T.redondear2(q * p)) : "—";
  }
  function aplicarConcepto() {
    if (conceptoAbierto < 0 || !reg) return;
    if (conceptoSinEditar) { conceptoSinEditar = false; $("c-quitar").hidden = false; $("c-cerrar").textContent = "Cerrar"; }
    const it = reg.datos.items[conceptoAbierto];
    const q = numero($("c-quantity").value), p = numero($("c-unit_price").value);
    const err = $("c-error");
    let problema = "";
    if (!Number.isFinite(q) || q < 0 || q > 1e9) problema = "La cantidad debe ser un número entre 0 y 1,000,000,000.";
    else if (!Number.isFinite(p) || p < 0 || p > 1e11) problema = "El precio debe ser un número entre 0 y 100,000,000,000.";
    $("c-quantity").setAttribute("aria-invalid", String(!Number.isFinite(q) || q < 0 || q > 1e9));
    $("c-unit_price").setAttribute("aria-invalid", String(!Number.isFinite(p) || p < 0 || p > 1e11));
    err.hidden = !problema; err.textContent = problema;
    it.title = $("c-title").value; it.description = $("c-description").value; it.subitems = lineas($("c-subitems").value); it.unit = $("c-unit").value.trim() || "Lote";
    if (!problema) { it.quantity = q; it.unit_price = p; }
    contarAlcances(); importeConcepto(); cambioEnConceptos();
  }
  async function quitarConcepto() {
    if (!(await confirmar(`¿Quitar «${reg.datos.items[conceptoAbierto].title || `Concepto ${conceptoAbierto + 1}`}» de la cotización?`, { aceptar: "Quitar concepto", peligro: true }))) return;
    reg.datos.items.splice(conceptoAbierto, 1);
    conceptoAbierto = -1;
    $("dlg-concepto").close();
    cambioEnConceptos();
  }
  function conceptoNuevo(base) {
    const idioma = reg.datos.language;
    const it = Object.assign({ title: idioma === "en" ? "New item" : "Nuevo concepto", description: "", subitems: [], unit: idioma === "en" ? "Lot" : "Lote", quantity: 1, unit_price: 0 }, ITT.cot.copiar(base || {}));
    reg.datos.items.push(it);
    cambioEnConceptos();
    return reg.datos.items.length - 1;
  }
  // -- Machotes ---------------------------------------------------------------------------------------------------------------------
  // Paridad con el escritorio: catálogo de fábrica (ES/EN) y machotes propios; insertar, reemplazar el concepto abierto («Cargar para
  // editar»), guardar el concepto como machote, y administrar (editar, renombrar, restaurar, eliminar, importar, exportar todo o uno).
  const SIN_RED_MACHOTES = "Sin conexión. Guardar o cambiar machotes necesita señal.";
  const propios = () => (ctx && ctx.machotes) || [];
  const catalogo = (idioma) => Object.entries(((ctx && ctx.plantillas) || {})[idioma] || {}).map(([nombre, concepto]) => ({ nombre, concepto }));
  const idiomaCorto = (i) => (i === "en" ? "EN" : "ES");

  async function guardarComoMachote() {
    const it = reg.datos.items[conceptoAbierto];
    if (!enLinea()) return mensaje(SIN_RED_MACHOTES, "error");
    const nombre = await confirmar("Ponle un nombre para encontrarlo después.", { entrada: (it.title || "").slice(0, 160), etiqueta: "Nombre del machote", aceptar: "Guardar machote" });
    if (!nombre) return;
    await enviarMachote({ nombre, idioma: reg.datos.language, concepto: ITT.cot.copiar(it) });
  }
  /** Guarda en la cuenta; si el nombre ya existe, pregunta si se reemplaza (como el escritorio) y conserva la versión anterior. */
  async function enviarMachote(cuerpo) {
    try {
      const m = await ITT.cot.pedir(urls.machotes, { metodo: "POST", json: cuerpo });
      await recargarContexto();
      mensaje(`Machote «${m.nombre}» guardado.`, "info");
      return m;
    } catch (e) {
      if (e.clase === "rechazo" && /Ya existe/.test(e.mensaje || "")) {
        const previo = propios().find((x) => x.nombre.toLowerCase() === cuerpo.nombre.trim().toLowerCase());
        if (previo && (await confirmar(`Ya tienes un machote llamado «${previo.nombre}». ¿Reemplazarlo con este concepto? Podrás restaurar la versión anterior.`, { aceptar: "Reemplazar" }))) {
          try { const m = await ITT.cot.pedir(urls.machotes, { metodo: "POST", json: Object.assign({}, cuerpo, { id: previo.id, nombre: previo.nombre }) }); await recargarContexto(); mensaje(`Machote «${m.nombre}» actualizado.`, "info"); return m; }
          catch (e2) { mensaje(e2.mensaje || "No se pudo guardar el machote. Inténtalo de nuevo.", "error"); }
        }
      } else mensaje(e.clase === "red" ? SIN_RED_MACHOTES : e.mensaje || "No se pudo guardar el machote. Inténtalo de nuevo.", "error");
    }
    return null;
  }
  async function recargarContexto() { try { ctx = await ITT.cot.contexto(); } catch (_) { /* se queda el anterior */ } }

  // Selector: «insertar» agrega un concepto; «reemplazar» cambia el concepto abierto (el «Cargar para editar» del escritorio).
  let modoSelector = "insertar";
  function abrirPlantillas(modo) {
    modoSelector = modo === "reemplazar" ? "reemplazar" : "insertar";
    $("dlg-plantillas-titulo").textContent = modoSelector === "reemplazar" ? "Reemplazar con machote" : "Insertar machote";
    $("p-buscar").value = "";
    pintarPlantillas();
    $("dlg-plantillas").showModal();
    if (matchMedia("(pointer: fine)").matches) $("p-buscar").focus();
  }
  function filaPlantilla(nombre, concepto, extra, alElegir) {
    const precio = Number(concepto.unit_price || 0);
    return h("li", {}, h("button", { type: "button", class: "cot-plantilla", onclick: alElegir },
      h("span", { class: "cot-plantilla__texto" },
        h("strong", {}, nombre, extra ? h("span", { class: "cot-etiqueta" }, extra) : null),
        concepto.title && concepto.title !== nombre ? h("span", { class: "tv-tenue" }, concepto.title) : null),
      h("span", { class: "cot-plantilla__precio" }, precio ? dinero(precio) : "", h("small", {}, concepto.unit ? ` / ${concepto.unit}` : "")),
      icono(modoSelector === "reemplazar" ? "replace" : "plus")));
  }
  function pintarPlantillas() {
    const filtro = $("p-buscar").value.trim().toLowerCase();
    const idioma = reg.datos.language;
    const caja = $("p-lista");
    limpiar(caja);
    if (!ctx) { caja.append(h("div", { class: "itt-vacio" }, icono("cloud-off"), h("p", {}, "Conéctate una vez para descargar los machotes."))); return; }
    const coincide = (n, c) => !filtro || `${n} ${c.title} ${c.description}`.toLowerCase().includes(filtro);
    const grupo = (titulo, ico, filas) => {
      if (!filas.length) return;
      caja.append(h("h3", { class: "cot-grupo" }, icono(ico), h("span", {}, titulo), h("span", { class: "cot-grupo__cuenta" }, String(filas.length))));
      caja.append(h("ul", { class: "cot-plantillas__lista" }, filas));
    };
    const mios = propios().filter((m) => coincide(m.nombre, m.concepto)).sort((a, b) => (a.idioma === idioma ? 0 : 1) - (b.idioma === idioma ? 0 : 1) || a.nombre.localeCompare(b.nombre, "es"));
    grupo("Mis machotes", "star", mios.map((m) => filaPlantilla(m.nombre, m.concepto, m.idioma !== idioma ? idiomaCorto(m.idioma) : "", () => elegirPlantilla(m.nombre, m.concepto))));
    grupo(idioma === "en" ? "Catálogo · English" : "Catálogo", "book-open", catalogo(idioma).filter((f) => coincide(f.nombre, f.concepto)).map((f) => filaPlantilla(f.nombre, f.concepto, "", () => elegirPlantilla(f.nombre, f.concepto))));
    if (!caja.children.length) caja.append(h("p", { class: "tv-tenue cot-sin-resultados" }, "Sin resultados."));
  }
  async function elegirPlantilla(nombre, concepto) {
    if (modoSelector === "reemplazar" && conceptoAbierto >= 0) {
      const actual = reg.datos.items[conceptoAbierto];
      if (!(await confirmar(`¿Reemplazar «${actual.title || `Concepto ${conceptoAbierto + 1}`}» con el machote «${nombre}»?`, { aceptar: "Reemplazar" }))) return;
      $("dlg-plantillas").close();
      reg.datos.items[conceptoAbierto] = Object.assign({ title: "", description: "", subitems: [], unit: "Lote", quantity: 1, unit_price: 0 }, ITT.cot.copiar(concepto));
      conceptoSinEditar = false;
      abrirConcepto(conceptoAbierto);
      cambioEnConceptos();
      mensaje(`Concepto reemplazado con «${nombre}».`, "info");
      return;
    }
    $("dlg-plantillas").close();
    if (reg.datos.items.length >= (ctx.limites ? ctx.limites.conceptos : 200)) return mensaje("Llegaste al máximo de conceptos por cotización.", "error");
    conceptoNuevo(concepto);
    mensaje(`Se agregó «${nombre}».`, "info");
    const ultimo = $("cot-conceptos").lastElementChild;
    if (ultimo) { ultimo.scrollIntoView({ block: "nearest" }); ultimo.classList.add("cot-concepto--nuevo"); setTimeout(() => ultimo.classList.remove("cot-concepto--nuevo"), 1200); }
  }

  // Imágenes
  function pintarImagenes() {
    const ul = $("cot-imagenes");
    limpiar(ul);
    const imgs = reg.datos.images;
    $("cot-sin-imagenes").hidden = imgs.length > 0;
    imgs.forEach((im, i) => {
      const sha = (/^captura:([0-9a-f]{64})$/.exec(im.path) || [])[1];
      const mini = sha && reg.miniaturas ? reg.miniaturas[sha] : null;
      const foto = h("img", { alt: "", width: 64, height: 64 });
      if (mini) foto.src = mini;
      else if (sha && enLinea()) { foto.src = url("foto", sha); foto.addEventListener("error", () => foto.removeAttribute("src")); }
      ul.append(h("li", { class: "itt-captura" }, foto,
        h("div", {},
          h("div", { class: "tv-campo" }, h("label", { for: `im-${i}` }, `Pie de la imagen ${i + 1}`),
            h("input", { id: `im-${i}`, type: "text", maxLength: 300, value: im.caption || "", autocomplete: "off", oninput: (e) => { im.caption = e.target.value; ortografiaSucia(); programarGuardado(); } })),
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => { imgs.splice(i, 1); ortografiaSucia(); pintarImagenes(); recalcular(); programarGuardado(); } }, "Quitar"))));
    });
  }
  async function agregarFotos(archivos) {
    const limite = ctx && ctx.limites ? ctx.limites.imagenes : 24;
    for (const archivo of archivos) {
      if (reg.datos.images.length >= limite) { mensaje(`Una cotización admite hasta ${limite} imágenes.`, "error"); break; }
      try {
        const { sha256, miniatura } = await ITT.cot.agregarFoto(reg.id, archivo);
        if (reg.datos.images.some((x) => x.path === `captura:${sha256}`)) continue;
        reg.miniaturas = Object.assign({}, reg.miniaturas, { [sha256]: miniatura });
        reg.datos.images.push({ path: `captura:${sha256}`, caption: "" });
      } catch (e) {
        mensaje(`No se pudo agregar «${archivo.name || "la foto"}». Prueba con otra imagen.`, "error");
      }
    }
    pintarImagenes(); recalcular(); ortografiaSucia(); await guardarYa();
    ITT.subirAhora && ITT.subirAhora();
  }

  // Ortografía
  function camposOrtografia() {
    const d = reg.datos;
    const c = [];
    const mas = (id, etiqueta, valor) => { if (valor && String(valor).trim()) c.push({ id, etiqueta, valor: String(valor) }); };
    mas("client_name", "Nombre / Empresa", d.client_name); mas("contact", "Contacto", d.contact); mas("location", "Localización", d.location);
    mas("project_title", "Título del proyecto", d.project_title); mas("delivery_time", "Tiempo de entrega", d.delivery_time);
    mas("payment_terms", "Forma de pago", d.payment_terms); mas("notes", "Notas", d.notes);
    d.items.forEach((it, i) => {
      mas(`item.${i}.title`, `Concepto ${i + 1}: título`, it.title); mas(`item.${i}.description`, `Concepto ${i + 1}: descripción`, it.description);
      it.subitems.forEach((s, j) => mas(`item.${i}.sub.${j}`, `Concepto ${i + 1}: alcance ${j + 1}`, s));
    });
    d.images.forEach((im, i) => mas(`img.${i}.caption`, `Imagen ${i + 1}: pie`, im.caption));
    return c;
  }
  function aplicarOrtografia(id, valor) {
    const d = reg.datos;
    let m;
    if ((m = /^item\.(\d+)\.title$/.exec(id))) d.items[+m[1]].title = valor;
    else if ((m = /^item\.(\d+)\.description$/.exec(id))) d.items[+m[1]].description = valor;
    else if ((m = /^item\.(\d+)\.sub\.(\d+)$/.exec(id))) d.items[+m[1]].subitems[+m[2]] = valor;
    else if ((m = /^img\.(\d+)\.caption$/.exec(id))) d.images[+m[1]].caption = valor;
    else d[id] = valor;
  }
  async function revisarOrtografia() {
    if (!enLinea()) return mensaje("Sin conexión. Revisa la ortografía cuando vuelva la señal.", "error");
    await guardarYa();
    try {
      const r = await ITT.ortografia.revisar({ idioma: reg.datos.language, campos: camposOrtografia(), aplicar: aplicarOrtografia });
      if (r.cambios) { pintarCampos(); }
      if (r.revisado) {
        reg.datos.spelling_checked = true;
        indicadorOrtografia();
        mensaje(r.problemas ? "Revisión terminada." : "Sin errores de ortografía.", "info");
      }
      programarGuardado();
      programarVistaPrevia();
    } catch (e) { mensaje(e.mensaje || "No se pudo revisar la ortografía.", "error"); }
  }

  // Idioma
  async function cambiarIdioma(nuevo) {
    if (nuevo === reg.datos.language) return;
    if (!enLinea()) { $("f-idioma").value = reg.datos.language; return mensaje("Sin conexión. Cambia el idioma cuando vuelva la señal.", "error"); }
    try {
      const r = await ITT.cot.pedir(urls.traducir, { metodo: "POST", json: { datos: reg.datos, idioma: nuevo } });
      reg.datos = r.datos;
      pintarCampos(); programarGuardado(); programarVistaPrevia();
    } catch (e) { $("f-idioma").value = reg.datos.language; mensaje(e.mensaje || "No se pudo cambiar el idioma.", "error"); }
  }

  // -- PDF y vista previa ----------------------------------------------------------------------------------------------------------
  const limpioNombre = (t, respaldo) => { const l = (t || "").replace(/[<>:"/\\|?*\x00-\x1f]/g, " ").split(/\s+/).filter(Boolean).join(" ").replace(/^[ .]+|[ .]+$/g, ""); return l.slice(0, 90) || respaldo; };
  const nombreEditable = () => (reg ? `EDITABLE_COTIZACION ${limpioNombre(reg.datos.quote_number, "SIN FOLIO")} - ${limpioNombre(reg.datos.project_title, "SERVICIOS")}.json` : "editable.json");

  async function pedirPdf(exportar) {
    const r = await ITT.cot.pedir(urls.pdf + (exportar ? "?exportar=1" : ""), { metodo: "POST", json: { datos: reg.datos }, crudo: true });
    const blob = await r.blob();
    const disp = r.headers.get("Content-Disposition") || "";
    let nombre = `COTIZACION ${limpioNombre(reg.datos.quote_number, "SIN FOLIO")} - ${limpioNombre(reg.datos.project_title, "SERVICIOS")}.pdf`;
    const m = /filename\*=utf-8''([^;]+)/i.exec(disp) || /filename="([^"]+)"/i.exec(disp);
    if (m) { try { nombre = decodeURIComponent(m[1]); } catch (_) { nombre = m[1]; } }
    return { blob, nombre, pendientes: Number(r.headers.get("X-Imagenes-Pendientes") || 0), drive: r.headers.get("X-Drive") === "encolado" };
  }

  const dibujarPdf = (blob, contenedor) => ITT.vistaPdf.dibujar(blob, contenedor, { pdfjs: PDFJS, worker: PDFJS_WORKER });

  async function generarPdf() {
    const d = reg.datos;
    if (!d.items.length) return mensaje("Agrega al menos un concepto para generar el PDF.", "error");
    if (!enLinea()) return mensaje("Sin conexión. Genera el PDF cuando vuelva la señal; tu cotización ya está guardada.", "error");
    if (!d.spelling_checked) {
      const nombre = d.language === "en" ? "inglés" : "español";
      if (!(await confirmar(`Falta revisar la ortografía (${nombre}). ¿Generar el PDF de todos modos?`, { aceptar: "Generar PDF", cancelar: "Revisar primero" }))) return;
    }
    await guardarYa();
    const dlg = $("dlg-pdf");
    $("pdf-estado").textContent = "Generando el PDF…";
    $("pdf-advertencias").hidden = true;
    $("pdf-compartir").disabled = true; $("pdf-descargar").disabled = true;
    limpiar($("pdf-paginas"));
    pdfActual = null;
    dlg.showModal();
    try {
      const r = await pedirPdf(true);                      // el PDF final: va a Drive si la persona lo conectó
      pdfActual = r;
      if (r.pendientes) {
        const a = $("pdf-advertencias");
        a.hidden = false;
        a.textContent = `${r.pendientes} imagen${r.pendientes === 1 ? "" : "es"} aún se ${r.pendientes === 1 ? "está" : "están"} subiendo y no aparece${r.pendientes === 1 ? "" : "n"} en este PDF. Genéralo de nuevo en un momento.`;
        ITT.subirAhora && ITT.subirAhora();
      }
      $("pdf-compartir").disabled = false; $("pdf-descargar").disabled = false;
      $("pdf-estado").textContent = r.drive ? `${r.nombre} · se guardará en tu Drive` : r.nombre;
      await dibujarPdf(r.blob, $("pdf-paginas"));
    } catch (e) {
      $("pdf-estado").textContent = e.mensaje || "No se pudo generar el PDF. Inténtalo de nuevo.";
      if (pdfActual) $("pdf-estado").textContent = `${pdfActual.nombre} · no se puede mostrar aquí, pero puedes compartirlo o descargarlo.`;
    }
  }
  async function compartirPdf() {
    if (!pdfActual) return;
    const r = await ITT.compartirArchivo(pdfActual.blob, pdfActual.nombre, { tipo: "application/pdf", titulo: pdfActual.nombre });
    $("pdf-estado").textContent = r === "descargado" ? `${pdfActual.nombre} · descargado` : pdfActual.nombre;
    document.documentElement.dataset.cotCompartido = r;
  }
  function descargarPdf() {
    if (!pdfActual) return;
    const a = h("a", { href: URL.createObjectURL(pdfActual.blob), download: pdfActual.nombre, rel: "noopener" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }

  // Vista previa lateral (pantallas anchas): se actualiza sola, con retraso, solo con conexión.
  const ancha = () => matchMedia("(min-width: 1000px)").matches;
  function programarVistaPrevia() {
    if (ancha() && reg && !reg.datos.items.length) {
      const caja = $("cot-vp-paginas");
      limpiar(caja);
      caja.append(h("div", { class: "itt-vacio cot-vp-vacia" }, icono("file-text"), h("p", {}, "Agrega un concepto para ver el documento")));
      limpiar($("cot-vp-estado"));
      return;
    }
    if (!ancha() || !enLinea() || !reg || !reg.datos.items.length) return;
    clearTimeout(vistaPrevia);
    vistaPrevia = setTimeout(actualizarVistaPrevia, 1500);
  }
  async function actualizarVistaPrevia() {
    if (!ancha() || !enLinea() || !reg) return;
    const estado = $("cot-vp-estado");
    const poner = (ico, texto, tipo) => { limpiar(estado); if (texto) estado.append(icono(ico), h("span", {}, texto)); estado.dataset.estado = tipo || ""; };
    poner("refresh-cw", "Actualizando…", "cargando");
    $("cot-vp-paginas").setAttribute("aria-busy", "true");
    try { const r = await pedirPdf(); await dibujarPdf(r.blob, $("cot-vp-paginas")); poner("images", r.pendientes ? `${r.pendientes} imagen${r.pendientes === 1 ? "" : "es"} subiendo` : "", r.pendientes ? "pendiente" : ""); }
    catch (e) { poner("triangle-alert", "No se pudo actualizar", "error"); }
    finally { $("cot-vp-paginas").removeAttribute("aria-busy"); }
  }

  // -- Machotes (pantalla «Administrar machotes») -------------------------------------------------------------------------------------
  let coleccion = "propios";
  let machoteEditado = null;          // {id?, tiene_anterior?} del machote abierto en la hoja; null = nuevo
  const fechaCorta = (iso) => { try { return new Date(iso).toLocaleDateString("es-MX", { day: "numeric", month: "short", year: "numeric" }); } catch (_) { return ""; } };

  async function pintarMachotes(recargar = true) {
    if (recargar && enLinea()) await recargarContexto();
    const filtro = $("m-buscar").value.trim().toLowerCase();
    const coincide = (n, c) => !filtro || `${n} ${c.title} ${c.description}`.toLowerCase().includes(filtro);
    for (const b of document.querySelectorAll("[data-coleccion]")) b.setAttribute("aria-pressed", String(b.dataset.coleccion === coleccion));
    $("m-cuenta-propios").textContent = propios().length ? String(propios().length) : "";
    $("m-idioma-caja").hidden = coleccion !== "catalogo";
    const exportar = $("cot-machotes-exportar");
    exportar.setAttribute("href", urls["machotes-exportar"]);
    exportar.hidden = !propios().length;
    const ulP = $("cot-machotes-lista"), ulC = $("cot-catalogo-lista");
    limpiar(ulP); limpiar(ulC);
    ulP.hidden = coleccion !== "propios"; ulC.hidden = coleccion !== "catalogo";
    let vistos = 0;
    if (coleccion === "propios") {
      $("cot-machotes-vacio").hidden = propios().length > 0;
      for (const m of propios().filter((x) => coincide(x.nombre, x.concepto))) {
        vistos++;
        ulP.append(h("li", { class: "cot-fila", "data-id": m.id },
          h("button", { type: "button", class: "cot-fila__principal", onclick: () => abrirMachote(m), "aria-label": `Editar el machote ${m.nombre}` },
            h("strong", {}, m.nombre, h("span", { class: "cot-etiqueta" }, idiomaCorto(m.idioma))),
            h("span", { class: "tv-tenue" }, m.concepto.title || ""),
            h("span", { class: "cot-fila__meta" }, Number(m.concepto.unit_price) ? `${dinero(m.concepto.unit_price)} / ${m.concepto.unit || ""}` : m.concepto.unit || "", m.actualizado ? ` · ${fechaCorta(m.actualizado)}` : "")),
          h("div", { class: "cot-fila__acciones" },
            boton({ onclick: () => abrirMachote(m), etiqueta: `Editar ${m.nombre}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "pencil"),
            h("a", { class: "tv-boton tv-boton--secundario itt-boton-plano itt-boton-icono", href: `${urls["machotes-exportar"]}?id=${encodeURIComponent(m.id)}`, download: `machote_${m.nombre.replace(/[^\w\- ]+/g, "").trim() || "cotizacion"}.json`, "aria-label": `Exportar ${m.nombre}`, title: `Exportar ${m.nombre}` }, icono("download")),
            boton({ onclick: () => borrarMachote(m), etiqueta: `Eliminar ${m.nombre}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "trash-2"))));
      }
    } else {
      $("cot-machotes-vacio").hidden = true;
      const idioma = $("m-idioma").value;
      for (const f of catalogo(idioma).filter((x) => coincide(x.nombre, x.concepto))) {
        vistos++;
        ulC.append(h("li", { class: "cot-fila" },
          h("div", { class: "cot-fila__principal" }, h("strong", {}, f.nombre), h("span", { class: "tv-tenue" }, f.concepto.title || ""),
            h("span", { class: "cot-fila__meta" }, `${f.concepto.subitems.length} alcance${f.concepto.subitems.length === 1 ? "" : "s"} · ${f.concepto.unit || ""}`)),
          h("div", { class: "cot-fila__acciones" },
            boton({ onclick: () => abrirMachote(null, { nombre: f.nombre, idioma, concepto: f.concepto }), class: "tv-boton tv-boton--secundario" }, "copy", "Personalizar"))));
      }
    }
    $("cot-machotes-sin-resultados").hidden = !(filtro && vistos === 0);
  }

  function abrirMachote(m, base) {
    machoteEditado = m;
    const datos = m ? { nombre: m.nombre, idioma: m.idioma, concepto: m.concepto } : base || { nombre: "", idioma: "es", concepto: { title: "", description: "", subitems: [], unit: "Lote", quantity: 1, unit_price: 0 } };
    $("dlg-machote-titulo").textContent = m ? "Editar machote" : "Nuevo machote";
    $("m-nombre").value = datos.nombre; $("m-lengua").value = datos.idioma || "es";
    const c = datos.concepto;
    $("m-title").value = c.title || ""; $("m-description").value = c.description || ""; $("m-subitems").value = (c.subitems || []).join("\n");
    $("m-unit").value = c.unit || ""; $("m-quantity").value = fmtNum(Number(c.quantity ?? 1)); $("m-unit_price").value = fmtNum(Number(c.unit_price ?? 0));
    $("m-restaurar").hidden = !(m && m.tiene_anterior);
    $("m-eliminar").hidden = !m;
    $("m-error").hidden = true;
    $("dlg-machote").showModal();
  }
  async function guardarMachoteEditado(e) {
    e.preventDefault();
    const err = $("m-error");
    const q = numero($("m-quantity").value), p = numero($("m-unit_price").value);
    const problema = !$("m-nombre").value.trim() ? "Escribe un nombre para el machote." : !Number.isFinite(q) || q < 0 || q > 1e9 ? "La cantidad debe ser un número entre 0 y 1,000,000,000."
      : !Number.isFinite(p) || p < 0 || p > 1e11 ? "El precio debe ser un número entre 0 y 100,000,000,000." : !enLinea() ? SIN_RED_MACHOTES : "";
    err.hidden = !problema; err.textContent = problema;
    if (problema) return;
    const concepto = { title: $("m-title").value, description: $("m-description").value, subitems: lineas($("m-subitems").value), unit: $("m-unit").value.trim() || "Lote", quantity: q, unit_price: p };
    const cuerpo = { nombre: $("m-nombre").value, idioma: $("m-lengua").value, concepto };
    if (machoteEditado) cuerpo.id = machoteEditado.id;
    $("m-guardar").disabled = true;
    try {
      const m = await ITT.cot.pedir(urls.machotes, { metodo: "POST", json: cuerpo });
      $("dlg-machote").close();
      await recargarContexto();
      mensaje(`Machote «${m.nombre}» guardado.`, "info");
      if (!machoteEditado) coleccion = "propios";
      await pintarMachotes(false);
    } catch (x) { err.hidden = false; err.textContent = x.clase === "red" ? SIN_RED_MACHOTES : x.mensaje || "No se pudo guardar. Inténtalo de nuevo."; }
    finally { $("m-guardar").disabled = false; }
  }
  async function borrarMachote(m) {
    if (!enLinea()) return mensaje(SIN_RED_MACHOTES, "error");
    if (!(await confirmar(`¿Eliminar el machote «${m.nombre}»? Las cotizaciones que ya lo usan no cambian.`, { aceptar: "Eliminar machote", peligro: true }))) return false;
    try { await ITT.cot.pedir(url("machote", m.id), { metodo: "DELETE" }); await pintarMachotes(); mensaje(`Se eliminó «${m.nombre}».`, "info"); return true; }
    catch (e) { mensaje(e.mensaje || "No se pudo eliminar. Inténtalo de nuevo.", "error"); return false; }
  }
  async function restaurarMachote(m) {
    if (!enLinea()) return mensaje(SIN_RED_MACHOTES, "error");
    if (!(await confirmar(`¿Regresar «${m.nombre}» a su versión anterior?`, { aceptar: "Restaurar" }))) return;
    try {
      const r = await ITT.cot.pedir(url("machote-restaurar", m.id), { metodo: "POST", json: {} });
      $("dlg-machote").close(); await pintarMachotes(); mensaje(`«${r.nombre}» volvió a su versión anterior.`, "info");
    } catch (e) { mensaje(e.mensaje || "No se pudo restaurar. Inténtalo de nuevo.", "error"); }
  }
  async function importarMachotes(archivo) {
    if (!enLinea()) return mensaje(SIN_RED_MACHOTES, "error");
    const f = new FormData(); f.append("archivo", archivo);
    try {
      const r = await ITT.cot.pedir(urls["machotes-importar"], { metodo: "POST", formulario: f });
      coleccion = "propios";
      mensaje(`Se importaron ${r.agregados} machote${r.agregados === 1 ? "" : "s"}${r.renombrados ? ` (${r.renombrados} con nombre repetido, renombrado${r.renombrados === 1 ? "" : "s"})` : ""}.`, "info");
      await pintarMachotes();
    } catch (e) { mensaje(e.mensaje || "No se pudo importar el archivo.", "error"); }
  }
  async function importarEditable(archivo) {
    if (!enLinea()) return mensaje("Sin conexión. Importa el archivo cuando vuelva la señal.", "error");
    const f = new FormData(); f.append("archivo", archivo);
    try {
      const r = await ITT.cot.pedir(urls.importar, { metodo: "POST", formulario: f });
      await ITT.cot.sincronizar();
      mensaje(`Se importó ${r.folio || "la cotización"}.`, "info");
      await pintarLista();
    } catch (e) { mensaje(e.mensaje || "No se pudo importar.", "error"); }
  }

  // -- Enrutador -----------------------------------------------------------------------------------------------------------------------------
  function mostrar(vista) {
    for (const v of ["lista", "editor", "machotes"]) $(`cot-vista-${v}`).hidden = v !== vista;
    raiz.dataset.vista = vista;
    document.documentElement.dataset.ittVista = vista;
    if (vista !== "editor") document.title = vista === "machotes" ? "Machotes · Cotizaciones" : "Cotizaciones";
  }
  async function ruta() {
    mensaje("");
    if (reg && temporizador) await guardarYa();
    const hash = location.hash || "#/";
    let m;
    if ((m = /^#\/s\/([0-9a-f-]{36})$/.exec(hash))) {                  // desde la búsqueda: id del servidor
      const local = await ITT.cot.localDe(m[1]);
      history.replaceState(null, "", local ? `#/c/${local}` : "#/");
      return ruta();
    }
    if ((m = /^#\/c\/([0-9a-f-]{36})$/.exec(hash))) {
      const r = await ITT.cola.borrador(m[1]);
      if (!r || r.borrada) { location.hash = "#/"; return; }
      reg = r;
      mostrar("editor");
      pintarCampos();
      document.title = `${$("cot-titulo-editor").textContent} · Cotizaciones`;
      programarVistaPrevia();
      return;
    }
    reg = null;
    if (hash === "#/nueva") { history.replaceState(null, "", "#/"); if (ctx) return nueva(); }
    if (hash === "#/machotes") { mostrar("machotes"); await pintarMachotes(); return; }
    mostrar("lista");
    await pintarLista();
  }

  function enlazar() {
    for (const e of raiz.querySelectorAll("[data-dato]")) e.addEventListener(e.type === "checkbox" ? "change" : "input", alEscribir);
    $("f-idioma").addEventListener("change", (e) => cambiarIdioma(e.target.value));
    $("cot-nueva").addEventListener("click", nueva);
    $("cot-buscar").addEventListener("input", pintarLista);
    $("cot-importar").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importarEditable(f); });
    $("cot-machotes-importar").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importarMachotes(f); });
    $("cot-agregar").addEventListener("click", () => { const i = conceptoNuevo(); abrirConcepto(i, true); });
    $("cot-desde-machote").addEventListener("click", () => abrirPlantillas("insertar"));
    $("c-reemplazar").addEventListener("click", () => abrirPlantillas("reemplazar"));
    $("cot-nueva-vacia").addEventListener("click", nueva);
    for (const id of ["p-administrar", "mas-machotes"]) $(id).addEventListener("click", () => { for (const d of document.querySelectorAll("dialog[open]")) d.close(); });
    for (const b of document.querySelectorAll("[data-coleccion]")) b.addEventListener("click", () => { coleccion = b.dataset.coleccion; pintarMachotes(false); });
    $("m-buscar").addEventListener("input", () => pintarMachotes(false));
    $("m-idioma").addEventListener("change", () => pintarMachotes(false));
    $("cot-machote-nuevo").addEventListener("click", () => abrirMachote(null));
    $("form-machote").addEventListener("submit", guardarMachoteEditado);
    $("m-eliminar").addEventListener("click", async () => { const m = machoteEditado; if (m && (await borrarMachote(m))) $("dlg-machote").close(); });
    $("m-restaurar").addEventListener("click", () => machoteEditado && restaurarMachote(machoteEditado));
    $("cot-vp-actualizar").addEventListener("click", () => actualizarVistaPrevia());
    $("cot-machotes-exportar").addEventListener("click", (e) => { if (!enLinea()) { e.preventDefault(); mensaje("Sin conexión. Exporta cuando vuelva la señal.", "error"); } });
    $("p-buscar").addEventListener("input", pintarPlantillas);
    for (const id of ["c-title", "c-description", "c-subitems", "c-unit", "c-quantity", "c-unit_price"]) $(id).addEventListener("input", aplicarConcepto);
    $("c-quitar").addEventListener("click", quitarConcepto);
    $("c-machote").addEventListener("click", guardarComoMachote);
    $("form-concepto").addEventListener("submit", () => { conceptoAbierto = -1; });
    $("dlg-concepto").addEventListener("close", () => { conceptoAbierto = -1; });
    $("cot-foto-camara").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregarFotos(f); });
    $("cot-foto-galeria").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregarFotos(f); });
    $("cot-ortografia").addEventListener("click", revisarOrtografia);
    $("cot-ortografia-estado").addEventListener("click", revisarOrtografia);
    $("cot-pdf").addEventListener("click", generarPdf);
    $("pdf-compartir").addEventListener("click", compartirPdf);
    $("pdf-descargar").addEventListener("click", descargarPdf);
    $("cot-mas").addEventListener("click", () => $("dlg-mas").showModal());
    $("mas-vista-previa").addEventListener("click", () => { $("dlg-mas").close(); if (ancha()) actualizarVistaPrevia(); else generarPdf(); });
    $("mas-duplicar").addEventListener("click", async () => { $("dlg-mas").close(); await guardarYa(); const r = await ITT.cot.duplicar(ctx, reg); location.hash = `#/c/${r.id}`; });
    $("mas-editable").addEventListener("click", (e) => {
      e.preventDefault();
      const blob = new Blob([JSON.stringify(reg.datos, null, 2)], { type: "application/json" });
      const a = h("a", { href: URL.createObjectURL(blob), download: nombreEditable() });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      $("dlg-mas").close();
    });
    $("mas-borrar").addEventListener("click", async () => { $("dlg-mas").close(); const b = reg; if (await confirmarBorrado(b)) { await ITT.cot.borrar(b.id); reg = null; ITT.subirAhora(); location.hash = "#/"; } });
    $("c-cerrar").addEventListener("click", () => {                  // «Cancelar» en un concepto nuevo sin tocar: no deja un «Nuevo concepto» vacío en la cotización
      if (conceptoSinEditar && conceptoAbierto >= 0) { reg.datos.items.splice(conceptoAbierto, 1); conceptoSinEditar = false; cambioEnConceptos(); }
    });
    for (const b of document.querySelectorAll("[data-cerrar]")) b.addEventListener("click", () => b.closest("dialog").close());
    addEventListener("hashchange", ruta);
    const red = () => {
      const e = $("cot-red");
      limpiar(e);
      if (!enLinea()) e.append(icono("cloud-off"), h("span", {}, "Sin conexión. Puedes seguir trabajando: todo se guarda en este teléfono."));
      e.hidden = enLinea();
      e.dataset.estado = enLinea() ? "en_linea" : "sin_conexion";
      estadoGuardado();
    };
    addEventListener("online", red); addEventListener("offline", red); red();
    document.addEventListener("itt:doc", async (e) => {
      const d = e.detail || {};
      if (d.tipo !== "cotizacion") return;
      if (d.tipo_evento === "copia" && reg && reg.id === d.id) {
        await confirmar("Esta cotización cambió en otro dispositivo mientras la editabas. Tus cambios están a salvo en una copia nueva.", { aceptar: "Ver la copia", soloAviso: true });
        location.hash = `#/c/${d.copia}`;
      } else if (d.tipo_evento === "sincronizado") {
        if (raiz.dataset.vista === "lista") pintarLista();
        else if (reg) { const r = await ITT.cola.borrador(reg.id); if (r) { reg.sucio = r.sucio; reg.servidor_id = r.servidor_id; reg.revision_base = r.revision_base; estadoGuardado(); } }
      }
    });
  }
  function confirmarBorrado(b) {
    const nombre = [b.datos.quote_number, b.datos.project_title].filter(Boolean).join(" · ") || "esta cotización";
    return confirmar(b.sucio || !b.servidor_id ? `¿Eliminar «${nombre}»? Tiene cambios que aún no se suben y se perderán.` : `¿Eliminar «${nombre}»? No se puede deshacer.`, { aceptar: "Eliminar cotización", peligro: true });
  }

  async function asegurarCuenta() {
    if ((await ITT.cola.ajuste("cuenta", null)) != null || !enLinea()) return;
    try {
      const r = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
      if (r.ok) { const d = await r.json(); await ITT.cola.guardarAjuste("cuenta", d.cuenta); await ITT.cola.guardarAjuste("csrf", d.csrf); }
    } catch (_) { /* sin red: se sigue sin cuenta */ }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    ITT.cot.iniciar(urls);
    enlazar();
    await asegurarCuenta();
    try { ctx = await ITT.cot.contexto(); } catch (e) { ctx = null; mensaje(e.clase === "sesion" ? "Tu sesión terminó. Vuelve a iniciar sesión." : "Sin conexión. Abre Cotizaciones una vez con señal para poder trabajar sin ella.", "error"); }
    if (ctx) {
      const dl = $("unidades");
      for (const u of (ctx.unidades || {})[ctx.base && ctx.base.language === "en" ? "en" : "es"] || []) dl.append(h("option", { value: u }));
    }
    await ruta();
    ITT.cot.sincronizar();
    document.documentElement.dataset.cotLista = "1";
  });
})();

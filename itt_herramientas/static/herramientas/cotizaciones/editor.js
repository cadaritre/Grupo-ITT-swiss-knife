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
  let pdfActual = null;           // {blob, nombre}
  let vistaPrevia = null;

  // -- Utilidades -----------------------------------------------------------------------------------------------------------
  const { h, limpiar, confirmar } = ITT.ui;
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
    if (!reg) { e.textContent = ""; return; }
    e.textContent = reg.sucio ? (enLinea() ? "Guardado en este teléfono · subiendo…" : "Guardado en este teléfono · se sube con señal") : "Sincronizado";
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
      marca.textContent = pendiente ? (campo === "email" || campo === "phone" ? " · ⚠ correo o teléfono" : " · ⚠ recomendado") : "";
    }
    const n = [falta.quote_number, falta.client_name, falta.contact, sinContacto, falta.location, falta.project_title].filter(Boolean).length;
    const s = $("cot-recomendados");
    s.textContent = n ? `⚠ ${n} datos recomendados pendientes` : "✓ Datos recomendados completos";
    s.dataset.estado = n ? "pendiente" : "listo";
    $("resumen-datos").textContent = n ? `⚠ ${n}` : "";
  }

  function indicadorOrtografia() {
    const b = $("cot-ortografia-estado");
    const ok = !!reg.datos.spelling_checked;
    b.textContent = ok ? "✓ Ortografía revisada" : "⚠ Ortografía pendiente";
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
    const todos = await ITT.cot.listar();
    const visibles = todos.filter((b) => !filtro || [b.datos.quote_number, b.datos.client_name, b.datos.project_title].join(" ").toLowerCase().includes(filtro));
    limpiar(lista);
    $("cot-lista-vacia").hidden = todos.length > 0;
    for (const b of visibles) {
      const t = T.totales(b.datos);
      const estado = b.error ? "Rechazada por el servidor" : b.sucio ? "Sin sincronizar" : "Sincronizada";
      lista.append(h("li", { class: "cot-tarjeta", "data-id": b.id, "data-sucio": b.sucio ? "1" : "" },
        h("a", { class: "cot-tarjeta__enlace", href: `#/c/${b.id}` },
          h("strong", {}, b.datos.quote_number || "Sin folio", " · ", b.datos.project_title || "Sin título"),
          h("span", { class: "tv-tenue" }, b.datos.client_name || "Sin cliente"),
          h("span", { class: "cot-tarjeta__total" }, `${dinero(t.total)} ${b.datos.currency || ""}`)),
        h("div", { class: "cot-tarjeta__pie" },
          h("span", { class: "cot-insignia", "data-estado": b.sucio ? "pendiente" : "listo" }, estado),
          b.aviso ? h("span", { class: "cot-insignia", "data-estado": "pendiente" }, "Copia por conflicto") : null,
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => duplicarDesde(b), "aria-label": `Duplicar ${b.datos.quote_number || ""}` }, "Duplicar"),
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => borrarRegistro(b), "aria-label": `Eliminar ${b.datos.quote_number || ""}` }, "Eliminar"))));
    }
  }

  async function nueva() {
    if (!ctx) return mensaje("Abre esta pantalla una vez con conexión para preparar las cotizaciones sin señal.", "error");
    const r = await ITT.cot.nuevo(ctx);
    location.hash = `#/c/${r.id}`;
  }
  async function duplicarDesde(b) {
    if (!ctx) return;
    const r = await ITT.cot.duplicar(ctx, b);
    await ITT.refrescarCola();
    mensaje(`Se creó la copia ${r.datos.quote_number}.`, "info");
    await pintarLista();
    ITT.subirAhora();
  }
  async function borrarRegistro(b) {
    const msg = b.sucio || !b.servidor_id ? "Esta cotización tiene cambios que quizá no estén en el servidor. Si la eliminas, se pierde. ¿Eliminarla?" : "¿Eliminar esta cotización? No se puede deshacer.";
    if (!(await confirmar(msg, { aceptar: "Eliminar", peligro: true }))) return;
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
    } else if (k === "currency") d[k] = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);
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
        h("button", { type: "button", class: "cot-concepto__abrir", onclick: () => abrirConcepto(i), "aria-label": `Editar concepto ${i + 1}` },
          h("strong", {}, it.title || "Sin título"),
          h("span", { class: "tv-tenue" }, `${fmtNum(it.quantity)} ${it.unit || ""} × ${dinero(it.unit_price)}`),
          h("span", { class: "cot-concepto__importe" }, dinero(t.importes[i])),
          it.subitems.length ? h("span", { class: "tv-tenue" }, `${it.subitems.length} alcance${it.subitems.length === 1 ? "" : "s"}`) : null),
        h("div", { class: "cot-concepto__mover" },
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => mover(i, -1), disabled: i === 0, "aria-label": "Subir concepto" }, "↑"),
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => mover(i, 1), disabled: i === items.length - 1, "aria-label": "Bajar concepto" }, "↓"))));
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
  function abrirConcepto(i) {
    conceptoAbierto = i;
    const it = reg.datos.items[i];
    $("dlg-concepto-titulo").textContent = `Concepto ${i + 1}`;
    $("c-title").value = it.title; $("c-description").value = it.description; $("c-subitems").value = it.subitems.join("\n");
    $("c-unit").value = it.unit; $("c-quantity").value = fmtNum(it.quantity); $("c-unit_price").value = fmtNum(it.unit_price);
    $("c-error").hidden = true;
    contarAlcances(); importeConcepto();
    $("dlg-concepto").showModal();
  }
  function contarAlcances() { const n = lineas($("c-subitems").value).length; $("c-subitems-cuenta").textContent = `${n} alcance${n === 1 ? "" : "s"}`; }
  const lineas = (texto) => texto.split("\n").map((l) => l.replace(/^\s*(?:[•\-–—]|\d+[.)])\s*/, "").trim()).filter(Boolean);
  function importeConcepto() {
    const q = numero($("c-quantity").value), p = numero($("c-unit_price").value);
    $("c-importe").textContent = Number.isFinite(q) && Number.isFinite(p) ? dinero(T.redondear2(q * p)) : "—";
  }
  function aplicarConcepto() {
    if (conceptoAbierto < 0 || !reg) return;
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
    if (!(await confirmar("¿Quitar este concepto de la cotización?", { aceptar: "Quitar", peligro: true }))) return;
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
  async function guardarComoMachote() {
    const it = reg.datos.items[conceptoAbierto];
    if (!enLinea()) return mensaje("Guardar un machote necesita conexión: los machotes viven en tu cuenta.", "error");
    const nombre = await confirmar("Escribe un nombre para el machote.", { entrada: it.title.slice(0, 160), etiqueta: "Nombre del machote", aceptar: "Guardar" });
    if (!nombre) return;
    const concepto = ITT.cot.copiar(it);
    try {
      const m = await ITT.cot.pedir(urls.machotes, { metodo: "POST", json: { nombre, idioma: reg.datos.language, concepto } });
      await recargarContexto();
      mensaje(`Machote «${m.nombre}» guardado.`, "info");
    } catch (e) {
      if (e.clase === "rechazo" && /Ya existe/.test(e.mensaje || "")) {
        const previo = (ctx.machotes || []).find((x) => x.nombre.toLowerCase() === nombre.toLowerCase());
        if (previo && (await confirmar(`Ya existe un machote llamado «${previo.nombre}». ¿Sobrescribirlo? Se conserva la versión anterior para poder restaurarla.`, { aceptar: "Sobrescribir" }))) {
          try { await ITT.cot.pedir(urls.machotes, { metodo: "POST", json: { id: previo.id, nombre: previo.nombre, idioma: reg.datos.language, concepto } }); await recargarContexto(); mensaje(`Machote «${previo.nombre}» actualizado.`, "info"); }
          catch (e2) { mensaje(e2.mensaje, "error"); }
        }
      } else mensaje(e.mensaje || "No se pudo guardar el machote.", "error");
    }
  }
  async function recargarContexto() { try { ctx = await ITT.cot.contexto(); } catch (_) { /* se queda el anterior */ } }

  // Machotes: insertar
  function abrirPlantillas() {
    $("p-buscar").value = "";
    pintarPlantillas();
    $("dlg-plantillas").showModal();
  }
  function pintarPlantillas() {
    const filtro = $("p-buscar").value.trim().toLowerCase();
    const idioma = reg.datos.language;
    const caja = $("p-lista");
    limpiar(caja);
    const grupo = (titulo, filas) => {
      if (!filas.length) return;
      caja.append(h("h3", {}, titulo));
      const ul = h("ul", { class: "cot-lista" });
      for (const f of filas) ul.append(h("li", {}, h("button", { type: "button", class: "tv-boton tv-boton--secundario cot-plantilla", onclick: () => insertarPlantilla(f.concepto) },
        h("strong", {}, f.nombre), h("span", { class: "tv-tenue" }, f.concepto.title))));
      caja.append(ul);
    };
    const coincide = (n, c) => !filtro || `${n} ${c.title}`.toLowerCase().includes(filtro);
    grupo("Mis machotes", (ctx.machotes || []).filter((m) => m.idioma === idioma && coincide(m.nombre, m.concepto)).map((m) => ({ nombre: m.nombre, concepto: m.concepto })));
    grupo("Machotes de fábrica", Object.entries((ctx.plantillas || {})[idioma] || {}).filter(([n, c]) => coincide(n, c)).map(([nombre, concepto]) => ({ nombre, concepto })));
    if (!caja.children.length) caja.append(h("p", { class: "tv-tenue" }, "Sin resultados."));
  }
  function insertarPlantilla(concepto) {
    $("dlg-plantillas").close();
    if (reg.datos.items.length >= (ctx.limites ? ctx.limites.conceptos : 200)) return mensaje("Se alcanzó el máximo de conceptos por cotización.", "error");
    const i = conceptoNuevo(concepto);
    mensaje(`Se agregó «${reg.datos.items[i].title}».`, "info");
    $("cot-conceptos").lastElementChild.scrollIntoView({ block: "nearest" });
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
        mensaje(`No se pudo guardar «${archivo.name || "foto"}»: ${e.message || e}`, "error");
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
    if (!enLinea()) return mensaje("La revisión ortográfica necesita conexión: el diccionario está en el servidor. La cotización queda marcada como pendiente de revisar.", "error");
    await guardarYa();
    try {
      const r = await ITT.ortografia.revisar({ idioma: reg.datos.language, campos: camposOrtografia(), aplicar: aplicarOrtografia });
      if (r.cambios) { pintarCampos(); }
      if (r.revisado) {
        reg.datos.spelling_checked = true;
        indicadorOrtografia();
        mensaje(r.problemas ? "Revisión terminada." : "No se encontraron palabras desconocidas.", "info");
      }
      programarGuardado();
      programarVistaPrevia();
    } catch (e) { mensaje(e.mensaje || "No se pudo revisar la ortografía.", "error"); }
  }

  // Idioma
  async function cambiarIdioma(nuevo) {
    if (nuevo === reg.datos.language) return;
    if (!enLinea()) { $("f-idioma").value = reg.datos.language; return mensaje("Cambiar el idioma traduce el texto de fábrica en el servidor y necesita conexión.", "error"); }
    try {
      const r = await ITT.cot.pedir(urls.traducir, { metodo: "POST", json: { datos: reg.datos, idioma: nuevo } });
      reg.datos = r.datos;
      pintarCampos(); programarGuardado(); programarVistaPrevia();
    } catch (e) { $("f-idioma").value = reg.datos.language; mensaje(e.mensaje || "No se pudo cambiar el idioma.", "error"); }
  }

  // -- PDF y vista previa ----------------------------------------------------------------------------------------------------------
  const limpioNombre = (t, respaldo) => { const l = (t || "").replace(/[<>:"/\\|?*\x00-\x1f]/g, " ").split(/\s+/).filter(Boolean).join(" ").replace(/^[ .]+|[ .]+$/g, ""); return l.slice(0, 90) || respaldo; };
  const nombreEditable = () => (reg ? `EDITABLE_COTIZACION ${limpioNombre(reg.datos.quote_number, "SIN FOLIO")} - ${limpioNombre(reg.datos.project_title, "SERVICIOS")}.json` : "editable.json");

  async function pedirPdf() {
    const r = await ITT.cot.pedir(urls.pdf, { metodo: "POST", json: { datos: reg.datos }, crudo: true });
    const blob = await r.blob();
    const disp = r.headers.get("Content-Disposition") || "";
    let nombre = `COTIZACION ${limpioNombre(reg.datos.quote_number, "SIN FOLIO")} - ${limpioNombre(reg.datos.project_title, "SERVICIOS")}.pdf`;
    const m = /filename\*=utf-8''([^;]+)/i.exec(disp) || /filename="([^"]+)"/i.exec(disp);
    if (m) { try { nombre = decodeURIComponent(m[1]); } catch (_) { nombre = m[1]; } }
    return { blob, nombre, pendientes: Number(r.headers.get("X-Imagenes-Pendientes") || 0) };
  }

  const dibujarPdf = (blob, contenedor) => ITT.vistaPdf.dibujar(blob, contenedor, { pdfjs: PDFJS, worker: PDFJS_WORKER });

  async function generarPdf() {
    const d = reg.datos;
    if (!d.items.length) return mensaje("Agrega al menos un concepto antes de exportar.", "error");
    if (!enLinea()) return mensaje("El PDF se genera en el servidor y necesita conexión. La cotización está guardada en este teléfono.", "error");
    if (!d.spelling_checked) {
      const nombre = d.language === "en" ? "inglés" : "español";
      if (!(await confirmar(`Esta cotización no ha terminado su revisión ortográfica en ${nombre}. ¿Generar el PDF de todos modos?`, { aceptar: "Generar de todos modos", cancelar: "Revisar primero" }))) return;
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
      const r = await pedirPdf();
      pdfActual = r;
      if (r.pendientes) {
        const a = $("pdf-advertencias");
        a.hidden = false;
        a.textContent = `${r.pendientes} imagen${r.pendientes === 1 ? "" : "es"} de referencia aún no ${r.pendientes === 1 ? "llegó" : "llegaron"} al servidor y no aparece${r.pendientes === 1 ? "" : "n"} en este PDF. Se suben solas con señal; genera el PDF de nuevo cuando termine.`;
        ITT.subirAhora && ITT.subirAhora();
      }
      $("pdf-compartir").disabled = false; $("pdf-descargar").disabled = false;
      $("pdf-estado").textContent = r.nombre;
      await dibujarPdf(r.blob, $("pdf-paginas"));
    } catch (e) {
      $("pdf-estado").textContent = e.mensaje || `No se pudo mostrar el PDF: ${e.message || e}`;
      if (pdfActual) $("pdf-estado").textContent = `${pdfActual.nombre} (no se pudo mostrar aquí; puedes compartirlo o descargarlo)`;
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
    if (!ancha() || !enLinea() || !reg || !reg.datos.items.length) return;
    clearTimeout(vistaPrevia);
    vistaPrevia = setTimeout(actualizarVistaPrevia, 1500);
  }
  async function actualizarVistaPrevia() {
    if (!ancha() || !enLinea() || !reg) return;
    $("cot-vp-estado").textContent = "Actualizando…";
    try { const r = await pedirPdf(); await dibujarPdf(r.blob, $("cot-vp-paginas")); $("cot-vp-estado").textContent = r.pendientes ? `${r.pendientes} imagen(es) aún sin subir no aparecen.` : "Vista previa al día."; }
    catch (e) { $("cot-vp-estado").textContent = e.mensaje || "No se pudo actualizar la vista previa."; }
  }

  // -- Machotes (pantalla) --------------------------------------------------------------------------------------------------------------
  async function pintarMachotes() {
    const ul = $("cot-machotes-lista");
    limpiar(ul);
    if (enLinea()) await recargarContexto();
    const filas = (ctx && ctx.machotes) || [];
    $("cot-machotes-vacio").hidden = filas.length > 0;
    $("cot-machotes-exportar").setAttribute("href", urls["machotes-exportar"]);
    $("cot-machotes-exportar").setAttribute("download", "machotes_cotizacion.json");
    for (const m of filas) {
      ul.append(h("li", { class: "cot-tarjeta", "data-id": m.id },
        h("div", { class: "cot-tarjeta__enlace" }, h("strong", {}, m.nombre), h("span", { class: "tv-tenue" }, m.concepto.title), h("span", { class: "tv-tenue" }, m.idioma === "en" ? "English" : "Español")),
        h("div", { class: "cot-tarjeta__pie" },
          m.tiene_anterior ? h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => restaurarMachote(m) }, "Restaurar versión anterior") : null,
          h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: () => borrarMachote(m) }, "Eliminar"))));
    }
  }
  async function borrarMachote(m) {
    if (!(await confirmar(`¿Eliminar el machote «${m.nombre}»? Las cotizaciones que ya lo usan no cambian.`, { aceptar: "Eliminar", peligro: true }))) return;
    try { await ITT.cot.pedir(url("machote", m.id), { metodo: "DELETE" }); await pintarMachotes(); } catch (e) { mensaje(e.mensaje || "No se pudo eliminar.", "error"); }
  }
  async function restaurarMachote(m) {
    try { await ITT.cot.pedir(url("machote-restaurar", m.id), { metodo: "POST", json: {} }); await pintarMachotes(); mensaje("Se restauró la versión anterior.", "info"); } catch (e) { mensaje(e.mensaje || "No se pudo restaurar.", "error"); }
  }
  async function importarMachotes(archivo) {
    const f = new FormData(); f.append("archivo", archivo);
    try {
      const r = await ITT.cot.pedir(urls["machotes-importar"], { metodo: "POST", formulario: f });
      mensaje(`Se importaron ${r.agregados} machote${r.agregados === 1 ? "" : "s"}${r.renombrados ? ` (${r.renombrados} con nombre repetido se renombraron)` : ""}.`, "info");
      await pintarMachotes();
    } catch (e) { mensaje(e.mensaje || "No se pudo importar.", "error"); }
  }
  async function importarEditable(archivo) {
    if (!enLinea()) return mensaje("Importar un editable necesita conexión.", "error");
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
  }
  async function ruta() {
    mensaje("");
    if (reg && temporizador) await guardarYa();
    const hash = location.hash || "#/";
    let m;
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
    $("cot-agregar").addEventListener("click", () => { const i = conceptoNuevo(); abrirConcepto(i); });
    $("cot-desde-machote").addEventListener("click", abrirPlantillas);
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
    for (const b of document.querySelectorAll("[data-cerrar]")) b.addEventListener("click", () => b.closest("dialog").close());
    addEventListener("hashchange", ruta);
    const red = () => { $("cot-red").textContent = enLinea() ? "En línea" : "Sin conexión: puedes seguir editando; todo queda en este teléfono."; $("cot-red").dataset.estado = enLinea() ? "en_linea" : "sin_conexion"; estadoGuardado(); };
    addEventListener("online", red); addEventListener("offline", red); red();
    document.addEventListener("itt:doc", async (e) => {
      const d = e.detail || {};
      if (d.tipo !== "cotizacion") return;
      if (d.tipo_evento === "copia" && reg && reg.id === d.id) {
        await confirmar("Esta cotización cambió en otro dispositivo mientras la editabas aquí. Tus cambios NO se perdieron: se guardaron como una copia nueva. Te llevamos a la copia.", { aceptar: "Ver la copia", soloAviso: true });
        location.hash = `#/c/${d.copia}`;
      } else if (d.tipo_evento === "sincronizado") {
        if (raiz.dataset.vista === "lista") pintarLista();
        else if (reg) { const r = await ITT.cola.borrador(reg.id); if (r) { reg.sucio = r.sucio; reg.servidor_id = r.servidor_id; reg.revision_base = r.revision_base; estadoGuardado(); } }
      }
    });
  }
  function confirmarBorrado(b) {
    return confirmar(b.sucio || !b.servidor_id ? "Esta cotización tiene cambios que quizá no estén en el servidor. Si la eliminas, se pierde. ¿Eliminarla?" : "¿Eliminar esta cotización? No se puede deshacer.", { aceptar: "Eliminar", peligro: true });
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
    try { ctx = await ITT.cot.contexto(); } catch (e) { ctx = null; mensaje(e.clase === "sesion" ? "Vuelve a iniciar sesión para preparar las cotizaciones." : "Abre esta pantalla una vez con conexión para preparar las cotizaciones sin señal.", "error"); }
    if (ctx) {
      const dl = $("unidades");
      for (const u of (ctx.unidades || {})[ctx.base && ctx.base.language === "en" ? "en" : "es"] || []) dl.append(h("option", { value: u }));
    }
    await ruta();
    ITT.cot.sincronizar();
    document.documentElement.dataset.cotLista = "1";
  });
})();

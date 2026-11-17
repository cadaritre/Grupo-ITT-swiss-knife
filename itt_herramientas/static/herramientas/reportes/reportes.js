/* Reportes fotográficos en el teléfono (herramientas-web.md §4.2): captura con cámara o galería, hora y ubicación por foto, nota,
 * orden (arrastre, «Mover» y vista «Ordenar» por página del PDF, en orden.js), cola local con subida reanudable y PDF en el servidor.
 * Los borradores son los de offline/documentos.js.
 * Rutas con hash: #/ lista · #/r/<id> reporte.
 */
(function () {
  "use strict";
  const ITT = self.ITT;
  const $ = (id) => document.getElementById(id);
  const raiz = $("rep");
  const urls = {};
  for (const a of raiz.attributes) if (a.name.startsWith("data-url-")) urls[a.name.slice(9)] = a.value;
  const docs = ITT.crearDocumentos({ tipo: "reporte", lista: "reportes", avisoCopia: "Este reporte cambió mientras lo editabas sin conexión: tu versión, con todas sus fotos, quedó aparte.",
    claves: { fotos: "sha256" },                                       // las fotos se fusionan una por una, como en el servidor
    sintetico: (f) => ({ titulo: f.titulo || "", cliente: f.cliente || "", fecha: f.fecha || "", con_croquis: true, con_credenciales: false, fotos_por_pagina: 1, fotos: [] }) });

  let ctx = null;
  let reg = null;                        // registro abierto
  let temporizador = null;
  let posicion = null;                   // última posición del GPS {lat, lon, precision, en}
  let observador = null;
  let ubicando = -1;                     // índice de la foto que se está ubicando a mano
  let pdfActual = null;
  let ultimaLista = "#/";                // la lista con sus filtros, para volver a ella desde un reporte
  let ordenando = false;                 // vista «Ordenar»: miniaturas agrupadas por página del PDF
  let moviendo = -1;                     // índice de la foto de la hoja «Mover»
  const POR_PAGINA = [1, 2, 4, 6];
  const MAX_FOTOS = () => (ctx && ctx.max_fotos) || 150;
  const dos = (n) => String(n).padStart(2, "0");
  const hoyISO = () => { const d = new Date(); return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`; };
  const ahoraLocal = () => { const d = new Date(); return `${hoyISO()}T${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`; };
  const enLinea = () => navigator.onLine;

  // -- Utilidades ---------------------------------------------------------------------------------------------------------------
  const { h, limpiar, confirmar } = ITT.ui;

  const mensaje = (texto, tipo) => ITT.ui.mensaje("rep-mensaje", texto, tipo);
  const { icono, boton } = ITT.ui;

  // Reportes de la empresa: bloqueo de edición, historial y recuperación (offline/compartido.js).
  const comp = ITT.compartido.crear({
    tipo: "reporte", prefijo: "rep", docs, articulo: "lo", nombre: "reporte", alMensaje: (t, tipo) => mensaje(t, tipo),
    alRecargar: async (nuevo) => { if (reg && nuevo.id === reg.id) { reg = Object.assign(nuevo, { porSubir: reg.porSubir }); pintarCampos(); } },
    resumen: (d) => [
      h("dl", { class: "itt-historial__datos" }, ...[["Nombre", d.titulo], ["Cliente", d.cliente], ["Fecha", String(d.fecha || "").split("-").reverse().join("/")],
        ["Fotos", String((d.fotos || []).length)]].map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v || "—")))),
      h("ol", { class: "itt-historial__conceptos" }, (d.fotos || []).map((f, i) => h("li", {}, h("span", {}, f.nota || `Foto ${i + 1}`), h("span", { class: "tv-tenue" }, f.tomada ? f.tomada.replace("T", " ").slice(0, 16) : "")))),
    ],
  });


  // -- Guardado ------------------------------------------------------------------------------------------------------------------
  function estadoGuardado() {
    const e = $("rep-estado-guardado");
    limpiar(e);
    if (!reg) return;
    // Un solo estado por documento, contado igual que la banda de pendientes: el reporte mismo (si tiene cambios) y cada foto que no ha llegado.
    const n = (reg.sucio ? 1 : 0) + (reg.porSubir || 0);
    e.append(icono(n ? (enLinea() ? "cloud-upload" : "cloud-off") : "cloud-check"),
      h("span", {}, n ? `${n === 1 ? "1 pendiente" : `${n} pendientes`} · ${enLinea() ? "subiendo…" : "se suben con señal"}` : "Guardado"));
    e.dataset.estado = n ? "pendiente" : "sincronizado";
  }
  function programarGuardado() {
    if (comp.soloLectura) return;
    reg.sucio = true;
    estadoGuardado();
    clearTimeout(temporizador);
    temporizador = setTimeout(guardarYa, 300);
  }
  async function guardarYa() {
    clearTimeout(temporizador);
    temporizador = null;
    if (!reg || comp.soloLectura) return;
    const nuevo = await docs.guardar(reg.id, reg.datos, { miniaturas: reg.miniaturas, aviso: reg.aviso, sesion: comp.sesion });
    if (nuevo && reg && nuevo.id === reg.id) reg.actualizada = nuevo.actualizada;
    estadoGuardado();
    ITT.refrescarCola && (await ITT.refrescarCola());
    if (enLinea() && ITT.subirAhora) ITT.subirAhora();
  }
  addEventListener("pagehide", () => { if (temporizador) guardarYa(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && temporizador) guardarYa(); });

  // -- Contexto -------------------------------------------------------------------------------------------------------------------
  async function contexto() {
    const cuenta = await ITT.cola.ajuste("cuenta", null);
    try {
      const c = await docs.pedir(urls.contexto);
      await ITT.cola.guardarAjuste("rep_contexto", c);
      return c;
    } catch (e) {
      const guardado = await ITT.cola.ajuste("rep_contexto", null);
      if (guardado && (cuenta == null || guardado.cuenta === cuenta)) return Object.assign({ sin_conexion: true }, guardado);
      throw e;
    }
  }

  // -- Lista ----------------------------------------------------------------------------------------------------------------------
  async function pendientesDe(id) {
    const items = (await ITT.cola.listar()).filter((i) => i.meta && i.meta.referencia === `reporte:${id}`);
    return items.length;
  }

  // Clientes para sugerir y filtrar: los del servidor (también de las cotizaciones) y los escritos en este teléfono, recientes primero.
  async function clientesConocidos(registros) {
    const locales = (registros || (await docs.listar())).slice().sort((a, b) => b.actualizada - a.actualizada).map((b) => b.datos.cliente);
    return ITT.listas.unicos([...locales, ...((ctx && ctx.clientes) || [])]);
  }
  let sugerencias = [];
  const lista = ITT.listas.crear({
    prefijo: "rep", tipo: "reporte", zip: urls.zip, tarea: urls.tarea, mensaje: "rep-mensaje", alCambiar: () => pintarLista(),
    campos: {
      texto: (b) => [b.datos.titulo, b.datos.cliente, b.autor && b.autor.nombre, b.autor && b.autor.correo].join(" "),
      cliente: (b) => b.datos.cliente || "", titulo: (b) => b.datos.titulo || "", fecha: (b) => b.datos.fecha || "",
      estado: (b) => (!b.sucio && b.servidor_id && b.pdf_revision != null && b.pdf_revision === b.revision_base ? "con_pdf" : "pendiente"),
      reciente: (b) => b.actualizada || 0,
    },
  });

  async function pintarLista() {
    const ul = $("rep-lista");
    lista.desdeUrl();
    comp.avisos();
    const todos = await docs.listar();
    sugerencias = await clientesConocidos(todos);
    const pendientes = new Map();
    for (const b of todos) pendientes.set(b.id, await pendientesDe(b.id));
    // Todo lo asíncrono ya pasó: limpiar y pintar es síncrono, así dos llamadas seguidas no se mezclan.
    lista.ponerClientes(sugerencias);
    const visibles = lista.aplicar(todos);
    limpiar(ul);
    $("rep-lista-vacia").hidden = todos.length > 0;
    $("rep-herramientas-lista").hidden = todos.length === 0;
    for (const b of visibles) {
      const por = pendientes.get(b.id);
      const d = b.datos;
      const nombre = d.titulo || "Sin nombre";
      ul.append(h("li", { class: "cot-tarjeta", "data-id": b.id, "data-sucio": b.sucio ? "1" : "", "data-seleccionada": lista.seleccion.has(b.id) ? "1" : "" },
        lista.casilla(b, nombre),
        h("a", { class: "cot-tarjeta__enlace", href: `#/r/${b.id}` },
          h("span", { class: "cot-tarjeta__folio" }, String(d.fecha || "").split("-").reverse().join("/")),
          h("strong", { class: "cot-tarjeta__titulo" }, nombre),
          h("span", { class: "cot-tarjeta__cliente" }, [d.cliente, ((n) => `${n} foto${n === 1 ? "" : "s"}`)(b.parcial ? b.resumen.fotos : d.fotos.length)].filter(Boolean).join(" · ")),
          h("span", { class: "cot-tarjeta__autor" }, ITT.ui.autoriaCorta(b, ctx && ctx.autor)),
          b.conflicto ? h("span", { class: "cot-tarjeta__conflicto" }, b.conflicto) : null),
        h("div", { class: "cot-tarjeta__pie" },
          por || b.sucio ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono(enLinea() ? "cloud-upload" : "cloud-off"), h("span", {}, por ? `${por} foto${por === 1 ? "" : "s"} por subir` : "Sin subir"))
            : h("span", { class: "itt-estado" }, icono("cloud-check"), h("span", {}, "Guardado")),
          b.aviso || b.conflicto ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono("copy"), h("span", {}, "Copia en conflicto")) : null,
          ITT.ui.bloqueoVigente(b) ? h("span", { class: "itt-estado", "data-estado": "bloqueo" }, icono("lock"), h("span", {}, `Lo edita ${b.bloqueo.nombre}`)) : null,
          h("span", { class: "cot-tarjeta__espacio" }),
          b.puede_borrar === false ? null : boton({ onclick: () => borrarRegistro(b), etiqueta: `Eliminar ${d.titulo || "reporte"}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "trash-2"))));
    }
  }
  async function nuevo() {
    if (!ctx) return mensaje("Sin conexión. Abre Reportes una vez con señal para poder trabajar sin ella.", "error");
    const r = await docs.crear({ titulo: ctx.titulo || "Reporte fotográfico", cliente: "", fecha: hoyISO(), con_croquis: true, con_credenciales: false, fotos_por_pagina: 1, fotos: [] });
    location.hash = `#/r/${r.id}`;
  }
  async function borrarRegistro(b) {
    const por = await pendientesDe(b.id);
    const texto = !b.servidor_id
      ? `¿Eliminar «${b.datos.titulo || "este reporte"}»? Aún no se sube y no se podrá recuperar.${por ? " Sus fotos se suben de todos modos." : ""}`
      : `¿Eliminar «${b.datos.titulo || "este reporte"}»? Va a la papelera, de donde se puede recuperar durante 30 días.`;
    if (!(await confirmar(texto, { aceptar: "Eliminar reporte", peligro: true }))) return;
    // Las fotos que esperan en la cola nunca se tiran: se suben igual (un reporte de la papelera se recupera con todas sus fotos).
    await docs.borrar(b.id);
    await pintarLista();
    ITT.subirAhora && ITT.subirAhora();
  }

  // -- Editor ---------------------------------------------------------------------------------------------------------------------
  function pintarCampos() {
    const d = reg.datos;
    $("r-titulo").value = d.titulo;
    $("r-cliente").value = d.cliente || "";
    $("r-fecha").value = d.fecha;
    $("r-croquis").checked = !!d.con_croquis;
    $("r-credenciales").checked = !!d.con_credenciales;
    $(`r-por-pagina-${porPagina()}`).checked = true;
    $("r-credenciales-caja").hidden = !(ctx && ctx.credenciales);
    $("rep-titulo-editor").textContent = d.titulo || "Reporte";
    ITT.ui.autoria($("rep-autor"), reg, ctx && ctx.autor);
    $("mas-borrar").hidden = reg.puede_borrar === false;
    $("rep-paso-datos").open = !d.fotos.length;                       // con fotos, lo primero es seguir capturando
    const aviso = $("rep-aviso-doc");
    limpiar(aviso);
    aviso.hidden = !reg.aviso;
    if (reg.aviso) aviso.append(reg.aviso, " ", h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: async () => { reg.aviso = ""; aviso.hidden = true; await guardarYa(); } }, "Entendido"));
    pintarFotos();
    estadoGuardado();
  }

  async function pintarContador() {
    if (!reg) return;
    const n = reg.datos.fotos.length;
    const por = await pendientesDe(reg.id);
    if (reg.porSubir !== por) { reg.porSubir = por; estadoGuardado(); }
    const sin = reg.datos.fotos.filter((f) => f.lat == null).length;
    const c = $("rep-contador");
    c.textContent = `${n} de ${MAX_FOTOS()}`;
    $("rep-resumen-barra").textContent = n ? `${n} foto${n === 1 ? "" : "s"}${por ? ` · ${por} por subir` : " · todas subidas"}${sin ? ` · ${sin} sin ubicación` : ""}` : "Sin fotos";
    $("rep-resumen-barra").dataset.porSubir = String(por);
  }

  function miniaturaDe(f) {
    const mini = reg.miniaturas && reg.miniaturas[f.sha256];
    const img = h("img", { alt: "", width: 64, height: 64 });
    if (mini) img.src = mini;
    else if (enLinea()) {                                                    // la de otra persona del equipo se pide por el reporte que la usa
      img.src = (urls.foto || "").replace("SHA", f.sha256) + (reg && reg.servidor_id ? `?doc=${encodeURIComponent(reg.servidor_id)}` : "");
      img.addEventListener("error", () => img.removeAttribute("src"));
    }
    return img;
  }
  function textoUbicacion(f) {
    if (f.lat == null) return "Sin ubicación";
    return `${f.lat.toFixed(6)}, ${f.lon.toFixed(6)}${f.precision != null ? ` (±${Math.round(f.precision)} m)` : ""}`;
  }
  const horaCorta = (t) => (t ? `${t.slice(8, 10)}/${t.slice(5, 7)}/${t.slice(0, 4)} ${t.slice(11, 16)}` : "Sin hora");

  // -- Fotos: lista, páginas del PDF y orden -----------------------------------------------------------------------------------------
  const porPagina = () => (POR_PAGINA.includes(reg.datos.fotos_por_pagina) ? reg.datos.fotos_por_pagina : 1);
  /** Fotos de cada página del PDF (la misma regla que el generador): [[portada], [página 2], …]. */
  const paginasPdf = () => ITT.ordenFotos.paginas(reg.datos.fotos.length, porPagina(), !!reg.datos.con_croquis);
  const nombrePagina = (p) => (p === 0 ? "Portada" : `Página ${p + 1}`);

  function pintarFotos() {
    const ol = $("rep-fotos");
    limpiar(ol);
    const fotos = reg.datos.fotos;
    $("rep-sin-fotos").hidden = fotos.length > 0;
    if (fotos.length < 2) ordenando = false;
    $("rep-ordenar").hidden = fotos.length < 2;
    // Con varias fotos por página, la lista marca dónde empieza cada página del PDF.
    const inicios = new Map();
    if (porPagina() > 1) paginasPdf().forEach((g, p) => { if (g.length) inicios.set(g[0], nombrePagina(p)); });
    fotos.forEach((f, i) => {
      const n = String(i + 1).padStart(2, "0");
      ol.append(h("li", { class: "itt-captura rep-foto", "data-sha": f.sha256, "data-indice": i, "data-ubicada": f.lat == null ? "" : "1", "data-pagina": inicios.get(i) },
        h("span", { class: "rep-asa", "aria-hidden": "true", title: "Arrastra para mover" }, icono("grip-vertical")),
        miniaturaDe(f),
        h("div", {},
          h("strong", {}, `Foto ${n}`),
          h("span", { class: "tv-tenue rep-meta" }, ` · ${horaCorta(f.tomada)}`),
          h("div", { class: "tv-tenue rep-ubicacion" }, icono(f.lat == null ? "triangle-alert" : "map-pin"), h("span", {}, textoUbicacion(f))),
          h("div", { class: "tv-campo" },
            h("label", { for: `nota-${i}` }, `Nota de la foto ${i + 1}`),
            h("input", { id: `nota-${i}`, type: "text", maxLength: 1000, value: f.nota || "", autocomplete: "off", oninput: (e) => { f.nota = e.target.value.slice(0, 1000); programarGuardado(); } })),
          h("div", { class: "rep-botones" },
            fotos.length > 1 ? boton({ class: "tv-boton tv-boton--secundario", "data-itt": "mover", "aria-label": `Mover la foto ${i + 1}`, onclick: () => abrirMover(i) }, "move", "Mover") : null,
            boton({ class: "tv-boton tv-boton--secundario", "data-itt": "ubicar", onclick: () => abrirUbicar(i) }, "map-pin", f.lat == null ? "Ubicar" : "Cambiar ubicación"),
            boton({ class: "tv-boton tv-boton--secundario itt-boton-plano", onclick: () => quitarFoto(i), etiqueta: `Quitar la foto ${i + 1}` }, "trash-2")))));
    });
    ol.dataset.arrastrable = fotos.length > 1 ? "1" : "";
    pintarRejilla();
    pintarContador();
  }

  /** Vista «Ordenar»: miniaturas numeradas, agrupadas como quedan en el PDF. Tocar una abre «Mover»; también se arrastran. */
  function pintarRejilla() {
    const caja = $("rep-rejilla");
    limpiar(caja);
    $("rep-fotos").hidden = ordenando;
    caja.hidden = !ordenando;
    $("rep-ordenar").setAttribute("aria-pressed", String(ordenando));
    if (!ordenando) return;
    const fotos = reg.datos.fotos;
    const miniatura = (i) => h("li", { class: "rep-miniatura", "data-indice": i, "data-sha": fotos[i].sha256 },
      h("button", { type: "button", class: "rep-miniatura__boton", "aria-label": `Foto ${i + 1}. Mover`, title: `Foto ${i + 1}` },
        miniaturaDe(fotos[i]), h("span", { class: "rep-miniatura__numero", "aria-hidden": "true" }, String(i + 1))));
    if (porPagina() === 1) {                                                // una por página: el número ya dice la página
      caja.append(h("ol", { class: "rep-miniaturas" }, fotos.map((_, i) => miniatura(i))));
      return;
    }
    paginasPdf().forEach((g, p) => {
      if (!g.length) return;
      caja.append(h("section", { class: "rep-grupo", "aria-label": nombrePagina(p) },
        h("h3", { class: "rep-grupo__titulo" }, nombrePagina(p)),
        h("ol", { class: "rep-miniaturas", start: g[0] + 1 }, g.map(miniatura))));
    });
  }

  function anunciar(texto) {
    const e = $("rep-anuncio");
    e.textContent = "";
    setTimeout(() => { e.textContent = texto; }, 50);                           // vaciar y volver a escribir: se anuncia aunque se repita
  }

  /** Mueve la foto `desde` a la posición `hasta` (índices), guarda y deja la foto a la vista, resaltada y con el foco. */
  function moverFoto(desde, hasta) {
    const fotos = reg.datos.fotos;
    hasta = Math.max(0, Math.min(fotos.length - 1, hasta));
    if (desde === hasta || desde < 0 || desde >= fotos.length) return;
    const [f] = fotos.splice(desde, 1);
    fotos.splice(hasta, 0, f);
    pintarFotos();
    programarGuardado();
    anunciar(`Foto ${desde + 1} movida a la posición ${hasta + 1}`);
    const el = document.querySelector(ordenando ? `#rep-rejilla [data-sha="${f.sha256}"]` : `#rep-fotos > [data-sha="${f.sha256}"]`);
    if (!el) return;
    const quieto = matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: quieto ? "auto" : "smooth" });
    el.classList.add("rep-movida");
    setTimeout(() => el.classList.remove("rep-movida"), 1600);
    const foco = el.querySelector(ordenando ? ".rep-miniatura__boton" : "[data-itt=mover]");
    if (foco) foco.focus({ preventScroll: true });
  }

  function abrirMover(i) {
    const n = reg.datos.fotos.length;
    moviendo = i;
    $("dlg-mover-titulo").textContent = `Mover la foto ${String(i + 1).padStart(2, "0")}`;
    $("mover-principio").disabled = i === 0;
    $("mover-final").disabled = i === n - 1;
    const campo = $("mover-posicion");
    campo.value = ""; campo.max = String(n); campo.placeholder = `1 a ${n}`;
    campo.removeAttribute("aria-invalid");
    $("mover-error").hidden = true;
    $("dlg-mover").showModal();
  }
  function moverDesdeHoja(hasta) {
    const i = moviendo;
    $("dlg-mover").close();
    moviendo = -1;
    if (i >= 0) moverFoto(i, hasta);
  }
  function moverAPosicion(e) {
    e.preventDefault();
    const n = reg.datos.fotos.length;
    const campo = $("mover-posicion");
    const texto = campo.value.trim();
    const p = Number(texto);
    if (!/^\d+$/.test(texto) || p < 1 || p > n) {
      $("mover-error").textContent = `Escribe un número del 1 al ${n}.`;
      $("mover-error").hidden = false;
      campo.setAttribute("aria-invalid", "true");
      campo.focus();
      return;
    }
    moverDesdeHoja(p - 1);
  }

  async function quitarFoto(i) {
    const f = reg.datos.fotos[i];
    if (!(await confirmar(`¿Quitar la foto ${String(i + 1).padStart(2, "0")} del reporte?`, { aceptar: "Quitar foto", peligro: true }))) return;
    reg.datos.fotos.splice(i, 1);
    for (const it of await ITT.cola.listar()) if (it.sha256 === f.sha256 && it.meta && it.meta.referencia === `reporte:${reg.id}` && it.estado !== "subiendo") await ITT.cola.borrar(it.id);
    pintarFotos(); programarGuardado();
  }

  // -- Captura --------------------------------------------------------------------------------------------------------------------
  function iniciarUbicacion() {
    if (observador !== null || !navigator.geolocation) { if (!navigator.geolocation) $("rep-geo").textContent = "Este navegador no da ubicación: las fotos irán «sin ubicación»."; return; }
    observador = navigator.geolocation.watchPosition(
      (p) => { posicion = { lat: p.coords.latitude, lon: p.coords.longitude, precision: p.coords.accuracy, en: Date.now() }; $("rep-geo").textContent = `Ubicación activa (±${Math.round(p.coords.accuracy)} m)`; },
      (e) => { $("rep-geo").textContent = e.code === 1 ? "Ubicación negada: las fotos se guardan «sin ubicación» y puedes ubicarlas a mano." : "Ubicación no disponible por ahora."; },
      { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 });
  }

  const miniaturaBlob = async (blob) => {
    const mapa = await createImageBitmap(blob);
    const escala = Math.min(1, 160 / Math.max(mapa.width, mapa.height));
    const lienzo = document.createElement("canvas");
    lienzo.width = Math.max(1, Math.round(mapa.width * escala)); lienzo.height = Math.max(1, Math.round(mapa.height * escala));
    lienzo.getContext("2d").drawImage(mapa, 0, 0, lienzo.width, lienzo.height);
    return lienzo.toDataURL("image/jpeg", 0.7);
  };

  async function capturar(archivo, origen) {
    if (reg.datos.fotos.length >= MAX_FOTOS()) { mensaje(`Un reporte admite hasta ${MAX_FOTOS()} fotos.`, "error"); return false; }
    try {
      const r = await ITT.reducirImagen(archivo);
      if (reg.datos.fotos.some((f) => f.sha256 === r.sha256)) { mensaje("Esa foto ya está en el reporte.", "info"); return false; }
      const f = { sha256: r.sha256, nota: $("rep-nota-siguiente").value.trim().slice(0, 1000), tomada: r.exif.tomada || ahoraLocal(), lat: null, lon: null, precision: null, sin_ubicacion: true, nombre: (archivo.name || "foto").replace(/\.[^.]+$/, "").slice(0, 150) };
      if (r.exif.lat !== undefined) { f.lat = Math.round(r.exif.lat * 1e6) / 1e6; f.lon = Math.round(r.exif.lon * 1e6) / 1e6; }
      else if (origen === "camara" && posicion && Date.now() - posicion.en < 120000) { f.lat = Math.round(posicion.lat * 1e6) / 1e6; f.lon = Math.round(posicion.lon * 1e6) / 1e6; f.precision = Math.round(posicion.precision); }
      f.sin_ubicacion = f.lat === null;
      const meta = { tomada: f.tomada, referencia: `reporte:${reg.id}`, origen, orden: reg.datos.fotos.length, nota: f.nota };
      if (f.lat !== null) { meta.lat = f.lat; meta.lon = f.lon; if (f.precision !== null) meta.precision = f.precision; } else meta.sin_ubicacion = true;
      const cuenta = await ITT.cola.ajuste("cuenta", null);
      await ITT.cola.agregar({ destino: "herramientas.captura", blob: r.blob, nombre: `${f.nombre}.jpg`, tipo: "image/jpeg", sha256: r.sha256, ancho: r.ancho, alto: r.alto, meta, cuenta });
      reg.miniaturas = Object.assign({}, reg.miniaturas, { [r.sha256]: await miniaturaBlob(r.blob) });
      reg.datos.fotos.push(f);
      ITT.guardadas = (ITT.guardadas || 0) + 1;                       // las pruebas lo leen
      document.documentElement.dataset.guardadas = String(ITT.guardadas);
      return true;
    } catch (e) {
      mensaje(ITT.motivoAlGuardarFoto(e, archivo.name), "error");
      return false;
    }
  }

  async function agregar(archivos, origen) {
    let n = 0;
    for (const a of archivos) if (await capturar(a, origen)) n += 1;
    if (n) { pintarFotos(); await guardarYa(); ITT.pedirAlmacenamiento && ITT.pedirAlmacenamiento(); }
  }

  // Arrastrar archivos o carpetas (computadora).
  async function archivosDeEntrada(entrada, salida) {
    if (entrada.isFile) { await new Promise((ok) => entrada.file((f) => { if (/^image\//.test(f.type) || /\.(jpe?g|png)$/i.test(f.name)) salida.push(f); ok(); }, ok)); return; }
    if (entrada.isDirectory) {
      const lector = entrada.createReader();
      for (;;) {
        const lote = await new Promise((ok) => lector.readEntries(ok, () => ok([])));
        if (!lote.length) break;
        for (const hijo of lote) await archivosDeEntrada(hijo, salida);
      }
    }
  }
  async function soltar(e) {
    e.preventDefault();
    $("rep-soltar").classList.remove("rep-soltar--activa");
    if (!reg) return;
    const archivos = [];
    const items = [...(e.dataTransfer.items || [])];
    const planos = [...(e.dataTransfer.files || [])];                    // se copian ahora: el navegador vacía el DataTransfer al terminar el evento
    if (items.length && items[0].webkitGetAsEntry) {
      const entradas = items.map((i) => i.webkitGetAsEntry()).filter(Boolean);
      for (const entrada of entradas) await archivosDeEntrada(entrada, archivos);
    }
    if (!archivos.length) archivos.push(...planos.filter((f) => /^image\//.test(f.type) || /\.(jpe?g|png)$/i.test(f.name)));
    archivos.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    if (archivos.length) await agregar(archivos, "galeria");
  }

  // -- Ubicar a mano --------------------------------------------------------------------------------------------------------------
  let mapa = null, marcador = null, maplibreCargado = null;
  function cargarMapLibre() {
    if (maplibreCargado) return maplibreCargado;
    maplibreCargado = new Promise((ok, mal) => {
      const css = h("link", { rel: "stylesheet", href: raiz.dataset.maplibreCss });
      document.head.append(css);
      const s = h("script", { src: raiz.dataset.maplibre, onload: () => ok(self.maplibregl), onerror: () => mal(new Error("no se pudo cargar el mapa")) });
      document.head.append(s);
    });
    return maplibreCargado;
  }
  const plantillaTeselas = () => `${location.origin}${(urls.teselas || "").replace("{capa}", "osm")}`;

  function ponerPunto(lat, lon, centrar) {
    $("u-lat").value = lat.toFixed(6); $("u-lon").value = lon.toFixed(6);
    if (mapa && marcador) { marcador.setLngLat([lon, lat]); if (centrar) mapa.easeTo({ center: [lon, lat], duration: 0 }); }
  }
  async function abrirUbicar(i) {
    ubicando = i;
    const f = reg.datos.fotos[i];
    $("dlg-ubicar-titulo").textContent = `Ubicar la foto ${String(i + 1).padStart(2, "0")}`;
    $("u-lat").value = f.lat == null ? "" : f.lat.toFixed(6); $("u-lon").value = f.lon == null ? "" : f.lon.toFixed(6);
    $("u-error").hidden = true;
    $("u-mapa-info").textContent = "";
    $("dlg-ubicar").showModal();
    if (!enLinea()) { $("u-mapa-info").textContent = "Sin conexión: escribe las coordenadas. El mapa necesita señal."; return; }
    try {
      const lib = await cargarMapLibre();
      lib.setWorkerUrl(raiz.dataset.maplibreWorker);                       // de la plantilla: en producción el nombre lleva huella
      const centro = f.lat != null ? [f.lon, f.lat] : posicion ? [posicion.lon, posicion.lat] : (reg.datos.fotos.find((x) => x.lat != null) ? [reg.datos.fotos.find((x) => x.lat != null).lon, reg.datos.fotos.find((x) => x.lat != null).lat] : [-106.07, 28.63]);
      if (mapa) { mapa.remove(); mapa = null; }
      mapa = new lib.Map({ container: "u-mapa", attributionControl: false, center: centro, zoom: f.lat != null || posicion ? 17 : 12,
        style: { version: 8, sources: { osm: { type: "raster", tiles: [plantillaTeselas()], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" } },
          layers: [{ id: "fondo", type: "background", paint: { "background-color": "#E7EDF1" } }, { id: "osm", type: "raster", source: "osm" }] } });
      mapa.addControl(new lib.NavigationControl({ showCompass: false }), "top-right");
      mapa.addControl(new lib.AttributionControl({ compact: true }));
      marcador = new lib.Marker({ color: "#E53935", draggable: true }).setLngLat(centro).addTo(mapa);
      marcador.on("dragend", () => { const p = marcador.getLngLat(); $("u-lat").value = p.lat.toFixed(6); $("u-lon").value = p.lng.toFixed(6); });
      mapa.on("click", (e) => ponerPunto(e.lngLat.lat, e.lngLat.lng, false));
      document.documentElement.dataset.mapaListo = "1";
    } catch (e) {
      console.error("Mapa para ubicar la foto:", e);
      $("u-mapa-info").textContent = "No se pudo cargar el mapa. Escribe las coordenadas.";
    }
  }
  function guardarUbicacion() {
    const lat = parseFloat($("u-lat").value.replace(",", ".")), lon = parseFloat($("u-lon").value.replace(",", "."));
    const vacios = $("u-lat").value.trim() === "" && $("u-lon").value.trim() === "";
    const f = reg.datos.fotos[ubicando];
    if (vacios) { f.lat = f.lon = f.precision = null; f.sin_ubicacion = true; }
    else if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      $("u-error").textContent = "Escribe una latitud entre -90 y 90 y una longitud entre -180 y 180."; $("u-error").hidden = false; return false;
    } else { f.lat = Math.round(lat * 1e6) / 1e6; f.lon = Math.round(lon * 1e6) / 1e6; f.precision = null; f.sin_ubicacion = false; }
    $("dlg-ubicar").close("si");
    pintarFotos(); programarGuardado();
    return true;
  }

  // -- PDF -------------------------------------------------------------------------------------------------------------------------------
  const esperarMs = (ms) => new Promise((r) => setTimeout(r, ms));
  async function porSubir() { return pendientesDe(reg.id); }

  async function generarPdf() {
    const d = reg.datos;
    if (!d.titulo.trim()) return mensaje("Escribe un nombre para el reporte.", "error");
    if (!d.fotos.length) return mensaje("Agrega al menos una fotografía.", "error");
    if (!enLinea()) return mensaje("Sin conexión. Genera el PDF cuando vuelva la señal; el reporte y sus fotos ya están guardados.", "error");
    await guardarYa();
    const dlg = $("dlg-pdf");
    const estado = $("pdf-estado"), barra = $("pdf-progreso"), error = $("pdf-error");
    error.hidden = true; barra.hidden = false; barra.value = 0;
    $("pdf-compartir").disabled = true; $("pdf-descargar").disabled = true;
    limpiar($("pdf-paginas"));
    pdfActual = null;
    dlg.showModal();
    try {
      // 1. Todas las fotos tienen que estar en el servidor, y el reporte también.
      let ultimo = -1;
      for (let intento = 0; intento < 600; intento++) {
        const por = await porSubir();
        const r = await ITT.cola.borrador(reg.id);
        if (r && r.error) throw { mensaje: `El servidor no aceptó el reporte: ${r.error}` };
        if (!por && r && !r.sucio) break;
        if (!enLinea()) throw { mensaje: "Se perdió la señal. Las fotos que faltan se suben solas al volver; genera el PDF de nuevo entonces." };
        if (por !== ultimo) { estado.textContent = por ? `Subiendo fotos… faltan ${por}` : "Preparando el reporte…"; ultimo = por; }
        ITT.subirAhora && ITT.subirAhora();
        await esperarMs(1000);
        if (intento === 599) throw { mensaje: "Las fotos tardan en subir. Revisa la señal y vuelve a intentarlo; no se perdió nada." };
      }
      const actual = await ITT.cola.borrador(reg.id);
      reg.servidor_id = actual.servidor_id; reg.revision_base = actual.revision_base;
      // 2. Tarea en el servidor.
      estado.textContent = "Generando el PDF…";
      const t = await docs.pedir(docs.url("pdf", reg.servidor_id), { metodo: "POST", json: {} });
      let fin = null;
      for (let i = 0; i < 400; i++) {
        const tarea = await docs.pedir(`${(urls.tarea || "").replace("00000000-0000-0000-0000-000000000000", t.tarea)}?formato=json`);
        barra.value = Math.round(tarea.fraccion * 100);
        if (tarea.mensaje) estado.textContent = tarea.mensaje;
        if (tarea.terminada) { fin = tarea; break; }
        await esperarMs(1000);
      }
      if (!fin) throw { mensaje: "El PDF tarda más de lo normal. Vuelve a abrir el reporte en unos minutos." };
      if (fin.estado !== "completada") throw { mensaje: fin.estado === "cancelada" ? "Se canceló la generación del PDF." : (fin.error || "No se pudo generar el PDF.") };
      // 3. Descarga y vista previa.
      const respuesta = await docs.pedir(`${docs.url("descargar", reg.servidor_id)}?ver=1`, { crudo: true });
      const blob = await respuesta.blob();
      const disp = respuesta.headers.get("Content-Disposition") || "";
      let nombre = `${d.titulo}.pdf`;
      const m = /filename\*=utf-8''([^;]+)/i.exec(disp) || /filename="([^"]+)"/i.exec(disp);
      if (m) { try { nombre = decodeURIComponent(m[1]); } catch (_) { nombre = m[1]; } }
      pdfActual = { blob, nombre };
      barra.hidden = true;
      estado.textContent = nombre;
      $("pdf-compartir").disabled = false; $("pdf-descargar").disabled = false;
      try { await ITT.vistaPdf.dibujar(blob, $("pdf-paginas"), { pdfjs: raiz.dataset.pdfjs, worker: raiz.dataset.pdfjsWorker }); }
      catch (e) { estado.textContent = `${nombre} (no se pudo mostrar aquí; puedes compartirlo o descargarlo)`; }
    } catch (e) {
      barra.hidden = true;
      error.hidden = false;
      error.textContent = (e && e.mensaje) || `No se pudo generar el PDF: ${(e && e.message) || e || "error desconocido"}`;
    }
  }
  async function compartirPdf() {
    if (!pdfActual) return;
    const r = await ITT.compartirArchivo(pdfActual.blob, pdfActual.nombre, { tipo: "application/pdf", titulo: pdfActual.nombre });
    document.documentElement.dataset.repCompartido = r;
  }
  function descargarPdf() {
    if (!pdfActual) return;
    const a = h("a", { href: URL.createObjectURL(pdfActual.blob), download: pdfActual.nombre, rel: "noopener" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }

  // -- Enrutador ------------------------------------------------------------------------------------------------------------------------
  function mostrar(vista) {
    $("rep-vista-lista").hidden = vista !== "lista";
    $("rep-vista-editor").hidden = vista !== "editor";
    raiz.dataset.vista = vista;
  }
  async function ruta() {
    mensaje("");
    if (reg && temporizador) await guardarYa();
    const s = /^#\/s\/([0-9a-f-]{36})$/.exec(location.hash || "");          // desde la búsqueda: id del servidor
    if (s) { const local = await docs.localDe(s[1]); history.replaceState(null, "", local ? `#/r/${local}` : "#/"); return ruta(); }
    if (location.hash === "#/nuevo") { history.replaceState(null, "", "#/"); if (ctx) return nuevo(); }            // acción «Nuevo …» de la paleta
    const m = /^#\/r\/([0-9a-f-]{36})$/.exec(location.hash || "");
    if (m) {
      let r = await ITT.cola.borrador(m[1]);
      if (!r || r.borrada || r.tipo !== "reporte") { location.hash = "#/"; return; }
      if (r.parcial) {                                                  // del equipo y no se ha abierto aquí: sus datos se traen ahora
        try { r = await docs.abrir(r.id); }
        catch (e) { location.hash = ultimaLista; mensaje(e.clase === "red" ? "Sin conexión: este reporte aún no está en el teléfono. Ábrelo una vez con señal para tenerlo sin conexión." : e.mensaje || "No se pudo abrir.", "error"); return; }
      }
      reg = r;
      ordenando = false;
      if (reg.datos.cliente == null) reg.datos.cliente = "";
      if (!POR_PAGINA.includes(reg.datos.fotos_por_pagina)) reg.datos.fotos_por_pagina = 1;
      sugerencias = await clientesConocidos();
      $("rep-volver").setAttribute("href", ultimaLista);
      mostrar("editor");
      pintarCampos();
      document.title = `${r.datos.titulo || "Reporte"} · Reportes`;
      await comp.abrir(r, { latido: ctx && ctx.latido });
      return;
    }
    comp.cerrar();
    reg = null;
    ultimaLista = lista.hashLista();
    mostrar("lista");
    await pintarLista();
  }

  function enlazar() {
    $("rep-nuevo").addEventListener("click", nuevo);
    $("rep-nuevo-vacio").addEventListener("click", nuevo);
    $("r-titulo").addEventListener("input", (e) => { reg.datos.titulo = e.target.value; $("rep-titulo-editor").textContent = reg.datos.titulo || "Reporte"; programarGuardado(); });
    $("r-cliente").addEventListener("input", (e) => { reg.datos.cliente = e.target.value; programarGuardado(); });
    ITT.combobox($("r-cliente"), () => sugerencias);
    $("r-fecha").addEventListener("input", (e) => { if (e.target.value) { reg.datos.fecha = e.target.value; programarGuardado(); } });
    $("r-croquis").addEventListener("change", (e) => { reg.datos.con_croquis = e.target.checked; programarGuardado(); pintarFotos(); });
    $("r-credenciales").addEventListener("change", (e) => { reg.datos.con_credenciales = e.target.checked; programarGuardado(); });
    for (const r of document.querySelectorAll("[name=r-por-pagina]")) r.addEventListener("change", (e) => { if (e.target.checked) { reg.datos.fotos_por_pagina = Number(e.target.value); programarGuardado(); pintarFotos(); } });
    $("rep-ordenar").addEventListener("click", () => { ordenando = !ordenando; pintarFotos(); });
    ITT.ordenFotos.arrastre($("rep-fotos"), { elementos: ".rep-foto", asa: ".rep-asa", alSoltar: moverFoto });
    ITT.ordenFotos.arrastre($("rep-rejilla"), { elementos: ".rep-miniatura", mantener: ".rep-miniatura__boton", rejilla: true, alSoltar: moverFoto, alTocar: abrirMover });
    $("mover-principio").addEventListener("click", () => moverDesdeHoja(0));
    $("mover-final").addEventListener("click", () => moverDesdeHoja(reg.datos.fotos.length - 1));
    $("form-mover").addEventListener("submit", moverAPosicion);
    $("rep-camara").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregar(f, "camara"); });
    $("rep-galeria").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregar(f, "galeria"); });
    $("rep-camara").addEventListener("click", iniciarUbicacion);
    $("rep-ubicacion").addEventListener("click", iniciarUbicacion);
    const caja = $("rep-soltar");
    for (const ev of ["dragenter", "dragover"]) caja.addEventListener(ev, (e) => { e.preventDefault(); caja.classList.add("rep-soltar--activa"); });
    caja.addEventListener("dragleave", () => caja.classList.remove("rep-soltar--activa"));
    caja.addEventListener("drop", soltar);
    $("rep-pdf").addEventListener("click", generarPdf);
    $("pdf-compartir").addEventListener("click", compartirPdf);
    $("pdf-descargar").addEventListener("click", descargarPdf);
    $("rep-mas").addEventListener("click", () => $("dlg-mas").showModal());
    $("mas-historial").addEventListener("click", () => { $("dlg-mas").close(); comp.historial(); });
    $("mas-borrar").addEventListener("click", async () => { $("dlg-mas").close(); const b = reg; await borrarRegistro(b); if (!(await ITT.cola.borrador(b.id))) { reg = null; location.hash = "#/"; } });
    $("u-actual").addEventListener("click", () => {
      if (!posicion) { iniciarUbicacion(); $("u-error").textContent = "Activa la ubicación del teléfono y espera unos segundos."; $("u-error").hidden = false; return; }
      $("u-error").hidden = true; ponerPunto(posicion.lat, posicion.lon, true);
    });
    $("u-quitar").addEventListener("click", () => { $("u-lat").value = ""; $("u-lon").value = ""; guardarUbicacion(); });
    $("form-ubicar").addEventListener("submit", (e) => { if (!guardarUbicacion()) e.preventDefault(); });
    $("dlg-ubicar").addEventListener("close", () => { if (mapa) { mapa.remove(); mapa = null; } });
    for (const b of document.querySelectorAll("[data-cerrar]")) b.addEventListener("click", () => b.closest("dialog").close());
    addEventListener("hashchange", ruta);
    const red = () => {
      const e = $("rep-red");
      limpiar(e);
      if (!enLinea()) e.append(icono("cloud-off"), h("span", {}, "Sin conexión. Puedes seguir capturando: todo se guarda en este teléfono."));
      e.hidden = enLinea();
      e.dataset.estado = enLinea() ? "en_linea" : "sin_conexion";
      estadoGuardado();
    };
    addEventListener("online", red); addEventListener("offline", red); red();
    document.addEventListener("itt:cola", () => { if (raiz.dataset.vista === "editor" && reg) pintarContador(); else if (raiz.dataset.vista === "lista") pintarLista(); });
    document.addEventListener("itt:doc", async (e) => {
      const d = e.detail || {};
      if (d.tipo !== "reporte") return;
      if (d.tipo_evento === "copia" && reg && reg.id === d.id) {
        await confirmar("Este reporte cambió mientras lo editabas sin conexión y los cambios chocaban. Tu versión, con todas sus fotos, quedó a salvo como copia en conflicto.", { aceptar: "Ver la copia", soloAviso: true });
        location.hash = `#/r/${d.copia}`;
      } else if ((d.tipo_evento === "recuperacion" || d.tipo_evento === "fusionada") && reg && reg.id === d.id) {
        const r = await ITT.cola.borrador(reg.id);
        if (r && !temporizador) { reg = Object.assign(r, { porSubir: reg.porSubir }); pintarCampos(); }
        if (d.tipo_evento === "fusionada") mensaje("Tus cambios sin conexión se juntaron con los del equipo.", "info");
      } else if (d.tipo_evento === "no_borrado") {
        mensaje(d.mensaje || "No se pudo eliminar.", "error");
        if (raiz.dataset.vista === "lista") pintarLista();
      } else if (d.tipo_evento === "sincronizado") {
        if (raiz.dataset.vista === "lista") pintarLista();
        else if (reg) {
          const r = await ITT.cola.borrador(reg.id);
          if (r) { reg.sucio = r.sucio; reg.servidor_id = r.servidor_id; reg.revision_base = r.revision_base; Object.assign(reg, { autor: r.autor, modificado: r.modificado, puede_borrar: r.puede_borrar }); estadoGuardado(); }
        }
      }
    });
  }

  async function asegurarCuenta() {
    if ((await ITT.cola.ajuste("cuenta", null)) != null || !enLinea()) return;
    try {
      const r = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
      if (r.ok) { const d = await r.json(); await ITT.cola.guardarAjuste("cuenta", d.cuenta); await ITT.cola.guardarAjuste("csrf", d.csrf); }
    } catch (_) { /* sin red */ }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    docs.iniciar(urls);
    enlazar();
    await asegurarCuenta();
    try { ctx = await contexto(); } catch (e) { ctx = null; mensaje(e.clase === "sesion" ? "Tu sesión terminó. Vuelve a iniciar sesión." : "Sin conexión. Abre Reportes una vez con señal para poder trabajar sin ella.", "error"); }
    await ruta();
    docs.sincronizar();
    document.documentElement.dataset.repLista = "1";
  });
})();

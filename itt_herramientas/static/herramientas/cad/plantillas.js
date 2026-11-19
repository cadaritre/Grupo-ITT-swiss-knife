/* CAD: presentaciones desde una plantilla, como «Desde plantilla…» de AutoCAD (docs/cad.md, «Presentaciones desde plantilla»).
 *
 * El editor (con el parche 80 del fork, «tv-plantillas-layout») ofrece «Desde plantilla…» al hacer clic derecho en una pestaña de presentación, en la de Model y en
 * «+», y con LAYOUT › Plantilla; y «Guardar como plantilla…» en el menú de cada presentación. Con la página que lo incrusta, no abre sus
 * propios diálogos: llama a `openCadStudioLayoutTemplate({action, document_id, layout})` y esta página hace el resto:
 *
 * - «Desde plantilla…»: una hoja con «Mis plantillas» (las marcadas por esta cuenta), «De la biblioteca» (la misma búsqueda y carpetas que
 *   «Abrir otro plano», con la estrella para agregar a «Mis plantillas») y «De esta computadora» (un archivo, o un dibujo de la carpeta del
 *   proyecto vinculada; se lee aquí y nunca se sube). El editor lee la plantilla (`layout_list`: sus presentaciones con papel y una vista
 *   previa), la persona elige una o varias y el editor las trae con todo lo que usan, en un solo paso de deshacer (`layout_import`).
 * - «Guardar como plantilla…»: el editor escribe la presentación sola con lo que usa (`layout_template_save`) y queda como plano nuevo de
 *   la biblioteca, en el proyecto «Plantillas», marcado en «Mis plantillas».
 *
 * Solo para la pestaña que se puede editar (esta página tiene su bloqueo); en solo lectura y en el modo táctil el editor no muestra esas
 * opciones (`layout_templates {menu}`).
 */
(function plantillas() {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let C;
  try { C = JSON.parse($("cad-config").textContent); } catch (_) { return; }
  const P = C.plantillas;
  if (!P || !$("cad-editor") || !window.TVCad) return;
  // Las dos hojas no vienen en la página (en el teléfono y en el modo táctil no se usan): en la computadora llegan aparte, con su huella
  // en la dirección (se guardan un año), y entonces se arma todo lo de abajo.
  if (!$("cad-plantilla")) {
    if (!C.tactil && matchMedia("(max-width: 767px) and (pointer: coarse)").matches) return;
    let intentos = 0;
    const cargar = async () => {
      if (document.documentElement.classList.contains("cad-tactil")) return;
      try {
        const r = await fetch(P.hojas, { credentials: "same-origin" });
        if (!r.ok) throw new Error(String(r.status));
        const t = document.createElement("template");
        t.innerHTML = await r.text();
        document.body.append(t.content);
        plantillas();
      } catch (_) {
        if (++intentos < 4) setTimeout(cargar, 3000 * intentos);     // sin red un momento: unas veces más; sin plantillas el editor sigue igual
      }
    };
    window.TVCad.listo.then(cargar, () => {});
    return;
  }
  const frame = $("cad-editor"), hoja = $("cad-plantilla"), hojaGuardar = $("cad-plantilla-guardar");
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const LIMITE_LOCAL = 200 * 1048576;            // la memoria del editor en el navegador; la biblioteca ya tiene su propio tope
  const avisar = (texto, tipo) => { if (window.tvAviso) window.tvAviso(texto, tipo); };
  const csrf = () => { const x = document.querySelector("input[name=csrfmiddlewaretoken]"); return x ? x.value : ""; };
  const TELEFONO = !C.tactil && matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
  const enTactil = () => document.documentElement.classList.contains("cad-tactil");
  const tamano = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  function icono(nombre) {
    const x = document.querySelector(`#cad-plantilla-iconos [data-cad-icono="${nombre}"] svg`) || document.querySelector(`#cad-iconos [data-cad-icono="${nombre}"] svg`);
    return x ? x.cloneNode(true) : document.createTextNode("");
  }
  function base64(buffer) {
    const bytes = new Uint8Array(buffer); let texto = "";
    for (let i = 0; i < bytes.length; i += 0x8000) texto += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(texto);
  }
  function deBase64(texto) {
    const crudo = atob(texto); const bytes = new Uint8Array(crudo.length);
    for (let i = 0; i < crudo.length; i++) bytes[i] = crudo.charCodeAt(i);
    return bytes;
  }
  async function json(url, opciones = {}) {
    const r = await fetch(url, { credentials: "same-origin", ...opciones, headers: { Accept: "application/json", ...(opciones.headers || {}) } });
    let d = {};
    try { d = await r.json(); } catch (_) { /* sin cuerpo */ }
    if (!r.ok) throw new Error(d.error || (r.status === 401 ? "Tu sesión terminó. Entra de nuevo en otra pestaña." : "No se pudo completar."));
    return d;
  }
  // Una petición al editor; con una orden de la persona a medias, el editor la rechaza (`command_busy`): como AutoCAD al abrir un diálogo,
  // la orden se cancela (un Esc) y se pide otra vez.
  async function editor(peticion, tiempoMs = 120000) {
    try { return await window.TVCad.pedir(peticion, { tiempoMs }); }
    catch (err) {
      if (err.respuesta && err.respuesta.code === "command_busy") {
        await window.TVCad.pedir({ op: "cancel" }, { tiempoMs: 10000 }).catch(() => {});
        return window.TVCad.pedir(peticion, { tiempoMs });
      }
      throw err;
    }
  }

  // ── El editor: el gancho y el menú ─────────────────────────────────────────────────────────────────────────────────────────
  let editable = false;
  const puede = () => editable && !enTactil() && !TELEFONO;
  function enganchar() {
    try {
      frame.contentWindow.openCadStudioLayoutTemplate = (d) => {
        const pedido = { action: String(d && d.action || ""), documento: Number(d && d.document_id), layout: d && d.layout ? String(d.layout) : "" };
        setTimeout(() => alPedir(pedido), 0);               // fuera del ciclo del editor
      };
    } catch (_) { /* marco de otro origen: no pasa (mismo origen por diseño) */ }
  }
  let menuEnviado = null;
  async function menu() {
    if (!window.TVCad.estaListo()) return;
    const on = puede();
    if (menuEnviado === on) return;
    menuEnviado = on;
    try { await window.TVCad.pedir({ op: "layout_templates", menu: on }, { tiempoMs: 10000 }); }
    catch (err) { menuEnviado = null; console.warn("CAD: el editor no tomó el menú de plantillas", err.message, err.respuesta || ""); }
  }
  window.TVCad.on("cargado", (d) => { editable = !!d.editable; menuEnviado = null; enganchar(); menu(); });
  // Las hojas llegan aparte: si el plano ya se abrió antes, lo mismo que al cargarse.
  setTimeout(() => { if (window.TVCad.estaListo() && menuEnviado === null) { editable = !!window.TVCad.estado().editable; enganchar(); menu(); } }, 0);
  document.addEventListener("tvcad:editable", (ev) => { editable = !!(ev.detail && ev.detail.editable); menu(); });
  new MutationObserver(() => menu()).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

  function alPedir(pedido) {
    const est = window.TVCad.estado();
    if (pedido.documento && est.documento && pedido.documento !== est.documento) return;      // de otra pestaña: no pasa
    if (!puede()) { avisar(enTactil() ? "En el modo táctil no se insertan presentaciones." : "Este plano está en solo lectura.", "error"); return; }
    if (pedido.action === "import") abrir();
    else if (pedido.action === "save" && pedido.layout) abrirGuardar(pedido.layout);
  }

  // ── «Desde plantilla…» ───────────────────────────────────────────────────────────────────────────────────────────────────────
  const estado = $("cad-plantilla-estado"), insertarB = $("cad-plantilla-insertar");
  const decir = (texto) => { estado.textContent = texto || ""; };
  const LLAVE_FUENTE = "tv.cad.plantilla-fuente";
  let fuente = "mias", plantilla = null, mias = null, trabajando = false;

  function verPaso(presentaciones) {
    $("cad-plantilla-origen").hidden = presentaciones;
    $("cad-plantilla-presentaciones").hidden = !presentaciones;
    insertarB.hidden = !presentaciones;
  }
  function verFuente(f) {
    fuente = f;
    for (const r of document.querySelectorAll('input[name="cad-plantilla-fuente"]')) r.checked = r.value === f;
    for (const s of hoja.querySelectorAll("section[data-fuente]")) s.hidden = s.dataset.fuente !== f;
    try { localStorage.setItem(LLAVE_FUENTE, f); } catch (_) { /* sin almacenamiento */ }
    decir("");
    if (f === "mias") cargarMias();
    else if (f === "biblioteca") cargarBiblioteca(false);
    else pintarCarpeta();
  }
  async function abrir() {
    plantilla = null;
    verPaso(false);
    let f = "mias";
    try { f = localStorage.getItem(LLAVE_FUENTE) || "mias"; } catch (_) { /* */ }
    hoja.showModal();
    // Sin plantillas propias todavía, se empieza por la biblioteca.
    if (f === "mias") { await cargarMias(); if (mias && !mias.length) f = "biblioteca"; }
    verFuente(f);
  }
  for (const r of document.querySelectorAll('input[name="cad-plantilla-fuente"]')) r.addEventListener("change", () => { if (r.checked) verFuente(r.value); });
  hoja.addEventListener("close", () => { plantilla = null; if (!trabajando) window.TVCad.pedir({ op: "layout_templates", release: true }, { tiempoMs: 5000 }).catch(() => {}); });

  function filaPlano(x, { estrella = true } = {}) {
    const li = document.createElement("li"); li.className = "cad-plantilla__fila";
    const b = document.createElement("button"); b.type = "button"; b.className = "cad-elegir__plano"; b.disabled = !x.disponible;
    const fmt = document.createElement("span"); fmt.className = "itt-formato cad-elegir__formato"; fmt.dataset.formato = x.formato; fmt.textContent = x.formato.toUpperCase();
    const texto = document.createElement("span"); texto.className = "cad-elegir__texto";
    const fuerte = document.createElement("strong"); fuerte.textContent = x.nombre;
    const meta = document.createElement("span"); meta.className = "tv-tenue";
    meta.textContent = [x.proyecto, x.disponible ? tamano(x.bytes) : "Archivo no disponible"].filter(Boolean).join(" · ");
    texto.append(fuerte, meta); b.append(fmt, texto);
    b.addEventListener("click", () => desdeBiblioteca(x));
    li.append(b);
    if (estrella) {
      const s = document.createElement("button"); s.type = "button"; s.className = "tv-boton tv-boton--fantasma tv-boton--icono cad-plantilla__estrella";
      const pintar = () => {
        s.setAttribute("aria-pressed", String(!!x.plantilla));
        s.setAttribute("aria-label", x.plantilla ? `Quitar «${x.nombre}» de Mis plantillas` : `Agregar «${x.nombre}» a Mis plantillas`);
        s.dataset.tvTooltip = x.plantilla ? "Quitar de Mis plantillas" : "Agregar a Mis plantillas";
        s.replaceChildren(icono(x.plantilla ? "star" : "star-off"));
      };
      pintar();
      s.addEventListener("click", async () => {
        try {
          const d = await json(P.marcar.replace(MARCA, x.id), { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ en: !x.plantilla }) });
          x.plantilla = d.plantilla; mias = null; pintar();
          if (fuente === "mias" && !x.plantilla) li.remove();
          avisar(x.plantilla ? `«${x.nombre}» está en Mis plantillas.` : `«${x.nombre}» ya no está en Mis plantillas.`);
        } catch (err) { avisar(err.message, "error"); }
      });
      li.append(s);
    }
    return li;
  }
  async function cargarMias() {
    const lista = $("cad-plantilla-mias");
    if (!mias) {
      decir("Buscando…");
      try { mias = (await json(P.mias)).plantillas; } catch (err) { decir(err instanceof TypeError ? "Sin conexión." : err.message); return; }
    }
    lista.replaceChildren(...mias.map((x) => filaPlano(x)));
    $("cad-plantilla-mias-vacio").hidden = mias.length > 0;
    if (fuente === "mias") decir("");
  }
  const bib = { q: "", proyecto: "", pagina: 1, paginas: 1, n: 0 };
  async function cargarBiblioteca(mas) {
    const lista = $("cad-plantilla-lista");
    if (!mas) bib.pagina = 1;
    const n = ++bib.n;
    decir("Buscando…");
    let d;
    try { d = await json(`${C.pestanas.elegir}?${new URLSearchParams({ q: bib.q, proyecto: bib.proyecto, pagina: String(bib.pagina) })}`); }
    catch (err) { if (n === bib.n) decir(err instanceof TypeError ? "Sin conexión." : err.message); return; }
    if (n !== bib.n) return;
    bib.paginas = d.paginas;
    if (d.proyectos) pintarCarpetas(d.proyectos);
    const filas = d.planos.map((x) => filaPlano(x));
    if (mas) lista.append(...filas); else lista.replaceChildren(...filas);
    decir(d.total ? (d.total === 1 ? "1 plano" : `${d.total} planos`) : "Ningún plano coincide.");
    $("cad-plantilla-mas").hidden = bib.pagina >= bib.paginas;
  }
  function pintarCarpetas(proyectos) {
    const caja = $("cad-plantilla-carpetas");
    caja.hidden = !proyectos.length;
    const chip = (texto, valor) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "cad-elegir__carpeta"; b.textContent = texto;
      b.setAttribute("aria-pressed", String(bib.proyecto === valor));
      b.addEventListener("click", () => { bib.proyecto = valor; for (const x of caja.children) x.setAttribute("aria-pressed", String(x === b)); cargarBiblioteca(false); });
      return b;
    };
    // «Plantillas» (donde quedan las guardadas) va primero.
    const orden = [...proyectos].sort((a, b) => (b === "Plantillas") - (a === "Plantillas"));
    caja.replaceChildren(chip("Todos", ""), ...orden.slice(0, 24).map((p) => chip(p, p)));
  }
  let esperaQ = 0;
  $("cad-plantilla-q").addEventListener("input", (ev) => { clearTimeout(esperaQ); esperaQ = setTimeout(() => { bib.q = ev.target.value.trim(); cargarBiblioteca(false); }, 250); });
  $("cad-plantilla-form").addEventListener("submit", (ev) => { ev.preventDefault(); bib.q = $("cad-plantilla-q").value.trim(); cargarBiblioteca(false); });
  $("cad-plantilla-mas").addEventListener("click", () => { bib.pagina += 1; cargarBiblioteca(true); });

  // De esta computadora: un archivo elegido o soltado, o un dibujo de la carpeta del proyecto. Se lee aquí; nada se sube.
  function pintarCarpeta() {
    const dibujos = window.TVCadLocal && window.TVCadLocal.dibujos ? window.TVCadLocal.dibujos() : [];
    $("cad-plantilla-carpeta").hidden = !dibujos.length;
    $("cad-plantilla-carpeta-lista").replaceChildren(...dibujos.slice(0, 200).map((x) => {
      const li = document.createElement("li");
      const b = document.createElement("button"); b.type = "button"; b.className = "cad-elegir__plano";
      const fmt = document.createElement("span"); fmt.className = "itt-formato cad-elegir__formato";
      const ext = x.nombre.split(".").pop().toLowerCase(); fmt.dataset.formato = ext === "dxf" ? "dxf" : "dwg"; fmt.textContent = ext.toUpperCase();
      const texto = document.createElement("span"); texto.className = "cad-elegir__texto";
      const fuerte = document.createElement("strong"); fuerte.textContent = x.nombre;
      const meta = document.createElement("span"); meta.className = "tv-tenue"; meta.textContent = x.ruta;
      texto.append(fuerte, meta); b.append(fmt, texto);
      b.addEventListener("click", async () => {
        try { const f = await window.TVCadLocal.archivo(x.ruta); if (f) await desdeArchivo(f); else decir("Ese archivo ya no está en la carpeta."); }
        catch (_) { decir("El navegador no dejó leer ese archivo. Vuelve a vincular la carpeta."); }
      });
      li.append(b);
      return li;
    }));
  }
  $("cad-plantilla-archivo").addEventListener("change", (ev) => { const f = ev.target.files && ev.target.files[0]; ev.target.value = ""; if (f) desdeArchivo(f); });
  const zona = $("cad-plantilla-soltar");
  zona.addEventListener("dragover", (ev) => { ev.preventDefault(); zona.dataset.encima = "1"; });
  zona.addEventListener("dragleave", () => { delete zona.dataset.encima; });
  zona.addEventListener("drop", (ev) => { ev.preventDefault(); delete zona.dataset.encima; const f = ev.dataTransfer.files && ev.dataTransfer.files[0]; if (f) desdeArchivo(f); });

  async function desdeArchivo(f) {
    if (!/\.(dwt|dwg|dxf)$/i.test(f.name)) { decir("Elige un dibujo DWT, DWG o DXF."); return; }
    if (f.size > LIMITE_LOCAL) { decir(`«${f.name}» pesa ${tamano(f.size)}: para una plantilla, a lo más ${tamano(LIMITE_LOCAL)}.`); return; }
    decir(`Leyendo «${f.name}»…`);
    await leer(f.name, await f.arrayBuffer(), null);
  }
  async function desdeBiblioteca(x) {
    if (!x.disponible || trabajando) return;
    decir(`Abriendo «${x.nombre}»…`);
    let buffer;
    try {
      const r = await fetch(x.archivo, { credentials: "same-origin" });
      if (!r.ok) throw new Error(r.status === 404 ? "El archivo de ese plano no está en el servidor." : "No se pudo bajar la plantilla.");
      buffer = await r.arrayBuffer();
    } catch (err) { decir(err instanceof TypeError ? "Sin conexión." : err.message); return; }
    await leer(`${x.nombre}.${x.formato}`, buffer, x);
  }
  // El editor lee la plantilla (en su trabajador) y dice sus presentaciones; la guarda hasta insertar (`template_id`).
  async function leer(nombre, buffer, plano) {
    trabajando = true;
    try {
      const r = await editor({ op: "layout_list", name: nombre, data_base64: base64(buffer) });
      const res = r.result || {};
      if (!res.layouts || !res.layouts.length) { decir(`«${nombre}» no tiene presentaciones que insertar.`); return; }
      plantilla = { id: res.template_id, nombre, plano, presentaciones: res.layouts.map((x) => ({ ...x, elegida: res.layouts.length === 1 })) };
      pintarPresentaciones();
      verPaso(true);
      decir("");
    } catch (err) {
      const razon = err.respuesta && err.respuesta.error;
      decir(`No se pudo leer «${nombre}»${razon ? `: ${razon}` : "."}`);
    } finally { trabajando = false; }
  }

  // Vista previa: el SVG que hace el editor (rect y path, números nada más). Se arma con DOMParser y solo pasan esos elementos.
  function vistaPrevia(svgTexto) {
    const caja = document.createElement("span"); caja.className = "cad-plantilla__vista"; caja.setAttribute("aria-hidden", "true");
    if (!svgTexto) return caja;
    try {
      const doc = new DOMParser().parseFromString(svgTexto, "image/svg+xml");
      const raiz = doc.documentElement;
      if (raiz.nodeName !== "svg" || raiz.querySelector(":not(rect):not(path)")) return caja;
      for (const el of [raiz, ...raiz.querySelectorAll("*")]) for (const a of [...el.attributes]) if (!/^(xmlns|viewBox|fill|stroke|stroke-width|stroke-linecap|stroke-linejoin|opacity|x|y|width|height|d)$/.test(a.name)) el.removeAttribute(a.name);
      caja.append(document.importNode(raiz, true));
    } catch (_) { /* sin vista */ }
    return caja;
  }
  function papel(x) {
    const nombre = String(x.paper || "").replace(/_/g, " ").split(" (")[0].trim();
    const medida = x.width_mm > 0 ? `${Math.round(x.width_mm)} × ${Math.round(x.height_mm)} mm` : "";
    return [nombre, medida].filter(Boolean).join(" · ");
  }
  function pintarPresentaciones() {
    $("cad-plantilla-de").textContent = `Presentaciones de «${plantilla.nombre}»`;
    const lista = $("cad-plantilla-capas");
    lista.replaceChildren(...plantilla.presentaciones.map((x) => {
      const li = document.createElement("li");
      const etiqueta = document.createElement("label"); etiqueta.className = "cad-plantilla__presentacion";
      const casilla = document.createElement("input"); casilla.type = "checkbox"; casilla.checked = x.elegida;
      casilla.addEventListener("change", () => { x.elegida = casilla.checked; pintarInsertar(); });
      const texto = document.createElement("span"); texto.className = "cad-elegir__texto";
      const fuerte = document.createElement("strong"); fuerte.textContent = x.name;
      const meta = document.createElement("span"); meta.className = "tv-tenue";
      const vp = x.viewports === 1 ? "1 ventana" : x.viewports ? `${x.viewports} ventanas` : "sin ventanas";
      meta.textContent = [papel(x), vp].filter(Boolean).join(" · ");
      texto.append(fuerte, meta);
      etiqueta.append(casilla, vistaPrevia(x.preview_svg), texto);
      li.append(etiqueta);
      return li;
    }));
    pintarInsertar();
  }
  function pintarInsertar() {
    const n = plantilla ? plantilla.presentaciones.filter((x) => x.elegida).length : 0;
    insertarB.disabled = !n || trabajando;
    $("cad-plantilla-insertar-texto").textContent = n > 1 ? `Insertar ${n} presentaciones` : "Insertar";
    $("cad-plantilla-todas").textContent = plantilla && plantilla.presentaciones.every((x) => x.elegida) ? "Ninguna" : "Todas";
  }
  $("cad-plantilla-todas").addEventListener("click", () => {
    if (!plantilla) return;
    const todas = !plantilla.presentaciones.every((x) => x.elegida);
    for (const x of plantilla.presentaciones) x.elegida = todas;
    pintarPresentaciones();
  });
  $("cad-plantilla-atras").addEventListener("click", () => { plantilla = null; verPaso(false); verFuente(fuente); });

  insertarB.addEventListener("click", async () => {
    if (!plantilla || trabajando) return;
    const elegidas = plantilla.presentaciones.filter((x) => x.elegida).map((x) => x.name);
    if (!elegidas.length) return;
    if (!puede()) { decir("Este plano ya no se puede editar aquí."); return; }
    trabajando = true; pintarInsertar();
    decir(elegidas.length > 1 ? "Insertando las presentaciones…" : "Insertando la presentación…");
    window.TVCad.ocupar();
    let r;
    try { r = await editor({ op: "layout_import", template_id: plantilla.id, layouts: elegidas }); }
    catch (err) {
      const razon = err.respuesta && err.respuesta.code === "template_expired" ? "La plantilla ya no está en el editor; elígela otra vez." : err.respuesta && err.respuesta.error;
      decir(razon ? `No se insertó: ${razon}` : "No se insertó. Inténtalo de nuevo.");
      return;
    } finally { window.TVCad.soltar(); trabajando = false; pintarInsertar(); }
    const res = r.result || {};
    const hechas = res.layouts || [];
    let texto = hechas.length === 1
      ? (hechas[0].renamed ? `Se insertó «${hechas[0].template}» como «${hechas[0].name}»: ese nombre ya existía.` : `Se insertó la presentación «${hechas[0].name}».`)
      : `Se insertaron ${hechas.length} presentaciones: ${hechas.map((x) => `«${x.name}»`).join(", ")}.`;
    const mantenidos = [...(res.kept_blocks || []), ...(res.kept_styles || [])];
    if (mantenidos.length) texto += ` Este plano ya tenía ${mantenidos.slice(0, 4).join(", ")}${mantenidos.length > 4 ? "…" : ""}: se usó lo suyo.`;
    if (res.viewports) texto += " Ajusta las ventanas a lo que quieres mostrar.";
    const plano = plantilla.plano;
    hoja.close();
    avisar(texto);
    if (plano) fetch(P.marcar.replace(MARCA, plano.id), { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ usada: true }) }).then(() => { mias = null; }).catch(() => {});
  });

  // ── «Guardar como plantilla…» ────────────────────────────────────────────────────────────────────────────────────────────────
  let guardarDe = "";
  function abrirGuardar(layout) {
    guardarDe = layout;
    $("cad-plantilla-nombre").value = layout;
    $("cad-plantilla-guardar-estado").textContent = "";
    hojaGuardar.showModal();
    $("cad-plantilla-nombre").select();
  }
  const comprimir = (b) => (window.TVCadSesion && window.TVCadSesion.comprimir ? window.TVCadSesion.comprimir(b) : Promise.resolve({ cuerpo: b, cabeceras: {} }));
  $("cad-plantilla-guardar-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const nombre = $("cad-plantilla-nombre").value.trim();
    const estadoG = $("cad-plantilla-guardar-estado"), boton = $("cad-plantilla-guardar-ir");
    if (!nombre || boton.disabled) return;
    boton.disabled = true;
    estadoG.textContent = "Guardando la plantilla…";
    try {
      const r = await editor({ op: "layout_template_save", layout: guardarDe, format: "dwg" });
      const bytes = deBase64((r.result || {}).data_base64 || "");
      if (!bytes.length) throw new Error("El editor no escribió la plantilla.");
      const { cuerpo, cabeceras } = await comprimir(bytes.buffer);
      const est = window.TVCad.estado();
      const d = await json(P.guardar, { method: "POST", body: cuerpo, headers: { "Content-Type": "application/octet-stream", "X-CSRFToken": csrf(),
        "X-TV-Nombre": encodeURIComponent(nombre), "X-TV-Plano": (est.plano && est.plano.id) || "", ...cabeceras } });
      mias = null;
      hojaGuardar.close();
      avisar(`Se guardó la plantilla «${d.nombre}» en Mis plantillas.`);
    } catch (err) {
      estadoG.textContent = err instanceof TypeError ? "No se pudo guardar: revisa tu conexión." : (err.respuesta && err.respuesta.error) || err.message;
    } finally { boton.disabled = false; }
  });

  // Para las pruebas: lo que la hoja tiene ahora.
  window.TVCadPlantillas = Object.freeze({
    estado: () => ({ fuente, plantilla: plantilla && { id: plantilla.id, nombre: plantilla.nombre, presentaciones: plantilla.presentaciones.map((x) => ({ nombre: x.name, elegida: x.elegida })) }, menu: menuEnviado }),
    abrir, abrirGuardar,
  });
})();

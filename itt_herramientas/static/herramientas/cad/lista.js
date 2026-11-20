/* CAD, «Planos»: el árbol de proyectos y carpetas, la carpeta abierta y lo que se hace con ella (docs/cad.md, «Proyectos y carpetas»).
 *
 * - Árbol a la izquierda (arbol.js), migas arriba, las carpetas y los planos de la carpeta a la derecha.
 * - Nueva carpeta o proyecto, cambiar nombre, mover (arrastrando a una carpeta, al árbol o a las migas, y con «Mover a…», que sirve con
 *   teclado y en el teléfono), eliminar a la papelera (una carpeta, con todo lo suyo).
 * - Subir a la carpeta abierta: varios archivos, o una carpeta de la computadora (soltada o con «Subir una carpeta»), que recrea sus
 *   subcarpetas. Se suben solo los DWG y DXF.
 * - Descargar en ZIP (lo elegido, o una carpeta entera con sus subcarpetas) y, en el teléfono con el CAD descargado, una carpeta
 *   «Disponible sin conexión» (cada plano por el mismo camino que uno solo, campo.js).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const guion = $("cad-script");
  const URL_TAREA = (guion && guion.dataset.urlTarea) || "";
  const EDITA = !!(guion && guion.dataset.puedeEditar);
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const U = JSON.parse(($("cad-urls-carpetas") || {}).textContent || "{}");
  const ARBOL = JSON.parse(($("cad-arbol-datos") || {}).textContent || "{}");
  const ACTUAL = ($("cad") && $("cad").dataset.carpeta) || "";
  const A = window.TVCadArbol;
  const avisar = (texto, tipo) => (window.tvAviso ? window.tvAviso(texto, tipo) : null);
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : ""; };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const urlCarpeta = (id) => (id ? U.carpeta.replace(MARCA, id) : U.raiz);
  const MB = (n) => `${(n / 1048576).toFixed(n >= 10485760 ? 0 : 1)} MB`;

  // Un aviso que sobrevive a recargar la página (después de mover, crear o eliminar).
  const LLAVE_AVISO = "tv.cad.lista.aviso";
  function avisarAlVolver(texto, tipo) { try { sessionStorage.setItem(LLAVE_AVISO, JSON.stringify({ texto, tipo })); } catch (_) { avisar(texto, tipo); } }
  try { const a = JSON.parse(sessionStorage.getItem(LLAVE_AVISO) || "null"); sessionStorage.removeItem(LLAVE_AVISO); if (a) setTimeout(() => avisar(a.texto, a.tipo), 50); } catch (_) { /* */ }
  const recargar = (texto, tipo, url) => { if (texto) avisarAlVolver(texto, tipo); if (url) location.assign(url); else location.reload(); };

  async function enviar(url, datos, { formulario = false } = {}) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), Accept: "application/json", ...(formulario ? {} : { "Content-Type": "application/json" }) },
      body: formulario ? new URLSearchParams(datos) : JSON.stringify(datos) });
    let d = {}; try { d = await r.json(); } catch (_) { /* sin cuerpo */ }
    if (!r.ok && r.status !== 202) throw new Error(d.error || (r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : r.status === 403 ? "Tu cuenta no puede hacer eso." : "No se pudo. Inténtalo de nuevo."));
    return d;
  }

  // -- Clientes: sugerencias --------------------------------------------------------------------------------------------------------
  const clientes = JSON.parse(($("cad-clientes") || {}).textContent || "[]");
  if (window.ITT && ITT.combobox) document.querySelectorAll("[data-cad-clientes]").forEach((campo) => ITT.combobox(campo, () => clientes));

  // -- El árbol ------------------------------------------------------------------------------------------------------------------
  let arbol = null;
  if ($("cad-arbol") && A) {
    arbol = A.arbol($("cad-arbol"), ARBOL, { actual: ACTUAL, enlace: urlCarpeta, clave: "lista", soltar: EDITA ? (id, ev, fase) => soltarEn(id, ev, fase) : null });
  }

  // -- Filtros -------------------------------------------------------------------------------------------------------------------
  const resultados = $("cad-resultados");
  const cargando = () => { if (resultados) resultados.setAttribute("aria-busy", "true"); };
  const filtros = $("cad-filtros");
  if (filtros) filtros.addEventListener("submit", cargando);
  document.querySelectorAll("[data-cad-pagina]").forEach((a) => a.addEventListener("click", cargando));
  addEventListener("pageshow", () => { if (resultados) resultados.removeAttribute("aria-busy"); });
  const mas = $("cad-mas"), avanzados = $("cad-avanzados");
  if (mas && avanzados) mas.addEventListener("click", () => {
    avanzados.hidden = !avanzados.hidden;
    mas.setAttribute("aria-expanded", avanzados.hidden ? "false" : "true");
    if (!avanzados.hidden) avanzados.querySelector("select, input").focus();
  });
  if ($("cad-en")) $("cad-en").addEventListener("change", () => { if ($("cad-q").value.trim()) filtros.requestSubmit(); });

  // -- Subir -------------------------------------------------------------------------------------------------------------------
  const panel = $("cad-subir"), botonSubir = $("cad-boton-subir"), caja = $("cad-subida");
  const subida = caja ? caja.querySelector("tv-subida") : null;
  const estadoSubir = $("cad-subir-estado");
  const decirSubida = (t) => { if (estadoSubir) { estadoSubir.hidden = !t; estadoSubir.textContent = t || ""; } };
  function abrirSubida(enfocar) {
    if (!panel) return;
    panel.hidden = false;
    if (botonSubir) botonSubir.setAttribute("aria-expanded", "true");
    if (enfocar) (panel.querySelector("input[type=file]") || panel.querySelector("button") || panel).focus();
  }
  if (botonSubir) botonSubir.addEventListener("click", () => {
    if (panel.hidden) abrirSubida(false); else { panel.hidden = true; botonSubir.setAttribute("aria-expanded", "false"); }
  });

  let subidos = [], fallidos = 0, deCarpeta = false;
  if (caja) {
    caja.addEventListener("tv-subida:listo", (ev) => {
      const r = ev.detail && ev.detail.resultado;
      if (r && r.url) subidos.push(r.url);
      terminar();
    });
    caja.addEventListener("tv-subida:error", (ev) => {
      fallidos += 1;
      avisar((ev.detail && ev.detail.mensaje) || "No se pudo subir el archivo.", "error");
      terminar();
    });
  }
  let temporizador = null;
  function terminar() {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => {
      const uppy = subida && subida.uppy;
      const pendientes = uppy ? uppy.getFiles().filter((f) => !(f.progress && f.progress.uploadComplete) && !f.error).length : 0;
      if (pendientes) { decirSubida(`Subiendo… faltan ${pendientes}`); return; }
      decirSubida("");
      if (subidos.length === 1 && !fallidos && !deCarpeta) { location.assign(subidos[0]); return; }
      if (subidos.length) recargar(`${subidos.length} plano${subidos.length === 1 ? "" : "s"} en la biblioteca${fallidos ? ` · ${fallidos} no se pudo subir` : ""}`);
    }, 400);
  }

  const ES_PLANO = /\.(dwg|dxf)$/i;
  const OCULTO = (nombre) => nombre.startsWith(".") || nombre === "__MACOSX" || nombre === "Thumbs.db";
  const MAX_ARCHIVOS = 1000;

  // Lo soltado: archivos sueltos y carpetas enteras (cada archivo con su ruta relativa, p. ej. «Obra/Base/base.dwg»).
  async function leerSoltado(dt) {
    const salida = [];
    const entradas = [...(dt.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
    if (!entradas.length) return [...(dt.files || [])].map((f) => ({ archivo: f, ruta: "" }));
    async function recorrer(e, base) {
      if (salida.length > MAX_ARCHIVOS * 4) return;
      if (OCULTO(e.name)) return;
      if (e.isFile) {
        const f = await new Promise((ok, mal) => e.file(ok, mal)).catch(() => null);
        if (f) salida.push({ archivo: f, ruta: base });
        return;
      }
      if (!e.isDirectory) return;
      const lector = e.createReader();
      const ruta = base ? `${base}/${e.name}` : e.name;
      salida.push({ carpeta: ruta });
      for (;;) {
        const lote = await new Promise((ok, mal) => lector.readEntries(ok, mal)).catch(() => []);
        if (!lote.length) break;
        for (const x of lote) await recorrer(x, ruta);
      }
    }
    for (const e of entradas) await recorrer(e, "");
    return salida;
  }

  // Sube a la carpeta abierta; las carpetas de la computadora se crean antes (de a 500 rutas) y cada archivo va a la suya.
  async function subirLista(lista) {
    if (!subida || !subida.uppy) return;
    const archivos = lista.filter((x) => x.archivo);
    const planos = archivos.filter((x) => ES_PLANO.test(x.archivo.name));
    const otros = archivos.length - planos.length;
    const rutas = [...new Set([...lista.filter((x) => x.carpeta).map((x) => x.carpeta), ...planos.map((x) => x.ruta).filter(Boolean)])];
    if (!planos.length && !rutas.length) { avisar(otros ? "Ahí no hay planos DWG ni DXF." : "No llegó ningún archivo.", "error"); return; }
    if (planos.length > MAX_ARCHIVOS) { avisar(`Son ${planos.length} planos; sube hasta ${MAX_ARCHIVOS} a la vez.`, "error"); return; }
    abrirSubida(false);
    deCarpeta = rutas.length > 0;
    const mapa = {};
    if (rutas.length) {
      decirSubida(`Creando ${rutas.length} carpeta${rutas.length === 1 ? "" : "s"}…`);
      try {
        for (let i = 0; i < rutas.length; i += 500) Object.assign(mapa, (await enviar(U.rutas, { padre: ACTUAL || null, rutas: rutas.slice(i, i + 500) })).rutas);
      } catch (err) { decirSubida(""); avisar(err.message, "error"); return; }
    }
    if (otros) avisar(`Se omitieron ${otros} archivo${otros === 1 ? "" : "s"} que no ${otros === 1 ? "es" : "son"} DWG ni DXF.`, "info");
    if (!planos.length) { recargar(`Se crearon las carpetas de «${rutas[0].split("/")[0]}» (sin planos DWG ni DXF).`); return; }
    decirSubida(`Subiendo ${planos.length} plano${planos.length === 1 ? "" : "s"}…`);
    for (const x of planos) {
      const carpeta = x.ruta ? mapa[x.ruta] || ACTUAL : ACTUAL;
      try { subida.uppy.addFile({ name: x.archivo.name, type: x.archivo.type, data: x.archivo, source: "soltar", meta: { carpeta: carpeta || "" } }); }
      catch (err) { avisar(err.message || "No se pudo agregar el archivo.", "error"); }
    }
  }
  const entradaCarpeta = $("cad-subir-carpeta");
  if (entradaCarpeta) entradaCarpeta.addEventListener("change", () => {
    const lista = [...entradaCarpeta.files].filter((f) => !f.webkitRelativePath.split("/").some(OCULTO))
      .map((f) => ({ archivo: f, ruta: f.webkitRelativePath.split("/").slice(0, -1).join("/") }));
    entradaCarpeta.value = "";
    subirLista(lista);
  });

  // Soltar archivos o carpetas de la computadora en cualquier parte de la página (escritorio).
  const velo = $("cad-soltar");
  let profundidad = 0;
  const conArchivos = (ev) => ev.dataTransfer && [...(ev.dataTransfer.types || [])].includes("Files");
  if (subida && velo) {
    document.addEventListener("dragenter", (ev) => { if (!conArchivos(ev)) return; profundidad += 1; velo.hidden = false; });
    document.addEventListener("dragleave", (ev) => { if (!conArchivos(ev)) return; profundidad = Math.max(0, profundidad - 1); if (!profundidad) velo.hidden = true; });
    document.addEventListener("dragover", (ev) => { if (conArchivos(ev)) ev.preventDefault(); });
    document.addEventListener("drop", (ev) => {
      if (!conArchivos(ev)) return;
      profundidad = 0; velo.hidden = true;
      // La zona de subida ya agrega los archivos sueltos sobre ella; las carpetas (en cualquier parte) y lo soltado fuera de la zona va aquí.
      const hayCarpeta = [...(ev.dataTransfer.items || [])].some((i) => { const e = i.webkitGetAsEntry ? i.webkitGetAsEntry() : null; return e && e.isDirectory; });
      if (caja.contains(ev.target) && !hayCarpeta) return;
      ev.preventDefault();
      ev.stopPropagation();
      leerSoltado(ev.dataTransfer).then(subirLista);
    }, true);
  }

  // Acciones de la paleta: /cad/#nuevo y /cad/#subir.
  function porAncla() {
    if (location.hash === "#nuevo" && $("cad-nuevo")) { $("cad-nuevo").showModal(); history.replaceState(null, "", location.pathname + location.search); }
    if (location.hash === "#subir") { abrirSubida(true); history.replaceState(null, "", location.pathname + location.search); }
  }
  addEventListener("hashchange", porAncla);
  porAncla();

  // -- Esperar una tarea (ZIP) -------------------------------------------------------------------------------------------------
  async function seguirTarea(d, decir) {
    const limite = Date.now() + 10 * 60 * 1000;
    let fallos = 0;
    while (!d.terminada) {
      if (Date.now() > limite) throw new Error("El ZIP tardó demasiado. Inténtalo de nuevo con menos planos.");
      await espera(1200);
      let t = null;
      try {
        const rt = await fetch(`${URL_TAREA.replace(MARCA, d.tarea)}?formato=json`, { credentials: "same-origin", headers: { Accept: "application/json" } });
        t = rt.ok ? await rt.json() : null;
      } catch (_) { t = null; }
      if (!t || typeof t !== "object") {
        if (++fallos >= 2) throw new Error("Se perdió el seguimiento del ZIP. Recarga la página e inténtalo de nuevo.");
        continue;
      }
      fallos = 0;
      d = { ...t, tarea: d.tarea };
      if (!d.terminada) decir(`${d.mensaje || "Preparando…"} ${Math.round((d.fraccion || 0) * 100)} %`);
    }
    if (d.estado !== "completada" || !d.resultado || !d.resultado.url) throw new Error(d.error || "No se pudo armar el ZIP.");
    const a = document.createElement("a"); a.href = d.resultado.url; a.download = ""; document.body.append(a); a.click(); a.remove();
    avisar(d.resultado.mensaje || "ZIP listo");
  }
  const estadoCarpeta = $("cad-carpeta-estado");
  const decirCarpeta = (t) => { if (estadoCarpeta) { estadoCarpeta.hidden = !t; estadoCarpeta.textContent = t || ""; } };
  async function zipDeCarpeta(id, nombre) {
    decirCarpeta(`Preparando el ZIP de «${nombre}»…`);
    try { await seguirTarea(await enviar(U.zip.replace(MARCA, id), {}), decirCarpeta); }
    catch (err) { avisar(err.message, "error"); }
    finally { decirCarpeta(""); }
  }

  // -- Selección: ZIP de los planos elegidos y «Mover a…» -------------------------------------------------------------------
  const barra = $("cad-seleccion");
  const casillas = [...document.querySelectorAll("[data-cad-sel]")];
  const casillasCarpeta = [...document.querySelectorAll("[data-cad-sel-carpeta]")];
  const elegidos = () => ({ planos: casillas.filter((c) => c.checked).map((c) => c.dataset.cadSel),
    carpetas: casillasCarpeta.filter((c) => c.checked).map((c) => c.dataset.cadSelCarpeta) });
  let pintarSeleccion = () => {};
  if (barra) {
    const MAXIMO = Number(barra.dataset.maximo || 50);
    const cuenta = $("cad-sel-cuenta"), quitar = $("cad-sel-quitar"), zip = $("cad-sel-zip"), moverB = $("cad-sel-mover"), estadoZip = $("cad-zip-estado");
    const decir = (t) => { estadoZip.hidden = !t; estadoZip.textContent = t || ""; };
    pintarSeleccion = function () {
      const { planos, carpetas } = elegidos();
      const n = planos.length + carpetas.length;
      barra.dataset.activa = n ? "1" : "";
      cuenta.textContent = n ? `${n} seleccionado${n === 1 ? "" : "s"}` : "Elige planos o carpetas para moverlos o descargarlos";
      // El ZIP es de planos elegidos, o de UNA carpeta entera (con sus subcarpetas).
      const conArchivo = casillas.filter((c) => c.checked && !c.dataset.sinArchivo).length;
      zip.disabled = carpetas.length ? !(carpetas.length === 1 && !planos.length) : !conArchivo;
      zip.title = carpetas.length > 1 || (carpetas.length && planos.length) ? "Descarga cada carpeta desde su menú" : "";
      if (moverB) moverB.disabled = !n;
      quitar.hidden = !n;
      for (const c of [...casillas, ...casillasCarpeta]) c.closest("li").dataset.seleccionada = c.checked ? "1" : "";
    };
    [...casillas, ...casillasCarpeta].forEach((c) => c.addEventListener("change", pintarSeleccion));
    quitar.addEventListener("click", () => { [...casillas, ...casillasCarpeta].forEach((c) => { c.checked = false; }); pintarSeleccion(); });
    zip.addEventListener("click", async () => {
      const { planos, carpetas } = elegidos();
      if (carpetas.length === 1 && !planos.length) {
        const li = document.querySelector(`li[data-carpeta="${carpetas[0]}"]`);
        await zipDeCarpeta(carpetas[0], li ? li.dataset.nombre : "la carpeta");
        return;
      }
      const lista = casillas.filter((c) => c.checked && !c.dataset.sinArchivo).map((c) => c.dataset.cadSel);
      if (lista.length > MAXIMO) { avisar(`Elegiste ${lista.length}; el máximo por descarga es ${MAXIMO}.`, "error"); return; }
      zip.setAttribute("aria-busy", "true"); zip.disabled = true; decir("Preparando el ZIP…");
      try { await seguirTarea(await enviar(barra.dataset.urlZip, { elementos: lista }), decir); }
      catch (err) { avisar(err.message, "error"); }
      finally { decir(""); zip.removeAttribute("aria-busy"); pintarSeleccion(); }
    });
    if (moverB) moverB.addEventListener("click", () => moverDialogo(elegidos()));
    pintarSeleccion();
  }

  // -- Mover ---------------------------------------------------------------------------------------------------------------------
  async function mover(sel, destino) {
    const n = sel.planos.length + sel.carpetas.length;
    if (!n) return;
    try {
      const d = await enviar(U.mover, { planos: sel.planos, carpetas: sel.carpetas, destino: destino || null });
      recargar(d.mensaje);
    } catch (err) { avisar(err.message, "error"); }
  }
  async function moverDialogo(sel, titulo) {
    const n = sel.planos.length + sel.carpetas.length;
    if (!n) return;
    const destino = await A.moverA({ titulo: titulo || `Mover ${n === 1 ? "1 elemento" : `${n} elementos`} a…`, excluir: sel.carpetas, actual: ACTUAL, url: U.lista, nueva: U.nueva });
    if (destino === undefined) return;
    await mover(sel, destino);
  }

  // Arrastrar planos y carpetas (los elegidos, si el arrastrado está elegido) a una carpeta, al árbol o a las migas.
  const TIPO = "application/x-tv-cad";
  let arrastrando = null;
  function soltarEn(id, ev, fase) {
    if (!arrastrando || !ev.dataTransfer || ![...ev.dataTransfer.types].includes(TIPO)) return false;
    if (arrastrando.carpetas.includes(id)) return false;
    if (arbol && arrastrando.carpetas.some((c) => arbol.debajo(c).has(id))) return false;
    if (fase === "soltar") { const sel = arrastrando; arrastrando = null; mover(sel, id); }
    else ev.dataTransfer.dropEffect = "move";
    return true;
  }
  if (EDITA) {
    for (const li of document.querySelectorAll("#cad-planos > li[draggable=true]")) {
      li.addEventListener("dragstart", (ev) => {
        const propio = li.dataset.carpeta ? { planos: [], carpetas: [li.dataset.carpeta] } : { planos: [li.dataset.id], carpetas: [] };
        const casilla = li.querySelector("input[type=checkbox]");
        arrastrando = casilla && casilla.checked ? elegidos() : propio;
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData(TIPO, JSON.stringify(arrastrando));
        ev.dataTransfer.setData("text/plain", li.dataset.nombre || "");
        li.classList.add("cad-plano--arrastrando");
      });
      li.addEventListener("dragend", () => { li.classList.remove("cad-plano--arrastrando"); setTimeout(() => { arrastrando = null; }, 0); });
      if (li.dataset.carpeta) {
        const id = li.dataset.carpeta;
        li.addEventListener("dragover", (ev) => { if (soltarEn(id, ev, "sobre")) { ev.preventDefault(); li.classList.add("cad-plano--soltar"); } });
        li.addEventListener("dragleave", () => li.classList.remove("cad-plano--soltar"));
        li.addEventListener("drop", (ev) => { li.classList.remove("cad-plano--soltar"); if (soltarEn(id, ev, "soltar")) { ev.preventDefault(); ev.stopPropagation(); } });
      }
    }
    for (const m of document.querySelectorAll("#cad-migas [data-cad-destino]")) {
      const id = m.dataset.cadDestino;
      m.addEventListener("dragover", (ev) => { if (soltarEn(id, ev, "sobre")) { ev.preventDefault(); m.classList.add("cad-migas--soltar"); } });
      m.addEventListener("dragleave", () => m.classList.remove("cad-migas--soltar"));
      m.addEventListener("drop", (ev) => { m.classList.remove("cad-migas--soltar"); if (soltarEn(id, ev, "soltar")) ev.preventDefault(); });
    }
  }

  // -- Nueva carpeta, cambiar nombre, eliminar -------------------------------------------------------------------------------
  const formNueva = $("cad-carpeta-nueva-form");
  if (formNueva) formNueva.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const est = $("cad-carpeta-nueva-estado");
    const boton = formNueva.querySelector("[type=submit]");
    boton.disabled = true; est.hidden = true;
    try {
      const d = await enviar(U.nueva, { nombre: $("cad-carpeta-nombre").value, padre: ACTUAL || null, cliente: $("cad-carpeta-cliente") ? $("cad-carpeta-cliente").value : "" });
      recargar(`Se creó «${d.carpeta.nombre}».`, null, d.carpeta.url);
    } catch (err) { est.hidden = false; est.textContent = err.message; boton.disabled = false; }
  });

  let renombrando = null;
  function abrirRenombrar(x) {
    renombrando = x;
    const d = $("cad-renombrar");
    $("cad-renombrar-nombre").value = x.nombre;
    $("cad-renombrar-cliente-campo").hidden = !(x.tipo === "carpeta" && x.proyecto);
    $("cad-renombrar-cliente").value = x.cliente || "";
    $("cad-renombrar-estado").hidden = true;
    d.showModal();
    $("cad-renombrar-nombre").select();
  }
  const formRen = $("cad-renombrar-form");
  if (formRen) formRen.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const x = renombrando;
    const est = $("cad-renombrar-estado");
    try {
      if (x.tipo === "carpeta") {
        const datos = { nombre: $("cad-renombrar-nombre").value };
        if (x.proyecto) datos.cliente = $("cad-renombrar-cliente").value;
        await enviar(U.renombrar.replace(MARCA, x.id), datos);
      } else {
        await enviar(U.renombrar_plano.replace(MARCA, x.id), { nombre: $("cad-renombrar-nombre").value }, { formulario: true });
      }
      recargar("Cambios guardados.");
    } catch (err) { est.hidden = false; est.textContent = err.message; }
  });

  function totalDe(id) { const f = (ARBOL.carpetas || []).find((c) => c.id === id); return f ? f.total : 0; }
  function hijasDe(id) { return (ARBOL.carpetas || []).filter((c) => arbol && c.id !== id && arbol.debajo(id).has(c.id)).length; }
  function eliminarCarpeta(x) {
    const d = $("cad-eliminar-carpeta");
    const n = totalDe(x.id), h = hijasDe(x.id);
    const partes = [];
    if (n) partes.push(`${n} plano${n === 1 ? "" : "s"}`);
    if (h) partes.push(`${h} carpeta${h === 1 ? "" : "s"}`);
    $("cad-eliminar-carpeta-titulo").textContent = `¿Eliminar «${x.nombre}»?`;
    $("cad-eliminar-carpeta-texto").textContent = `Se mueve a la papelera${partes.length ? ` con ${partes.join(" y ")}` : ""}. Se puede restaurar entera durante 30 días.`;
    $("cad-eliminar-carpeta-ir").onclick = async () => {
      d.close();
      try { const r = await enviar(U.eliminar.replace(MARCA, x.id), {}); recargar(r.mensaje, null, x.id === ACTUAL ? r.volver : null); }
      catch (err) { avisar(err.message, "error"); }
    };
    d.showModal();
  }
  async function eliminarPlano(x) {
    if (!(await confirmar(`¿Eliminar «${x.nombre}»?`, "Se mueve a la papelera. Se puede restaurar durante 30 días.", "Mover a la papelera"))) return;
    try { const r = await enviar(U.eliminar_plano.replace(MARCA, x.id), {}); recargar(r.mensaje); }
    catch (err) { avisar(err.message, "error"); }
  }
  function confirmar(titulo, texto, si) {
    if (window.TVCadCampo && window.TVCadCampo.confirmar) return window.TVCadCampo.confirmar(titulo, texto, si);
    return new Promise((ok) => {
      const d = document.createElement("dialog"); d.className = "tv-hoja tv-hoja--confirmar";
      const c = document.createElement("div"); c.className = "tv-hoja__cuerpo";
      const h = document.createElement("h2"); h.textContent = titulo; const p = document.createElement("p"); p.textContent = texto;
      const a = document.createElement("div"); a.className = "tv-hoja__acciones";
      const no = document.createElement("button"); no.type = "button"; no.className = "tv-boton tv-boton--secundario"; no.textContent = "Cancelar";
      const sb = document.createElement("button"); sb.type = "button"; sb.className = "tv-boton tv-boton--peligro"; sb.textContent = si;
      a.append(no, sb); c.append(h, p, a); d.append(c); document.body.append(d);
      no.onclick = () => d.close("no"); sb.onclick = () => d.close("si");
      d.addEventListener("close", () => { d.remove(); ok(d.returnValue === "si"); }, { once: true });
      d.showModal();
    });
  }

  // -- Menú de cada fila (y de la carpeta abierta) -------------------------------------------------------------------------------
  const menu = $("cad-menu-fila");
  let deFila = null;
  function datosDe(boton) {
    if (boton.id === "cad-carpeta-acciones") return { tipo: "carpeta", id: boton.dataset.id, nombre: boton.dataset.nombre, cliente: boton.dataset.cliente, proyecto: !!boton.dataset.proyecto, propia: true };
    const li = boton.closest("li");
    return li.dataset.carpeta ? { tipo: "carpeta", id: li.dataset.carpeta, nombre: li.dataset.nombre, cliente: li.dataset.cliente, proyecto: !!li.dataset.proyecto, li }
      : { tipo: "plano", id: li.dataset.id, nombre: li.dataset.nombre, puedeEliminar: !!li.dataset.puedeEliminar, sinArchivo: !!(li.querySelector("[data-sin-archivo]")), li };
  }
  function cerrarMenu(enfocar) {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    if (enfocar && deFila && deFila.boton) deFila.boton.focus();
    if (deFila && deFila.boton) deFila.boton.setAttribute("aria-expanded", "false");
  }
  async function abrirMenu(boton) {
    const x = datosDe(boton);
    deFila = { ...x, boton };
    const ver = (accion, si) => { const b = menu.querySelector(`[data-accion="${accion}"]`); if (b) b.hidden = !si; };
    ver("abrir", !x.propia);
    ver("renombrar", EDITA);
    ver("mover", EDITA);
    ver("descargar", x.tipo === "plano" && !x.sinArchivo);
    ver("zip", x.tipo === "carpeta");
    ver("eliminar", EDITA && (x.tipo === "carpeta" || x.puedeEliminar));
    const sc = menu.querySelector('[data-accion="sin-conexion"]');
    if (sc) {
      sc.hidden = !(x.tipo === "carpeta" && window.TVCadCampo);
      if (!sc.hidden) sc.querySelector("[data-texto]").textContent = (await carpetaMarcada(x.id)) ? "Quitar del teléfono" : "Disponible sin conexión";
    }
    menu.hidden = false;
    const r = boton.getBoundingClientRect();
    const ancho = menu.offsetWidth, alto = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(innerWidth - ancho - 8, r.right - ancho))}px`;
    menu.style.top = `${r.bottom + 4 + alto > innerHeight ? Math.max(8, r.top - alto - 4) : r.bottom + 4}px`;
    boton.setAttribute("aria-expanded", "true");
    const primero = [...menu.querySelectorAll("[role=menuitem]")].find((b) => !b.hidden);
    if (primero) primero.focus();
  }
  if (menu) {
    document.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-cad-menu]");
      if (b) { ev.preventDefault(); if (!menu.hidden && deFila && deFila.boton === b) cerrarMenu(); else abrirMenu(b); return; }
      if (!menu.contains(ev.target)) cerrarMenu();
    });
    menu.addEventListener("keydown", (ev) => {
      const items = [...menu.querySelectorAll("[role=menuitem]")].filter((b) => !b.hidden);
      const i = items.indexOf(document.activeElement);
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); cerrarMenu(true); }
      else if (ev.key === "ArrowDown") { ev.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (ev.key === "Home") { ev.preventDefault(); items[0].focus(); }
      else if (ev.key === "End") { ev.preventDefault(); items[items.length - 1].focus(); }
      else if (ev.key === "Tab") cerrarMenu();
    });
    addEventListener("resize", () => cerrarMenu());
    addEventListener("scroll", () => cerrarMenu(), { passive: true });
    menu.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-accion]");
      if (!b || !deFila) return;
      const x = deFila;
      cerrarMenu();
      const accion = b.dataset.accion;
      if (accion === "abrir") location.assign(x.tipo === "carpeta" ? urlCarpeta(x.id) : x.li.querySelector(".cad-plano__abrir").href);
      else if (accion === "renombrar") abrirRenombrar(x);
      else if (accion === "mover") moverDialogo(x.tipo === "carpeta" ? { planos: [], carpetas: [x.id] } : { planos: [x.id], carpetas: [] }, `Mover «${x.nombre}» a…`);
      else if (accion === "descargar") { const a = x.li.querySelector(".cad-plano__bajar"); if (a) a.click(); }
      else if (accion === "zip") zipDeCarpeta(x.id, x.nombre);
      else if (accion === "eliminar") (x.tipo === "carpeta" ? eliminarCarpeta(x) : eliminarPlano(x));
      else if (accion === "sin-conexion") alternarSinConexion(x);
    });
  }

  // -- Una carpeta «Disponible sin conexión» (teléfono con el CAD descargado; detrás de TV_CAD_TACTIL) ----------------------
  // Cada plano se lleva por el mismo camino que uno solo (campo.js: no aparta el plano; lo cambiado sin conexión se sube como versión o
  // copia). La carpeta queda marcada en el teléfono: al abrirla con conexión se dice si tiene planos nuevos o con versión nueva.
  const Campo = () => window.TVCadCampo;
  async function carpetaMarcada(id) { return Campo() && Campo().carpetas ? (await Campo().carpetas()).some((c) => c.id === id) : false; }
  async function pendientesDe(id) {
    const r = await fetch(U.planos.replace(MARCA, id), { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo leer la carpeta.");
    const d = await r.json();
    const tengo = new Map((await Campo().planos()).map((p) => [p.id, p]));
    const lista = d.planos.filter((p) => p.disponible);
    return { todos: lista, faltan: lista.filter((p) => { const t = tengo.get(p.id); return !t || (p.sha256 && t.sha256 !== p.sha256) || t.revision !== p.revision; }) };
  }
  async function llevarCarpeta(x, faltan) {
    const total = faltan.reduce((s, p) => s + p.bytes, 0);
    let hechos = 0, mal = 0;
    for (const p of faltan) {
      decirCarpeta(`Llevando «${x.nombre}» al teléfono: ${hechos + 1} de ${faltan.length}…`);
      try { await Campo().llevar(p.id); hechos += 1; } catch (err) { mal += 1; if (/sesión/.test(err.message)) { avisar(err.message, "error"); break; } }
    }
    decirCarpeta("");
    await Campo().marcarCarpeta({ id: x.id, nombre: x.nombre }, true);
    avisar(mal ? `Se llevaron ${hechos} de ${faltan.length} planos (${MB(total)}); ${mal} no se pudo. Vuelve a intentarlo con conexión.`
      : `«${x.nombre}» quedó en este teléfono: ${hechos} plano${hechos === 1 ? "" : "s"}. Los abres sin conexión desde CAD.`, mal ? "error" : undefined);
    pintarMarcas();
  }
  async function alternarSinConexion(x) {
    const C = Campo();
    if (!C) return;
    try {
      if (await carpetaMarcada(x.id)) {
        const { todos } = await pendientesDe(x.id).catch(() => ({ todos: [] }));
        let quitados = 0, conCambios = 0;
        for (const p of todos) {
          if (!(await C.plano(p.id))) continue;
          if (await C.cambios(p.id)) { conCambios += 1; continue; }
          await C.quitar(p.id); quitados += 1;
        }
        await C.marcarCarpeta({ id: x.id, nombre: x.nombre }, false);
        avisar(conCambios ? `Se quitaron ${quitados} planos; ${conCambios} con cambios sin subir se quedan en el teléfono.` : `«${x.nombre}» se quitó de este teléfono.`);
        pintarMarcas();
        return;
      }
      const ed = await C.estadoEditor();
      if (!C.instalada()) { avisar("Para usar CAD sin conexión, instala la app en la pantalla de inicio y ábrela desde ahí.", "error"); return; }
      // (La lista no sabe la versión fijada del editor: basta con que esté en el teléfono; la página del plano lo pone al día al abrirlo.)
      if (!ed.instalada) { avisar("Primero descarga el CAD en este teléfono: abre un plano y toca Más › Sin conexión.", "error"); return; }
      const { todos, faltan } = await pendientesDe(x.id);
      if (!todos.length) { avisar("Esta carpeta no tiene planos."); return; }
      const peso = faltan.reduce((s, p) => s + p.bytes, 0);
      if (faltan.length && !(await confirmar(`¿Llevar «${x.nombre}» al teléfono?`,
        `${faltan.length} plano${faltan.length === 1 ? "" : "s"}, ${MB(peso)}. Mejor con wifi. No aparta los planos: otras personas pueden seguir editándolos.`, "Llevar al teléfono"))) return;
      await llevarCarpeta(x, faltan);
    } catch (err) { decirCarpeta(""); avisar(err.message, "error"); }
  }
  async function pintarMarcas() {
    if (!Campo() || !Campo().carpetas) return;
    const marcadas = new Set((await Campo().carpetas()).map((c) => c.id));
    for (const li of document.querySelectorAll("#cad-planos > li[data-carpeta]")) {
      const m = li.querySelector("[data-cad-campo-marca]");
      if (m) m.hidden = !marcadas.has(li.dataset.carpeta);
    }
    // La carpeta abierta (o una de arriba) está sin conexión: ¿tiene planos nuevos o con versión nueva que el teléfono no tiene?
    const caja = $("cad-campo-carpeta");
    if (!caja || !navigator.onLine) return;
    const ruta = arbol ? arbol.ruta(ACTUAL).map((f) => f.id) : [];
    const marcada = [...ruta].reverse().find((id) => marcadas.has(id));
    if (!marcada) { caja.hidden = true; return; }
    const lista = await Campo().carpetas();
    const x = lista.find((c) => c.id === marcada);
    let p;
    try { p = await pendientesDe(marcada); } catch (_) { return; }
    if (!p.faltan.length) { caja.hidden = true; return; }
    $("cad-campo-carpeta-texto").textContent = `«${x.nombre}» está sin conexión en este teléfono: ${p.faltan.length} plano${p.faltan.length === 1 ? " nuevo o con versión nueva no está" : "s nuevos o con versión nueva no están"} en el teléfono (${MB(p.faltan.reduce((s, y) => s + y.bytes, 0))}).`;
    caja.hidden = false;
    $("cad-campo-carpeta-ir").onclick = async () => { caja.hidden = true; await llevarCarpeta(x, p.faltan); };
  }
  if (guion && guion.dataset.tactil) {
    const arrancar = () => pintarMarcas().catch(() => {});
    if (document.readyState === "complete") arrancar(); else addEventListener("load", arrancar);
  }

  // Para las pruebas: lo que la página sabe de la carpeta abierta.
  window.TVCadLista = Object.freeze({ carpeta: () => ACTUAL, arbol: () => arbol, subir: subirLista, mover, pendientesDe });
})();

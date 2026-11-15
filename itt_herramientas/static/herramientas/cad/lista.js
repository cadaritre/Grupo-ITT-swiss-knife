/* CAD, biblioteca de planos: subir (también soltando archivos en la página), nuevo plano, filtros y descarga de varios en un ZIP. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const guion = $("cad-script");
  const URL_TAREA = (guion && guion.dataset.urlTarea) || "";
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const avisar = (texto, tipo) => (window.tvAviso ? window.tvAviso(texto, tipo) : null);
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : ""; };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));

  // -- Proyecto o cliente: sugerencias ------------------------------------------------------------------------------------------
  const proyectos = JSON.parse(($("cad-proyectos") || {}).textContent || "[]");
  if (window.ITT && ITT.combobox) {
    document.querySelectorAll("[data-cad-proyectos]").forEach((campo) => {
      ITT.combobox(campo, () => proyectos, campo.hasAttribute("data-cad-filtrar") ? () => campo.form.requestSubmit() : undefined);
    });
  }

  // -- Filtros ---------------------------------------------------------------------------------------------------------------------
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

  // -- Subir -----------------------------------------------------------------------------------------------------------------------
  const panel = $("cad-subir"), botonSubir = $("cad-boton-subir"), caja = $("cad-subida");
  const subida = caja ? caja.querySelector("tv-subida") : null;
  function abrirSubida(enfocar) {
    if (!panel) return;
    panel.hidden = false;
    if (botonSubir) botonSubir.setAttribute("aria-expanded", "true");
    if (enfocar) (panel.querySelector("input[type=file]") || panel.querySelector("button") || panel).focus();
  }
  if (botonSubir) botonSubir.addEventListener("click", () => {
    if (panel.hidden) abrirSubida(false); else { panel.hidden = true; botonSubir.setAttribute("aria-expanded", "false"); }
  });
  const campoProyecto = $("cad-subir-proyecto");
  function metaProyecto() {
    if (subida && subida.uppy) subida.uppy.setMeta({ proyecto: (campoProyecto.value || "").trim().slice(0, 160) });
  }
  if (campoProyecto) ["input", "change"].forEach((t) => campoProyecto.addEventListener(t, metaProyecto));

  let subidos = [], fallidos = 0;
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
      if (pendientes) return;
      if (subidos.length === 1 && !fallidos) { location.assign(subidos[0]); return; }
      if (subidos.length) { avisar(`${subidos.length} plano${subidos.length === 1 ? "" : "s"} en la biblioteca`); setTimeout(() => location.reload(), 600); }
    }, 400);
  }

  // Soltar archivos en cualquier parte de la página (escritorio).
  const velo = $("cad-soltar");
  let profundidad = 0;
  const conArchivos = (ev) => ev.dataTransfer && [...(ev.dataTransfer.types || [])].includes("Files");
  if (subida && velo) {
    document.addEventListener("dragenter", (ev) => { if (!conArchivos(ev)) return; profundidad += 1; velo.hidden = false; });
    document.addEventListener("dragleave", () => { profundidad = Math.max(0, profundidad - 1); if (!profundidad) velo.hidden = true; });
    document.addEventListener("dragover", (ev) => { if (conArchivos(ev)) ev.preventDefault(); });
    document.addEventListener("drop", (ev) => {
      if (!conArchivos(ev)) return;
      profundidad = 0; velo.hidden = true;
      if (caja.contains(ev.target)) return;                                 // la zona de subida ya lo atiende
      ev.preventDefault();
      abrirSubida(false);
      metaProyecto();
      for (const archivo of ev.dataTransfer.files) {
        try { subida.uppy.addFile({ name: archivo.name, type: archivo.type, data: archivo, source: "soltar" }); }
        catch (err) { avisar(err.message || "No se pudo agregar el archivo.", "error"); }
      }
    });
  }

  // Acciones de la paleta: /cad/#nuevo y /cad/#subir.
  function porAncla() {
    if (location.hash === "#nuevo" && $("cad-nuevo")) { $("cad-nuevo").showModal(); history.replaceState(null, "", location.pathname + location.search); }
    if (location.hash === "#subir") { abrirSubida(true); history.replaceState(null, "", location.pathname + location.search); }
  }
  addEventListener("hashchange", porAncla);
  porAncla();

  // -- Selección y ZIP ------------------------------------------------------------------------------------------------------------
  const barra = $("cad-seleccion");
  if (barra) {
    const MAXIMO = Number(barra.dataset.maximo || 50);
    const casillas = [...document.querySelectorAll("[data-cad-sel]")];
    const cuenta = $("cad-sel-cuenta"), quitar = $("cad-sel-quitar"), zip = $("cad-sel-zip"), estadoZip = $("cad-zip-estado");
    const decir = (t) => { estadoZip.hidden = !t; estadoZip.textContent = t || ""; };
    function pintar() {
      const n = casillas.filter((c) => c.checked).length;
      barra.dataset.activa = n ? "1" : "";
      cuenta.textContent = n ? `${n} seleccionado${n === 1 ? "" : "s"}` : "Elige planos para descargarlos juntos";
      zip.disabled = !n;
      quitar.hidden = !n;
      for (const c of casillas) c.closest("li").dataset.seleccionada = c.checked ? "1" : "";
    }
    casillas.forEach((c) => c.addEventListener("change", pintar));
    quitar.addEventListener("click", () => { casillas.forEach((c) => { c.checked = false; }); pintar(); });
    zip.addEventListener("click", async () => {
      const elegidos = casillas.filter((c) => c.checked).map((c) => c.dataset.cadSel);
      if (elegidos.length > MAXIMO) { avisar(`Elegiste ${elegidos.length}; el máximo por descarga es ${MAXIMO}.`, "error"); return; }
      zip.setAttribute("aria-busy", "true"); zip.disabled = true; decir("Preparando el ZIP…");
      try {
        const r = await fetch(barra.dataset.urlZip, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ elementos: elegidos }) });
        let d = await r.json().catch(() => ({}));
        if (!r.ok && r.status !== 202) throw new Error(d.error || "No se pudo armar el ZIP. Inténtalo de nuevo.");
        while (!d.terminada) {
          await espera(1200);
          d = { ...(await (await fetch(`${URL_TAREA.replace(MARCA, d.tarea)}?formato=json`, { credentials: "same-origin", headers: { Accept: "application/json" } })).json()), tarea: d.tarea };
          if (!d.terminada) decir(`${d.mensaje || "Preparando…"} ${Math.round((d.fraccion || 0) * 100)} %`);
        }
        if (d.estado !== "completada" || !d.resultado || !d.resultado.url) throw new Error(d.error || "No se pudo armar el ZIP.");
        const a = document.createElement("a"); a.href = d.resultado.url; a.download = ""; document.body.append(a); a.click(); a.remove();
        decir(""); avisar(d.resultado.mensaje || "ZIP listo");
      } catch (err) { decir(""); avisar(err.message, "error"); }
      finally { zip.removeAttribute("aria-busy"); pintar(); }
    });
    pintar();
  }
})();

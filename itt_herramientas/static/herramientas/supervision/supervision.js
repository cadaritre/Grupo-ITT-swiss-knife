/* «Documentos de todas las cuentas»: estados de carga de filtros y páginas, y descarga de PDF (con su avance si hay que generarlo). */
(function () {
  "use strict";
  const guion = document.getElementById("sup-script");
  const URL_TAREA = (guion && guion.dataset.urlTarea) || "";
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const aviso = (texto, tipo) => (window.tvAviso ? window.tvAviso(texto, tipo) : alert(texto));
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : ""; };
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const resultados = document.getElementById("sup-resultados");
  const progreso = document.getElementById("sup-progreso");

  function cargando() { if (resultados) resultados.setAttribute("aria-busy", "true"); }
  const filtros = document.getElementById("sup-filtros");
  if (filtros) filtros.addEventListener("submit", cargando);
  document.querySelectorAll("[data-sup-pagina]").forEach((a) => a.addEventListener("click", cargando));
  addEventListener("pageshow", () => { if (resultados) resultados.removeAttribute("aria-busy"); });

  function nombreDe(r, respaldo) {
    const cd = r.headers.get("Content-Disposition") || "";
    const utf = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    if (utf) return decodeURIComponent(utf[1]);
    const simple = /filename="?([^";]+)"?/i.exec(cd);
    return simple ? simple[1] : respaldo;
  }

  async function bajar(url, respaldo) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(r.status === 404 ? "Este documento ya no existe." : "No se pudo descargar. Inténtalo de nuevo.");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = nombreDe(r, respaldo);
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }

  function ocupado(boton, si) {
    if (si) { boton.setAttribute("aria-busy", "true"); boton.setAttribute("aria-disabled", "true"); }
    else { boton.removeAttribute("aria-busy"); boton.removeAttribute("aria-disabled"); }
  }
  function decir(texto) { if (progreso) { progreso.hidden = !texto; progreso.textContent = texto || ""; } }

  async function esperarTarea(tarea, decirle) {
    for (;;) {
      await espera(1200);
      const t = await (await fetch(`${URL_TAREA.replace(MARCA, tarea)}?formato=json`, { credentials: "same-origin", headers: { Accept: "application/json" } })).json();
      if (!t.terminada) { decirle(`${t.mensaje || "Preparando…"} (${Math.round((t.fraccion || 0) * 100)} %)`); continue; }
      if (t.estado !== "completada" || !t.resultado || !t.resultado.url) throw new Error(t.error || "No se pudo completar. Inténtalo de nuevo.");
      return t.resultado;
    }
  }

  // -- Cliente: sugerencias de los clientes de todas las cuentas; elegir uno filtra --------------------------------------------
  const campoCliente = document.querySelector("[data-sup-clientes]");
  const datosClientes = document.getElementById("sup-clientes");
  if (campoCliente && datosClientes && window.ITT && ITT.combobox) {
    const nombres = JSON.parse(datosClientes.textContent || "[]");
    ITT.combobox(campoCliente, () => nombres, () => { cargando(); campoCliente.form.requestSubmit(); });
  }

  // -- Selección y descarga en ZIP ------------------------------------------------------------------------------------------------
  const barra = document.getElementById("sup-seleccion");
  if (barra) {
    const MAXIMO = Number(barra.dataset.maximo || 50);
    const TOTAL = Number(barra.dataset.total || 0);
    const casillas = [...document.querySelectorAll("[data-sup-sel]")];
    const pagina = document.getElementById("sup-sel-pagina");
    const cuenta = document.getElementById("sup-sel-cuenta");
    const todos = document.getElementById("sup-sel-todos");
    const quitar = document.getElementById("sup-sel-quitar");
    const zip = document.getElementById("sup-sel-zip");
    const estadoZip = document.getElementById("sup-zip-estado");
    let todosLosResultados = false;
    const decirZip = (t) => { estadoZip.hidden = !t; estadoZip.textContent = t || ""; };

    function pintar() {
      const marcadas = casillas.filter((c) => c.checked);
      const n = todosLosResultados ? TOTAL : marcadas.length;
      barra.dataset.activa = n ? "1" : "";
      cuenta.textContent = n ? `${n} seleccionado${n === 1 ? "" : "s"}` : "Elige documentos para descargarlos juntos";
      zip.disabled = !n;
      pagina.checked = casillas.length > 0 && marcadas.length === casillas.length;
      pagina.indeterminate = marcadas.length > 0 && marcadas.length < casillas.length;
      todos.hidden = todosLosResultados || TOTAL <= casillas.length || marcadas.length < casillas.length;
      quitar.hidden = !n;
      for (const c of casillas) c.closest("tr").dataset.seleccionada = c.checked ? "1" : "";
    }
    casillas.forEach((c) => c.addEventListener("change", () => { todosLosResultados = false; pintar(); }));
    pagina.addEventListener("change", () => { todosLosResultados = false; casillas.forEach((c) => { c.checked = pagina.checked; }); pintar(); });
    todos.addEventListener("click", () => {
      if (TOTAL > MAXIMO) { aviso(`Son ${TOTAL} documentos; el máximo por descarga es ${MAXIMO}. Ajusta los filtros.`, "error"); return; }
      todosLosResultados = true; casillas.forEach((c) => { c.checked = true; }); pintar();
    });
    quitar.addEventListener("click", () => { todosLosResultados = false; casillas.forEach((c) => { c.checked = false; }); pintar(); });
    zip.addEventListener("click", async () => {
      const elegidos = casillas.filter((c) => c.checked).map((c) => c.dataset.supSel.split(":"));
      const cuerpo = todosLosResultados ? { todos: true, filtros: Object.fromEntries(new URLSearchParams(location.search)) } : { elementos: elegidos };
      if (!todosLosResultados && elegidos.length > MAXIMO) { aviso(`Elegiste ${elegidos.length}; el máximo por descarga es ${MAXIMO}.`, "error"); return; }
      ocupado(zip, true); zip.disabled = true;
      decirZip("Preparando el ZIP…");
      try {
        const r = await fetch(barra.dataset.urlZip, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(cuerpo) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok && r.status !== 202) throw new Error(d.error || "No se pudo armar el ZIP. Inténtalo de nuevo.");
        const resultado = d.terminada && d.resultado && d.resultado.url ? d.resultado : await esperarTarea(d.tarea, decirZip);
        await bajar(resultado.url, "Documentos.zip");
        decirZip("");
        aviso(resultado.omitidos && resultado.omitidos.length ? `${resultado.mensaje} ${resultado.omitidos[0]}` : resultado.mensaje, resultado.omitidos && resultado.omitidos.length ? "error" : undefined);
      } catch (err) { decirZip(""); aviso(err.message, "error"); }
      finally { ocupado(zip, false); pintar(); }
    });
    pintar();
  }

  document.addEventListener("click", async (e) => {
    const enlace = e.target.closest("[data-sup-descargar]");
    if (enlace) {
      e.preventDefault();
      if (enlace.getAttribute("aria-busy")) return;
      ocupado(enlace, true);
      try { await bajar(enlace.href, "Cotizacion.pdf"); }
      catch (err) { aviso(err.message, "error"); }
      finally { ocupado(enlace, false); }
      return;
    }
    const boton = e.target.closest("[data-sup-reporte-pdf]");
    if (!boton || boton.getAttribute("aria-busy")) return;
    ocupado(boton, true);
    try {
      const r = await fetch(boton.dataset.supReportePdf, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), Accept: "application/json" } });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "No se pudo preparar el PDF. Inténtalo de nuevo.");
      let url = d.url;
      if (!url && d.tarea) {
        decir("Generando el PDF…");
        url = (await esperarTarea(d.tarea, (t) => decir(`Generando el PDF… ${t}`))).url;
        decir("");
      }
      await bajar(url, "Reporte.pdf");
    } catch (err) { decir(""); aviso(err.message, "error"); }
    finally { ocupado(boton, false); }
  });
})();

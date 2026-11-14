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
        for (;;) {
          await espera(1500);
          const t = await (await fetch(`${URL_TAREA.replace(MARCA, d.tarea)}?formato=json`, { credentials: "same-origin", headers: { Accept: "application/json" } })).json();
          decir(t.terminada ? "" : `Generando el PDF… ${Math.round((t.fraccion || 0) * 100)} %`);
          if (t.terminada) {
            if (t.estado !== "completada" || !t.resultado || !t.resultado.url) throw new Error(t.error || "No se pudo generar el PDF. Inténtalo de nuevo.");
            url = t.resultado.url;
            break;
          }
        }
      }
      await bajar(url, "Reporte.pdf");
    } catch (err) { decir(""); aviso(err.message, "error"); }
    finally { ocupado(boton, false); }
  });
})();

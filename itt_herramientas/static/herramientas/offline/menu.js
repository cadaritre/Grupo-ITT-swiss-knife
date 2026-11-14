/* Menú de la cabecera de las páginas de campo (capturar, cotizaciones, reportes, croquis, instalar).
 *
 * El HTML es el mismo para todas las cuentas (así la página se guarda y abre sin conexión). Aquí se rellena con lo que dice `/sesion/`
 * (nombre, herramientas a las que tiene acceso y el token para cerrar sesión), que `ui.js` anuncia con el evento `itt:sesion` y guarda en
 * la cola local; sin conexión se usa lo último que se supo.
 *  - Cerrar sesión necesita red (cierra la sesión en el servidor): sin red el botón avisa en vez de fallar en silencio.
 *  - Si hay pendientes, antes de cerrar sesión se dice qué pasa con ellos (se conservan en este teléfono y se suben al volver a entrar).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const menu = $("itt-menu");
  if (!menu) return;
  let csrf = "";
  let conocida = false;

  function mostrar(sel, visible) { for (const e of menu.querySelectorAll(sel)) e.hidden = !visible; }

  function pintar(datos) {
    const activa = !!(datos && datos.activa);
    conocida = true;
    $("itt-menu-nombre").textContent = activa ? datos.nombre : "Menú";
    const titulo = $("itt-menu-titulo");
    titulo.hidden = !activa;
    titulo.textContent = activa ? datos.nombre : "";
    const herramientas = new Set(activa ? datos.herramientas || [] : []);
    for (const a of menu.querySelectorAll("a[data-itt-menu]")) {
      const clave = a.dataset.ittMenu;
      if (["capturar", "cotizaciones", "reportes", "croquis"].includes(clave)) a.hidden = !herramientas.has(clave);
    }
    menu.querySelector("hr[data-itt-menu-campo]").hidden = herramientas.size === 0;
    mostrar("[data-itt-menu=cuenta]", activa);
    mostrar("[data-itt-menu=entrar]", !activa);
    $("itt-salir").hidden = !activa;
    if (datos && datos.csrf) csrf = datos.csrf;
    for (const a of menu.querySelectorAll("a[data-itt-menu]")) {
      if (a.pathname === location.pathname && a.origin === location.origin) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
  }

  async function recordado() {
    try {
      const m = await ITT.cola.ajuste("menu", null);
      const caducada = await ITT.cola.ajuste("sesion_caducada", false);
      if (m && !conocida) { csrf = await ITT.cola.ajuste("csrf", ""); pintar({ activa: !caducada, nombre: m.nombre, herramientas: m.herramientas, csrf }); }
    } catch (_) { /* sin IndexedDB: el menú queda con lo básico */ }
  }

  document.addEventListener("itt:sesion", (e) => pintar(e.detail));
  document.addEventListener("DOMContentLoaded", recordado);
  pintar({ activa: false });                                    // estado inicial hasta saber quién es: solo «Inicio» y «Iniciar sesión»
  conocida = false;

  function actualizarRed() {
    const sin = !navigator.onLine;
    $("itt-salir-aviso").hidden = !sin;
    $("itt-salir-boton").setAttribute("aria-disabled", sin ? "true" : "false");
  }
  addEventListener("online", actualizarRed);
  addEventListener("offline", actualizarRed);
  actualizarRed();

  // Se cierra al tocar fuera o con Escape, como el menú del resto del portal.
  document.addEventListener("click", (e) => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });

  $("itt-salir").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!navigator.onLine) { actualizarRed(); return; }
    const total = (window.ITT && ITT.estado && ITT.estado.resumen.total) || 0;
    const dlg = $("dlg-salir");
    $("dlg-salir-texto").textContent = total > 0
      ? `Tienes ${total === 1 ? "1 pendiente" : total + " pendientes"} por subir. No se pierden: se quedan guardados en este dispositivo y se suben cuando vuelvas a iniciar sesión con la misma cuenta.`
      : "Para volver a usar Herramientas tendrás que iniciar sesión otra vez.";
    $("dlg-salir-no").onclick = () => dlg.close("no");
    dlg.returnValue = "no";
    dlg.addEventListener("close", async () => {
      if (dlg.returnValue !== "si") return;
      if (!csrf) { try { const r = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } }); if (r.ok) csrf = (await r.json()).csrf; } catch (_) { /* sin red: se intentará con lo que haya */ } }
      const f = $("itt-salir");
      const campo = document.createElement("input");                      // el token no viaja en el HTML (es igual para todas las cuentas): se agrega aquí
      campo.type = "hidden"; campo.name = "csrfmiddlewaretoken"; campo.value = csrf;
      f.append(campo);
      f.submit();
    }, { once: true });
    dlg.showModal();
  });
})();

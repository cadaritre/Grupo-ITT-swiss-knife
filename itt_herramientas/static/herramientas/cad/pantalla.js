/* CAD · el editor usa todo el alto disponible y «Pantalla completa» (pedido del dueño, 16-11-2026: «se ve muy chiquito, … pantalla
 * completa … y que se siga viendo el módulo de civil»).
 *
 * - Siempre: el área del editor llega hasta abajo de la ventana (100dvh menos lo que hay arriba de ella, medido de verdad: cabecera,
 *   avisos y la barra de Civil cambian de alto).
 * - «Pantalla completa» (botón, Ctrl/⌘+Mayús+F, F11): fuera el lateral, la cabecera de Herramientas y los datos del plano; queda una
 *   tira con el nombre, el estado, Guardar, Versiones y «Salir de pantalla completa», la barra de Civil y el editor en todo lo demás.
 *   Con la API de pantalla completa del navegador donde existe; donde no (Safari de iPhone) la página se maximiza dentro de la ventana.
 *   Esc NUNCA sale (pedido del dueño, 16-11-2026: en un CAD Esc es cancelar y deseleccionar): en Chromium se bloquea la tecla para el
 *   navegador (`navigator.keyboard.lock(["Escape"])`, que llega al editor; el navegador mismo avisa «mantén presionada Esc para salir»);
 *   donde no se puede (Safari, Firefox), si el navegador quita su pantalla completa con Esc, la página sigue maximizada y lo dice una
 *   vez. Solo el botón, Ctrl/⌘+Mayús+F o F11 quitan «Pantalla completa». Se recuerda en este dispositivo (al volver a abrir se maximiza en la página: el navegador solo deja entrar a su pantalla
 *   completa con un toque o una tecla).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const raiz = document.documentElement;
  const lienzo = $("cad-lienzo");
  if (!lienzo) return;
  const boton = $("cad-completa");
  const CLAVE = "tv.cad.completa";
  const recordar = (si) => { try { localStorage.setItem(CLAVE, si ? "1" : "0"); } catch (_) { /* solo esta vez */ } };
  const recordado = () => { try { return localStorage.getItem(CLAVE) === "1"; } catch (_) { return false; } };
  const nativa = () => document.fullscreenElement || document.webkitFullscreenElement;

  // -- Alto del editor: hasta abajo de la ventana -----------------------------------------------------------------------------
  function medir() {
    if (raiz.classList.contains("cad-tactil")) return;          // el modo táctil acomoda el editor por su cuenta
    const arriba = lienzo.getBoundingClientRect().top + (raiz.classList.contains("cad-completa") ? 0 : scrollY);
    const alto = (window.visualViewport ? visualViewport.height : innerHeight) - arriba - (raiz.classList.contains("cad-completa") ? 0 : 12);
    raiz.style.setProperty("--cad-alto-editor", `${Math.max(raiz.classList.contains("cad-completa") ? 240 : 360, Math.floor(alto))}px`);
  }
  let pendiente = 0;
  const programar = () => { cancelAnimationFrame(pendiente); pendiente = requestAnimationFrame(medir); };
  addEventListener("resize", programar);
  if (window.visualViewport) visualViewport.addEventListener("resize", programar);
  // Todo lo que está arriba del editor puede cambiar de alto (avisos, la barra de Civil cuando se llena, la cabecera al partirse en dos
  // líneas): se vigila cada pieza, y también al terminar de cargar y cuando el editor abre el plano.
  const ro = new ResizeObserver(programar);
  const cabecera = document.querySelector(".tv-encabezado"); if (cabecera) ro.observe(cabecera);
  for (let e = lienzo.previousElementSibling; e; e = e.previousElementSibling) ro.observe(e);
  if (lienzo.parentElement) for (let e = lienzo.parentElement.previousElementSibling; e; e = e.previousElementSibling) ro.observe(e);
  new MutationObserver(programar).observe(lienzo.parentElement || document.body, { childList: true });
  addEventListener("load", programar);
  // Y por si algo mueve el editor sin cambiar de tamaño (un elemento que aparece más arriba en la página): se revisa su posición.
  let ultimaArriba = NaN;
  setInterval(() => {
    if (document.visibilityState !== "visible" || raiz.classList.contains("cad-tactil")) return;
    const arriba = Math.round(lienzo.getBoundingClientRect().top + scrollY);
    if (arriba !== ultimaArriba) { ultimaArriba = arriba; medir(); }
  }, 400);
  document.addEventListener("tvcad:cargado", programar);

  // -- Pantalla completa ---------------------------------------------------------------------------------------------------------
  function pintarBoton() {
    if (!boton) return;
    const si = raiz.classList.contains("cad-completa");
    boton.setAttribute("aria-pressed", String(si));
    boton.querySelector("[data-cad-completa-texto]").textContent = si ? "Salir de pantalla completa" : "Pantalla completa";
    for (const i of boton.querySelectorAll("[data-cad-completa-icono]")) i.hidden = i.dataset.cadCompletaIcono !== (si ? "salir" : "entrar");
  }
  function aplicar(si) {
    raiz.classList.toggle("cad-completa", si);
    pintarBoton();
    programar();
    document.dispatchEvent(new CustomEvent("tvcad:pantalla", { detail: { completa: si } }));
  }
  // Esc para el editor: con la pantalla completa del navegador, Chromium deja que la página se quede con la tecla.
  async function bloquearEsc() {
    try { if (navigator.keyboard && navigator.keyboard.lock && nativa()) await navigator.keyboard.lock(["Escape"]); } catch (_) { /* sin bloqueo: ver alCambiarNativa */ }
  }
  function soltarEsc() { try { if (navigator.keyboard && navigator.keyboard.unlock) navigator.keyboard.unlock(); } catch (_) { /* nada */ } }
  let saliendo = false;
  async function entrar() {
    aplicar(true); recordar(true);
    const e = raiz;
    try {
      if (!nativa()) {
        if (e.requestFullscreen) await e.requestFullscreen({ navigationUI: "hide" });
        else if (e.webkitRequestFullscreen) e.webkitRequestFullscreen();
      }
      await bloquearEsc();
    } catch (_) { /* sin pantalla completa del navegador: queda maximizada en la página */ }
  }
  async function salir() {
    saliendo = true;
    aplicar(false); recordar(false);
    soltarEsc();
    try {
      if (nativa()) { if (document.exitFullscreen) await document.exitFullscreen(); else if (document.webkitExitFullscreen) document.webkitExitFullscreen(); }
    } catch (_) { /* nada */ }
    finally { saliendo = false; }
  }
  const alternar = () => (raiz.classList.contains("cad-completa") ? salir() : entrar());
  if (boton) boton.addEventListener("click", alternar);
  // El navegador quitó su pantalla completa (Esc en Safari o Firefox, o Esc sostenida en Chromium): la página sigue maximizada, con el
  // editor en toda la ventana; se dice una sola vez en este dispositivo.
  const AVISO = "tv.cad.completa-aviso-esc";
  function avisarUnaVez() {
    try { if (localStorage.getItem(AVISO) === "1") return; localStorage.setItem(AVISO, "1"); } catch (_) { /* sin almacenamiento: se dice ahora */ }
    if (window.tvAviso) window.tvAviso("Esc salió de la pantalla del navegador; el editor sigue maximizado.");
  }
  const alCambiarNativa = () => {
    if (!nativa() && ultimaNativa) {
      soltarEsc();
      if (raiz.classList.contains("cad-completa") && !saliendo) avisarUnaVez();
    }
    ultimaNativa = !!nativa(); programar();
  };
  let ultimaNativa = false;
  document.addEventListener("fullscreenchange", alCambiarNativa);
  document.addEventListener("webkitfullscreenchange", alCambiarNativa);
  document.addEventListener("keydown", (ev) => {
    const combo = (ev.ctrlKey || ev.metaKey) && ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "f";
    if (combo || ev.key === "F11") { ev.preventDefault(); alternar(); }
    // Esc nunca quita «Pantalla completa»: es del editor (cancelar, deseleccionar) y de las hojas.
  });
  // Desde el editor (el foco está en su marco): la página de arranque reenvía el atajo.
  addEventListener("message", (ev) => {
    const marco = $("cad-editor");
    if (ev.origin !== location.origin || !marco || ev.source !== marco.contentWindow) return;
    const m = ev.data || {};
    if (m.source === "tresvizo-cad-editor" && m.type === "pantalla") alternar();
  });

  window.TVCadPantalla = Object.freeze({ entrar, salir, alternar, completa: () => raiz.classList.contains("cad-completa") });
  if (recordado()) aplicar(true); else pintarBoton();
  programar();
})();

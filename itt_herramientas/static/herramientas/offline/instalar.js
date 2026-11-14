/* Guía de instalación: dice si la app está instalada y si el almacenamiento está protegido; en Android ofrece instalar. */
(function () {
  "use strict";
  let evento = null;
  const $ = (id) => document.getElementById(id);
  addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); evento = e; $("itt-instalar-boton").hidden = false; });
  $("itt-instalar-boton").addEventListener("click", async () => { if (!evento) return; evento.prompt(); await evento.userChoice; evento = null; $("itt-instalar-boton").hidden = true; });
  document.addEventListener("DOMContentLoaded", async () => {
    const instalada = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    let persistente = "no se pudo comprobar";
    if (navigator.storage && navigator.storage.persisted) persistente = (await navigator.storage.persisted()) ? "protegido" : "sin proteger";
    $("itt-estado-instalacion").textContent = (instalada ? "Estás usando la app instalada." : "Estás en una pestaña del navegador: instala la app para capturar con seguridad.")
      + (persistente === "sin proteger" ? " Tus fotos pendientes aún no están protegidas en este navegador." : "");
    // La guía del sistema de este teléfono va primero.
    if (/Android/i.test(navigator.userAgent)) { const a = $("guia-android"); a.parentNode.prepend(a); }
    $("itt-estado-instalacion").dataset.instalada = String(instalada);
  });
})();

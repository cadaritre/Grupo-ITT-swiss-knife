/* Comportamiento común de la PWA en todas las páginas de Herramientas:
 *  - contador «N pendientes» siempre visible (y banda fija si hay pendientes);
 *  - aviso antes de cerrar con pendientes (`beforeunload`);
 *  - `navigator.storage.persist()` y aviso al llegar al 80 % de la cuota;
 *  - disparadores de subida: al abrir, al volver la señal, al volver a primer plano y cada 30 s;
 *  - registro del service worker y aviso «Hay una versión nueva · Actualizar».
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const ES_PAGINA = typeof document !== "undefined";
  if (!ES_PAGINA) return;
  const BASE = document.currentScript ? new URL("./", document.currentScript.src).href : "/static/herramientas/offline/";
  const $ = (id) => document.getElementById(id);
  const estado = { resumen: { total: 0, en_cola: 0, subiendo: 0, error: 0, bytes: 0, items: [] }, pausada: "", subiendo: false };
  ITT.estado = estado;

  function texto(n) { return n === 1 ? "1 pendiente" : `${n} pendientes`; }
  const enApp = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  function pintar() {
    const r = estado.resumen;
    const caja = $("itt-pendientes");
    if (!caja) return;
    caja.dataset.total = String(r.total);
    caja.dataset.estado = r.error ? "error" : estado.pausada ? "pausada" : r.total ? "pendiente" : "limpio";
    const etiqueta = caja.querySelector("[data-itt=texto]");
    let detalle = "";
    if (r.total === 0) detalle = "Sin pendientes";
    else {
      detalle = texto(r.total);
      if (r.subiendo) detalle += ` · subiendo ${r.items.find((i) => i.estado === "subiendo")?.progreso ?? 0} %`;
      if (estado.pausada === "red") detalle += " · esperando señal";
      if (estado.pausada === "sesion") detalle += " · vuelve a iniciar sesión";
      if (r.error) detalle += ` · ${r.error} con error`;
      if (!enApp() && r.total) detalle += " · están en este navegador";
    }
    etiqueta.textContent = detalle;
    document.title = r.total ? `(${r.total}) ${document.title.replace(/^\(\d+\) /, "")}` : document.title.replace(/^\(\d+\) /, "");
    document.dispatchEvent(new CustomEvent("itt:cola", { detail: { resumen: r, pausada: estado.pausada } }));
  }

  async function refrescar() {
    try { estado.resumen = await ITT.cola.resumen(); } catch (e) { /* IndexedDB no disponible: el contador queda en 0 */ }
    pintar();
  }

  /** Guarda quién está con sesión y su token CSRF (los usa también el service worker). Sin red no hace nada. */
  async function sesion() {
    if (!navigator.onLine) return;
    try {
      const r = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
      if (r.ok) {
        const d = await r.json();
        await ITT.cola.guardarAjuste("cuenta", d.cuenta);
        await ITT.cola.guardarAjuste("csrf", d.csrf);
        await ITT.cola.guardarAjuste("sesion_caducada", false);
        if (estado.pausada === "sesion") estado.pausada = "";
      } else if (r.status === 401 || r.type === "opaqueredirect") {
        await ITT.cola.guardarAjuste("sesion_caducada", true);
        estado.pausada = "sesion";
      }
    } catch (e) { /* sin red: se conserva lo último conocido */ }
  }

  async function subir() {
    if (estado.subiendo) return;
    estado.subiendo = true;
    try {
      await sesion();
      // Sincronizadores de documentos (cotizaciones...): se registran en ITT.sincronizadores y corren antes y después de las
      // fotos. Un documento nunca espera a sus fotos (el PDF avisa de las que aún no llegan) y las fotos nunca esperan al documento.
      for (const sincronizar of ITT.sincronizadores || []) { try { await sincronizar(); } catch (e) { /* se reintenta en el siguiente ciclo */ } }
      const resumen = await ITT.subidor.procesar({ alCambiar: () => refrescar() });
      for (const sincronizar of ITT.sincronizadores || []) { try { await sincronizar(); } catch (e) { /* idem */ } }
      if (resumen && !resumen.omitido) estado.pausada = resumen.pausada || "";
      if (!estado.pausada && (await ITT.cola.ajuste("sesion_caducada", false))) estado.pausada = "sesion";
    } catch (e) { /* un fallo no detiene el siguiente intento */ }
    estado.subiendo = false;
    await refrescar();
    if (ITT.solicitarSyncDeFondo) ITT.solicitarSyncDeFondo();
  }

  async function almacenamiento() {
    if (!navigator.storage) return;
    try {
      if (navigator.storage.persist && !(await navigator.storage.persisted())) await navigator.storage.persist();
      const persistente = navigator.storage.persisted ? await navigator.storage.persisted() : false;
      const est = navigator.storage.estimate ? await navigator.storage.estimate() : {};
      const uso = est.quota ? est.usage / est.quota : 0;
      document.documentElement.dataset.almacenamientoPersistente = String(persistente);
      document.documentElement.dataset.almacenamientoUso = uso.toFixed(3);
      const aviso = $("itt-almacenamiento");
      if (aviso) {
        aviso.hidden = uso < 0.8;
        if (uso >= 0.8) aviso.textContent = `El espacio del teléfono para esta app está al ${Math.round(uso * 100)} %. Sube los pendientes con señal antes de capturar más.`;
      }
    } catch (e) { /* no es crítico */ }
  }
  ITT.pedirAlmacenamiento = almacenamiento;

  // Aviso al cerrar con pendientes (solo existe en la pestaña; en la app instalada lo cubre la banda fija).
  addEventListener("beforeunload", (e) => {
    if (estado.resumen.total > 0) { e.preventDefault(); e.returnValue = "Tienes capturas sin subir. Se conservan en este teléfono, pero aún no están en el servidor."; return e.returnValue; }
  });

  // Service worker y actualizaciones.
  async function registrarSW() {
    if (!("serviceWorker" in navigator)) return;
    try {
      const registro = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      const ofrecer = (trabajador) => {
        const banda = $("itt-actualizar");
        if (!banda) return;
        banda.hidden = false;
        banda.querySelector("button").onclick = () => { estado.actualizando = true; trabajador.postMessage({ tipo: "SALTAR_ESPERA" }); };
      };
      if (registro.waiting && navigator.serviceWorker.controller) ofrecer(registro.waiting);
      registro.addEventListener("updatefound", () => {
        const nuevo = registro.installing;
        nuevo && nuevo.addEventListener("statechange", () => { if (nuevo.state === "installed" && navigator.serviceWorker.controller) ofrecer(nuevo); });
      });
      // Recargar SOLO cuando la persona pidió actualizar (nunca por la primera instalación ni a mitad de una captura).
      navigator.serviceWorker.addEventListener("controllerchange", () => { if (estado.actualizando) { estado.actualizando = false; location.reload(); } });
      navigator.serviceWorker.addEventListener("message", (e) => { if (e.data && e.data.tipo === "subir") subir(); });
      ITT.solicitarSyncDeFondo = async () => {
        if (estado.resumen.total > 0 && registro.sync) { try { await registro.sync.register("itt-subir"); } catch (_) { /* sin Background Sync (iOS) */ } }
      };
    } catch (e) { /* sin service worker: la app sigue funcionando, sin carga offline */ }
  }

  new BroadcastChannel("itt-cola").onmessage = () => refrescar();
  addEventListener("online", () => subir());
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { refrescar(); subir(); } });
  setInterval(() => { if (document.visibilityState === "visible") subir(); }, 30000);

  ITT.subirAhora = subir;
  ITT.refrescarCola = refrescar;
  ITT.rutaOffline = BASE;
  document.addEventListener("DOMContentLoaded", async () => {
    await refrescar();
    registrarSW();
    almacenamiento();
    subir();
  });
})(typeof self !== "undefined" ? self : this);

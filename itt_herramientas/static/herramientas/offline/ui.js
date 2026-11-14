/* Comportamiento común de la PWA en todas las páginas de Herramientas:
 *  - banda «N pendientes» (solo cuando hay pendientes o error, y solo en las páginas donde sirve);
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
  const $ = (id) => document.getElementById(id);
  const estado = { resumen: { total: 0, en_cola: 0, subiendo: 0, error: 0, bytes: 0, items: [] }, pausada: "", subiendo: false, version: 0 };
  ITT.estado = estado;

  const plural = (n, uno, varios) => (n === 1 ? uno : varios);
  const enApp = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  /** Dónde está lo pendiente: página de campo → etiqueta del enlace. Un borrador sucio es de su tipo; una foto, de su referencia. */
  const DESTINOS = { cotizacion: ["/cotizaciones/", "Ver cotizaciones"], reporte: ["/reportes/", "Ver reportes"], croquis: ["/croquis/", "Ver croquis"], libre: ["/capturar/", "Ver capturas"] };
  async function destinos() {
    const hallados = new Set();
    for (const i of estado.resumen.items || []) {
      const ref = String((i.meta && i.meta.referencia) || "libre").split(":")[0];
      hallados.add(DESTINOS[ref] ? ref : "libre");
    }
    try {
      const cuenta = await ITT.cola.ajuste("cuenta", null);
      for (const b of await ITT.cola.borradores()) if (b.sucio && (b.cuenta == null || cuenta == null || b.cuenta === cuenta)) hallados.add(DESTINOS[b.tipo || "cotizacion"] ? (b.tipo || "cotizacion") : "libre");
    } catch (_) { /* sin IndexedDB: sin enlace */ }
    return [...hallados].map((k) => DESTINOS[k]);
  }

  let temporizadorListo = 0;
  function pintar() {
    const r = estado.resumen;
    const caja = $("itt-pendientes");
    if (!caja) return;
    const anterior = Number(caja.dataset.total || 0);
    caja.dataset.total = String(r.total);
    caja.dataset.estado = r.error ? "error" : estado.pausada ? "pausada" : r.total ? "pendiente" : "limpio";
    const etiqueta = caja.querySelector("[data-itt=texto]");
    let detalle = "";
    if (r.total === 0) detalle = "Todo está al día";
    else {
      detalle = plural(r.total, "1 pendiente", `${r.total} pendientes`);
      if (r.subiendo) detalle += ` · subiendo ${r.items.find((i) => i.estado === "subiendo")?.progreso ?? 0} %`;
      if (estado.pausada === "red") detalle += " · esperando señal";
      if (estado.pausada === "sesion") detalle += " · vuelve a iniciar sesión";
      if (r.error) detalle += ` · ${r.error} con error`;
      detalle += ` · ${plural(r.total, "guardado", "guardados")} ${enApp() ? "en este dispositivo" : "en este navegador"}`;
    }
    etiqueta.textContent = detalle;
    document.title = r.total ? `(${r.total}) ${document.title.replace(/^\(\d+\) /, "")}` : document.title.replace(/^\(\d+\) /, "");
    // ¿Hay algo que decir? Pendientes o error (la sesión caducada se dice dentro del texto de los pendientes). Al quedar todo subido se avisa un momento y se retira.
    const algo = r.total > 0 || r.error > 0;
    const dondeSirve = caja.dataset.banda === "si";
    clearTimeout(temporizadorListo);
    if (!algo && anterior > 0 && dondeSirve) {
      caja.hidden = false;
      document.documentElement.dataset.banda = "si";
      caja.dataset.estado = "listo";
      temporizadorListo = setTimeout(() => { caja.hidden = true; document.documentElement.dataset.banda = "no"; }, 4000);
    } else {
      const ver = algo && dondeSirve;
      caja.hidden = !ver;
      document.documentElement.dataset.banda = ver ? "si" : "no";
    }
    const enlace = caja.querySelector("[data-itt=enlace]");
    if (enlace) {
      const mio = ++estado.version;
      destinos().then((lista) => {
        if (mio !== estado.version) return;
        const otro = lista.find(([ruta]) => ruta !== location.pathname);          // lo pendiente de ESTA página ya está a la vista
        enlace.hidden = !otro || r.total === 0;
        if (otro) { enlace.href = otro[0]; enlace.textContent = otro[1]; }
      });
    }
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
        await ITT.cola.guardarAjuste("menu", { nombre: d.nombre, herramientas: d.herramientas || [], nav: d.nav || [], tema: d.tema || "claro" });
        document.dispatchEvent(new CustomEvent("itt:sesion", { detail: { activa: true, nombre: d.nombre, herramientas: d.herramientas || [], nav: d.nav || [], tema: d.tema || "claro", csrf: d.csrf } }));
        await ITT.cola.guardarAjuste("sesion_caducada", false);
        if (estado.pausada === "sesion") estado.pausada = "";
      } else if (r.status === 401 || r.type === "opaqueredirect") {
        await ITT.cola.guardarAjuste("sesion_caducada", true);
        estado.pausada = "sesion";
        document.dispatchEvent(new CustomEvent("itt:sesion", { detail: { activa: false } }));
      }
    } catch (e) { /* sin red: se conserva lo último conocido */ }
  }

  async function subir() {
    if (estado.subiendo) { estado.documentosPendientes = true; return; }   // un documento cambió durante un ciclo: se sincroniza en cuanto termine (las fotos no se reintentan en caliente)
    estado.subiendo = true;
    estado.documentosPendientes = false;
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
    if (estado.documentosPendientes) {
      estado.documentosPendientes = false;
      for (const sincronizar of ITT.sincronizadores || []) { try { await sincronizar(); } catch (e) { /* se reintenta en el siguiente ciclo */ } }
    }
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
        const principal = document.getElementById("contenido");
        if (principal && banda.parentElement !== principal) principal.prepend(banda);          // franja bajo la cabecera: no tapa nada
        banda.hidden = false;
        banda.querySelector("[data-itt=actualizar]").onclick = () => { estado.actualizando = true; trabajador.postMessage({ tipo: "SALTAR_ESPERA" }); };
        banda.querySelector("[data-itt=luego]").onclick = () => { banda.hidden = true; };
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
  document.addEventListener("DOMContentLoaded", async () => {
    await refrescar();
    registrarSW();
    almacenamiento();
    subir();
  });
})(typeof self !== "undefined" ? self : this);

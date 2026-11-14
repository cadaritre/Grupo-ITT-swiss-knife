/* Página «Capturar»: toma o elige fotos, las reduce en un Worker, las guarda en la cola local y las sube cuando hay
 * señal. Funciona sin conexión (la sirve el service worker). La cola y el subidor viven en cola.js y subidor.js.
 */
(function () {
  "use strict";
  const ITT = self.ITT;
  const $ = (id) => document.getElementById(id);
  const referencia = new URLSearchParams(location.search).get("ref") || "libre";
  const miniaturas = new Map();
  let posicion = null;                       // última posición del GPS: {lat, lon, precision, en}
  let observador = null;
  let ordenSiguiente = 0;

  function iniciarUbicacion() {
    if (observador !== null || !navigator.geolocation) return;
    observador = navigator.geolocation.watchPosition(
      (p) => { posicion = { lat: p.coords.latitude, lon: p.coords.longitude, precision: p.coords.accuracy, en: Date.now() }; $("itt-geo").textContent = `Ubicación activa (±${Math.round(p.coords.accuracy)} m)`; },
      (e) => { $("itt-geo").textContent = e.code === 1 ? "Ubicación negada: las fotos se guardarán «sin ubicación»." : "Ubicación no disponible por ahora."; },
      { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 });
  }

  const tamano = (n) => (n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`);
  function ahoraLocal() {
    const d = new Date(); const dos = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}T${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`;
  }

  async function capturar(archivo, origen) {
    const aviso = $("itt-mensaje");
    try {
      const r = await ITT.reducirImagen(archivo);
      const meta = { tomada: r.exif.tomada || ahoraLocal(), referencia, origen, orden: ordenSiguiente++, nota: $("itt-nota").value.trim().slice(0, 1000) };
      if (r.exif.lat !== undefined) { meta.lat = r.exif.lat; meta.lon = r.exif.lon; }
      else if (origen === "camara" && posicion && Date.now() - posicion.en < 120000) { meta.lat = Math.round(posicion.lat * 1e6) / 1e6; meta.lon = Math.round(posicion.lon * 1e6) / 1e6; meta.precision = Math.round(posicion.precision); }
      else meta.sin_ubicacion = true;
      const cuenta = await ITT.cola.ajuste("cuenta", null);
      await ITT.cola.agregar({
        destino: "herramientas.captura", blob: r.blob, nombre: (archivo.name || "foto").replace(/\.[^.]+$/, "") + ".jpg", tipo: "image/jpeg",
        sha256: r.sha256, ancho: r.ancho, alto: r.alto, meta, cuenta,
      });
      aviso.textContent = "";
      ITT.guardadas = (ITT.guardadas || 0) + 1;                       // cuántas capturas se han escrito en esta sesión (las pruebas lo leen)
      document.documentElement.dataset.guardadas = String(ITT.guardadas);
      ITT.pedirAlmacenamiento && ITT.pedirAlmacenamiento();
    } catch (e) {
      const llena = e && (e.name === "QuotaExceededError" || /quota/i.test(String(e.message)));
      aviso.textContent = llena
        ? "No hay espacio en el teléfono para guardar la foto. Sube los pendientes con señal o libera espacio; la foto NO se guardó."
        : `No se pudo guardar «${archivo.name || "foto"}»: ${e.message || e}`;
      aviso.dataset.tipo = "error";
    }
  }

  async function agregar(archivos, origen) {
    $("itt-instalar-tip").hidden = !(await primeraVez());
    for (const archivo of archivos) await capturar(archivo, origen);
    await ITT.refrescarCola();
    ITT.subirAhora();
  }

  async function primeraVez() {
    if (matchMedia("(display-mode: standalone)").matches || navigator.standalone === true) return false;
    return !(await ITT.cola.ajuste("guia_vista", false));
  }

  async function dibujar() {
    const lista = $("itt-lista");
    const { items } = ITT.estado.resumen;
    const vistos = new Set(items.map((i) => i.id));
    for (const li of [...lista.children]) if (!vistos.has(li.dataset.id)) { const url = miniaturas.get(li.dataset.id); if (url) URL.revokeObjectURL(url); miniaturas.delete(li.dataset.id); li.remove(); }
    for (const item of items) {
      let li = lista.querySelector(`[data-id="${item.id}"]`);
      if (!li) {
        li = document.createElement("li");
        li.dataset.id = item.id;
        li.className = "itt-captura";
        const img = document.createElement("img");
        img.alt = ""; img.width = 64; img.height = 64;
        const cuerpo = document.createElement("div");
        const t = document.createElement("strong"); t.dataset.itt = "titulo";
        const s = document.createElement("span"); s.className = "tv-tenue"; s.dataset.itt = "estado";
        const nota = document.createElement("input"); nota.type = "text"; nota.maxLength = 1000; nota.placeholder = "Nota"; nota.setAttribute("aria-label", "Nota de la foto");
        nota.addEventListener("change", () => ITT.cola.actualizar(item.id, { meta: Object.assign({}, item.meta, { nota: nota.value.trim() }) }));
        const barra = document.createElement("progress"); barra.max = 100;
        const acciones = document.createElement("div");
        const quitar = document.createElement("button"); quitar.type = "button"; quitar.textContent = "Quitar"; quitar.className = "tv-boton tv-boton--secundario";
        quitar.addEventListener("click", async () => {
          if (!(await ITT.ui.confirmar("Esta foto aún no está en el servidor. Si la quitas, se pierde y no se puede recuperar.", { aceptar: "Quitar foto", peligro: true }))) return;
          await ITT.cola.borrar(item.id);
        });
        const reintentar = document.createElement("button"); reintentar.type = "button"; reintentar.textContent = "Reintentar"; reintentar.dataset.itt = "reintentar"; reintentar.className = "tv-boton";
        reintentar.addEventListener("click", async () => { await ITT.subidor.reintentar(item.id); ITT.subirAhora(); });
        acciones.append(reintentar, quitar);
        cuerpo.append(t, s, nota, barra, acciones);
        li.append(img, cuerpo);
        lista.append(li);
        const foto = await ITT.cola.blob(item.id);
        if (foto) { const url = URL.createObjectURL(foto); miniaturas.set(item.id, url); img.src = url; }
        nota.value = (item.meta && item.meta.nota) || "";
      }
      li.dataset.estado = item.estado;
      li.querySelector("[data-itt=titulo]").textContent = `${item.nombre} · ${tamano(item.bytes)}`;
      const ubicacion = item.meta && item.meta.sin_ubicacion ? " · sin ubicación" : "";
      li.querySelector("[data-itt=estado]").textContent = (item.estado === "subiendo" ? `Subiendo ${item.progreso} %` : item.estado === "error" ? `Error: ${item.error}` : item.error ? `En cola · ${item.error}` : "En cola · guardada en este teléfono") + ubicacion;
      const barra = li.querySelector("progress"); barra.value = item.progreso || 0; barra.hidden = item.estado !== "subiendo";
      li.querySelector("[data-itt=reintentar]").hidden = item.estado !== "error";
    }
    $("itt-vacio").hidden = items.length > 0;
  }

  function enlaces() {
    $("itt-camara").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregar(f, "camara"); });
    $("itt-galeria").addEventListener("change", (e) => { const f = [...e.target.files]; e.target.value = ""; agregar(f, "galeria"); });
    $("itt-subir-ahora").addEventListener("click", () => ITT.subirAhora());
    $("itt-camara").addEventListener("click", iniciarUbicacion);
    $("itt-ubicacion").addEventListener("click", iniciarUbicacion);
    $("itt-cerrar-tip").addEventListener("click", async () => { $("itt-instalar-tip").hidden = true; await ITT.cola.guardarAjuste("guia_vista", true); });
    const red = () => { $("itt-red").textContent = navigator.onLine ? "En línea" : "Sin conexión: puedes seguir capturando"; $("itt-red").dataset.estado = navigator.onLine ? "en_linea" : "sin_conexion"; };
    addEventListener("online", red); addEventListener("offline", red); red();
    let pintando = Promise.resolve();
    document.addEventListener("itt:cola", () => { pintando = pintando.then(dibujar).catch(() => {}); });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    enlaces();
    $("itt-referencia").textContent = referencia === "libre" ? "Capturas sueltas" : `Reporte ${referencia}`;
    await ITT.refrescarCola();
    await dibujar();
    $("itt-instalar-tip").hidden = !(await primeraVez()) || ITT.estado.resumen.total === 0;
    document.documentElement.dataset.capturaLista = "1";
  });
})();

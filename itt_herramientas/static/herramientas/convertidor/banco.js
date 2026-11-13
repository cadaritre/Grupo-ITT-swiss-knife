/* Mesa de trabajo del convertidor: vista previa con MapLibre (base neutra, sin teselas en línea), capas, zona y compartir. */
(function () {
  "use strict";
  const uno = (selector, raiz) => (raiz || document).querySelector(selector);
  const varios = (selector, raiz) => [...(raiz || document).querySelectorAll(selector)];
  const principal = uno("#itt-conv");
  if (!principal) return;

  // Colores de las capas (por CSSOM: la CSP no permite estilos en línea).
  varios(".itt-swatch").forEach((s) => { s.style.background = s.dataset.color; });

  // La zona y el hemisferio se piden en un formulario y se mandan en el otro.
  const zona = uno("#zona"), hem = uno("#hemisferio");
  const espejo = () => { uno("[data-itt=zona-espejo]").value = zona.value; uno("[data-itt=hem-espejo]").value = hem.value; };
  zona.addEventListener("change", espejo); hem.addEventListener("change", espejo); espejo();

  const casillas = varios("input[data-itt=capa]");
  uno("[data-itt=todas]").addEventListener("click", () => { casillas.forEach((c) => { c.checked = true; }); filtrar(); });
  uno("[data-itt=ninguna]").addEventListener("click", () => { casillas.forEach((c) => { c.checked = false; }); filtrar(); });
  casillas.forEach((c) => c.addEventListener("change", () => filtrar()));
  uno("#itt-form-convertir").addEventListener("submit", (e) => {
    if (!casillas.some((c) => c.checked)) { e.preventDefault(); uno("#itt-mapa-info").textContent = "Activa al menos una capa para exportar."; }
  });

  // Compartir el último archivo (Web Share con archivo; si no se puede, queda el botón Descargar).
  const compartir = uno("#itt-compartir");
  if (compartir && navigator.canShare && navigator.share) {
    compartir.hidden = false;
    compartir.addEventListener("click", async () => {
      const r = await fetch(compartir.dataset.url, { credentials: "same-origin" });
      const blob = await r.blob();
      const nombre = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)"?/);
      await self.ITT.compartirArchivo(blob, nombre ? decodeURIComponent(nombre[1]) : "convertido", { tipo: blob.type });
    });
  }

  let mapa = null;
  const info = uno("#itt-mapa-info");
  const limites = JSON.parse((uno("#itt-limites") || { textContent: "null" }).textContent);
  const TIPO = { "itt-relleno": "Polygon", "itt-borde": "Polygon", "itt-lineas": "LineString", "itt-puntos": "Point" };

  function filtrar() {
    if (!mapa || !mapa.getLayer("itt-puntos")) return;
    const activas = casillas.filter((c) => c.checked).map((c) => c.value);
    const f = ["in", ["get", "capa"], ["literal", activas]];
    Object.keys(TIPO).forEach((id) => mapa.setFilter(id, ["all", f, ["==", ["get", "tipo"], TIPO[id]]]));
    info.textContent = `${activas.length} de ${casillas.length} capas visibles`;
  }

  function ajustar() {
    if (!mapa || !limites) return;
    mapa.fitBounds([[limites[0], limites[1]], [limites[2], limites[3]]], { padding: 32, duration: 0, maxZoom: 19 });
  }
  uno("#itt-ajustar").addEventListener("click", ajustar);

  function iniciar() {
    try {
      maplibregl.setWorkerUrl(uno("script[src*='maplibre-gl-csp.js']").src.replace("maplibre-gl-csp.js", "maplibre-gl-csp-worker.js"));
      mapa = new maplibregl.Map({
        container: "itt-mapa", attributionControl: false, dragRotate: false, pitchWithRotate: false,
        style: { version: 8, sources: {}, layers: [{ id: "fondo", type: "background", paint: { "background-color": "#E7EDF1" } }] },
        center: limites ? [(limites[0] + limites[2]) / 2, (limites[1] + limites[3]) / 2] : [-106.07, 28.63], zoom: 12,
      });
    } catch (e) {
      info.textContent = "Tu navegador no puede dibujar la vista previa del mapa. La conversión funciona igual.";
      return;
    }
    mapa.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapa.on("load", async () => {
      try {
        const r = await fetch(principal.dataset.vista, { credentials: "same-origin" });
        const datos = await r.json();
        mapa.addSource("itt", { type: "geojson", data: datos });
        const color = ["get", "color"];
        mapa.addLayer({ id: "itt-relleno", type: "fill", source: "itt", paint: { "fill-color": color, "fill-opacity": 0.22 } });
        mapa.addLayer({ id: "itt-borde", type: "line", source: "itt", paint: { "line-color": color, "line-width": 1.6 } });
        mapa.addLayer({ id: "itt-lineas", type: "line", source: "itt", paint: { "line-color": color, "line-width": 1.8 } });
        mapa.addLayer({ id: "itt-puntos", type: "circle", source: "itt", paint: { "circle-color": color, "circle-radius": 4, "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 } });
        mapa.on("click", (e) => {
          const objetos = mapa.queryRenderedFeatures(e.point, { layers: ["itt-puntos", "itt-lineas", "itt-relleno"] });
          if (!objetos.length) return;
          const p = objetos[0].properties;
          new maplibregl.Popup({ closeButton: true }).setLngLat(e.lngLat).setText(`${p.nombre || "Sin nombre"} · ${p.capa}`).addTo(mapa);
        });
        filtrar();
        ajustar();
        document.documentElement.dataset.mapaListo = "1";
        info.textContent = `${datos.features.length.toLocaleString("es-MX")} elementos en la vista · ${casillas.filter((c) => c.checked).length} capas visibles. Arrastra para mover, pellizca o usa +/− para acercar.`;
      } catch (e) {
        info.textContent = "No se pudo cargar la vista previa.";
      }
    });
    mapa.on("error", () => { if (!document.documentElement.dataset.mapaListo) info.textContent = "La vista previa no está disponible en este navegador. La conversión funciona igual."; });
  }
  iniciar();
})();

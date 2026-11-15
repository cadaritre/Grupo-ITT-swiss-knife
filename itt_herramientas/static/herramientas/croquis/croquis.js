/* Croquis de ubicación (herramientas-web.md §4.3): mapa MapLibre con teselas del propio servidor, polígono y rectángulo con Terra Draw,
 * lista de vértices, importación CSV/KML/KMZ, capas, vectorización desde OpenStreetMap en el servidor y PDF/DXF.
 * El proyecto se autoguarda como borrador (offline/documentos.js) y se sincroniza solo. Rutas con hash: #/ lista · #/q/<id> croquis.
 */
(function () {
  "use strict";
  const ITT = self.ITT;
  const $ = (id) => document.getElementById(id);
  const { h, limpiar, confirmar } = ITT.ui;
  const raiz = $("cq");
  const urls = {};
  for (const a of raiz.attributes) if (a.name.startsWith("data-url-")) urls[a.name.slice(9)] = a.value;
  const docs = ITT.crearDocumentos({ tipo: "croquis", lista: "croquis", avisoCopia: "Se guardó como copia porque este croquis cambió en otro dispositivo mientras lo editabas aquí." });
  const mensaje = (texto, tipo) => ITT.ui.mensaje("cq-mensaje", texto, tipo);
  const { icono, boton } = ITT.ui;
  const enLinea = () => navigator.onLine;
  const esperarMs = (ms) => new Promise((r) => setTimeout(r, ms));

  let ctx = null, reg = null, temporizador = null;
  let mapa = null, dibujo = null, mapaListo = null, marcaUbicacion = null;
  let estado = { vectores: "ninguno", resumen: {}, datos: null };          // lo que dice el servidor (GET detalle)
  let enDibujo = false;                                                    // evita bucles entre la tabla de vértices y Terra Draw
  let salida = null;                                                       // {blob, nombre} del último archivo generado
  const COLORES = { building: "#8A5A44", road: "#4B5563", parking: "#9CA3AF", water: "#2A7FBF", vegetation: "#4F9A52", recreation: "#76B947", railway: "#6B21A8",
    barrier: "#A16207", power: "#DC2626", structure: "#7C3AED", amenity: "#DB2777", contour: "#B45309", landuse: "#D6A96B", other: "#6B7280" };

  // -- Cálculos (los mismos que el servidor: osm_vector.selection_area_km2) ------------------------------------------------------
  function areaKm2(puntos) {
    if (puntos.length < 3) return 0;
    const lat = puntos.reduce((s, p) => s + p.latitude, 0) / puntos.length;
    const lon = puntos.reduce((s, p) => s + p.longitude, 0) / puntos.length;
    const sx = 111320 * Math.cos((lat * Math.PI) / 180), sy = 110574;
    const q = puntos.map((p) => [(p.longitude - lon) * sx, (p.latitude - lat) * sy]);
    let a = 0;
    for (let i = 0; i < q.length; i++) { const j = (i + 1) % q.length; a += q[i][0] * q[j][1] - q[j][0] * q[i][1]; }
    return Math.abs(a) / 2 / 1e6;
  }
  const redondear7 = (x) => Math.round(x * 1e7) / 1e7;
  const firma = (d) => JSON.stringify([d.points.map((p) => [redondear7(p.latitude), redondear7(p.longitude)]), d.map_layer, d.contour_interval]);
  const textoKm2 = (km2) => (km2 >= 10 ? km2.toFixed(1) : km2 >= 1 ? km2.toFixed(2) : `${Math.round(km2 * 100)} ha`.replace(/^0 ha$/, "<1 ha"));

  // -- Guardado ------------------------------------------------------------------------------------------------------------------------
  function estadoGuardado() {
    const e = $("cq-estado-guardado");
    limpiar(e);
    if (!reg) return;
    const [ico, texto] = reg.sucio ? (enLinea() ? ["cloud-upload", "Guardando…"] : ["cloud-off", "Guardado en este teléfono"]) : ["cloud-check", "Guardado"];
    e.append(icono(ico), h("span", {}, texto));
    e.dataset.estado = reg.sucio ? "pendiente" : "sincronizado";
  }
  function programarGuardado() {
    reg.sucio = true;
    estadoGuardado();
    clearTimeout(temporizador);
    temporizador = setTimeout(guardarYa, 300);
  }
  async function guardarYa() {
    clearTimeout(temporizador);
    temporizador = null;
    if (!reg) return;
    const nuevo = await docs.guardar(reg.id, reg.datos, { aviso: reg.aviso });
    if (nuevo && reg && nuevo.id === reg.id) reg.actualizada = nuevo.actualizada;
    estadoGuardado();
    ITT.refrescarCola && (await ITT.refrescarCola());
    if (enLinea() && ITT.subirAhora) ITT.subirAhora();
  }
  addEventListener("pagehide", () => { if (temporizador) guardarYa(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && temporizador) guardarYa(); });

  async function contexto() {
    const cuenta = await ITT.cola.ajuste("cuenta", null);
    try {
      const c = await docs.pedir(urls.contexto);
      await ITT.cola.guardarAjuste("cq_contexto", c);
      return c;
    } catch (e) {
      const guardado = await ITT.cola.ajuste("cq_contexto", null);
      if (guardado && (cuenta == null || guardado.cuenta === cuenta)) return Object.assign({ sin_conexion: true }, guardado);
      throw e;
    }
  }

  // -- Lista ----------------------------------------------------------------------------------------------------------------------------
  async function pintarLista() {
    const ul = $("cq-lista");
    const todos = await docs.listar();
    limpiar(ul);
    $("cq-lista-vacia").hidden = todos.length > 0;
    for (const b of todos) {
      const d = b.datos;
      const km2 = areaKm2(d.points);
      ul.append(h("li", { class: "cot-tarjeta", "data-id": b.id, "data-sucio": b.sucio ? "1" : "" },
        h("a", { class: "cot-tarjeta__enlace", href: `#/q/${b.id}` },
          h("span", { class: "cot-tarjeta__folio" }, d.points.length ? `${d.points.length} vértices · ${textoKm2(km2)}${km2 >= 1 ? " km²" : ""}` : "Sin área"),
          h("strong", { class: "cot-tarjeta__titulo" }, d.title || "Sin título"),
          h("span", { class: "cot-tarjeta__cliente" }, [d.client, d.project].filter(Boolean).join(" · ") || "Sin cliente")),
        h("div", { class: "cot-tarjeta__pie" },
          b.sucio ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono(enLinea() ? "cloud-upload" : "cloud-off"), h("span", {}, "Sin subir"))
            : h("span", { class: "itt-estado" }, icono("cloud-check"), h("span", {}, "Guardado")),
          b.aviso ? h("span", { class: "itt-estado", "data-estado": "pendiente" }, icono("copy"), h("span", {}, "Copia por conflicto")) : null,
          h("span", { class: "cot-tarjeta__espacio" }),
          boton({ onclick: () => borrarRegistro(b), etiqueta: `Eliminar ${d.title || "croquis"}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "trash-2"))));
    }
  }
  async function nuevo() {
    if (!ctx) return mensaje("Sin conexión. Abre Croquis una vez con señal para poder trabajar sin ella.", "error");
    const d = new Date(), dos = (n) => String(n).padStart(2, "0");
    const base = JSON.parse(JSON.stringify(ctx.base));
    base.sketch_date = `${dos(d.getDate())}/${dos(d.getMonth() + 1)}/${d.getFullYear()}`;
    const r = await docs.crear(base);
    location.hash = `#/q/${r.id}`;
  }
  async function borrarRegistro(b) {
    const nombre = b.datos.title || "este croquis";
    if (!(await confirmar(b.sucio || !b.servidor_id ? `¿Eliminar «${nombre}»? Tiene cambios que aún no se suben y se perderán.` : `¿Eliminar «${nombre}» y su cartografía? No se puede deshacer.`, { aceptar: "Eliminar croquis", peligro: true }))) return;
    await docs.borrar(b.id);
    await pintarLista();
    ITT.subirAhora && ITT.subirAhora();
  }

  // -- Mapa ----------------------------------------------------------------------------------------------------------------------------
  const plantillaTeselas = (capa) => `${location.origin}${(urls.teselas || "").replace("{capa}", capa)}`;

  function iniciarMapa() {
    if (mapaListo) return mapaListo;
    mapaListo = new Promise((resolver) => {
      try {
        const script = document.querySelector("script[data-itt-maplibre-worker]");     // atributo propio: otros scripts traen sus propios workers
        maplibregl.setWorkerUrl(script.dataset.ittMaplibreWorker);
        mapa = new maplibregl.Map({
          container: "cq-mapa", attributionControl: false, dragRotate: false, pitchWithRotate: false, center: [-106.0691, 28.633], zoom: 12, maxZoom: 19,
          style: { version: 8, sources: {
            osm: { type: "raster", tiles: [plantillaTeselas("osm")], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" },
            topo: { type: "raster", tiles: [plantillaTeselas("topo")], tileSize: 256, maxzoom: 17, attribution: "Datos © OpenStreetMap contributors, SRTM | Mapa © OpenTopoMap (CC-BY-SA)" } },
            layers: [{ id: "fondo", type: "background", paint: { "background-color": "#E7EDF1" } }, { id: "osm", type: "raster", source: "osm", layout: { visibility: "none" } }, { id: "topo", type: "raster", source: "topo", layout: { visibility: "none" } }] },
        });
      } catch (e) {
        $("cq-mapa-info").textContent = "Este navegador no puede mostrar el mapa. Escribe o importa los vértices; todo lo demás funciona igual.";
        resolver(false);
        return;
      }
      mapa.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      mapa.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
      mapa.addControl(new maplibregl.AttributionControl({ compact: true }));
      mapa.on("load", () => {
        mapa.addSource("vec", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        const color = ["match", ["get", "categoria"], ...Object.entries(COLORES).flat(), "#6B7280"];
        mapa.addLayer({ id: "vec-relleno", type: "fill", source: "vec", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": color, "fill-opacity": 0.3 } });
        mapa.addLayer({ id: "vec-borde", type: "line", source: "vec", paint: { "line-color": color, "line-width": 1.4 } });
        mapa.addSource("vert", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        mapa.addLayer({ id: "vert-puntos", type: "circle", source: "vert", paint: { "circle-radius": 5, "circle-color": "#E53935", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
        try {
          const adaptador = new terraDrawMaplibreGlAdapter.TerraDrawMapLibreGLAdapter({ map: mapa });
          dibujo = new terraDraw.TerraDraw({ adapter: adaptador, modes: [
            new terraDraw.TerraDrawSelectMode({ flags: { polygon: { feature: { draggable: true, rotateable: false, scaleable: false, coordinates: { midpoints: true, draggable: true, deletable: true } } },
              rectangle: { feature: { draggable: true, rotateable: false, scaleable: false, coordinates: { draggable: true } } } } }),
            new terraDraw.TerraDrawPolygonMode(), new terraDraw.TerraDrawRectangleMode()] });
          dibujo.start();
          dibujo.on("finish", alTerminarDibujo);
          dibujo.on("select", () => { /* la edición se confirma en «finish» */ });
        } catch (e) {
          $("cq-mapa-info").textContent = "No se pudo activar el dibujo en este navegador. Escribe o importa los vértices.";
        }
        document.documentElement.dataset.mapaListo = "1";
        resolver(true);
      });
      mapa.on("error", () => {});
    });
    return mapaListo;
  }

  function aplicarCapaBase() {
    if (!mapa || !reg) return;
    const capa = (ctx.capas_mapa.find((c) => c.nombre === reg.datos.map_layer) || {}).capa;
    for (const id of ["osm", "topo"]) if (mapa.getLayer(id)) mapa.setLayoutProperty(id, "visibility", capa === id ? "visible" : "none");
  }

  function anillo(puntos) { const r = puntos.map((p) => [p.longitude, p.latitude]); return [...r, r[0]]; }

  function pintarVertices() {
    if (!mapa || !mapa.getSource("vert")) return;
    const p = reg.datos.points;
    mapa.getSource("vert").setData({ type: "FeatureCollection", features: p.length && p.length < 3 ? p.map((q) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [q.longitude, q.latitude] } })) : [] });
  }

  /** Dibuja en Terra Draw el polígono de los vértices actuales (la tabla manda; Terra Draw es la vista editable). */
  function aDibujo() {
    if (!dibujo) return;
    enDibujo = true;
    try {
      dibujo.clear();
      const p = reg.datos.points;
      reg.datos.layer_visibility.selection === false || p.length < 3 || dibujo.addFeatures([{ type: "Feature", properties: { mode: reg.datos.geometry === "Rectángulo" && p.length === 4 ? "rectangle" : "polygon" }, geometry: { type: "Polygon", coordinates: [anillo(p)] } }]);
    } catch (e) { /* un polígono que Terra Draw no acepta (autointerseccion) sigue en la tabla */ }
    enDibujo = false;
    pintarVertices();
  }

  function alTerminarDibujo(id, contexto) {
    if (enDibujo || !reg) return;
    const f = dibujo.getSnapshot().find((x) => x.id === id);
    if (!f || f.geometry.type !== "Polygon") return;
    const modo = f.properties.mode;
    const nuevos = f.geometry.coordinates[0].slice(0, -1).map(([lon, lat]) => ({ latitude: redondear7(lat), longitude: redondear7(lon) }));
    if (nuevos.length < 3) return;
    const viejos = reg.datos.points;
    reg.datos.points = nuevos.map((q, i) => {
      const igual = viejos.find((v) => Math.abs(v.latitude - q.latitude) < 1e-7 && Math.abs(v.longitude - q.longitude) < 1e-7);
      return Object.assign({ name: `V${i + 1}`, description: "" }, igual ? { name: igual.name, description: igual.description } : {}, q);
    });
    reg.datos.geometry = modo === "rectangle" ? "Rectángulo" : "Polígono";
    if (contexto && (contexto.mode === "polygon" || contexto.mode === "rectangle") && contexto.action === "draw") {
      for (const otro of dibujo.getSnapshot()) if (otro.id !== id) dibujo.removeFeatures([otro.id]);          // un solo área a la vez
      setTimeout(() => ponerModo("select"), 0);
    }
    pintarTablaVertices(); pintarVertices(); cambioDeArea();             // la tabla sigue al mapa (antes quedaba vacía o con coordenadas viejas)
  }

  function ponerModo(modo) {
    if (!dibujo) return;
    try { dibujo.setMode(modo); } catch (e) { return; }
    for (const b of document.querySelectorAll(".cq-modos [data-modo]")) b.setAttribute("aria-pressed", String(b.dataset.modo === modo));
    raiz.dataset.modoActivo = modo;
  }

  function ajustar() {
    if (!mapa) return;
    const p = reg.datos.points;
    if (!p.length) return;
    const lons = p.map((q) => q.longitude), lats = p.map((q) => q.latitude);
    mapa.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40, duration: 0, maxZoom: 18 });
  }

  function miUbicacion() {
    if (!navigator.geolocation) return mensaje("Este navegador no da la ubicación.", "error");
    navigator.geolocation.getCurrentPosition((p) => {
      if (!mapa) return;
      const c = [p.coords.longitude, p.coords.latitude];
      if (marcaUbicacion) marcaUbicacion.remove();
      marcaUbicacion = new maplibregl.Marker({ color: "#0A79A4" }).setLngLat(c).addTo(mapa);
      mapa.easeTo({ center: c, zoom: Math.max(mapa.getZoom(), 16), duration: 0 });
    }, (e) => mensaje(e.code === 1 ? "Ubicación negada: puedes seguir dibujando el área a mano." : "No se pudo obtener la ubicación.", "error"), { enableHighAccuracy: true, timeout: 20000 });
  }

  // -- Vectores en el mapa ---------------------------------------------------------------------------------------------------------------
  const categoriasVisibles = () => Object.keys(ctx.categorias).filter((k) => reg.datos.layer_visibility[k] !== false);

  function filtroVectores() {
    if (!mapa || !mapa.getLayer("vec-borde")) return;
    const vis = ["in", ["get", "categoria"], ["literal", categoriasVisibles()]];
    mapa.setFilter("vec-relleno", ["all", ["==", ["geometry-type"], "Polygon"], vis]);
    mapa.setFilter("vec-borde", vis);
  }
  async function cargarVectores() {
    if (!mapa || !mapa.getSource("vec")) return;
    let datos = { type: "FeatureCollection", features: [] };
    if (estado.vectores === "vigentes" && reg.servidor_id && enLinea()) {
      try { datos = await docs.pedir(docs.url("vectores", reg.servidor_id)); } catch (e) { /* se ven sin cartografía */ }
    }
    mapa.getSource("vec").setData(datos);
    filtroVectores();
  }

  // -- Estado del servidor ----------------------------------------------------------------------------------------------------------------
  async function refrescarEstado() {
    if (!reg || !reg.servidor_id || !enLinea()) { pintarAcciones(); return; }
    try {
      const d = await docs.pedir(docs.url("detalle", reg.servidor_id));
      estado = { vectores: d.vectores, resumen: d.resumen || {}, datos: d.datos, vectorizado: d.vectorizado, pdf_al_dia: d.pdf_al_dia };
    } catch (e) { /* sin red */ }
    await cargarVectores();
    pintarAcciones(); pintarCapas();
  }
  const vectoresVigentes = () => estado.vectores === "vigentes" && estado.datos && firma(estado.datos) === firma(reg.datos);

  // -- Pantalla del croquis -------------------------------------------------------------------------------------------------------------------
  function pintarCampos() {
    const d = reg.datos;
    for (const e of raiz.querySelectorAll("[data-dato]")) e.value = e.type === "date" ? ITT.ui.fechaAIso(d[e.dataset.dato]) : (d[e.dataset.dato] ?? "");
    $("cq-titulo-editor").textContent = d.title || "Croquis";
    const aviso = $("cq-aviso-doc");
    limpiar(aviso);
    aviso.hidden = !reg.aviso;
    if (reg.aviso) aviso.append(reg.aviso, " ", h("button", { type: "button", class: "tv-boton tv-boton--secundario", onclick: async () => { reg.aviso = ""; aviso.hidden = true; await guardarYa(); } }, "Entendido"));
    pintarTablaVertices(); pintarCapas(); pintarArea(); pintarAcciones(); estadoGuardado();
  }

  function pintarArea() {
    const p = reg.datos.points, km2 = areaKm2(p), limite = ctx ? ctx.limite_km2 : 50;
    const a = $("cq-area");
    if (p.length < 3) { a.textContent = p.length ? `${p.length} punto${p.length === 1 ? "" : "s"} · falta el área` : "Sin área"; a.dataset.estado = "vacia"; }
    else if (km2 > limite) { a.textContent = `${textoKm2(km2)} km² · pasa de ${limite} km²: exporta el proyecto para el módulo local`; a.dataset.estado = "excedida"; }
    else { a.textContent = `${p.length} vértices · ${textoKm2(km2)}${km2 >= 1 ? " km²" : ""}`; a.dataset.estado = "ok"; }
    $("cq-resumen-vertices").textContent = p.length ? String(p.length) : "";
    $("cq-sin-vertices").hidden = p.length > 0;
  }

  function pintarAcciones() {
    if (!reg) return;
    const p = reg.datos.points, km2 = areaKm2(p), limite = ctx ? ctx.limite_km2 : 50;
    const excedida = p.length >= 3 && km2 > limite, hayArea = p.length >= 3 && km2 > 0;
    const vigentes = vectoresVigentes();
    $("cq-modulo").hidden = !excedida;
    $("cq-vectorizar").hidden = excedida || vigentes;
    $("cq-vectorizar").disabled = !hayArea;
    $("cq-pdf").hidden = excedida || !vigentes;
    $("cq-dxf").hidden = excedida || !vigentes;
    $("mas-vectorizar").hidden = excedida || !vigentes;
    const r = estado.resumen || {};
    $("cq-resumen-barra").textContent = excedida ? "Área mayor que el límite de la web" : !hayArea ? "Dibuja el área para empezar" : vigentes ? `${r.total || 0} elementos de OpenStreetMap` : estado.vectores === "obsoletos" ? "El área cambió: vectoriza de nuevo" : "Falta vectorizar el mapa";
    $("q-contornos-caja").hidden = !reg.datos.map_layer.startsWith("Topográfico");
    $("cq-resumen-capas").textContent = reg.datos.map_layer.replace(/ - .*/, "");
  }

  function pintarCapas() {
    const ul = $("cq-capas");
    limpiar(ul);
    const conteos = (estado.resumen && estado.resumen.conteos) || {};
    const fila = (clave, texto, n) => h("li", {}, h("div", { class: "tv-campo tv-campo--casilla" },
      h("input", { id: `capa-${clave}`, type: "checkbox", checked: reg.datos.layer_visibility[clave] !== false, onchange: (e) => { reg.datos.layer_visibility[clave] = e.target.checked; filtroVectores(); if (clave === "selection") aDibujo(); programarGuardado(); } }),
      h("label", { for: `capa-${clave}` }, texto, n != null ? ` (${n})` : "")));
    ul.append(fila("selection", "Polígono de selección"), fila("labels", "Etiquetas"));
    for (const [k, texto] of Object.entries(ctx.categorias)) ul.append(fila(k, texto, conteos[k] || 0));
  }
  function todasLasCapas(valor) { for (const k of Object.keys(ctx.categorias)) reg.datos.layer_visibility[k] = valor; pintarCapas(); filtroVectores(); programarGuardado(); }

  function pintarTablaVertices() {
    const ol = $("cq-vertices");
    const activo = document.activeElement && ol.contains(document.activeElement) ? document.activeElement.id : "";
    limpiar(ol);
    const p = reg.datos.points;
    p.forEach((v, i) => {
      const campo = (etiqueta, id, valor, props, alCambiar) => h("div", { class: "tv-campo" }, h("label", { for: id }, etiqueta),
        h("input", Object.assign({ id, type: "text", value: valor, autocomplete: "off", onchange: alCambiar }, props)));
      ol.append(h("li", { class: "cq-vertice", "data-i": String(i) },
        h("strong", {}, `Vértice ${i + 1}`),
        campo("Nombre", `v-n-${i}`, v.name, { maxLength: 60 }, (e) => { v.name = e.target.value.slice(0, 60); programarGuardado(); }),
        h("div", { class: "cot-fila-numeros" },
          campo("Latitud", `v-la-${i}`, v.latitude.toFixed(7), { inputMode: "decimal" }, (e) => editarCoordenada(i, "latitude", e.target, 85.0511)),
          campo("Longitud", `v-lo-${i}`, v.longitude.toFixed(7), { inputMode: "decimal" }, (e) => editarCoordenada(i, "longitude", e.target, 180))),
        campo("Descripción", `v-d-${i}`, v.description || "", { maxLength: 200 }, (e) => { v.description = e.target.value.slice(0, 200); programarGuardado(); }),
        h("div", { class: "rep-botones" },
          boton({ onclick: () => moverVertice(i, -1), disabled: i === 0, etiqueta: `Subir el vértice ${i + 1}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "chevron-up"),
          boton({ onclick: () => moverVertice(i, 1), disabled: i === p.length - 1, etiqueta: `Bajar el vértice ${i + 1}`, class: "tv-boton tv-boton--secundario itt-boton-plano" }, "chevron-down"),
          boton({ onclick: () => quitarVertice(i), etiqueta: `Quitar el vértice ${i + 1}`, "data-itt": "quitar-vertice", class: "tv-boton tv-boton--secundario itt-boton-plano" }, "trash-2"))));
    });
    if (activo && $(activo)) $(activo).focus();
  }
  function editarCoordenada(i, campo, entrada, limite) {
    const n = parseFloat(String(entrada.value).replace(",", "."));
    if (!Number.isFinite(n) || Math.abs(n) > limite) { entrada.setAttribute("aria-invalid", "true"); return; }
    entrada.setAttribute("aria-invalid", "false");
    reg.datos.points[i][campo] = redondear7(n);
    entrada.value = reg.datos.points[i][campo].toFixed(7);
    aDibujo(); cambioDeArea();
  }
  function moverVertice(i, d) { const p = reg.datos.points; [p[i], p[i + d]] = [p[i + d], p[i]]; pintarTablaVertices(); aDibujo(); cambioDeArea(); }
  function quitarVertice(i) { reg.datos.points.splice(i, 1); pintarTablaVertices(); aDibujo(); cambioDeArea(); }
  function agregarVertice() {
    const p = reg.datos.points;
    if (p.length >= ctx.max_vertices) return mensaje(`Un croquis admite hasta ${ctx.max_vertices} vértices.`, "error");
    const c = mapa ? mapa.getCenter() : { lat: 28.633, lng: -106.0691 };
    p.push({ name: `V${p.length + 1}`, latitude: redondear7(p.length ? p[p.length - 1].latitude + 0.0005 : c.lat), longitude: redondear7(p.length ? p[p.length - 1].longitude + 0.0005 : c.lng), description: "" });
    pintarTablaVertices(); aDibujo(); cambioDeArea();
    $(`v-la-${p.length - 1}`).focus();
  }
  async function borrarArea() {
    if (!reg.datos.points.length) return;
    if (!(await confirmar("¿Borrar el área y sus vértices? La cartografía vectorizada deja de valer.", { aceptar: "Borrar el área", peligro: true }))) return;
    reg.datos.points = [];
    pintarTablaVertices(); aDibujo(); cambioDeArea();
  }

  /** Cualquier cambio del área o de la capa: los vectores del servidor dejan de coincidir (se avisa), se guarda. */
  function cambioDeArea() {
    pintarArea(); pintarAcciones(); pintarVertices(); programarGuardado();
  }

  function alEscribir(e) {
    const k = e.target.dataset.dato;
    if (!k || !reg) return;
    reg.datos[k] = e.target.type === "date" ? ITT.ui.fechaAEs(e.target.value) : e.target.value;
    if (k === "title") $("cq-titulo-editor").textContent = e.target.value || "Croquis";
    if (k === "map_layer") { aplicarCapaBase(); pintarAcciones(); }
    if (k === "map_layer" || k === "contour_interval") pintarAcciones();
    programarGuardado();
  }

  // -- Importar ------------------------------------------------------------------------------------------------------------------------
  async function importar(archivo) {
    if (!enLinea()) return mensaje("Sin conexión. Importa el archivo cuando vuelva la señal.", "error");
    const f = new FormData(); f.append("archivo", archivo);
    try {
      const r = await docs.pedir(urls.importar, { metodo: "POST", formulario: f });
      if (r.reemplaza) {
        if (reg.datos.points.length && !(await confirmar("El polígono del KML reemplazará el área actual y su cartografía. ¿Continuar?", { aceptar: "Reemplazar" }))) return;
        reg.datos.points = r.puntos;
        mensaje(r.poligonos > 1 ? `Polígono importado: de ${r.poligonos} polígonos se tomó el mayor.` : "Polígono importado.", "info");
      } else {
        const hay = reg.datos.points.length;
        let decision = "alt";                                          // sin área dibujada no hay nada que preguntar: se agregan
        if (hay) {
          // Importar no debe mezclar en silencio dos áreas (el resultado suele ser un polígono cruzado): se pregunta.
          decision = await confirmar(`El croquis ya tiene ${hay} vértice${hay === 1 ? "" : "s"} y el archivo trae ${r.puntos.length}. ¿Los reemplazas o los agregas al final?`, { aceptar: "Reemplazar", alternativa: "Agregar al final", cancelar: "Cancelar" });
          if (!decision) return;
        }
        if (decision === true) {
          reg.datos.points = r.puntos.slice(0, ctx.max_vertices);
          mensaje(`Vértices reemplazados: ${Math.min(r.puntos.length, ctx.max_vertices)} importados.`, "info");
        } else {
          const faltan = ctx.max_vertices - hay;
          reg.datos.points = reg.datos.points.concat(r.puntos.slice(0, Math.max(0, faltan)));
          mensaje(`${Math.min(r.puntos.length, faltan)} puntos agregados al final.`, "info");
        }
      }
      pintarTablaVertices(); aDibujo(); cambioDeArea(); ajustar();
      $("cq-paso-vertices").open = true;
    } catch (e) { mensaje(e.mensaje || "No se pudo importar el archivo.", "error"); }
  }

  // -- Trabajos del servidor (vectorizar, PDF, DXF) ------------------------------------------------------------------------------------------
  async function sincronizado() {
    await guardarYa();
    for (let i = 0; i < 120; i++) {
      const r = await ITT.cola.borrador(reg.id);
      if (r && r.error) throw { mensaje: `El servidor no aceptó el croquis: ${r.error}` };
      if (r && !r.sucio && r.servidor_id) { reg.servidor_id = r.servidor_id; reg.revision_base = r.revision_base; return; }
      if (!enLinea()) throw { mensaje: "Se perdió la señal. El croquis está guardado en este teléfono; inténtalo de nuevo con conexión." };
      ITT.subirAhora && ITT.subirAhora();
      await esperarMs(500);
    }
    throw { mensaje: "No se pudo subir el croquis al servidor. Revisa la señal e inténtalo de nuevo." };
  }

  async function correrTrabajo(titulo, iniciar, alTerminar, tituloFinal) {
    const dlg = $("dlg-salida");
    $("dlg-salida-titulo").textContent = titulo;
    const e = $("salida-estado"), barra = $("salida-progreso"), err = $("salida-error"), res = $("salida-resumen");
    err.hidden = true; res.hidden = true; barra.hidden = false; barra.value = 0;
    for (const id of ["salida-compartir", "salida-descargar"]) { $(id).hidden = true; $(id).disabled = true; }
    $("salida-cad").hidden = true;
    $("salida-cancelar").hidden = false;
    limpiar($("salida-paginas"));
    salida = null;
    let tareaId = null;
    $("salida-cancelar").onclick = async () => { if (tareaId) { try { await fetch(urls.tarea.replace("00000000-0000-0000-0000-000000000000", tareaId) + "cancelar/", { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": await ITT.cola.ajuste("csrf", "") } }); } catch (_) { /* se verá al sondear */ } } };
    dlg.showModal();
    try {
      e.textContent = "Guardando el croquis…";
      await sincronizado();
      e.textContent = "Iniciando…";
      const t = await iniciar();
      tareaId = t.tarea;
      let fin = null;
      for (let i = 0; i < 600; i++) {
        const tarea = await docs.pedir(`${urls.tarea.replace("00000000-0000-0000-0000-000000000000", tareaId)}?formato=json`);
        barra.value = Math.round(tarea.fraccion * 100);
        if (tarea.mensaje) e.textContent = tarea.mensaje;
        if (tarea.terminada) { fin = tarea; break; }
        await esperarMs(1000);
      }
      if (!fin) throw { mensaje: "El trabajo tarda más de lo normal. Vuelve a abrir el croquis en unos minutos." };
      if (fin.estado !== "completada") throw { mensaje: fin.estado === "cancelada" ? "Se canceló el trabajo." : (fin.error || "El trabajo falló.") };
      barra.hidden = true; $("salida-cancelar").hidden = true;
      $("dlg-salida-titulo").textContent = tituloFinal || titulo;               // el título deja de decir «en curso» cuando ya terminó
      await alTerminar(fin);
    } catch (x) {
      barra.hidden = true; $("salida-cancelar").hidden = true;
      $("dlg-salida-titulo").textContent = "No se pudo completar";
      err.hidden = false;
      err.textContent = x.mensaje || `No se pudo completar: ${x.message || x}`;
    }
  }

  async function vectorizar() {
    const km2 = areaKm2(reg.datos.points);
    if (km2 > (ctx.limite_km2 || 50)) return mensaje(`El área mide ${textoKm2(km2)} km²: la web procesa hasta ${ctx.limite_km2} km².`, "error");
    if (!enLinea()) return mensaje("Sin conexión. Vectoriza el mapa cuando vuelva la señal.", "error");
    if (km2 > 10 && !(await confirmar(`Vas a procesar ${textoKm2(km2)} km² de OpenStreetMap. Puede tardar un par de minutos. ¿Continuar?`, { aceptar: "Vectorizar" }))) return;
    await correrTrabajo("Vectorizando el mapa", () => docs.pedir(docs.url("vectorizar", reg.servidor_id), { metodo: "POST", json: {} }), async () => {
      await refrescarEstado();
      const r = estado.resumen || {};
      const partes = Object.entries(r.conteos || {}).map(([k, n]) => `${ctx.categorias[k] || k}: ${n}`).join(" · ");
      const sinEdificios = !(r.conteos || {}).building ? " OpenStreetMap no devolvió huellas de edificios en esta área; la aplicación no las inventa." : "";
      $("salida-resumen").hidden = false;
      $("salida-resumen").textContent = r.total ? `Se obtuvieron ${r.total} elementos recortados al polígono. ${partes}.${sinEdificios}` : "OpenStreetMap no devolvió geometría dentro del área seleccionada.";
      $("salida-estado").textContent = "Listo";
      ajustar();
    }, "Mapa vectorizado");
  }

  async function exportar(formato) {
    if (!enLinea()) return mensaje("Sin conexión. Exporta cuando vuelva la señal; el croquis ya está guardado.", "error");
    await correrTrabajo(formato === "pdf" ? "Generando el PDF" : "Generando el DXF", () => docs.pedir(docs.url("exportar", reg.servidor_id), { metodo: "POST", json: { formato } }), async () => {
      const r = await docs.pedir(`${docs.url("descargar", reg.servidor_id)}?ver=1`, { crudo: true });
      const blob = await r.blob();
      const disp = r.headers.get("Content-Disposition") || "";
      let nombre = `CROQUIS DE UBICACION.${formato}`;
      const m = /filename\*=utf-8''([^;]+)/i.exec(disp) || /filename="([^"]+)"/i.exec(disp);
      if (m) { try { nombre = decodeURIComponent(m[1]); } catch (_) { nombre = m[1]; } }
      salida = { blob, nombre };
      $("salida-estado").textContent = nombre;
      for (const id of ["salida-compartir", "salida-descargar"]) { $(id).hidden = false; $(id).disabled = false; }
      if (formato === "pdf") {
        try { await ITT.vistaPdf.dibujar(blob, $("salida-paginas"), { pdfjs: raiz.dataset.pdfjs, worker: raiz.dataset.pdfjsWorker }); }
        catch (e) { $("salida-estado").textContent = `${nombre} · no se puede mostrar aquí, pero puedes compartirlo o descargarlo.`; }
      } else {
        $("salida-resumen").hidden = false;
        $("salida-resumen").textContent = "DXF R2010 en metros, proyectado a WGS84 / UTM.";
        $("salida-cad").hidden = !puedeCad;
      }
    }, formato === "pdf" ? "PDF del croquis" : "DXF del croquis");
  }
  // «Abrir en CAD»: el DXF recién generado entra a la biblioteca de planos y se abre en el editor (necesita conexión y el rol de CAD).
  let puedeCad = false;
  document.addEventListener("itt:sesion", (ev) => { puedeCad = !!(ev.detail && ev.detail.activa && ev.detail.cad); });
  async function abrirEnCad() {
    if (!enLinea()) return mensaje("CAD necesita conexión. Ábrelo cuando vuelva la señal; el croquis ya está guardado.", "error");
    const b = $("salida-cad");
    b.setAttribute("aria-busy", "true"); b.disabled = true;
    try {
      const r = await docs.pedir(docs.url("cad", reg.servidor_id), { metodo: "POST" });
      location.assign(r.url);
    } catch (e) { mensaje(e.mensaje || "No se pudo abrir en CAD. Inténtalo de nuevo.", "error"); b.removeAttribute("aria-busy"); b.disabled = false; }
  }
  async function compartirSalida() {
    if (!salida) return;
    const tipo = salida.nombre.endsWith(".pdf") ? "application/pdf" : salida.nombre.endsWith(".dxf") ? "image/vnd.dxf" : "application/json";
    document.documentElement.dataset.cqCompartido = await ITT.compartirArchivo(salida.blob, salida.nombre, { tipo, titulo: salida.nombre });
  }
  function descargarSalida() {
    if (!salida) return;
    const a = h("a", { href: URL.createObjectURL(salida.blob), download: salida.nombre, rel: "noopener" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }

  /** Proyecto para el módulo local: el SketchData del escritorio (sin vectores desde la web; el módulo local los vectoriza). */
  async function exportarProyecto() {
    $("dlg-mas").open && $("dlg-mas").close();
    await guardarYa();
    const d = Object.assign({}, reg.datos, { features: [], vectorized_at: "", version: 5 });
    const proyecto = (reg.datos.project || "").replace(/[<>:"/\\|?*\x00-\x1f]/g, " ").split(/\s+/).filter(Boolean).join(" ").slice(0, 80);
    const nombre = `EDITABLE_CROQUIS DE UBICACION${proyecto ? ` - ${proyecto}` : ""}.json`;
    salida = { blob: new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }), nombre };
    const r = await ITT.compartirArchivo(salida.blob, nombre, { tipo: "application/json", titulo: nombre });
    document.documentElement.dataset.cqCompartido = r;
    mensaje(`Proyecto «${nombre}» ${r === "descargado" ? "descargado" : "compartido"}. Ábrelo en el módulo local.`, "info");
  }

  // -- Enrutador ----------------------------------------------------------------------------------------------------------------------------
  function mostrar(vista) {
    $("cq-vista-lista").hidden = vista !== "lista";
    $("cq-vista-editor").hidden = vista !== "editor";
    raiz.dataset.vista = vista;
  }
  async function ruta() {
    mensaje("");
    if (reg && temporizador) await guardarYa();
    const s = /^#\/s\/([0-9a-f-]{36})$/.exec(location.hash || "");          // desde la búsqueda: id del servidor
    if (s) { const local = await docs.localDe(s[1]); history.replaceState(null, "", local ? `#/q/${local}` : "#/"); return ruta(); }
    if (location.hash === "#/nuevo") { history.replaceState(null, "", "#/"); if (ctx) return nuevo(); }            // acción «Nuevo …» de la paleta
    const m = /^#\/q\/([0-9a-f-]{36})$/.exec(location.hash || "");
    if (m) {
      const r = await ITT.cola.borrador(m[1]);
      if (!r || r.borrada || r.tipo !== "croquis") { location.hash = "#/"; return; }
      reg = r;
      estado = { vectores: "ninguno", resumen: {}, datos: null };
      mostrar("editor");
      document.title = `${r.datos.title || "Croquis"} · Croquis`;
      const sel = $("q-map_layer"); limpiar(sel);
      for (const c of ctx.capas_mapa) sel.append(h("option", { value: c.nombre }, c.nombre));
      const sc = $("q-contour_interval"); limpiar(sc);
      for (const c of ctx.contornos) sc.append(h("option", { value: c }, c));
      pintarCampos();
      if (await iniciarMapa()) {
        mapa.resize();
        aplicarCapaBase();
        aDibujo();
        ponerModo(r.datos.points.length >= 3 ? "select" : "static");
        if (r.datos.points.length) ajustar();
      } else $("cq-mapa").hidden = true;
      refrescarEstado();
      return;
    }
    reg = null;
    mostrar("lista");
    await pintarLista();
  }

  function enlazar() {
    $("cq-nuevo").addEventListener("click", nuevo);
    $("cq-nuevo-vacio").addEventListener("click", nuevo);
    for (const e of raiz.querySelectorAll("[data-dato]")) e.addEventListener(e.tagName === "SELECT" ? "change" : "input", alEscribir);
    for (const b of document.querySelectorAll(".cq-modos [data-modo]")) b.addEventListener("click", () => ponerModo(b.dataset.modo));
    $("cq-ajustar").addEventListener("click", ajustar);
    $("cq-ubicacion").addEventListener("click", miUbicacion);
    $("cq-completa").addEventListener("click", () => {
      const activa = raiz.dataset.completo !== "1";
      raiz.dataset.completo = activa ? "1" : "";
      $("cq-completa").setAttribute("aria-pressed", String(activa));
      const bc = $("cq-completa"), rotulo = activa ? "Salir de pantalla completa" : "Pantalla completa";
      limpiar(bc); bc.append(icono(activa ? "minimize" : "maximize"), h("span", {}, rotulo)); bc.setAttribute("aria-label", rotulo); bc.title = rotulo;
      document.body.classList.toggle("cq-sin-desplazar", activa);
      if (mapa) setTimeout(() => mapa.resize(), 0);
    });
    $("cq-importar").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importar(f); });
    $("cq-agregar-vertice").addEventListener("click", agregarVertice);
    $("cq-limpiar").addEventListener("click", borrarArea);
    $("cq-capas-todas").addEventListener("click", () => todasLasCapas(true));
    $("cq-capas-ninguna").addEventListener("click", () => todasLasCapas(false));
    $("cq-vectorizar").addEventListener("click", vectorizar);
    $("mas-vectorizar").addEventListener("click", () => { $("dlg-mas").close(); vectorizar(); });
    $("cq-pdf").addEventListener("click", () => exportar("pdf"));
    $("cq-dxf").addEventListener("click", () => exportar("dxf"));
    $("cq-modulo").addEventListener("click", exportarProyecto);
    $("mas-modulo").addEventListener("click", exportarProyecto);
    $("cq-mas").addEventListener("click", () => $("dlg-mas").showModal());
    $("mas-borrar").addEventListener("click", async () => { $("dlg-mas").close(); const b = reg; await borrarRegistro(b); if (!(await ITT.cola.borrador(b.id))) { reg = null; location.hash = "#/"; } });
    $("salida-compartir").addEventListener("click", compartirSalida);
    $("salida-descargar").addEventListener("click", descargarSalida);
    $("salida-cad").addEventListener("click", abrirEnCad);
    for (const b of document.querySelectorAll("[data-cerrar]")) b.addEventListener("click", () => b.closest("dialog").close());
    addEventListener("hashchange", ruta);
    const red = () => {
      const e = $("cq-red");
      limpiar(e);
      if (!enLinea()) e.append(icono("cloud-off"), h("span", {}, "Sin conexión. Puedes seguir editando; el mapa y la exportación vuelven con la señal."));
      e.hidden = enLinea();
      e.dataset.estado = enLinea() ? "en_linea" : "sin_conexion";
      estadoGuardado();
    };
    addEventListener("online", red); addEventListener("offline", red); red();
    document.addEventListener("itt:doc", async (e) => {
      const d = e.detail || {};
      if (d.tipo !== "croquis") return;
      if (d.tipo_evento === "copia" && reg && reg.id === d.id) {
        await confirmar("Este croquis cambió en otro dispositivo mientras lo editabas. Tus cambios están a salvo en una copia nueva.", { aceptar: "Ver la copia", soloAviso: true });
        location.hash = `#/q/${d.copia}`;
      } else if (d.tipo_evento === "sincronizado") {
        if (raiz.dataset.vista === "lista") pintarLista();
        else if (reg) { const r = await ITT.cola.borrador(reg.id); if (r) { reg.sucio = r.sucio; reg.servidor_id = r.servidor_id; reg.revision_base = r.revision_base; estadoGuardado(); if (!r.sucio) refrescarEstado(); } }
      }
    });
  }

  async function asegurarCuenta() {
    if ((await ITT.cola.ajuste("cuenta", null)) != null || !enLinea()) return;
    try {
      const r = await fetch("/sesion/", { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json" } });
      if (r.ok) { const d = await r.json(); await ITT.cola.guardarAjuste("cuenta", d.cuenta); await ITT.cola.guardarAjuste("csrf", d.csrf); }
    } catch (_) { /* sin red */ }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    docs.iniciar(urls);
    enlazar();
    await asegurarCuenta();
    try { ctx = await contexto(); } catch (e) { ctx = null; mensaje(e.clase === "sesion" ? "Tu sesión terminó. Vuelve a iniciar sesión." : "Sin conexión. Abre Croquis una vez con señal para poder trabajar sin ella.", "error"); }
    if (ctx) await ruta(); else { mostrar("lista"); await pintarLista(); }
    docs.sincronizar();
    document.documentElement.dataset.cqLista = "1";
  });
  ITT.croquis = { areaKm2, firma, get mapa() { return mapa; }, get dibujo() { return dibujo; } };
})();

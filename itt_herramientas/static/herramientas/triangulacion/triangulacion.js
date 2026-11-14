/* Mesa de trabajo de triangulación: visor 2D en <canvas> con zoom y paneo (ratón, rueda y pellizco), superficie rasterizada por el servidor
 * (un PNG con TODOS los triángulos de la ventana) y capa vectorial ligera (puntos de origen o flechas de escurrimiento), pestañas de
 * cálculo, tablas de rangos editables y Compartir del DXF generado. Sin estilos en línea (CSP): todo por CSSOM.
 */
(function () {
  "use strict";
  const raiz = document.getElementById("tri");
  if (!raiz) return;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const lienzo = $("#tri-lienzo");
  const ctx2 = lienzo.getContext("2d");
  const mensaje = $("#tri-mensaje");
  const datos = raiz.dataset;
  const limites = datos.limites ? datos.limites.split(",").map(Number) : null;
  let modo = datos.modo || "tin";
  let vista = null;                         // {x0,y0,x1,y1} en coordenadas de dibujo
  let raster = null;                        // {bitmap, vista} lo último que llegó del servidor
  let vectores = null;                      // {vista, datos}
  let ficha = 0;
  let pendiente = null;
  const punteros = new Map();

  // -- Muestras de color y pestañas ------------------------------------------------------------------------------------------------
  for (const m of $$("[data-color]")) m.style.background = m.dataset.color;
  for (const b of $$("[data-pestana]")) b.addEventListener("click", () => {
    for (const x of $$("[data-pestana]")) x.setAttribute("aria-selected", String(x === b));
    for (const f of $$("[data-panel]")) f.hidden = f.dataset.panel !== b.dataset.pestana;
    const hacia = { superficie: "tin", pendientes: "pendientes", escurrimiento: "flujo" }[b.dataset.pestana];
    if (hacia) ponerModo(hacia);
  });
  const selModo = $("[data-itt=modo-superficie]");
  const alternarOrigen = () => { for (const d of $$("[data-modo-superficie]")) d.hidden = d.dataset.modoSuperficie !== selModo.value; };
  if (selModo) { selModo.addEventListener("change", alternarOrigen); alternarOrigen(); }

  // -- Rangos editables ---------------------------------------------------------------------------------------------------------------------
  for (const caja of $$(".tri-rangos")) caja.addEventListener("click", (e) => {
    if (e.target.dataset.itt !== "quitar-rango" || caja.children.length <= 1) return;
    const fila = e.target.closest(".tri-rango");
    const eraUltimo = fila === caja.lastElementChild;
    fila.remove();
    if (eraUltimo) $$("input", caja.lastElementChild)[2].value = "";               // el último rango siempre queda abierto («Hasta» vacío)
  });
  for (const b of $$("[data-itt=agregar-rango]")) b.addEventListener("click", () => {
    const caja = $(`.tri-rangos[data-rangos=${b.dataset.tipo}]`);
    const ultimo = caja.lastElementChild;
    const copia = ultimo.cloneNode(true);
    const valores = $$("input", ultimo).map((i) => i.value);
    const campos = $$("input", copia);
    campos.forEach((i, k) => { i.value = valores[k]; });
    const nombre = campos[0], desde = campos[1], hasta = campos[2];
    desde.value = hasta.value !== "" ? hasta.value : desde.value;                 // el nuevo rango empieza donde terminaba el último
    const nuevoDesde = Number(String(desde.value).replace(",", "."));
    if (hasta.value === "" && Number.isFinite(nuevoDesde)) { $$("input", ultimo)[2].value = String(nuevoDesde + 10); desde.value = String(nuevoDesde + 10); }
    hasta.value = "";
    nombre.value = "Nuevo rango";
    caja.append(copia);
    nombre.focus();
  });

  // -- Visor -------------------------------------------------------------------------------------------------------------------------------
  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
  function medir() {
    const caja = lienzo.parentElement;
    const w = Math.max(200, Math.round(caja.clientWidth * dpr())), h = Math.max(200, Math.round(caja.clientHeight * dpr()));
    if (lienzo.width !== w || lienzo.height !== h) { lienzo.width = w; lienzo.height = h; }
  }
  function ajustar() {
    if (!limites) { vista = null; return; }
    medir();
    let [x0, y0, x1, y1] = limites;
    const ancho = Math.max(x1 - x0, 1e-6), alto = Math.max(y1 - y0, 1e-6);
    x0 -= ancho * 0.06; x1 += ancho * 0.06; y0 -= alto * 0.06; y1 += alto * 0.06;
    const razonLienzo = lienzo.width / lienzo.height, razonDatos = (x1 - x0) / (y1 - y0);
    if (razonDatos > razonLienzo) { const extra = (x1 - x0) / razonLienzo - (y1 - y0); y0 -= extra / 2; y1 += extra / 2; }
    else { const extra = (y1 - y0) * razonLienzo - (x1 - x0); x0 -= extra / 2; x1 += extra / 2; }
    vista = { x0, y0, x1, y1 };
  }
  const aPantalla = (x, y, v) => [(x - v.x0) / (v.x1 - v.x0) * lienzo.width, lienzo.height - (y - v.y0) / (v.y1 - v.y0) * lienzo.height];

  function hayDatos() { return modo === "tin" ? datos.superficie === "1" : modo === "pendientes" ? datos.pendientes === "1" : datos.flujo === "1"; }
  function ponerModo(nuevo) {
    modo = nuevo;
    for (const b of $$(".tri-modos [data-modo]")) b.setAttribute("aria-pressed", String(b.dataset.modo === modo));
    $("#tri-leyenda-pendientes").hidden = modo !== "pendientes" || datos.pendientes !== "1";
    $("#tri-leyenda-flujo").hidden = modo !== "flujo" || datos.flujo !== "1";
    raster = null; vectores = null;
    mensaje.textContent = hayDatos() ? "" : (datos.superficie !== "1" ? "Calcula la superficie para ver el resultado." : modo === "pendientes" ? "Calcula las pendientes para verlas aquí." : "Calcula el escurrimiento para verlo aquí.");
    dibujar();
    pedir(true);
  }
  for (const b of $$(".tri-modos [data-modo]")) b.addEventListener("click", () => ponerModo(b.dataset.modo));
  $("[data-itt=ajustar]").addEventListener("click", () => { ajustar(); dibujar(); pedir(true); });

  function dibujar() {
    medir();
    const c = ctx2;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.fillStyle = "#132331";
    c.fillRect(0, 0, lienzo.width, lienzo.height);
    if (!vista || !hayDatos()) return;
    if (raster) {                                                                 // lo último que llegó, movido a la ventana actual mientras llega lo nuevo
      const v = raster.vista;
      const [ax, ay] = aPantalla(v.x0, v.y1, vista), [bx, by] = aPantalla(v.x1, v.y0, vista);
      c.drawImage(raster.bitmap, ax, ay, bx - ax, by - ay);
    }
    if (vectores && modo === "tin" && vectores.datos.puntos) {
      c.fillStyle = "#FF6B55";
      for (const [x, y] of vectores.datos.puntos) { const [px, py] = aPantalla(x, y, vista); c.fillRect(px - 1.2, py - 1.2, 2.4, 2.4); }
    } else if (vectores && modo === "flujo" && vectores.datos.flechas) {
      const rangos = vectores.datos.rangos;
      for (const f of vectores.datos.flechas) {
        const r = rangos[f[8]] || rangos[rangos.length - 1];
        const [sx, sy] = aPantalla(f[0], f[1], vista), [tx, ty] = aPantalla(f[2], f[3], vista), [lx, ly] = aPantalla(f[4], f[5], vista), [rx, ry] = aPantalla(f[6], f[7], vista);
        c.strokeStyle = c.fillStyle = r.color;
        c.lineWidth = Math.max(1, Math.min(5, Math.round(r.grosor * 7))) * dpr();
        c.beginPath(); c.moveTo(sx, sy); c.lineTo(tx, ty); c.stroke();
        c.beginPath(); c.moveTo(tx, ty); c.lineTo(lx, ly); c.lineTo(rx, ry); c.closePath(); c.fill();
      }
    }
  }

  function pedir(ya) {
    clearTimeout(pendiente);
    if (!vista || !hayDatos()) return;
    pendiente = setTimeout(cargar, ya ? 0 : 140);
  }
  async function cargar() {
    const mi = ++ficha;
    const v = { ...vista };
    const q = `x0=${v.x0}&y0=${v.y0}&x1=${v.x1}&y1=${v.y1}&w=${lienzo.width}&h=${lienzo.height}&modo=${modo}`;
    document.documentElement.dataset.triCargando = "1";
    try {
      const r = await fetch(`${datos.vista}?${q}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(`Error ${r.status}`);
      const bitmap = await createImageBitmap(await r.blob());
      if (mi !== ficha) return;
      raster = { bitmap, vista: v };
      if (modo === "tin" || modo === "flujo") {
        const rv = await fetch(`${datos.vector}?${q}`, { credentials: "same-origin" });
        if (mi !== ficha) return;
        vectores = rv.ok ? { vista: v, datos: await rv.json() } : null;
      } else vectores = null;
      mensaje.textContent = "";
      dibujar();
      document.documentElement.dataset.triVista = String((Number(document.documentElement.dataset.triVista) || 0) + 1);
    } catch (e) {
      if (mi === ficha) mensaje.textContent = "No se pudo cargar la vista previa. Revisa tu conexión.";
    } finally { if (mi === ficha) document.documentElement.dataset.triCargando = ""; }
  }

  // -- Gestos ------------------------------------------------------------------------------------------------------------------------------
  function acercar(factor, px, py) {                      // (px, py) en píxeles del lienzo: ese punto de los datos no se mueve
    if (!vista) return;
    const dx = vista.x0 + (px / lienzo.width) * (vista.x1 - vista.x0), dy = vista.y1 - (py / lienzo.height) * (vista.y1 - vista.y0);
    vista = { x0: dx - (dx - vista.x0) / factor, x1: dx + (vista.x1 - dx) / factor, y0: dy - (dy - vista.y0) / factor, y1: dy + (vista.y1 - dy) / factor };
    dibujar(); pedir(false);
  }
  function mover(dpx, dpy) {
    const ax = (vista.x1 - vista.x0) / lienzo.width, ay = (vista.y1 - vista.y0) / lienzo.height;
    vista = { x0: vista.x0 - dpx * ax, x1: vista.x1 - dpx * ax, y0: vista.y0 + dpy * ay, y1: vista.y1 + dpy * ay };
    dibujar(); pedir(false);
  }
  const local = (e) => { const r = lienzo.getBoundingClientRect(); return [(e.clientX - r.left) * lienzo.width / r.width, (e.clientY - r.top) * lienzo.height / r.height]; };
  lienzo.addEventListener("pointerdown", (e) => { lienzo.setPointerCapture(e.pointerId); punteros.set(e.pointerId, local(e)); });
  lienzo.addEventListener("pointermove", (e) => {
    if (!punteros.has(e.pointerId) || !vista) return;
    const antes = [...punteros.values()], previo = punteros.get(e.pointerId), ahora = local(e);
    punteros.set(e.pointerId, ahora);
    if (punteros.size === 1) mover(ahora[0] - previo[0], ahora[1] - previo[1]);
    else if (punteros.size === 2) {
      const despues = [...punteros.values()];
      const d0 = Math.hypot(antes[0][0] - antes[1][0], antes[0][1] - antes[1][1]) || 1, d1 = Math.hypot(despues[0][0] - despues[1][0], despues[0][1] - despues[1][1]);
      acercar(d1 / d0, (despues[0][0] + despues[1][0]) / 2, (despues[0][1] + despues[1][1]) / 2);
    }
  });
  for (const ev of ["pointerup", "pointercancel"]) lienzo.addEventListener(ev, (e) => { punteros.delete(e.pointerId); });
  lienzo.addEventListener("wheel", (e) => { e.preventDefault(); const [x, y] = local(e); acercar(e.deltaY < 0 ? 1.25 : 0.8, x, y); }, { passive: false });
  addEventListener("resize", () => { if (vista) { dibujar(); pedir(false); } });

  // -- Compartir el DXF -------------------------------------------------------------------------------------------------------------------------
  const salida = $("#tri-salida");
  if (salida) $("[data-itt=compartir]", salida).addEventListener("click", async () => {
    const r = await fetch(salida.dataset.url, { credentials: "same-origin" });
    const blob = await r.blob();
    document.documentElement.dataset.triCompartido = await ITT.compartirArchivo(blob, salida.dataset.nombre, { tipo: "image/vnd.dxf", titulo: salida.dataset.nombre });
  });

  // -- Inicio -----------------------------------------------------------------------------------------------------------------------------------
  ajustar();
  const pestanaInicial = { tin: "superficie", pendientes: "pendientes", flujo: "escurrimiento" }[modo];
  if (datos.superficie === "1") $(`[data-pestana=${pestanaInicial}]`).click(); else ponerModo("tin");
  document.documentElement.dataset.triLista = "1";
})();

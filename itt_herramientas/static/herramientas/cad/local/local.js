/* «Carpeta del proyecto en esta computadora» y «Referencias externas» de la página de un plano CAD.
 *
 * El plano (DWG/DXF) vive en la biblioteca; sus imágenes (IMAGE + IMAGEDEF) y referencias a otros dibujos (xref) guardan rutas, como en
 * AutoCAD. Esta página resuelve esas rutas contra una carpeta de la computadora y le da al editor lo que la vista necesita:
 *   - imágenes: decodificadas en trabajadores, a la resolución de la pantalla (una vista completa reducida y, al acercarse, solo la parte
 *     visible), por `ocs_image_supply` del editor; las ortofotos enormes se leen por mosaicos y vistas generales, y si el archivo no las
 *     tiene se arma una pirámide local en el almacenamiento privado del navegador (OPFS), una vez, en segundo plano;
 *   - referencias a dibujos: los bytes del DWG/DXF por `ocs_xref_supply`; se muestran como en AutoCAD (solo lectura).
 * NADA de la carpeta se sube: ni píxeles, ni nombres (fuera de lo que el plano ya trae), ni miniaturas, ni métricas. El servidor guarda
 * solo el DWG, con las rutas tal como la persona las tenía.
 *
 * Partes: carpeta.js (vincular, índice, reglas para resolver), tiff.js y trabajador.js (leer y decodificar), este archivo (la página).
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let config;
  try { config = JSON.parse($("cad-config").textContent); } catch (_) { return; }
  const cfg = config.local;
  if (!cfg || !$("cad-editor")) return;
  const CLAVE = `${cfg.cuenta}:${config.id}`;
  const C = window.TVCarpeta;

  const e = {
    raices: [],           // [{handle?, archivos?, nombre, sesion?}] con permiso
    guardadas: [],        // las de IndexedDB (con o sin permiso)
    indice: null,
    refs: { images: [], xrefs: [] },
    res: new Map(),       // ruta del plano → {estado, entrada, coincide, candidatos, archivo, info, error}
    sum: new Map(),       // ruta → {base: [w, h], detalle: {rect, w, h}|null, pidiendo}
    piramides: new Map(), // ruta → {progreso, id, detenida}
    xrefsDadas: new Set(),
    revCamara: -1,
    listo: false,
    avisoCrs: false,
    errores: [],
  };

  // ── Editor ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  function editor() { try { return $("cad-editor").contentWindow.wasmBindings || null; } catch (_) { return null; } }
  const pedir = (p, o) => window.TVCad.pedir(p, o);

  // ── Trabajadores ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
  const pool = (() => {
    const n = Math.max(1, Math.min(3, Math.floor((navigator.hardwareConcurrency || 4) / 3)));
    const hilos = [];
    let sig = 0;
    const pendientes = new Map();
    const crear = () => {
      const w = new Worker(cfg.trabajador);
      const h = { w, ocupados: 0 };
      w.onmessage = (ev) => {
        const m = ev.data;
        const p = pendientes.get(m.id);
        if (!p) return;
        if (m.progreso !== undefined && m.ok === undefined) { if (p.progreso) p.progreso(m); return; }
        pendientes.delete(m.id);
        h.ocupados--;
        if (m.ok) p.ok(m); else p.mal(new Error(m.error || "error"));
      };
      w.onerror = (ev) => console.error("CAD: trabajador de imágenes", ev.message);
      hilos.push(h);
      enviar(h, { tipo: "iniciar", tiff: cfg.tiff });
      return h;
    };
    function enviar(h, msg, transferir, progreso) {
      const id = ++sig;
      h.ocupados++;
      return { id, promesa: new Promise((ok, mal) => { pendientes.set(id, { ok, mal, progreso }); h.w.postMessage({ ...msg, id }, transferir || []); }) };
    }
    return {
      pedir(msg, { progreso, hilo } = {}) {
        if (!hilos.length) for (let i = 0; i < n; i++) crear();
        const h = hilo !== undefined ? hilos[hilo % hilos.length] : hilos.reduce((a, b) => (b.ocupados < a.ocupados ? b : a));
        return enviar(h, msg, null, progreso);
      },
      cancelar(id) { for (const h of hilos) h.w.postMessage({ tipo: "cancelar", objetivo: id, id: -1 }); },
      libres() { return hilos.length ? hilos.reduce((s, h) => s + Math.max(0, 2 - h.ocupados), 0) : 2 * n; },
      cerrar() { for (const h of hilos) h.w.terminate(); hilos.length = 0; },
    };
  })();

  // ── Carpeta ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  async function cargarGuardadas() {
    if (!C.soportaRecordar()) return;
    e.guardadas = await C.guardadas(CLAVE);
    if (!e.guardadas.length) return;
    const p = await C.permisos(e.guardadas);
    if (p.every((x) => x === "granted")) await usar(e.guardadas);
    else pintarCarpeta();
  }

  async function usar(raices) {
    e.raices = raices;
    pintarCarpeta("indexando");
    const { indice } = await C.indexar(raices, (n) => pintarCarpeta("indexando", n));
    e.indice = indice;
    e.res.clear();
    pintarCarpeta();
    await refrescar();
  }

  async function vincular({ agregar = false } = {}) {
    if (C.soportaRecordar()) {
      try { e.guardadas = await C.vincular(CLAVE, { agregar }); } catch (err) { if (err && err.name === "AbortError") return; throw err; }
      await usar(e.guardadas);
    } else {
      $("cad-local-entrada").click();
    }
  }

  async function permitir() {
    if (await C.permitir(e.guardadas)) await usar(e.guardadas);
    else pintarCarpeta();
  }

  async function desvincular() {
    if (C.soportaRecordar()) await C.desvincular(CLAVE);
    e.guardadas = []; e.raices = []; e.indice = null; e.res.clear(); e.sum.clear(); e.xrefsDadas.clear();
    for (const [, p] of e.piramides) if (p.id) pool.cancelar(p.id);
    e.piramides.clear();
    const w = editor();
    if (w && w.ocs_local_reset) w.ocs_local_reset();
    pintarCarpeta();
    pintarPanel();
  }

  // ── Referencias del plano ─────────────────────────────────────────────────────────────────────────────────────────────────────
  async function refrescar() {
    if (!window.TVCad.estaListo()) return;
    let r;
    try { r = await pedir({ op: "references" }); } catch (_) { return; }
    e.refs = r;
    const rutas = new Set();
    for (const im of r.images) rutas.add(im.path);
    for (const x of r.xrefs) rutas.add(x.path);
    if (e.indice) {
      for (const ruta of rutas) {
        if (!ruta || e.res.has(ruta)) continue;
        e.res.set(ruta, C.resolver(e.indice, ruta));
      }
    }
    pintarPanel();
    pintarAviso();
    await darXrefs();
    planear(true);
  }

  async function archivoDe(ruta) {
    const r = e.res.get(ruta);
    if (!r || !r.entrada) return null;
    if (!r.archivo) r.archivo = await C.archivoDe(r.entrada);
    return r.archivo;
  }

  async function infoDe(ruta) {
    const r = e.res.get(ruta);
    if (!r || !r.entrada) return null;
    if (r.info || r.error) return r.info;
    if (!r.pidiendoInfo) {
      r.pidiendoInfo = (async () => {
        try {
          const archivo = await archivoDe(ruta);
          r.info = await pool.pedir({ tipo: "info", archivo }).promesa;
          if (r.info.motivo) r.error = r.info.motivo;
        } catch (err) { r.error = String(err.message || err); }
        pintarPanel();
        return r.info;
      })();
    }
    return r.pidiendoInfo;
  }

  async function darXrefs() {
    const w = editor();
    if (!w || !w.ocs_xref_supply) return;
    let dio = false;
    for (const x of e.refs.xrefs) {
      if (["loaded", "recovered"].includes(x.status) || e.xrefsDadas.has(x.path)) continue;
      const archivo = await archivoDe(x.path);
      if (!archivo) continue;
      e.xrefsDadas.add(x.path);
      w.ocs_xref_supply(x.path, new Uint8Array(await archivo.arrayBuffer()));
      dio = true;
    }
    // Las anidadas aparecen al cargar su padre: otra vuelta (hasta 4 niveles).
    if (dio && (e.vueltasXref = (e.vueltasXref || 0) + 1) <= 4) setTimeout(refrescar, 400);
  }

  // ── Imágenes: qué pide la vista y qué se le da al editor ─────────────────────────────────────────────────────────────────────
  const BASE_MAX = 1024, BASE_MIN = 64, DETALLE_MAX = 2560;
  const potencia = (v) => 2 ** Math.ceil(Math.log2(Math.max(1, v)));
  let ciclo = null, enVuelo = 0, enPaso = false, otraVez = false;

  function planear(pronto) {
    if (enPaso) { otraVez = true; return; }
    if (ciclo) { if (!pronto) return; clearTimeout(ciclo); }
    ciclo = setTimeout(async () => {
      ciclo = null;
      enPaso = true;
      try { await paso(); } finally {
        enPaso = false;
        if (otraVez) { otraVez = false; planear(true); }
      }
    }, pronto ? 0 : 140);
  }

  async function paso() {
    // Sin nada resuelto (sin carpeta y sin imágenes insertadas desde esta computadora) no hay nada que pedir.
    const algo = e.indice || [...e.res.values()].some((x) => x.entrada);
    if (!window.TVCad.estaListo() || !algo || document.hidden) { if (algo) setTimeout(() => planear(), 500); return; }
    let v;
    try { v = await pedir({ op: "image_views" }, { tiempoMs: 5000 }); } catch (_) { setTimeout(() => planear(), 500); return; }
    const escala = (v.screen_scale || 1) * (window.devicePixelRatio || 1);
    const [vw, vh] = v.viewport_size || [1, 1];
    const pedidos = [];
    for (const f of v.views) {
      const r = e.res.get(f.path);
      if (!r || !r.entrada || r.error) continue;
      const info = r.info;
      if (!info) { infoDe(f.path).then(() => planear(true)); continue; }
      if (info.motivo) continue;
      const s = e.sum.get(f.path) || { base: null, detalle: null };
      e.sum.set(f.path, s);
      const [ew, eh] = f.size;
      const kx = info.ancho / ew, ky = info.alto / eh;            // píxeles del archivo por píxel de la entidad
      const d = (f.density * escala) / Math.max(kx, ky);           // px del dispositivo por px del archivo
      const lado = Math.max(info.ancho, info.alto);
      // Vista completa: el tamaño con que se ve la imagen entera, en potencias de 2, entre 64 y 1024.
      const quiere = Math.min(lado, Math.max(BASE_MIN, Math.min(BASE_MAX, potencia(lado * d))));
      const tiene = s.base ? Math.max(s.base[0], s.base[1]) : 0;
      if (!s.pidiendo && quiere > tiene && !(e.piramides.get(f.path) || {}).corriendo) {
        pedidos.push({ prioridad: f.screen_area + 1e9, hacer: () => base(f.path, info, quiere) });
      }
      if (info.piramide === "falta" && !e.piramides.has(f.path)) piramide(f.path);
      // Detalle: la parte visible a la resolución de la pantalla, cuando la vista completa no alcanza.
      if (lado * d > Math.max(tiene, quiere) * 1.25 && lado > Math.max(tiene, quiere)) {
        const [x0, y0, x1, y1] = f.rect;
        const mx = (x1 - x0) * 0.15, my = (y1 - y0) * 0.15;      // margen para pequeños movimientos
        const rect = [Math.max(0, (x0 - mx) * kx), Math.max(0, (y0 - my) * ky), Math.min(info.ancho, (x1 + mx) * kx), Math.min(info.alto, (y1 + my) * ky)];
        let w = Math.round((rect[2] - rect[0]) * d), h = Math.round((rect[3] - rect[1]) * d);
        const lim = Math.min(DETALLE_MAX, Math.ceil(Math.max(vw, vh) * escala * 1.3));
        // Nunca más píxeles que los del archivo, ni más que la pantalla.
        const k = Math.min(1, lim / Math.max(w, h), (rect[2] - rect[0]) / Math.max(1, w));
        w = Math.max(1, Math.round(w * k)); h = Math.max(1, Math.round(h * k));
        const dt = s.detalle;
        const cubre = dt && dt.rect[0] <= x0 * kx && dt.rect[1] <= y0 * ky && dt.rect[2] >= x1 * kx && dt.rect[3] >= y1 * ky
          && (dt.w / (dt.rect[2] - dt.rect[0])) >= 0.8 * (w / (rect[2] - rect[0]));
        if (!cubre && !s.pidiendoDetalle && !(s.esperarHasta > performance.now())) pedidos.push({ prioridad: f.screen_area, hacer: () => detalle(f.path, info, rect, w, h) });
      } else if (s.detalle && !s.pidiendoDetalle) {
        s.detalle = null;
        const w8 = editor(); if (w8 && w8.ocs_image_drop_detail) w8.ocs_image_drop_detail(f.path);
      }
    }
    pedidos.sort((a, b) => b.prioridad - a.prioridad);
    for (const p of pedidos) {
      if (pool.libres() <= 0 || enVuelo > 6) break;
      p.hacer();
    }
    e.revCamara = v.camera_revision;
    setTimeout(() => planear(), 140);
  }

  function dar(ruta, info, rect, r) {
    const w = editor();
    if (!w || !w.ocs_image_supply) return false;
    const motivo = w.ocs_image_supply(ruta, info.ancho, info.alto, rect[0], rect[1], rect[2], rect[3], r.ancho, r.alto, r.rgba);
    if (motivo) { console.warn("CAD: el editor no tomó la imagen", ruta, motivo); return false; }
    return true;
  }

  async function base(ruta, info, lado) {
    const s = e.sum.get(ruta);
    s.pidiendo = true; enVuelo++;
    try {
      const k = lado / Math.max(info.ancho, info.alto);
      const ancho = Math.max(1, Math.round(info.ancho * k)), alto = Math.max(1, Math.round(info.alto * k));
      const archivo = await archivoDe(ruta);
      const r = await pool.pedir({ tipo: "region", archivo, rect: [0, 0, info.ancho, info.alto], ancho, alto }).promesa;
      if (r.pendiente) return;
      if (dar(ruta, info, [0, 0, info.ancho, info.alto], r)) { s.base = r.parcial ? [Math.min(r.ancho, 64), Math.min(r.alto, 64)] : [r.ancho, r.alto]; s.detalle = null; }
    } catch (err) {
      const x = e.res.get(ruta); if (x) x.error = String(err.message || err);
      pintarPanel();
    } finally { s.pidiendo = false; enVuelo--; planear(true); }
  }

  async function detalle(ruta, info, rect, ancho, alto) {
    const s = e.sum.get(ruta);
    s.pidiendoDetalle = true; enVuelo++;
    try {
      const archivo = await archivoDe(ruta);
      const r = await pool.pedir({ tipo: "region", archivo, rect, ancho, alto }).promesa;
      // Sin la pirámide todavía: lo más fino que hay, y otra vez en un momento (no en cada paso).
      if (r.pendiente || r.parcial) s.esperarHasta = performance.now() + 1500;
      if (r.pendiente || !s.base) return;
      if (dar(ruta, info, rect, r)) s.detalle = r.parcial ? { rect, w: 1, h: 1 } : { rect, w: r.ancho, h: r.alto };
    } catch (err) {
      console.warn("CAD: detalle", ruta, err);
    } finally { s.pidiendoDetalle = false; enVuelo--; planear(true); }
  }

  async function piramide(ruta) {
    const archivo = await archivoDe(ruta);
    const info = e.res.get(ruta).info;
    const p = { progreso: 0, corriendo: true };
    e.piramides.set(ruta, p);
    pintarPanel();
    const { id, promesa } = pool.pedir({ tipo: "piramide", archivo }, {
      progreso: (m) => {
        p.progreso = m.progreso;
        if (m.base) {
          const r = { rgba: new Uint8Array(m.base.rgba.buffer || m.base.rgba), ancho: m.base.ancho, alto: m.base.alto };
          if (dar(ruta, info, [0, 0, info.ancho, info.alto], r)) {
            const s = e.sum.get(ruta) || {}; s.base = [r.ancho, r.alto]; s.detalle = null; e.sum.set(ruta, s);
          }
        }
        pintarProgreso(ruta);
      },
    });
    p.id = id;
    try {
      const r = await promesa;
      p.corriendo = false;
      if (r.lista) { info.piramide = "lista"; p.progreso = 1; p.segundos = r.segundos; }
      else p.detenida = true;
    } catch (err) {
      p.corriendo = false; p.error = String(err.message || err);
    }
    pintarPanel();
    planear(true);
  }

  function detenerPiramide(ruta) {
    const p = e.piramides.get(ruta);
    if (p && p.id && p.corriendo) pool.cancelar(p.id);
  }

  // ── Insertar imagen georreferenciada ─────────────────────────────────────────────────────────────────────────────────────────
  const MUNDO = (nombre) => {
    const i = nombre.lastIndexOf(".");
    if (i < 0) return [];
    const base = nombre.slice(0, i), ext = nombre.slice(i + 1).toLowerCase();
    const out = [];
    for (const c of [ext[0] + ext[ext.length - 1] + "w", ext + "w", "wld"]) for (const v of [c, c.toUpperCase()]) if (!out.includes(`${base}.${v}`)) out.push(`${base}.${v}`);
    return out;
  };
  const EXT_IMAGEN = /\.(tif|tiff|jpg|jpeg|png|bmp|gif|webp)$/i;
  let insercion = null;

  async function elegirParaInsertar() {
    let archivo, mundo = null, ruta = null, enCarpeta = null;
    if (window.showOpenFilePicker) {
      let hs;
      try {
        hs = await window.showOpenFilePicker({ id: "tv-cad-imagen", multiple: true, types: [{ description: "Imágenes y archivos de mundo",
          accept: { "image/*": [".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"], "text/plain": [".tfw", ".tifw", ".jgw", ".jpgw", ".pgw", ".pngw", ".wld"] } }] });
      } catch (err) { if (err && err.name === "AbortError") return; throw err; }
      const img = hs.find((h) => EXT_IMAGEN.test(h.name));
      if (!img) return aviso("Elige una imagen (TIFF, JPEG, PNG…).");
      archivo = await img.getFile();
      const m = hs.find((h) => h !== img && MUNDO(img.name).some((n) => n.toLowerCase() === h.name.toLowerCase()));
      if (m) mundo = await (await m.getFile()).text();
      for (const r of e.raices) {
        if (!r.handle) continue;
        const camino = await r.handle.resolve(img);
        if (camino) { enCarpeta = r; ruta = camino.join("\\"); break; }
      }
      if (!mundo && enCarpeta) {
        // el archivo de mundo junto a la imagen
        const ents = await C.indexar([enCarpeta]);
        const dir = ruta.split("\\").slice(0, -1).join("/");
        for (const n of MUNDO(img.name)) {
          const x = C.resolver(ents.indice, (dir ? dir + "/" : "") + n);
          if (x.entrada && x.coincide === (dir ? dir.split("/").length + 1 : 1)) { mundo = await (await C.archivoDe(x.entrada)).text(); break; }
        }
      }
      insercion = { archivo, mundo, ruta: ruta || img.name, handle: img, enCarpeta };
    } else {
      $("cad-local-imagen").click();
      return;
    }
    await prepararInsercion();
  }

  async function desdeEntradaImagen(lista) {
    const fs = Array.from(lista || []);
    const archivo = fs.find((f) => EXT_IMAGEN.test(f.name));
    if (!archivo) return aviso("Elige una imagen (TIFF, JPEG, PNG…).");
    const m = fs.find((f) => f !== archivo && MUNDO(archivo.name).some((n) => n.toLowerCase() === f.name.toLowerCase()));
    // Sin acceso a carpetas: si está indexada en la carpeta de la sesión, su ruta relativa; si no, su nombre.
    let ruta = archivo.name;
    if (e.indice) {
      const x = C.resolver(e.indice, archivo.name);
      if (x.estado === "nombre") ruta = x.entrada.ruta.replace(/\//g, "\\");
    }
    insercion = { archivo, mundo: m ? await m.text() : null, ruta, enCarpeta: null };
    await prepararInsercion();
  }

  const fmt = (n, d = 2) => Number(n).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });

  async function prepararInsercion() {
    const x = insercion;
    const hoja = $("cad-local-insertar");
    $("cad-local-ins-nombre").textContent = x.archivo.name;
    $("cad-local-ins-detalle").textContent = "Leyendo la imagen…";
    $("cad-local-ins-ir").disabled = true;
    hoja.showModal ? hoja.showModal() : hoja.setAttribute("open", "");
    try {
      x.info = await pool.pedir({ tipo: "info", archivo: x.archivo }).promesa;
    } catch (err) {
      $("cad-local-ins-detalle").textContent = `No se pudo leer: ${err.message}.`;
      return;
    }
    if (x.info.motivo) { $("cad-local-ins-detalle").textContent = `No se puede mostrar: ${x.info.motivo}.`; return; }
    const tam = `${x.info.ancho.toLocaleString("es-MX")} × ${x.info.alto.toLocaleString("es-MX")} px`;
    let lugar;
    if (x.mundo) {
      const t = x.mundo.split(/\s+/).filter(Boolean).map((v) => Number(v.replace(",", ".")));
      lugar = t.length >= 6 && t.every(Number.isFinite)
        ? `Se coloca con su archivo de mundo: píxel de ${fmt(Math.hypot(t[0], t[1]), 3)} m, esquina superior izquierda en X ${fmt(t[4] - t[0] / 2 - t[2] / 2)}, Y ${fmt(t[5] - t[1] / 2 - t[3] / 2)}.`
        : "El archivo de mundo no tiene seis números: se inserta sin georreferencia.";
      if (!(t.length >= 6 && t.every(Number.isFinite))) x.mundo = null;
    } else if (x.info.geo) {
      lugar = `Se coloca con la georreferencia del GeoTIFF${x.info.geo.epsg ? ` (EPSG:${x.info.geo.epsg})` : ""}.`;
    } else {
      lugar = "Sin archivo de mundo ni GeoTIFF: elige dónde va.";
    }
    $("cad-local-ins-detalle").textContent = `${tam} · ${lugar}`;
    $("cad-local-ins-manual").hidden = !!(x.mundo || x.info.geo);
    if (!x.mundo && !x.info.geo) {
      try {
        const vista = await window.TVCad.vista();
        $("cad-local-ins-x").value = vista.cx.toFixed(3);
        $("cad-local-ins-y").value = vista.cy.toFixed(3);
        $("cad-local-ins-ancho").value = (vista.w * vista.k * 0.5).toPrecision(4);
      } catch (_) { /* sin vista: campos vacíos */ }
    }
    $("cad-local-ins-fuera").hidden = !!x.enCarpeta || !C.soportaRecordar();
    $("cad-local-ins-ir").disabled = false;
  }

  async function insertar() {
    const x = insercion;
    if (!x || !x.info) return;
    const peticion = { op: "image_attach", path: x.ruta, size: [x.info.ancho, x.info.alto], send_to_back: $("cad-local-ins-atras").checked };
    const capa = $("cad-local-ins-capa").value.trim();
    if (capa) peticion.layer = capa;
    if (x.mundo) peticion.world_file = x.mundo;
    else if (x.info.geo) peticion.geotiff = x.info.geo;
    else {
      const n = (id) => Number(String($(id).value).replace(",", "."));
      peticion.insert = [n("cad-local-ins-x"), n("cad-local-ins-y")];
      peticion.width = n("cad-local-ins-ancho");
      peticion.rotation = n("cad-local-ins-rot") || 0;
      if (!peticion.insert.every(Number.isFinite) || !(peticion.width > 0)) return aviso("Revisa el punto de inserción y el ancho.");
    }
    $("cad-local-ins-ir").disabled = true;
    let r;
    try { r = await pedir(peticion); } catch (err) {
      $("cad-local-ins-ir").disabled = false;
      return aviso(`No se insertó la imagen: ${(err.respuesta && err.respuesta.error) || err.message}.`);
    }
    $("cad-local-insertar").close();
    // Una imagen georreferenciada suele caer lejos de lo que se ve: si no está en la vista, se muestra entera.
    try {
      const res = r.result, [w, h] = res.size;
      const c = [0, 1].map((i) => res.insert[i] + (res.u[i] * w + res.v[i] * h) / 2);
      const v = await pedir({ op: "image_views" });
      if (!v.views.some((f) => f.path === x.ruta)) {
        const alto = Math.hypot(res.v[0] * h, res.v[1] * h), ancho = Math.hypot(res.u[0] * w, res.u[1] * w);
        const [vw, vh] = v.viewport_size;
        await pedir({ op: "view_set", center: c, height: 1.1 * Math.max(alto, (ancho * vh) / Math.max(1, vw)) });
      }
    } catch (_) { /* la vista se queda donde estaba */ }
    // Sus píxeles, desde este archivo.
    e.res.set(x.ruta, { estado: x.enCarpeta ? "ruta" : "nombre", entrada: { ruta: x.ruta.replace(/\\/g, "/"), nombre: x.archivo.name, archivo: x.archivo }, archivo: x.archivo, coincide: 1, candidatos: [], info: x.info });
    const w = r.result && r.result.crs_warning;
    if (w && !e.avisoCrs) {
      e.avisoCrs = true;
      aviso(`La imagen está en EPSG:${w.image_epsg} y el plano en EPSG:${w.drawing_epsg.join(", ")}. Revisa que sean el mismo sistema.`, "aviso");
    }
    await refrescar();
    if (!x.enCarpeta && C.soportaRecordar() && $("cad-local-ins-vincular").checked && x.handle) {
      try { e.guardadas = await C.vincular(CLAVE, { agregar: true, inicio: x.handle }); await usar(e.guardadas); } catch (_) { /* la persona canceló */ }
    }
  }

  // ── Panel «Referencias externas» y avisos ─────────────────────────────────────────────────────────────────────────────────────
  function aviso(texto, tipo = "error") {
    const el = $("cad-local-mensaje");
    if (!el) return;
    el.textContent = texto; el.dataset.tipo = tipo; el.hidden = false;
  }

  function pintarCarpeta(fase, n) {
    const t = $("cad-local-carpeta-texto");
    if (!t) return;
    const nombres = (e.raices.length ? e.raices : e.guardadas).map((r) => `«${r.nombre || (r.handle && r.handle.name)}»`).join(", ");
    $("cad-local-permitir").hidden = true;
    if (fase === "indexando") t.textContent = `Buscando archivos en ${nombres}…${n ? ` ${n.toLocaleString("es-MX")}` : ""}`;
    else if (e.raices.length) t.textContent = `Carpeta del proyecto: ${nombres}${e.raices.some((r) => r.sesion) ? " (solo mientras esta página esté abierta)" : ""}.`;
    else if (e.guardadas.length) { t.textContent = `Esta computadora tiene vinculada ${nombres}. El navegador pide permiso para leerla.`; $("cad-local-permitir").hidden = false; }
    else t.textContent = "Sin carpeta del proyecto en esta computadora.";
    $("cad-local-vincular").hidden = !!(e.raices.length || e.guardadas.length);
    $("cad-local-otra").hidden = !e.raices.length || !C.soportaRecordar();
    $("cad-local-desvincular").hidden = !(e.raices.length || e.guardadas.length);
    $("cad-local-buscar").hidden = !e.raices.length;
    $("cad-local-sesion").hidden = C.soportaRecordar();
    pintarAviso();
  }

  const ESTADOS = {
    ruta: ["Encontrada", "exito"], nombre: ["Encontrada por nombre", "exito"], ambigua: ["Varias con ese nombre", "aviso"],
    falta: ["No encontrada", "error"], sin: ["Sin carpeta", "neutra"],
  };
  const tamano = (n) => (n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB` : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const nombreDe = (ruta) => String(ruta).split(/[\\/]/).pop();

  function fila(ruta, tipo, extra) {
    const r = e.indice ? e.res.get(ruta) || { estado: "falta" } : { estado: "sin" };
    const li = document.createElement("li");
    li.className = "cad-local__fila";
    li.dataset.ruta = ruta;
    const [txt, cls] = ESTADOS[r.estado];
    const titulo = document.createElement("div");
    titulo.className = "cad-local__nombre";
    const b = document.createElement("strong"); b.textContent = nombreDe(ruta);
    const ins = document.createElement("span"); ins.className = `tv-insignia tv-insignia--${cls}`; ins.textContent = txt;
    titulo.append(b, " ", ins);
    const det = document.createElement("span");
    det.className = "tv-tenue cad-local__detalle";
    const partes = [];
    if (r.entrada) partes.push(r.entrada.ruta);
    else partes.push(ruta);
    if (r.archivo) partes.push(tamano(r.archivo.size));
    if (r.info) partes.push(`${r.info.ancho.toLocaleString("es-MX")} × ${r.info.alto.toLocaleString("es-MX")} px`);
    if (r.estado === "ambigua") partes.push(`también: ${r.candidatos.slice(1, 4).map((c) => c.ruta).join(", ")}`);
    if (r.error) partes.push(`no se puede mostrar: ${r.error}`);
    if (extra) partes.push(extra);
    const p = e.piramides.get(ruta);
    if (p && p.corriendo) partes.push(`preparando la vista general… ${Math.round(100 * p.progreso)} %`);
    else if (p && p.detenida) partes.push("vista general a medias");
    det.textContent = partes.join(" · ");
    det.id = `cad-local-det-${btoa(unescape(encodeURIComponent(ruta))).replace(/[^a-z0-9]/gi, "")}`;
    li.append(titulo, det);
    const acc = document.createElement("div");
    acc.className = "cad-local__acciones";
    const boton = (texto, fn, titulo2) => { const x = document.createElement("button"); x.type = "button"; x.className = "tv-boton tv-boton--secundario cad-local__chico"; x.textContent = texto; if (titulo2) x.title = titulo2; x.addEventListener("click", fn); acc.append(x); };
    if (p && p.corriendo) boton("Detener", () => detenerPiramide(ruta));
    else if (p && p.detenida) boton("Continuar", () => { e.piramides.delete(ruta); piramide(ruta); });
    if (tipo === "imagen" && extra !== undefined) {
      const im = e.refs.images.find((x) => x.path === ruta);
      if (im) {
        boton("Enviar atrás", () => ajustar(im.handle, { draw_order: "back" }));
        boton(im.shown ? "Ocultar" : "Mostrar", () => ajustar(im.handle, { shown: !im.shown }));
        boton("Transparencia", () => pedirDesvanecer(im));
        boton("Recortar", () => recortar(im));
        boton("Quitar del plano", () => confirmar(`¿Quitar «${nombreDe(ruta)}» del plano?`, "Se quitan las imágenes de ese archivo y su referencia. El archivo sigue en tu computadora.", "Quitar", () => ajustar(im.handle, { detach: true })));
      }
    }
    if (acc.childElementCount) li.append(acc);
    return li;
  }

  async function ajustar(handle, cambios) {
    try { await pedir({ op: "image_set", handle, ...cambios }); } catch (err) { aviso(`No se cambió la imagen: ${(err.respuesta && err.respuesta.error) || err.message}.`); }
    await refrescar();
  }

  function confirmar(titulo, texto, accion, fn) {
    const d = $("cad-local-confirmar");
    $("cad-local-confirmar-titulo").textContent = titulo;
    $("cad-local-confirmar-texto").textContent = texto;
    const ir = $("cad-local-confirmar-ir");
    ir.textContent = accion;
    ir.onclick = () => { d.close(); fn(); };
    d.showModal();
  }

  function pedirDesvanecer(im) {
    const d = $("cad-local-desvanecer");
    const r = $("cad-local-desvanecer-valor");
    r.value = String(im.fade || 0);
    $("cad-local-desvanecer-ir").onclick = () => { d.close(); ajustar(im.handle, { fade: Number(r.value) }); };
    d.showModal();
  }

  async function recortar(im) {
    // El recorte rectangular se elige en el dibujo con IMAGECLIP del editor (dos esquinas), como en AutoCAD.
    try {
      await pedir({ op: "select", handles: [im.handle] });
      await window.TVCad.ejecutar("IMAGECLIP");
      $("cad-referencias").close();
    } catch (err) { aviso(`No se pudo recortar: ${err.message}.`); }
  }

  function pintarPanel() {
    const lista = $("cad-local-lista");
    if (!lista) return;
    lista.replaceChildren();
    const unicas = [...new Set(e.refs.images.map((x) => x.path))];
    const titulo = (t) => { const h = document.createElement("li"); h.className = "cad-local__grupo"; h.textContent = t; lista.append(h); };
    if (unicas.length) {
      titulo(`Imágenes (${unicas.length})`);
      // Cientos de imágenes (fachadas): las que faltan primero; con muchas, las que se encontraron se cuentan en una línea.
      const faltan = unicas.filter((r) => !(e.res.get(r) || {}).entrada);
      const hay = unicas.filter((r) => (e.res.get(r) || {}).entrada);
      for (const r of faltan.slice(0, 200)) lista.append(fila(r, "imagen"));
      if (hay.length > 60) {
        const li = document.createElement("li"); li.className = "cad-local__fila tv-tenue";
        li.textContent = `${hay.length.toLocaleString("es-MX")} encontradas en la carpeta.`;
        lista.append(li);
        for (const r of hay.filter((x) => (e.res.get(x) || {}).estado === "ambigua").slice(0, 100)) lista.append(fila(r, "imagen"));
      } else for (const r of hay) lista.append(fila(r, "imagen", ""));
    }
    if (e.refs.xrefs.length) {
      titulo(`Referencias a dibujos (${e.refs.xrefs.length})`);
      for (const x of e.refs.xrefs) lista.append(fila(x.path, "xref", { loaded: "abierta (solo lectura)", recovered: "abierta con reparaciones", failed: "no se pudo leer", not_found: "", unloaded: "descargada en el plano" }[x.status] || ""));
    }
    if (!lista.childElementCount) {
      const li = document.createElement("li"); li.className = "tv-tenue"; li.textContent = "Este plano no tiene imágenes ni referencias a otros dibujos.";
      lista.append(li);
    }
    const imp = $("cad-local-imprimir-aviso");
    if (imp) imp.hidden = !e.refs.images.length;
    const faltan = contarFaltan();
    const ins = $("cad-local-boton-cuenta");
    if (ins) { ins.hidden = !faltan; ins.textContent = String(faltan); }
  }

  function pintarProgreso(ruta) {
    const p = e.piramides.get(ruta);
    const el = document.getElementById(`cad-local-det-${btoa(unescape(encodeURIComponent(ruta))).replace(/[^a-z0-9]/gi, "")}`);
    if (el && p) el.textContent = el.textContent.replace(/preparando la vista general… \d+ %/, `preparando la vista general… ${Math.round(100 * p.progreso)} %`);
  }

  function contarFaltan() {
    const rutas = new Set([...e.refs.images.map((x) => x.path), ...e.refs.xrefs.filter((x) => !["loaded", "recovered"].includes(x.status)).map((x) => x.path)]);
    let n = 0;
    for (const r of rutas) if (!(e.res.get(r) || {}).entrada) n++;
    return n;
  }

  function pintarAviso() {
    const av = $("cad-aviso-local");
    if (!av) return;
    // Solo lo que falta: una imagen insertada desde esta computadora, o ya encontrada, no pide carpeta.
    const n = contarFaltan();
    if (!n) { av.hidden = true; return; }
    if (!e.raices.length) {
      av.hidden = false;
      $("cad-aviso-local-texto").textContent = e.guardadas.length
        ? `Este plano usa ${n === 1 ? "un archivo" : `${n.toLocaleString("es-MX")} archivos`} de la carpeta del proyecto. El navegador pide permiso para leerla.`
        : `Este plano usa ${n === 1 ? "un archivo" : `${n.toLocaleString("es-MX")} archivos`} (imágenes o dibujos) que están en una carpeta de tu computadora.`;
      $("cad-aviso-local-vincular").hidden = !!e.guardadas.length;
      $("cad-aviso-local-permitir").hidden = !e.guardadas.length;
    } else av.hidden = true;
  }

  // ── Arranque ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  function enganchar() {
    const clic = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", () => fn().catch((err) => aviso(String(err.message || err)))); };
    clic("cad-local-vincular", () => vincular());
    clic("cad-aviso-local-vincular", () => vincular());
    clic("cad-local-otra", () => vincular({ agregar: true }));
    clic("cad-local-permitir", permitir);
    clic("cad-aviso-local-permitir", permitir);
    clic("cad-local-desvincular", desvincular);
    clic("cad-local-buscar", async () => { if (e.raices.length) await usar(e.raices); });
    clic("cad-local-insertar-boton", elegirParaInsertar);
    clic("cad-menu-insertar-imagen", elegirParaInsertar);
    clic("cad-local-ins-ir", insertar);
    clic("cad-local-borrar-piramides", async () => confirmar("¿Borrar las vistas generales de este navegador?",
      "Son copias reducidas de las ortofotos, guardadas solo en esta computadora para abrirlas rápido. Se vuelven a preparar al abrir cada una.",
      "Borrar", async () => { await pool.pedir({ tipo: "borrar_piramides" }).promesa; e.piramides.clear(); aviso("Se borraron las vistas generales de este navegador.", "info"); }));
    const entrada = $("cad-local-entrada");
    if (entrada) entrada.addEventListener("change", () => { if (entrada.files.length) usar([C.desdeEntrada(entrada.files)]); entrada.value = ""; });
    const img = $("cad-local-imagen");
    if (img) img.addEventListener("change", () => { if (img.files.length) desdeEntradaImagen(img.files); img.value = ""; });
    const zona = $("cad-referencias");
    if (zona) {
      zona.addEventListener("dragover", (ev) => { ev.preventDefault(); zona.classList.add("cad-local--soltar"); });
      zona.addEventListener("dragleave", () => zona.classList.remove("cad-local--soltar"));
      zona.addEventListener("drop", async (ev) => {
        ev.preventDefault(); zona.classList.remove("cad-local--soltar");
        const raiz = await C.desdeSoltar(ev.dataTransfer);
        if (!raiz) return aviso("Suelta una carpeta, no archivos sueltos.");
        if (raiz.handle && C.soportaRecordar()) {
          e.guardadas = await C.agregar(CLAVE, raiz.handle);
          if (!(await C.permitir(e.guardadas))) return pintarCarpeta();
        }
        await usar(raiz.handle ? e.guardadas.filter((x) => x.handle) : [...e.raices, raiz]);
      });
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) planear(true); });
    window.TVCad.on("cargado", () => { e.res.clear(); e.sum.clear(); e.xrefsDadas.clear(); e.vueltasXref = 0; refrescar(); });
    window.TVCad.on("cambio", () => setTimeout(refrescar, 300));
    $("cad-boton-referencias") && $("cad-boton-referencias").addEventListener("click", () => refrescar());
  }

  enganchar();
  pintarCarpeta();
  cargarGuardadas().catch((err) => console.warn("CAD: carpeta del proyecto", err));
  window.TVCad.listo.then(() => refrescar());

  // Para las pruebas y el diagnóstico (nada de esto sale de la computadora).
  window.TVCadLocal = Object.freeze({
    estado: () => ({
      raices: e.raices.map((r) => r.nombre || (r.handle && r.handle.name)),
      resueltas: [...e.res.entries()].map(([ruta, r]) => ({ ruta, estado: r.estado, archivo: r.entrada && r.entrada.ruta, error: r.error || null })),
      suministradas: [...e.sum.entries()].map(([ruta, s]) => ({ ruta, base: s.base, detalle: s.detalle && { rect: s.detalle.rect, w: s.detalle.w, h: s.detalle.h } })),
      piramides: [...e.piramides.entries()].map(([ruta, p]) => ({ ruta, progreso: p.progreso, corriendo: !!p.corriendo, segundos: p.segundos || null, error: p.error || null })),
    }),
    usar, refrescar, vincular,
  });
})();

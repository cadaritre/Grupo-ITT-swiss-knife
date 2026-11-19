/* «Carpeta del proyecto en esta computadora» y «Referencias externas» de la página de un plano CAD.
 *
 * El plano (DWG/DXF) vive en la biblioteca; sus imágenes (IMAGE + IMAGEDEF) y referencias a otros dibujos (xref) guardan rutas, como en
 * AutoCAD. Esta página resuelve esas rutas contra una carpeta de la computadora y le da al editor lo que la vista necesita:
 *   - imágenes: por mosaicos y niveles, como un SIG. El editor dice qué mosaicos necesita la vista (`image_tiles`: el nivel cuyos
 *     píxeles miden hasta un píxel de la pantalla, del centro hacia afuera, un anillo alrededor y los niveles vecinos) y dibuja lo que
 *     tiene, con el ancestro más cercano mientras falta uno; esta página los lee del archivo en sus trabajadores, a lo sumo unos pocos
 *     a la vez, corta los que salieron de la vista, y se los da (`ocs_image_tile`). Un mosaico ya visto se queda en el editor: volver
 *     a pasar por ahí no espera nada. Las ortofotos enormes se leen de sus mosaicos y vistas generales internas, y si el archivo no las
 *     tiene se arma una pirámide local en el almacenamiento privado del navegador (OPFS), una vez, en segundo plano;
 *   - referencias a dibujos: los bytes del DWG/DXF por `ocs_xref_supply`; se muestran como en AutoCAD (solo lectura).
 * NADA de la carpeta se sube: ni píxeles, ni nombres (fuera de lo que el plano ya trae), ni miniaturas, ni métricas. El servidor guarda
 * solo el DWG, con las rutas tal como la persona las tenía.
 *
 * Varios planos en la ventana (pestañas, editor.js): cada plano tiene su carpeta (por cuenta y plano), su índice y sus referencias; lo
 * que se ve es siempre el de la pestaña activa. El editor guarda los mosaicos por documento y ruta, atados al archivo exacto que esta
 * página resolvió (ruta en la carpeta, nombre, tamaño y fecha): dos proyectos con «f001.png» nunca se mezclan, y dos planos del mismo
 * proyecto comparten los mosaicos del mismo archivo. Los planos de atrás conservan solo sus niveles gruesos (el editor, hasta 256 MB
 * entre todos); cerrar una pestaña suelta lo suyo. Los dibujos de referencia se dan al documento de su plano (ocs_xref_supply).
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
  const C = window.TVCarpeta;

  // Un estado por plano abierto (pestaña); `e` es el de la pestaña activa.
  const planos = new Map();
  function nuevoEstado(id) {
    const st = {
      plano: id,
      clave: `${cfg.cuenta}:${id}`,
      documento: null,         // document_id del editor
      raices: [],              // [{handle?, archivos?, nombre, sesion?}] con permiso
      guardadas: [],           // las de IndexedDB (con o sin permiso)
      permisoPendiente: false, // carpeta guardada que espera el permiso del navegador
      usarAlVolver: false,     // el permiso llegó con la pestaña detrás: se indexa al mostrarla
      indice: null,
      refs: { images: [], xrefs: [] },
      res: new Map(),          // ruta del plano → {estado, entrada, coincide, candidatos, archivo, info, error}
      piramides: new Map(),    // ruta → {progreso, id, detenida, vistaLista}
      abiertas: new Map(),     // ruta → ident con que se ató en el editor (ocs_image_tiles_open)
      vista: null,             // la última respuesta de image_tiles
      xrefsDadas: new Set(),
      vueltasXref: 0,
      avisoCrs: false,
    };
    planos.set(id, st);
    st.cargadas = cargarGuardadas(st).catch((err) => console.warn("CAD: carpeta del proyecto", err));
    return st;
  }
  const estadoDe = (id) => planos.get(id) || nuevoEstado(id);
  let e = null;


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
      const h = { w, ocupados: 0, ids: new Set() };
      w.onmessage = (ev) => {
        const m = ev.data;
        const p = pendientes.get(m.id);
        if (!p) return;
        if (m.progreso !== undefined && m.ok === undefined) { if (p.progreso) p.progreso(m); return; }
        pendientes.delete(m.id);
        if (h.ids.delete(m.id)) h.ocupados--;
        if (m.ok) p.ok(m); else p.mal(new Error(m.error || "error"));
      };
      w.onerror = (ev) => console.error("CAD: trabajador de imágenes", ev.message);
      hilos.push(h);
      enviar(h, { tipo: "iniciar", tiff: cfg.tiff });
      return h;
    };
    function enviar(h, msg, transferir, progreso) {
      const id = ++sig;
      h.ocupados++; h.ids.add(id);
      return { id, promesa: new Promise((ok, mal) => { pendientes.set(id, { ok, mal, progreso }); h.w.postMessage({ ...msg, id }, transferir || []); }) };
    }
    return {
      // Una pirámide local ocupa su trabajador entero mientras se arma: las lecturas de mosaicos van a los demás (si hay más de uno),
      // así nunca le quitan turnos.
      pedir(msg, { progreso, hilo } = {}) {
        if (!hilos.length) for (let i = 0; i < n; i++) crear();
        const libres = hilos.length > 1 ? hilos.filter((x) => !x.armando) : hilos;
        const lista = libres.length ? libres : hilos;
        const h = hilo !== undefined ? lista[hilo % lista.length] : lista.reduce((a, b) => (b.ocupados < a.ocupados ? b : a));
        const r = enviar(h, msg, null, progreso);
        if (msg.tipo === "piramide") {
          h.armando = (h.armando || 0) + 1;
          const fin = () => { h.armando--; };
          r.promesa.then(fin, fin);
        }
        return r;
      },
      cancelar(id) {
        for (const h of hilos) h.w.postMessage({ tipo: "cancelar", objetivo: id, id: -1 });
        const p = pendientes.get(id);
        if (p) { pendientes.delete(id); for (const h of hilos) if (h.ids && h.ids.delete(id)) h.ocupados--; p.ok({ cancelada: true }); }
      },
      cupo: () => 2 * n,
      hilos: () => n,
      cerrar() { for (const h of hilos) h.w.terminate(); hilos.length = 0; },
    };
  })();

  // ── Carpeta ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  async function cargarGuardadas(st) {
    if (!C.soportaRecordar()) return;
    st.guardadas = await C.guardadas(st.clave);
    if (!st.guardadas.length) return;
    const p = await C.permisos(st.guardadas);
    if (p.every((x) => x === "granted")) {
      st.permisoPendiente = false;
      if (st === e) await usar(st, st.guardadas); else st.usarAlVolver = true;
    } else {
      st.permisoPendiente = true;
      if (st === e) pintarCarpeta();
    }
  }

  async function usar(st, raices) {
    st.raices = raices;
    st.usarAlVolver = false; st.permisoPendiente = false;
    if (st === e) pintarCarpeta("indexando");
    const { indice } = await C.indexar(raices, (n) => { if (st === e) pintarCarpeta("indexando", n); });
    st.indice = indice;
    st.res.clear();
    for (const [, p] of st.piramides) if (p.id && p.corriendo) pool.cancelar(p.id);
    st.piramides.clear();
    // Lo que el editor tenía de este plano venía de la carpeta anterior: se vuelve a leer de esta.
    soltarPlano(st);
    if (st === e) pintarCarpeta();
    await refrescar(st);
  }

  async function vincular({ agregar = false } = {}) {
    const st = e;
    if (C.soportaRecordar()) {
      try { st.guardadas = await C.vincular(st.clave, { agregar }); } catch (err) { if (err && err.name === "AbortError") return; throw err; }
      await usar(st, st.guardadas);
    } else {
      $("cad-local-entrada").click();
    }
  }

  // Un solo toque: el permiso de la carpeta de este plano y, de paso, el de las demás pestañas que lo esperan (tras reiniciar el
  // navegador todas vuelven a «preguntar»). Una carpeta que comparten varios planos se pide una vez.
  async function permitir() {
    const st = e;
    const otros = [...planos.values()].filter((x) => x !== st && x.permisoPendiente && x.guardadas.length);
    const lista = [...st.guardadas];
    for (const x of otros) for (const c of x.guardadas) {
      let ya = false;
      for (const y of lista) { try { if (await y.handle.isSameEntry(c.handle)) { ya = true; break; } } catch (_) { /* otra */ } }
      if (!ya) lista.push(c);
    }
    await C.permitir(lista);
    for (const x of otros) {
      const p = await C.permisos(x.guardadas);
      if (p.every((v) => v === "granted")) { x.permisoPendiente = false; x.usarAlVolver = true; }
    }
    const p = await C.permisos(st.guardadas);
    if (st.guardadas.length && p.every((v) => v === "granted")) await usar(st, st.guardadas);
    else pintarCarpeta();
  }

  async function desvincular() {
    const st = e;
    if (C.soportaRecordar()) await C.desvincular(st.clave);
    st.guardadas = []; st.raices = []; st.indice = null; st.res.clear(); st.xrefsDadas.clear(); st.permisoPendiente = false; st.usarAlVolver = false;
    for (const [, p] of st.piramides) if (p.id) pool.cancelar(p.id);
    st.piramides.clear();
    // Solo lo de este plano: las demás pestañas conservan su carpeta y sus imágenes.
    soltarPlano(st);
    pintarCarpeta();
    pintarPanel();
  }

  // ── Lo que el editor tiene, con varias pestañas ──────────────────────────────────────────────────────────────────────────────
  // El editor ata cada (documento, ruta) al archivo exacto (`ident`) y nunca dibuja otro: cambiar de pestaña no necesita nada aquí.
  // La identidad de un archivo: su carpeta (la misma carpeta en dos pestañas es la misma ficha; otra carpeta, otra, aunque se llame
  // igual), su ruta en ella, su nombre, tamaño y fecha. Dos proyectos copiados con los mismos nombres y tamaños nunca comparten.
  const fichas = [];
  let sigFicha = 0;
  async function fichaDe(raiz) {
    if (raiz.ficha) return raiz.ficha;
    if (raiz.handle) {
      for (const f of fichas) {
        try { if (f.handle && (await f.handle.isSameEntry(raiz.handle))) return (raiz.ficha = f.id); } catch (_) { /* otra */ }
      }
    }
    const id = `c${++sigFicha}`;
    fichas.push({ handle: raiz.handle || null, id });
    return (raiz.ficha = id);
  }
  async function identDe(st, r, archivo) {
    const raiz = r && r.entrada && r.entrada.raiz !== undefined ? st.raices[r.entrada.raiz] : null;
    const ficha = raiz ? await fichaDe(raiz) : (r.ficha || (r.ficha = `s${++sigFicha}`));
    return `${ficha}|${(r && r.entrada && r.entrada.ruta) || ""}|${archivo.name}|${archivo.size}|${archivo.lastModified}`;
  }

  /** Lo de este plano sale del editor (otra carpeta, desvincular, cerrar): sus imágenes vuelven a su marco. */
  function soltarPlano(st) {
    cancelarDe(st);
    st.abiertas.clear();
    st.vista = null;
    const w = editor();
    if (w && w.ocs_image_tiles_release && st.documento) w.ocs_image_tiles_release(st.documento, "");
  }

  // Se cerró una pestaña: lo suyo sale del editor y de esta página (la carpeta sigue recordada en el navegador para ese plano).
  function alCerrar(id) {
    const st = planos.get(id);
    if (!st) return;
    planos.delete(id);
    for (const [, p] of st.piramides) if (p.id && p.corriendo) pool.cancelar(p.id);
    soltarPlano(st);
  }

  // ── Referencias del plano ─────────────────────────────────────────────────────────────────────────────────────────────────────
  async function refrescar(st = e) {
    if (!st || !window.TVCad.estaListo() || !planos.has(st.plano)) return;
    let r;
    // Las referencias de ESTE plano, aunque su pestaña pase atrás mientras tanto (el editor responde por su documento).
    try { r = await pedir(st.documento ? { op: "references", document_id: st.documento } : { op: "references" }); } catch (_) { return; }
    if (st.documento && r.document_id && r.document_id !== st.documento) return;
    st.refs = r;
    const rutas = new Set();
    for (const im of r.images) rutas.add(im.path);
    for (const x of r.xrefs) rutas.add(x.path);
    if (st.indice) {
      for (const ruta of rutas) {
        if (!ruta || st.res.has(ruta)) continue;
        st.res.set(ruta, C.resolver(st.indice, ruta));
      }
    }
    if (st !== e) return;
    pintarPanel();
    pintarAviso();
    await darXrefs(st);
    planear(true);
  }

  async function archivoDe(st, ruta) {
    const r = st.res.get(ruta);
    if (!r || !r.entrada) return null;
    if (!r.archivo) r.archivo = await C.archivoDe(r.entrada);
    return r.archivo;
  }

  async function infoDe(st, ruta) {
    const r = st.res.get(ruta);
    if (!r || !r.entrada) return null;
    if (r.info || r.error) return r.info;
    if (!r.pidiendoInfo) {
      r.pidiendoInfo = (async () => {
        try {
          const archivo = await archivoDe(st, ruta);
          r.info = await pool.pedir({ tipo: "info", archivo }).promesa;
          if (r.info.motivo) r.error = r.info.motivo;
        } catch (err) { r.error = String(err.message || err); }
        if (st === e) pintarPanel();
        return r.info;
      })();
    }
    return r.pidiendoInfo;
  }

  // Los dibujos de referencia van al documento de su plano: otra pestaña que nombre el mismo «base.dwg» (de otro proyecto) no se toca.
  async function darXrefs(st) {
    const w = editor();
    if (!w || !w.ocs_xref_supply || !st.documento) return;
    let dio = false;
    for (const x of st.refs.xrefs) {
      if (["loaded", "recovered"].includes(x.status) || st.xrefsDadas.has(x.path)) continue;
      const archivo = await archivoDe(st, x.path);
      if (!archivo || !planos.has(st.plano)) continue;
      st.xrefsDadas.add(x.path);
      w.ocs_xref_supply(x.path, new Uint8Array(await archivo.arrayBuffer()), st.documento);
      dio = true;
    }
    // Las anidadas aparecen al cargar su padre: otra vuelta (hasta 4 niveles).
    if (dio && (st.vueltasXref = (st.vueltasXref || 0) + 1) <= 4) setTimeout(() => refrescar(st), 400);
  }

  // ── Imágenes: qué mosaicos pide la vista y cuáles se le dan al editor ─────────────────────────────────────────────────────────
  // La rejilla es la del editor (local_raster.rs: Grid): nivel z = el archivo reducido 2^z veces, en mosaicos de `tile` píxeles desde
  // la esquina superior izquierda; cada mosaico se da con `gutter` píxeles de más en los lados que tienen vecino.
  const VISTA_LADO = 2048;            // la vista general de la pirámide local (trabajador.js, VISTA)
  const ENTREGAS_POR_CUADRO = 4;      // mosaicos que se le dan al editor por cuadro, para que nunca se trabe
  const ESPERA_SIN_FUENTE = 1500;     // ms antes de volver a pedir un mosaico que aún no tiene de dónde salir
  const rejilla = (info, set) => {
    const W = info.ancho, H = info.alto, T = set.tile, g = set.gutter;
    const lado = (v, z) => Math.max(1, Math.ceil(v / 2 ** z));
    return {
      W, H, T, g, top: set.top,
      bloque(z, c, r) {
        const lw = lado(W, z), lh = lado(H, z);
        const x0 = Math.min(c * T, lw), y0 = Math.min(r * T, lh), x1 = Math.min((c + 1) * T, lw), y1 = Math.min((r + 1) * T, lh);
        return [Math.max(0, x0 - g), Math.max(0, y0 - g), Math.min(lw, x1 + g), Math.min(lh, y1 + g)];
      },
      archivo(z, b) { const k = 2 ** z; return [Math.min(W, b[0] * k), Math.min(H, b[1] * k), Math.min(W, b[2] * k), Math.min(H, b[3] * k)]; },
      // Los mosaicos del nivel z que cubren el rectángulo del archivo (como Grid::range del editor).
      visibles(z, r) {
        const k = 2 ** z * T, cols = Math.ceil(lado(W, z) / T), filas = Math.ceil(lado(H, z) / T);
        const out = new Set();
        if (!r || !(r[2] > r[0] && r[3] > r[1])) return out;
        const c0 = Math.min(cols - 1, Math.floor(Math.max(0, r[0]) / k)), c1 = Math.min(cols - 1, Math.max(1, Math.ceil(r[2] / k)) - 1);
        const f0 = Math.min(filas - 1, Math.floor(Math.max(0, r[1]) / k)), f1 = Math.min(filas - 1, Math.max(1, Math.ceil(r[3] / k)) - 1);
        for (let f = f0; f <= f1; f++) for (let c = c0; c <= c1; c++) out.add(`${z}/${c}/${f}`);
        return out;
      },
      // El nivel más fino que sale entero de la vista general de la pirámide local.
      nivelVista() { let z = 0; while (z < set.top && Math.max(lado(W, z), lado(H, z)) > VISTA_LADO) z++; return z; },
    };
  };

  const QUIETO_MS = 250;              // la vista se considera quieta tras este tiempo sin cambiar
  let ultimaCamara = null, movidaEn = 0;
  let ciclo = null, enPaso = false, otraVez = false;
  const trabajos = new Map();         // clave → {id, st}: lecturas en curso
  const esperando = new Map();        // clave → hasta (ms): sin fuente todavía
  const entregas = [];                // mosaicos leídos que esperan su cuadro
  const claveDe = (doc, ruta, ident, z, c, r) => `${doc}|${ruta}|${ident}|${z}/${c}/${r}`;

  function planear(pronto, ms = 140) {
    if (enPaso) { otraVez = true; return; }
    if (ciclo) { if (!pronto) return; clearTimeout(ciclo); }
    ciclo = setTimeout(async () => {
      ciclo = null;
      enPaso = true;
      let espera = null;
      try { espera = await paso(); } catch (err) { console.warn("CAD: imágenes", err); espera = 500; } finally {
        enPaso = false;
        // Lo que pidió una vuelta mientras corría esta va enseguida; si no, la siguiente a su tiempo (nunca un bucle sin pausa: cada
        // vuelta despierta al editor, y sin pausa le quita la CPU al dibujo y a la pirámide local).
        if (otraVez) { otraVez = false; planear(true); } else if (espera !== null && espera !== undefined) planear(espera === 0, espera);
      }
    }, pronto ? 0 : ms);
  }

  function cancelarDe(st) {
    for (const [clave, t] of trabajos) if (t.st === st) { pool.cancelar(t.id); trabajos.delete(clave); }
    for (let i = entregas.length - 1; i >= 0; i--) if (entregas[i].st === st) entregas.splice(i, 1);
  }

  // Para el diagnóstico (TVCadLocal.estado().contadores): pasos, ataduras, lecturas por nivel y mosaicos dados.
  const contadores = { pasos: 0, aperturas: 0, lecturas: {}, entregados: 0, vistas: 0 };
  async function paso() {
    contadores.pasos++;
    const st = e;
    // Sin nada resuelto (sin carpeta y sin imágenes insertadas desde esta computadora) no hay nada que pedir.
    const algo = st && (st.indice || [...st.res.values()].some((x) => x.entrada));
    if (!window.TVCad.estaListo() || !algo || document.hidden) { if (algo) setTimeout(() => planear(), 500); return; }
    let v;
    try { v = await pedir({ op: "image_tiles" }, { tiempoMs: 5000 }); } catch (_) { setTimeout(() => planear(), 500); return; }
    // La pestaña cambió mientras tanto, o la vista es de otro documento: otra vuelta con la de ahora.
    if (st !== e || (st.documento && v.document_id && v.document_id !== st.documento)) { setTimeout(() => planear(true), 0); return; }
    const doc = st.documento || v.document_id;
    st.vista = v;
    contadores.vistas += v.views.length;
    // Mientras la vista se mueve, solo lo que se ve (y la raíz): el anillo y los niveles vecinos se leen cuando se detiene, así los
    // trabajadores no compiten con el dibujo durante un arrastre o un acercamiento seguidos.
    const ahoraMov = performance.now();
    if (v.camera_revision !== ultimaCamara) { ultimaCamara = v.camera_revision; movidaEn = ahoraMov; }
    const moviendose = ahoraMov - movidaEn < QUIETO_MS;
    const ahora = performance.now();
    const deseados = new Set();
    const pedidos = [];
    let abrio = false;
    const vistas = [...v.views].sort((a, b) => b.screen_area - a.screen_area);
    for (const f of vistas) {
      const r = st.res.get(f.path);
      if (!r || !r.entrada || r.error) continue;
      const info = r.info;
      if (!info) { infoDe(st, f.path).then(() => planear(true)); continue; }
      if (info.motivo) continue;
      const archivo = await archivoDe(st, f.path);
      if (!archivo || st !== e) continue;
      const ident = await identDe(st, r, archivo);
      if (st !== e) continue;
      if (!f.set || f.set.ident !== ident) {
        // Atar la ruta, en este documento, a este archivo; el editor empieza vacío y pide la raíz.
        const w = editor();
        if (w && w.ocs_image_tiles_open) {
          contadores.aperturas++;
          const motivo = w.ocs_image_tiles_open(doc, f.path, ident, info.ancho, info.alto);
          if (motivo) console.warn("CAD: el editor no tomó la imagen", f.path, motivo); else { st.abiertas.set(f.path, ident); abrio = true; }
        }
        continue;
      }
      st.abiertas.set(f.path, ident);
      const g = rejilla(info, f.set);
      if (info.piramide === "falta" && !st.piramides.has(f.path)) piramide(st, f.path, f.set);
      const pir = st.piramides.get(f.path);
      // Mientras se arma la pirámide local, ella va primero: los niveles que salen de su vista general llegan con su progreso, y del
      // archivo completo solo se leen los mosaicos que se ven de los niveles ½ y completo (los baratos: pocos bloques del archivo). Los
      // niveles intermedios esperan a la pirámide: mientras tanto el editor dibuja el nivel ya armado más cercano. Sin anillo ni niveles
      // vecinos hasta que termine.
      const armando = pir && pir.corriendo;
      const desdeVista = armando && !pir.vistaLista ? g.nivelVista() : Infinity;
      let quiere = moviendose ? f.want.filter(([z]) => z === f.level || z === f.set.top).slice(0, 24) : f.want;
      if (armando) {
        const vis = g.visibles(f.level, f.file_rect);
        const nv = g.nivelVista();
        quiere = quiere.filter(([z, c, rr]) => z === f.set.top
          || (vis.has(`${z}/${c}/${rr}`) && (z <= 1 || (z >= nv && pir.vistaLista))));
      }
      quiere.forEach(([z, c, rr], i) => {
        const clave = claveDe(doc, f.path, ident, z, c, rr);
        deseados.add(clave);
        if (z >= desdeVista || trabajos.has(clave) || (esperando.get(clave) || 0) > ahora) return;
        if (entregas.some((x) => x.clave === clave)) return;
        pedidos.push({ orden: i, area: f.screen_area, clave, hacer: () => leer(st, doc, f.path, ident, archivo, g, z, c, rr, clave) });
      });
    }
    // Lo que salió de la vista se corta: los trabajadores quedan para lo que se ve.
    for (const [clave, t] of trabajos) if (t.st !== st || !deseados.has(clave)) { pool.cancelar(t.id); trabajos.delete(clave); }
    pedidos.sort((a, b) => a.orden - b.orden || b.area - a.area);
    for (const p of pedidos) {
      if (trabajos.size >= pool.cupo()) break;
      p.hacer();
    }
    // Sin nada pendiente, se mira menos seguido (la vista quieta no pide nada).
    const ocupado = abrio || trabajos.size || pedidos.length || entregas.length;
    return abrio ? 0 : (ocupado ? 90 : 250);
  }

  async function leer(st, doc, ruta, ident, archivo, g, z, c, r, clave) {
    contadores.lecturas[z] = (contadores.lecturas[z] || 0) + 1;
    const b = g.bloque(z, c, r);
    const rect = g.archivo(z, b);
    // Los mosaicos de una misma fila y nivel van al mismo trabajador: comparten sus bloques del TIFF (tiras anchas).
    let h = 0;
    for (const ch of `${ruta}|${z}|${r}`) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const { id, promesa } = pool.pedir({ tipo: "region", archivo, rect, ancho: b[2] - b[0], alto: b[3] - b[1], factor: 2 ** z }, { hilo: h % pool.hilos() });
    trabajos.set(clave, { id, st });
    try {
      const x = await promesa;
      if (trabajos.get(clave)?.id !== id) return;          // cortado o reemplazado
      if (x.cancelada) return;
      if (x.pendiente) { esperando.set(clave, performance.now() + ESPERA_SIN_FUENTE); return; }
      entregas.push({ st, doc, ruta, ident, z, c, r, clave, ancho: x.ancho, alto: x.alto, rgba: x.rgba });
      entregar();
    } catch (err) {
      console.warn("CAD: mosaico", ruta, z, c, r, err);
      esperando.set(clave, performance.now() + 5 * ESPERA_SIN_FUENTE);
    } finally {
      if (trabajos.get(clave)?.id === id) trabajos.delete(clave);
      planear(true);
    }
  }

  // Al editor, unos pocos por cuadro: cada mosaico nuevo es una textura que sube, y así ningún cuadro se alarga.
  let cuadro = 0;
  function entregar() {
    if (cuadro) return;
    cuadro = requestAnimationFrame(() => {
      cuadro = 0;
      const w = editor();
      for (let k = 0; k < ENTREGAS_POR_CUADRO && entregas.length; k++) {
        const x = entregas.shift();
        // Lo que llega tarde de una pestaña cerrada o de un archivo que ya no es el de esa ruta se descarta (el editor también).
        if (!w || !w.ocs_image_tile || !planos.has(x.st.plano) || x.st.abiertas.get(x.ruta) !== x.ident) continue;
        contadores.entregados++;
        const motivo = w.ocs_image_tile(x.doc, x.ruta, x.ident, x.z, x.c, x.r, x.ancho, x.alto, x.rgba);
        if (motivo && motivo !== "Stale") console.warn("CAD: el editor no tomó el mosaico", x.ruta, x.z, x.c, x.r, motivo);
      }
      if (entregas.length) entregar();
    });
  }

  async function piramide(st, ruta, set) {
    const archivo = await archivoDe(st, ruta);
    const info = st.res.get(ruta).info;
    const g = rejilla(info, set);
    const p = { progreso: 0, corriendo: true, vistaLista: false };
    st.piramides.set(ruta, p);
    if (st === e) pintarPanel();
    const ident = await identDe(st, st.res.get(ruta), archivo);
    const { id, promesa } = pool.pedir({ tipo: "piramide", archivo,
      teselas: { ancho: info.ancho, alto: info.alto, tile: set.tile, gutter: set.gutter, top: set.top, desde: g.nivelVista() } }, {
      progreso: (m) => {
        p.progreso = m.progreso;
        p.vistaLista = !!m.vistaLista;
        const doc = st.documento || (st.vista && st.vista.document_id);
        for (const t of m.teselas || []) {
          entregas.push({ st, doc, ruta, ident, z: t.z, c: t.c, r: t.r, clave: claveDe(doc, ruta, ident, t.z, t.c, t.r),
            ancho: t.ancho, alto: t.alto, rgba: new Uint8Array(t.rgba.buffer || t.rgba) });
        }
        if (m.teselas && m.teselas.length) entregar();
        if (st === e) pintarProgreso(ruta);
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
    // Lo que esperaba fuente puede salir ya de la pirámide.
    for (const [clave] of esperando) if (clave.includes(`|${ruta}|`)) esperando.delete(clave);
    if (st === e) pintarPanel();
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
    const st = e;
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
      for (const r of st.raices) {
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
      insercion = { st, archivo, mundo, ruta: ruta || img.name, handle: img, enCarpeta };
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
    const st = e;
    let ruta = archivo.name;
    if (st.indice) {
      const x = C.resolver(st.indice, archivo.name);
      if (x.estado === "nombre") ruta = x.entrada.ruta.replace(/\//g, "\\");
    }
    insercion = { st, archivo, mundo: m ? await m.text() : null, ruta, enCarpeta: null };
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
    const st = x.st;
    // La imagen va al plano para el que se eligió (la hoja es modal: no se cambia de pestaña con ella abierta).
    if (st !== e) { $("cad-local-insertar").close(); return; }
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
    st.res.set(x.ruta, { estado: x.enCarpeta ? "ruta" : "nombre", entrada: { ruta: x.ruta.replace(/\\/g, "/"), nombre: x.archivo.name, archivo: x.archivo }, archivo: x.archivo, coincide: 1, candidatos: [], info: x.info });
    // Si el editor tenía esa ruta atada a otro archivo, la próxima vuelta la ata a este (otro `ident`) y empieza de cero.
    const w = r.result && r.result.crs_warning;
    if (w && !st.avisoCrs) {
      st.avisoCrs = true;
      aviso(`La imagen está en EPSG:${w.image_epsg} y el plano en EPSG:${w.drawing_epsg.join(", ")}. Revisa que sean el mismo sistema.`, "aviso");
    }
    await refrescar(st);
    if (!x.enCarpeta && C.soportaRecordar() && $("cad-local-ins-vincular").checked && x.handle) {
      try { st.guardadas = await C.vincular(st.clave, { agregar: true, inicio: x.handle }); await usar(st, st.guardadas); } catch (_) { /* la persona canceló */ }
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
    else if (p && p.detenida) boton("Continuar", () => { e.piramides.delete(ruta); planear(true); });
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
    clic("cad-local-buscar", async () => { if (e.raices.length) await usar(e, e.raices); });
    clic("cad-local-insertar-boton", elegirParaInsertar);
    clic("cad-menu-insertar-imagen", elegirParaInsertar);
    clic("cad-local-ins-ir", insertar);
    clic("cad-local-borrar-piramides", async () => confirmar("¿Borrar las vistas generales de este navegador?",
      "Son copias reducidas de las ortofotos, guardadas solo en esta computadora para abrirlas rápido. Se vuelven a preparar al abrir cada una.",
      "Borrar", async () => { await pool.pedir({ tipo: "borrar_piramides" }).promesa; e.piramides.clear(); aviso("Se borraron las vistas generales de este navegador.", "info"); }));
    const entrada = $("cad-local-entrada");
    if (entrada) entrada.addEventListener("change", () => { if (entrada.files.length) usar(e, [C.desdeEntrada(entrada.files)]); entrada.value = ""; });
    const img = $("cad-local-imagen");
    if (img) img.addEventListener("change", () => { if (img.files.length) desdeEntradaImagen(img.files); img.value = ""; });
    const zona = $("cad-referencias");
    if (zona) {
      zona.addEventListener("dragover", (ev) => { ev.preventDefault(); zona.classList.add("cad-local--soltar"); });
      zona.addEventListener("dragleave", () => zona.classList.remove("cad-local--soltar"));
      zona.addEventListener("drop", async (ev) => {
        ev.preventDefault(); zona.classList.remove("cad-local--soltar");
        const st = e;
        const raiz = await C.desdeSoltar(ev.dataTransfer);
        if (!raiz) return aviso("Suelta una carpeta, no archivos sueltos.");
        if (raiz.handle && C.soportaRecordar()) {
          st.guardadas = await C.agregar(st.clave, raiz.handle);
          if (!(await C.permitir(st.guardadas))) return pintarCarpeta();
        }
        await usar(st, raiz.handle ? st.guardadas.filter((x) => x.handle) : [...st.raices, raiz]);
      });
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) planear(true); });
    // La pestaña activa (al abrir el plano, al cambiar de pestaña, al reabrirlo tras tomar el control o restaurar una versión).
    window.TVCad.on("cargado", (d) => {
      const id = d && d.plano && d.plano.id;
      if (!id) return;
      const st = estadoDe(id);
      documentoDe(st, d.documento);
      e = st;
      const msj = $("cad-local-mensaje"); if (msj) msj.hidden = true;
      pintarCarpeta();
      pintarPanel();
      if (st.usarAlVolver) usar(st, st.guardadas).catch((err) => console.warn("CAD: carpeta del proyecto", err));
      else refrescar(st);
    });
    // Las demás pestañas: su carpeta se mira al abrirlas (sin pedir nada) y lo suyo se suelta al cerrarlas.
    window.TVCad.on("pestana", (d) => {
      const id = d && d.plano && d.plano.id;
      if (!id) return;
      if (d.accion === "cerrada") { alCerrar(id); return; }
      documentoDe(estadoDe(id), d.documento);
      if (e) pintarAviso();
    });
    window.TVCad.on("cambio", () => setTimeout(() => refrescar(e), 300));
    $("cad-boton-referencias") && $("cad-boton-referencias").addEventListener("click", () => refrescar(e));
  }
  // Un documento nuevo del editor para ese plano (se reabrió): sus dibujos de referencia se le vuelven a dar.
  function documentoDe(st, documento) {
    if (!documento || st.documento === documento) return;
    st.documento = documento;
    st.xrefsDadas.clear(); st.vueltasXref = 0;
  }

  e = estadoDe(config.id);
  enganchar();
  pintarCarpeta();

  // Para las pruebas y el diagnóstico (nada de esto sale de la computadora).
  window.TVCadLocal = Object.freeze({
    // De la pestaña activa; `suministradas` son las de este plano y `editor`, todo lo que el editor tiene (de todas las pestañas).
    estado: () => ({
      plano: e.plano,
      documento: e.documento,
      raices: e.raices.map((r) => r.nombre || (r.handle && r.handle.name)),
      permisoPendiente: e.permisoPendiente,
      resueltas: [...e.res.entries()].map(([ruta, r]) => ({ ruta, estado: r.estado, archivo: r.entrada && r.entrada.ruta, error: r.error || null })),
      // Lo que el editor tiene de cada imagen de la vista: atada a qué archivo, si ya está su raíz (la imagen entera), cuántos mosaicos,
      // el nivel que la vista necesita y si ya están todos los que se ven.
      suministradas: ((e.vista && e.vista.views) || []).filter((f) => f.set).map((f) => ({ ruta: f.path, ident: f.set.ident,
        raiz: f.set.root, teselas: f.set.tiles, bytes: f.set.bytes, nivel: f.level, completa: !!f.complete, faltan: f.want.length })),
      leyendo: trabajos.size, porEntregar: entregas.length, contadores: JSON.parse(JSON.stringify(contadores)),
      piramides: [...e.piramides.entries()].map(([ruta, p]) => ({ ruta, progreso: p.progreso, corriendo: !!p.corriendo, segundos: p.segundos || null, error: p.error || null })),
      editorBytes: (e.vista && e.vista.tiles_bytes) || 0,
      planos: [...planos.values()].map((st) => ({ plano: st.plano, documento: st.documento, raices: st.raices.length, guardadas: st.guardadas.length,
        permisoPendiente: st.permisoPendiente, resueltas: st.res.size })),
    }),
    usar: (raices) => usar(e, raices), refrescar: () => refrescar(e), vincular,
    // Entrada estable de «Insertar imagen georreferenciada» para otros módulos de la página: `archivos` es una lista de File (la
    // imagen y, si hay, su archivo de mundo), como la daría el selector. Abre la misma hoja (lugar, capa, «Insertar»).
    insertarImagen: (archivos) => desdeEntradaImagen(archivos),
  });
})();

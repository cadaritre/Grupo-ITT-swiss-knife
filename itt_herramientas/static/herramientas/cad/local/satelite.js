/* «Imagen satelital desde una polilínea» (Civil → Imágenes; docs/cad.md, «Imagen satelital»).
 *
 * La persona elige el área (una polilínea cerrada o dos esquinas) y la calidad (cm por píxel en el terreno); antes de bajar nada ve el
 * tamaño en píxeles, las teselas, lo que pesa y cuánto tarda. Luego:
 *   1. las teselas (Web Mercator) se bajan DIRECTO del navegador al proveedor (nada pasa por nuestro servidor);
 *   2. un trabajador las reproyecta a la cuadrícula UTM del plano (cada píxel en su lugar, no un rectángulo estirado) y codifica el JPEG,
 *      con la atribución del proveedor en la esquina;
 *   3. se guarda en ESTA computadora: en la carpeta del proyecto vinculada (subcarpeta «Satelital»: JPEG, archivo de mundo .jgw, .prj y
 *      la atribución en .txt) o, sin carpeta (o en Safari y Firefox), como descarga, y se inserta para la sesión;
 *   4. se inserta como un IMAGE estándar por el camino de «Insertar imagen georreferenciada» (local.js), recortado a la polilínea si se
 *      pide, en un solo paso de deshacer.
 * Proveedor: Esri World Imagery por ArcGIS Location Platform (la clave es configuración del servidor, nunca del repositorio).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let config;
  try { config = JSON.parse($("cad-config").textContent); } catch (_) { return; }
  const cfg = config.satelite;
  if (!cfg) return;
  const G = window.TVSateliteGeo;
  const CAPA = "Imagen satelital", CARPETA = "Satelital";
  const PARALELAS = 8, REINTENTOS = 3, ZMIN_MAX = 15, NIVELES_ATRAS = 4;

  const est = { area: null, plan: null, aviso: null, disp: new Map(), seq: 0, trabajo: null, atribucion: "", info: false };
  // El proveedor y su atribución llegan con la hoja (el panel de Civil, que se guarda una vez por versión), no en cada página.
  const textos = () => { const f = $("civil-sat-form"); return { atribucion: (f && f.dataset.atribucion) || "", proveedor: (f && f.dataset.proveedor) || "" }; };
  const civil = () => window.TVCivil;
  const avisar = (t, tipo = "error") => (window.tvAviso ? window.tvAviso(t, tipo) : console.log(t));
  const mil = (v, d = 0) => Number(v).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });
  const tamano = (b) => (b < 1e6 ? `${mil(Math.max(1, b / 1e3))} KB` : `${mil(b / 1e6, b < 1e7 ? 1 : 0)} MB`);
  const duracion = (s) => (s < 60 ? `${Math.max(1, Math.round(s))} s` : `${mil(s / 60, s < 600 ? 1 : 0)} min`);
  const enCm = (m) => (m < 1 ? `${mil(m * 100, Math.abs(m * 100 - Math.round(m * 100)) > 1e-6 ? 1 : 0)} cm` : `${mil(m, 2)} m`);
  const numero = (v) => parseFloat(String(v || "").replace(",", "."));
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));

  class ErrorSat extends Error {}

  // ── Proveedor (ArcGIS MapServer REST: /tile/z/y/x, /tilemap/z/y/x/w/h, ?f=json) ─────────────────────────────────────────────────
  let bajados = 0;
  function urlDe(ruta) {
    const u = `${cfg.servicio}${ruta}`;
    return cfg.clave ? `${u}${u.includes("?") ? "&" : "?"}token=${encodeURIComponent(cfg.clave)}` : u;
  }
  // `referrerPolicy: "origin"`: la clave del proveedor se restringe a nuestro sitio por el Referer, y la página va con «same-origin».
  const pedirProveedor = (ruta, senal) => fetch(urlDe(ruta), { signal: senal, credentials: "omit", referrerPolicy: "origin", mode: "cors" });

  async function infoServicio() {
    if (est.info) return;
    est.info = true;
    try {
      const r = await pedirProveedor("?f=json");
      const d = await r.json();
      if (d && typeof d.copyrightText === "string" && d.copyrightText.trim()) est.atribucion = `Powered by Esri · ${d.copyrightText.trim()}`;
    } catch (_) { /* se queda la atribución por omisión */ }
    pintarFuente();
  }

  /** Las teselas disponibles del nivel z en el rango (Set de "x/y"), del mapa de teselas del proveedor; null si no se sabe. */
  async function disponibles(z, { tx0, tx1, ty0, ty1 }) {
    const clave = `${z}:${tx0}:${tx1}:${ty0}:${ty1}`;
    if (est.disp.has(clave)) return est.disp.get(clave);
    const hay = new Set();
    let ok = true;
    const LADO = 32, trozos = [];
    for (let y = ty0; y <= ty1; y += LADO) for (let x = tx0; x <= tx1; x += LADO) trozos.push([x, y, Math.min(LADO, tx1 - x + 1), Math.min(LADO, ty1 - y + 1)]);
    if (trozos.length > 24) ok = false;               // un área enorme: no se pregunta (se sabrá al bajar)
    for (const [x, y, w, h] of ok ? trozos : []) {
      try {
        const r = await pedirProveedor(`/tilemap/${z}/${y}/${x}/${w}/${h}`);
        if (!r.ok) { ok = false; break; }
        const d = await r.json();
        if (!d || !Array.isArray(d.data)) { ok = false; break; }
        const loc = d.location || { left: x, top: y, width: w, height: h };
        for (let j = 0; j < loc.height; j++) for (let i = 0; i < loc.width; i++) if (d.data[j * loc.width + i]) hay.add(`${loc.left + i}/${loc.top + j}`);
      } catch (_) { ok = false; break; }
    }
    const res = ok ? hay : null;
    est.disp.set(clave, res);
    return res;
  }
  const cobertura = (hay, r) => (hay ? [...Array(r.ty1 - r.ty0 + 1).keys()].reduce((s, j) => s + [...Array(r.tx1 - r.tx0 + 1).keys()].filter((i) => hay.has(`${r.tx0 + i}/${r.ty0 + j}`)).length, 0) / r.n : null);

  // ── La hoja ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  const hoja = () => $("civil-hoja-satelite");
  let enganchada = false;

  function abrir() {
    if (!hoja()) return avisar("La imagen satelital no está disponible en esta página.");
    if (!window.TVCad.estado().editable) return avisar("Para dibujar, toma el control del plano.");
    if (!enganchada) enganchar();
    if (!est.atribucion) est.atribucion = textos().atribucion;
    civil().abrirHoja("civil-hoja-satelite");
    if (!$("civil-sat-nombre").value) $("civil-sat-nombre").value = nombrePorOmision();
    pintarDonde();
    pintarFuente();
    if (!cfg.clave) {
      pintarPlan([{ t: "La imagen satelital aún no está activada en este servidor: falta la clave del proveedor.", tipo: "error" }]);
      $("civil-sat-ir").disabled = true;
      return;
    }
    infoServicio();
    recalcular();
  }

  function nombrePorOmision() {
    const d = new Date(), dos = (n) => String(n).padStart(2, "0");
    return `Satelital ${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`;
  }

  function enganchar() {
    enganchada = true;
    const f = $("civil-sat-form");
    $("civil-sat-polilinea").addEventListener("click", () => elegirPolilinea().catch(errorDe));
    $("civil-sat-esquinas").addEventListener("click", () => elegirEsquinas().catch(errorDe));
    $("civil-sat-vincular").addEventListener("click", async () => {
      try { await window.TVCadLocal.vincular(); } catch (err) { errorDe(err); }
      pintarDonde();
    });
    f.addEventListener("change", (ev) => {
      if (ev.target.name === "calidad") $("civil-sat-otra-campo").hidden = ev.target.value !== "otra";
      if (["calidad", "otra", "margen"].includes(ev.target.name)) recalcular();
    });
    f.addEventListener("input", (ev) => { if (["otra", "margen"].includes(ev.target.name)) recalcular(true); });
    f.addEventListener("submit", (ev) => { ev.preventDefault(); ir().catch(errorDe); });
    $("civil-sat-detener").addEventListener("click", detener);
    // Cerrar la hoja (Cancelar, Esc) mientras baja: se detiene. (Las elecciones en el dibujo la cierran y reabren solas.)
    hoja().addEventListener("close", () => { if (est.trabajo && !est.trabajo.eligiendo) detener(); });
  }

  function errorDe(err) {
    const C = civil();
    const m = err instanceof ErrorSat || (C && err instanceof C.ErrorCivil) ? err.message : "Algo salió mal. Inténtalo de nuevo.";
    if (!(err instanceof ErrorSat) && !(C && err instanceof C.ErrorCivil)) console.error(err);
    decir("");
    avisar(m, "error");
  }
  function decir(t) { const el = $("civil-sat-estado"); el.hidden = !t; el.textContent = t || ""; }

  function pintarFuente() {
    $("civil-sat-fuente").textContent = `Imagen de ${textos().proveedor}. La atribución queda en la imagen y en un archivo junto a ella; no la quites al usar la imagen.`;
  }

  function carpetas() { return window.TVCadLocal && window.TVCadLocal.carpetas ? window.TVCadLocal.carpetas() : []; }
  const puedeEscribir = () => !!(window.showDirectoryPicker && carpetas().length && carpetas()[0].getDirectoryHandle);

  function pintarDonde() {
    const n = ($("civil-sat-nombre").value || nombrePorOmision()).trim();
    if (puedeEscribir()) {
      $("civil-sat-donde").textContent = `Se guarda en la carpeta del proyecto: ${CARPETA}\\${n}.jpg (con su archivo de mundo y la atribución). Nunca se sube al servidor.`;
      $("civil-sat-vincular").hidden = true;
    } else {
      const recordar = !!window.showDirectoryPicker;
      $("civil-sat-donde").textContent = recordar
        ? "Sin carpeta del proyecto: la imagen se descarga a esta computadora y se inserta para esta sesión. Vincula la carpeta para guardarla junto al proyecto."
        : "En este navegador la imagen se descarga (con su archivo de mundo) y se inserta para esta sesión. Ponla en la carpeta del proyecto para volver a verla.";
      $("civil-sat-vincular").hidden = !recordar;
    }
  }

  // ── El área ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  /** Los vértices de la polilínea, con sus arcos en cuerdas (un recorte de imagen es un polígono). */
  function verticesDe(d) {
    const v = d.vertices.map((q) => [q[0], q[1]]), b = d.combas || [];
    let n = v.length;
    if (n > 2 && v[0][0] === v[n - 1][0] && v[0][1] === v[n - 1][1]) { v.pop(); n--; }
    const out = [];
    for (let i = 0; i < n; i++) {
      const p = v[i], q = v[(i + 1) % n], c = b[i] || 0;
      out.push(p);
      if (!c || (!d.cerrada && i === n - 1)) continue;
      // arco: ángulo incluido 4·atan(comba), 16 cuerdas por semicírculo
      const th = 4 * Math.atan(c), dx = q[0] - p[0], dy = q[1] - p[1], L = Math.hypot(dx, dy);
      if (L < 1e-9) continue;
      const r = L / (2 * Math.sin(th / 2)), h = r * Math.cos(th / 2);
      const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
      const cx = mx - (dy / L) * h, cy = my + (dx / L) * h;
      const a0 = Math.atan2(p[1] - cy, p[0] - cx), k = Math.max(2, Math.ceil((Math.abs(th) / Math.PI) * 16));
      for (let j = 1; j < k; j++) { const a = a0 + (th * j) / k; out.push([cx + Math.abs(r) * Math.cos(a), cy + Math.abs(r) * Math.sin(a)]); }
    }
    return out;
  }
  const cajaDe = (v) => v.reduce((a, p) => [Math.min(a[0], p[0]), Math.min(a[1], p[1]), Math.max(a[2], p[0]), Math.max(a[3], p[1])], [Infinity, Infinity, -Infinity, -Infinity]);

  async function elegirPolilinea() {
    const C = civil();
    if (est.trabajo) return;
    const e = await C.elegir({ mensaje: "Elige la polilínea cerrada del área", tipos: ["Polyline", "LwPolyline", "Polyline3D"], maximo: 1, sinResultados: true });
    if (!e) return;
    const d = C.polilinea(e[0]);
    const v0 = d.vertices;
    const cerrada = d.cerrada || (v0.length > 3 && v0[0][0] === v0[v0.length - 1][0] && v0[0][1] === v0[v0.length - 1][1]);
    if (!cerrada || v0.length < 3) throw new ErrorSat("La polilínea debe estar cerrada.");
    const vertices = verticesDe({ ...d, cerrada: true });
    const caja = cajaDe(vertices);
    est.area = { caja, vertices, texto: `Polilínea de ${vertices.length} vértices · ${mil(caja[2] - caja[0])} × ${mil(caja[3] - caja[1])} m` };
    pintarArea();
    await recalcular();
  }

  async function elegirEsquinas() {
    const C = civil();
    if (est.trabajo) return;
    const a = await C.senalar("Señala una esquina del área");
    if (!a) return;
    const b = await C.senalar("Señala la esquina opuesta");
    if (!b) return;
    const caja = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
    if (caja[2] - caja[0] < 1 || caja[3] - caja[1] < 1) throw new ErrorSat("El área es demasiado pequeña.");
    est.area = { caja, vertices: null, texto: `Rectángulo de ${mil(caja[2] - caja[0])} × ${mil(caja[3] - caja[1])} m` };
    pintarArea();
    await recalcular();
  }

  function pintarArea() {
    const el = $("civil-sat-area");
    el.textContent = est.area ? est.area.texto : "Sin elegir · una polilínea cerrada o dos esquinas";
    el.classList.toggle("civil-elegido", !!est.area);
    $("civil-sat-recortar").disabled = !(est.area && est.area.vertices);
  }

  // ── El plan y su estimación ──────────────────────────────────────────────────────────────────────────────────────────────────
  function pintarPlan(lineas) {
    const el = $("civil-sat-plan");
    el.replaceChildren(...lineas.map((l) => {
      const p = document.createElement("span");
      if (l.tipo) p.dataset.tipo = l.tipo;
      if (l.fuerte) { const s = document.createElement("strong"); s.textContent = l.fuerte; p.append(s, l.t ? ` · ${l.t}` : ""); }
      else p.textContent = l.t;
      return p;
    }));
  }

  const redondear = (m) => Math.max(0.01, Math.round(m * 100) / 100);
  /** La calidad que la persona pidió, en metros por píxel; para «Máxima disponible», null (la decide el proveedor). */
  function calidadPedida() {
    const v = $("civil-sat-calidad").value;
    if (v === "max") return null;
    if (v === "otra") {
      const cm = numero($("civil-sat-otra").value);
      if (!(cm >= 1 && cm <= 10000)) throw new ErrorSat("Escribe el píxel en centímetros (de 1 a 10 000).");
      return cm / 100;
    }
    return Number(v);
  }
  /** La calidad más fina que cabe en los topes para esa área (cm enteros hacia arriba). */
  function calidadQueCabe(plan) {
    const f = Math.max(1, Math.sqrt((plan.ancho * plan.alto) / G.TOPES.pixeles), Math.max(plan.ancho, plan.alto) / G.TOPES.lado, Math.sqrt(plan.teselas.n / G.TOPES.teselas));
    return Math.ceil(plan.metros * f * 1.02 * 100) / 100;
  }

  let temporizador = null;
  function recalcular(despues = false) {
    clearTimeout(temporizador);
    return new Promise((ok) => { temporizador = setTimeout(() => calcularPlan().then(ok, (err) => { errorDe(err); ok(); }), despues ? 350 : 0); });
  }

  async function calcularPlan() {
    const seq = ++est.seq;
    est.plan = null;
    $("civil-sat-ir").disabled = true;
    if (!cfg.clave) return;
    if (!est.area) { pintarPlan([{ t: "Elige el área: una polilínea cerrada o dos esquinas." }]); return; }
    const { zona, hemisferio } = civil().zona();
    const sur = hemisferio === "S";
    const margen = Math.max(0, numero($("civil-sat-margen").value) || 0);
    let pedida;
    try { pedida = calidadPedida(); } catch (err) { pintarPlan([{ t: err.message, tipo: "error" }]); return; }
    const base = { caja: est.area.caja, margen, zona, sur, zmax: cfg.zmax };
    const [lat] = G.utmAGeo((base.caja[0] + base.caja[2]) / 2, (base.caja[1] + base.caja[3]) / 2, zona, sur);
    if (!(Math.abs(lat) < 84) || !Number.isFinite(lat)) { pintarPlan([{ t: `El área no cae en la zona UTM ${zona} ${sur ? "sur" : "norte"} del plano. Revisa la zona en la escala de Civil.`, tipo: "error" }]); return; }
    pintarPlan([{ t: "Calculando…" }]);
    let plan, notas = [];
    if (pedida === null) {
      // Máxima disponible: el nivel más fino con imagen en al menos el 95 % del área, sin pasar de los topes.
      let elegido = null, limitada = false;
      for (let z = cfg.zmax; z >= ZMIN_MAX; z--) {
        const p = G.planear({ ...base, metros: redondear(G.resolucion(lat, z)), nivel: z });
        if (p.motivo) { limitada = true; continue; }
        const c = cobertura(await disponibles(z, p.teselas), p.teselas);
        if (seq !== est.seq) return;
        if (c === null || c >= 0.95) { elegido = p; break; }
      }
      if (!elegido) {
        // ni el nivel 15 cabe: la calidad más fina que cabe en los topes
        elegido = G.planear({ ...base, metros: calidadQueCabe(G.planear({ ...base, metros: redondear(G.resolucion(lat, ZMIN_MAX)), nivel: ZMIN_MAX })) });
      }
      plan = elegido;
      if (limitada) notas.push({ t: "Limitada por el tamaño del área: para más detalle, divide el área.", tipo: "aviso" });
    } else {
      plan = G.planear({ ...base, metros: pedida });
      if (plan.fuente_m > pedida * 1.02) notas.push({ t: `El proveedor llega hasta ≈ ${enCm(plan.fuente_m)} por píxel aquí: más fino solo agranda el archivo.`, tipo: "aviso" });
    }
    if (seq !== est.seq) return;
    const lineas = [
      { fuerte: `${mil(plan.ancho)} × ${mil(plan.alto)} px`, t: `${enCm(plan.metros)} por píxel · ${mil(plan.x1 - plan.x0)} × ${mil(plan.y1 - plan.y0)} m` },
      { t: `${mil(plan.teselas.n)} teselas (nivel ${plan.z}) · descarga ≈ ${tamano(plan.bytes_descarga)} · archivo ≈ ${tamano(plan.bytes_archivo)} · ≈ ${duracion(plan.segundos)}` },
    ];
    if (plan.motivo === "pixeles") {
      lineas.push({ t: `Demasiado grande: ${mil(plan.ancho * plan.alto / 1e6, 1)} millones de píxeles (el máximo es 40, unos 6 300 × 6 300, o 12 000 por lado). Baja la calidad a ${enCm(calidadQueCabe(plan))} o divide el área: una imagen mayor ya no cabe en la memoria del navegador.`, tipo: "error" });
    } else if (plan.motivo === "teselas") {
      lineas.push({ t: `Son ${mil(plan.teselas.n)} teselas y el máximo por imagen es ${mil(G.TOPES.teselas)}. Baja la calidad a ${enCm(calidadQueCabe(plan))} o divide el área.`, tipo: "error" });
    } else {
      const c = cobertura(await disponibles(plan.z, plan.teselas), plan.teselas);
      if (seq !== est.seq) return;
      if (c !== null && c < 0.999) notas.push({ t: c > 0 ? `El ${mil((1 - c) * 100)} % del área no tiene imagen a esta calidad: ahí va la mejor que haya, más borrosa.` : "Aquí no hay imagen a esta calidad: se usará la mejor que haya, más borrosa.", tipo: "aviso" });
    }
    pintarPlan([...lineas, ...notas]);
    if (!plan.motivo) { est.plan = plan; $("civil-sat-ir").disabled = false; }
  }

  // ── Bajar, armar, guardar e insertar ─────────────────────────────────────────────────────────────────────────────────────────
  function detener() {
    const t = est.trabajo;
    if (!t || t.detenido) return;
    t.detenido = true;
    t.control.abort();
    if (t.hilo) t.hilo.postMessage({ tipo: "cancelar" });
  }

  function progreso(f, texto) {
    const p = $("civil-sat-progreso");
    p.hidden = false; p.value = Math.max(0, Math.min(1, f));
    decir(texto);
  }

  /** A cada tesela del nivel del plan, su fuente: ella misma o, donde el proveedor no tiene ese nivel, la de un nivel más bajo. */
  async function asignar(plan) {
    const { z, teselas: r } = plan;
    const hay = await disponibles(z, r);
    const asig = new Map(), fuentes = new Set();
    const faltan = [];
    for (let ty = r.ty0; ty <= r.ty1; ty++) for (let tx = r.tx0; tx <= r.tx1; tx++) {
      if (!hay || hay.has(`${tx}/${ty}`)) { const k = `${z}/${tx}/${ty}`; asig.set(`${tx}/${ty}`, k); fuentes.add(k); }
      else faltan.push([tx, ty]);
    }
    for (let k = 1; k <= NIVELES_ATRAS && faltan.length; k++) {
      const s = 2 ** k, zz = z - k;
      const rr = { tx0: Math.floor(r.tx0 / s), tx1: Math.floor(r.tx1 / s), ty0: Math.floor(r.ty0 / s), ty1: Math.floor(r.ty1 / s) };
      rr.n = (rr.tx1 - rr.tx0 + 1) * (rr.ty1 - rr.ty0 + 1);
      const h = await disponibles(zz, rr);
      for (let i = faltan.length - 1; i >= 0; i--) {
        const [tx, ty] = faltan[i], px = Math.floor(tx / s), py = Math.floor(ty / s);
        if (!h || h.has(`${px}/${py}`)) { const kk = `${zz}/${px}/${py}`; asig.set(`${tx}/${ty}`, kk); fuentes.add(kk); faltan.splice(i, 1); }
      }
    }
    for (const [tx, ty] of faltan) asig.set(`${tx}/${ty}`, null);
    return { asig, fuentes: [...fuentes] };
  }

  async function bajar(t, fuentes, alAvanzar) {
    let hechas = 0, fallidas = 0, i = 0;
    const una = async (k) => {
      const [z, x, y] = k.split("/");
      for (let intento = 0; ; intento++) {
        if (t.detenido) return;
        let r;
        try { r = await pedirProveedor(`/tile/${z}/${y}/${x}`, t.control.signal); }
        catch (err) { if (t.detenido) return; r = null; }
        if (r && r.ok && !/json|text/.test(r.headers.get("content-type") || "")) {
          const b = await r.arrayBuffer();
          bajados += b.byteLength;
          t.hilo.postMessage({ tipo: "fuente", clave: k, bytes: b }, [b]);
          return;
        }
        if (r && (r.ok || r.status === 401 || r.status === 403 || r.status === 498 || r.status === 499)) {
          // El proveedor responde 200 con un error en JSON cuando la clave no sirve.
          let d = null; try { d = await r.json(); } catch (_) { /* nada */ }
          const c = d && d.error && d.error.code;
          if ([401, 403, 498, 499].includes(c) || [401, 403, 498, 499].includes(r.status)) throw new ErrorSat("El proveedor rechazó la clave de este servidor (no es válida, venció o no permite este sitio). Avisa a quien administra.");
          if (r.ok) { fallidas++; return; }
        }
        if (r && r.status === 404) { fallidas++; return; }
        if (intento >= REINTENTOS - 1) { fallidas++; return; }
        await espera(400 * 2 ** intento);
      }
    };
    const hilos = [...Array(Math.min(PARALELAS, fuentes.length)).keys()].map(async () => {
      while (i < fuentes.length && !t.detenido) { const k = fuentes[i++]; await una(k); hechas++; alAvanzar(hechas, fuentes.length); }
    });
    await Promise.all(hilos);
    if (!t.detenido && fallidas > Math.max(2, fuentes.length * 0.1)) throw new ErrorSat("El proveedor no respondió a tiempo. Revisa la conexión e inténtalo de nuevo.");
    return fallidas;
  }

  function armar(t, plan) {
    return new Promise((ok, mal) => {
      t.hilo.onmessage = (ev) => {
        const m = ev.data;
        if (m.progreso !== undefined && m.ok === undefined) { progreso(0.7 + 0.25 * m.progreso, `Armando la imagen… ${mil(m.progreso * 100)} %`); return; }
        if (m.ok) ok(m); else if (m.cancelado) ok(null); else mal(new ErrorSat(`No se pudo armar la imagen: ${m.error}.`));
      };
      t.hilo.onerror = (ev) => mal(new ErrorSat(`No se pudo armar la imagen: ${ev.message || "error del trabajador"}.`));
      t.hilo.postMessage({ tipo: "armar", id: 1 });
    });
  }

  function limpiarNombre(n) {
    return (n || "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim().replace(/[. ]+$/, "").slice(0, 100) || nombrePorOmision();
  }

  function textoAtribucion(plan, nombre) {
    const [la, lo] = plan.centro;
    return [
      `Imagen satelital: ${nombre}.jpg`,
      "",
      `Fuente: ${est.atribucion}`,
      `Proveedor: ${textos().proveedor}. Conserva esta atribución en todo uso de la imagen (planos impresos o PDF incluidos).`,
      "",
      `Sistema: UTM zona ${plan.zona} ${plan.sur ? "sur" : "norte"} (WGS84 / ITRF), metros. Archivo de mundo: ${nombre}.jgw.`,
      `Esquina superior izquierda: E ${plan.x0.toFixed(3)}, N ${plan.y1.toFixed(3)}. Esquina inferior derecha: E ${plan.x1.toFixed(3)}, N ${plan.y0.toFixed(3)}.`,
      `Píxel: ${plan.metros} m. Tamaño: ${plan.ancho} × ${plan.alto} px. Nivel de teselas: ${plan.z} (≈ ${plan.fuente_m.toFixed(2)} m por píxel en la fuente).`,
      `Centro: ${la.toFixed(6)}, ${lo.toFixed(6)}. Bajada el ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC.`,
      "",
    ].join("\r\n");
  }

  async function escribirEn(dir, nombre, contenido) {
    const w = await (await dir.getFileHandle(nombre, { create: true })).createWritable();
    await w.write(contenido); await w.close();
  }

  /** Guarda en la carpeta del proyecto (subcarpeta «Satelital»); un nombre que ya existe toma « (2)», « (3)»… */
  async function guardarEnCarpeta(raiz, nombre, archivos) {
    const dir = await raiz.getDirectoryHandle(CARPETA, { create: true });
    let final = nombre;
    for (let k = 2; k < 1000; k++) {
      try { await dir.getFileHandle(`${final}.jpg`); final = `${nombre} (${k})`; } catch (_) { break; }
    }
    for (const [ext, c] of archivos) await escribirEn(dir, `${final}.${ext}`, c);
    const archivo = await (await dir.getFileHandle(`${final}.jpg`)).getFile();
    return { archivo, ruta: `${CARPETA}\\${final}.jpg`, nombre: final };
  }

  function descargar(nombre, contenido) {
    const url = URL.createObjectURL(contenido instanceof Blob ? contenido : new Blob([contenido], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url; a.download = nombre; a.hidden = true;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  /** Los pasos del mismo lote que la imagen: el recorte a la polilínea (polígono del IMAGE) y la atribución en el plano. */
  function pasosDe(plan, recortar) {
    if (!recortar || !est.area || !est.area.vertices) return null;
    const r = plan.metros;
    // Espacio del recorte del IMAGE: píxeles del archivo con y hacia abajo y el CENTRO del primer píxel en (0, 0) (como image_set).
    const vertices = est.area.vertices.map(([x, y]) => ({ x: (x - plan.x0) / r - 0.5, y: (plan.y1 - y) / r - 0.5 }));
    return (enlace) => [
      { op: "set_properties", collection: "entities", bindings: { handle: enlace }, updates: [
        { path: "/clip_boundary", value: { clip_type: "Polygonal", clip_mode: "Outside", vertices } },
        { path: "/clipping_enabled", value: true },
      ] },
      atribucionEnPlano(),
    ];
  }
  /** La atribución también en el plano, bajo el área (el recorte puede dejar fuera la esquina de la imagen donde va), a 1.8 mm de papel. */
  function atribucionEnPlano() {
    const c = est.area.caja, alto = (1.8 * ((civil().escala() || {}).denominador || 1000)) / 1000;
    return { op: "entities_create", entities: [{ type: "MText", value: est.atribucion, position: [c[0], c[1] - alto * 0.6], height: alto, layer: CAPA }] };
  }

  async function ir() {
    if (est.trabajo) return;
    const plan = est.plan;
    if (!plan) return;
    if (!window.TVCad.estado().editable) throw new ErrorSat("Para dibujar, toma el control del plano.");
    if (!window.TVCadLocal || !window.TVCadLocal.insertarArchivo) throw new ErrorSat("Esta página no puede insertar imágenes.");
    const nombre = limpiarNombre($("civil-sat-nombre").value);
    // El permiso de escritura en la carpeta del proyecto se pide con el toque (el navegador lo exige así) y una sola vez.
    let raiz = null;
    if (puedeEscribir()) {
      const h = carpetas()[0];
      try {
        let p = await h.queryPermission({ mode: "readwrite" });
        if (p !== "granted") p = await h.requestPermission({ mode: "readwrite" });
        if (p === "granted") raiz = h;
      } catch (_) { raiz = null; }
      if (!raiz) avisar("Sin permiso para escribir en la carpeta del proyecto: la imagen se descarga.", "aviso");
    }
    const t = { control: new AbortController(), detenido: false, hilo: null, eligiendo: false };
    est.trabajo = t;
    window.TVCad.ocupar();
    $("civil-sat-ir").disabled = true; $("civil-sat-detener").hidden = false;
    ["civil-sat-polilinea", "civil-sat-esquinas"].forEach((id) => { $(id).disabled = true; });
    const t0 = performance.now(), bajadosAntes = bajados;
    try {
      progreso(0, "Preparando…");
      const { asig, fuentes } = await asignar(plan);
      if (t.detenido) return cancelado();
      t.hilo = new Worker(cfg.trabajador);
      t.hilo.postMessage({ tipo: "iniciar", plan, atribucion: est.atribucion, geo: cfg.geo });
      const fallidas = await bajar(t, fuentes, (n, total) => progreso((0.7 * n) / total, `Bajando la imagen… ${mil(n)} de ${mil(total)} teselas`));
      if (t.detenido) return cancelado();
      t.hilo.postMessage({ tipo: "asignar", lista: [...asig.entries()] });
      const res = await armar(t, plan);
      if (!res || t.detenido) return cancelado();
      progreso(0.96, "Guardando…");
      const mundo = G.mundo(plan), prj = G.prj(plan.zona, plan.sur), txt = textoAtribucion(plan, nombre);
      let guardado;
      if (raiz) {
        guardado = await guardarEnCarpeta(raiz, nombre, [["jpg", res.jpeg], ["jgw", mundo], ["prj", prj], ["txt", txt]]);
        guardado.enCarpeta = true;
      } else {
        descargar(`${nombre}.jpg`, res.jpeg); descargar(`${nombre}.jgw`, mundo); descargar(`${nombre}.txt`, txt);
        guardado = { archivo: new File([res.jpeg], `${nombre}.jpg`, { type: "image/jpeg", lastModified: Date.now() }), ruta: `${nombre}.jpg`, nombre, enCarpeta: false };
      }
      progreso(0.98, "Insertando…");
      const recortar = $("civil-sat-recortar").checked && !$("civil-sat-recortar").disabled;
      const comun = { archivo: guardado.archivo, ruta: guardado.ruta, mundo, enCarpeta: guardado.enCarpeta, capa: CAPA, atras: $("civil-sat-atras").checked, etiqueta: "Imagen satelital" };
      let insertada, recorte = recortar ? "poligono" : null;
      try {
        insertada = await window.TVCadLocal.insertarArchivo({ ...comun, pasos: pasosDe(plan, recortar) });
      } catch (err) {
        if (!recortar) throw err;
        // Un editor que no acepta el recorte poligonal: el rectángulo de la polilínea (el lote se deshizo entero).
        const c = est.area.caja, r = plan.metros;
        recorte = "rectangulo";
        insertada = await window.TVCadLocal.insertarArchivo({ ...comun, pasos: (enlace) => [
          { op: "image_set", bindings: { handle: enlace }, clip_rect: [(c[0] - plan.x0) / r, (plan.y1 - c[3]) / r, (c[2] - plan.x0) / r, (plan.y1 - c[1]) / r] },
          atribucionEnPlano(),
        ] });
      }
      // Que se vea entera.
      try {
        const [w, h] = insertada.size;
        await window.TVCad.pedir({ op: "view_set", center: [insertada.insert[0] + (insertada.u[0] * w + insertada.v[0] * h) / 2, insertada.insert[1] + (insertada.u[1] * w + insertada.v[1] * h) / 2], height: 1.1 * Math.max(plan.y1 - plan.y0, (plan.x1 - plan.x0) * 0.6) });
      } catch (_) { /* la vista se queda */ }
      est.ultimo = { ruta: guardado.ruta, enCarpeta: guardado.enCarpeta, ancho: res.ancho, alto: res.alto, faltantes: res.faltantes + fallidas, recorte, handle: insertada.handle,
        segundos: (performance.now() - t0) / 1000, bajados: bajados - bajadosAntes, plan };
      const fin = guardado.enCarpeta ? `Guardada en la carpeta del proyecto: ${guardado.ruta}.` : `Descargada como ${guardado.nombre}.jpg (con su .jgw): ponla en la carpeta del proyecto para volver a verla.`;
      hoja().close();
      avisar(`Imagen satelital insertada (${mil(res.ancho)} × ${mil(res.alto)} px, ${enCm(plan.metros)} por píxel). ${fin}` + (recorte === "rectangulo" ? " El editor la recortó al rectángulo de la polilínea." : ""), "exito");
    } finally {
      if (t.hilo) t.hilo.terminate();
      est.trabajo = null;
      window.TVCad.soltar();
      $("civil-sat-detener").hidden = true; $("civil-sat-progreso").hidden = true;
      ["civil-sat-polilinea", "civil-sat-esquinas"].forEach((id) => { $(id).disabled = false; });
      $("civil-sat-ir").disabled = !est.plan;
      decir("");
      pintarDonde();
    }
    function cancelado() { decir(""); avisar("Se detuvo. No se guardó ni se insertó nada.", "info"); }
  }

  // «Más acciones» → «Imagen satelital…» (la barra de la página no lleva el grupo «Imágenes»; la cinta «Civil» del editor sí).
  const boton = $("cad-menu-imagen-satelite");
  if (boton) boton.addEventListener("click", () => {
    const m = $("cad-menu"); if (m) m.open = false;
    if (!civil()) return avisar("Civil aún se está cargando. Inténtalo en un momento.");
    abrir();
  });

  // La cinta «Civil» del editor (grupo «Imágenes», acción `satelite` de civil/pagina.py MENUS): la acción se registra en Civil, que puede
  // llegar después (civil-panel.js carga civil.js aparte); hasta entonces el botón se ve como no disponible.
  function registrar() {
    const c = civil();
    if (c && typeof c.registrarAccion === "function" && !c.tieneAccion("satelite")) c.registrarAccion("satelite", abrir);
  }
  registrar();
  document.addEventListener("tvcivil:listo", registrar);

  window.TVCadSatelite = Object.freeze({
    abrir,
    // Para las pruebas y el diagnóstico (nada de esto sale de la computadora).
    estado: () => ({ area: est.area && { caja: est.area.caja, vertices: est.area.vertices && est.area.vertices.length }, plan: est.plan, ultimo: est.ultimo || null,
      corriendo: !!est.trabajo, bajados, atribucion: est.atribucion }),
  });
})();

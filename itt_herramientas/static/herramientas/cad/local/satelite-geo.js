/* «Imagen satelital desde una polilínea»: la geometría, sin nada de la página (la usan satelite.js, su trabajador y las pruebas en Node).
 *
 *   - UTM ↔ geográficas: Transversa de Mercator con las series de Krüger a cuarto orden (Karney 2011, «Transverse Mercator with an
 *     accuracy of a few nanometers»), elipsoide GRS80 como Civil (docs/civil.md, «Geodesia»): error < 0.1 mm dentro de la zona;
 *   - Web Mercator (EPSG:3857, teselas de 256 px): píxel global ↔ geográficas, y la resolución en el terreno de cada nivel;
 *   - el plan de una imagen: extensión en la cuadrícula UTM del plano, tamaño en píxeles, nivel de teselas, teselas y estimaciones;
 *   - la malla gruesa de la reproyección (UTM → píxel de Mercator, exacta en sus nodos e interpolada entre ellos);
 *   - el archivo de mundo y el .prj de la imagen.
 */
(function (raiz) {
  "use strict";

  const A_ELIP = 6378137, F_ELIP = 1 / 298.257222101, K0 = 0.9996;
  const N3 = F_ELIP / (2 - F_ELIP);
  const n = N3, n2 = n * n, n3 = n2 * n, n4 = n3 * n;
  const A_RECT = (A_ELIP / (1 + n)) * (1 + n2 / 4 + n4 / 64);
  const ALFA = [n / 2 - (2 * n2) / 3 + (5 * n3) / 16 + (41 * n4) / 180, (13 * n2) / 48 - (3 * n3) / 5 + (557 * n4) / 1440, (61 * n3) / 240 - (103 * n4) / 140, (49561 * n4) / 161280];
  const BETA = [n / 2 - (2 * n2) / 3 + (37 * n3) / 96 - n4 / 360, n2 / 48 + n3 / 15 - (437 * n4) / 1440, (17 * n3) / 480 - (37 * n4) / 840, (4397 * n4) / 161280];
  const DELTA = [2 * n - (2 * n2) / 3 - 2 * n3 + (116 * n4) / 45, (7 * n2) / 3 - (8 * n3) / 5 - (227 * n4) / 45, (56 * n3) / 15 - (136 * n4) / 35, (4279 * n4) / 630];
  const EXC = (2 * Math.sqrt(n)) / (1 + n);
  const RAD = Math.PI / 180;

  const meridiano = (zona) => (zona * 6 - 183) * RAD;

  /** Geográficas (grados) → UTM [E, N] de `zona` (1–60); `sur`: hemisferio sur (falso norte 10 000 km). */
  function geoAUtm(lat, lon, zona, sur = false) {
    const phi = lat * RAD, dl = lon * RAD - meridiano(zona);
    const s = Math.sin(phi);
    const t = Math.sinh(Math.atanh(s) - EXC * Math.atanh(EXC * s));
    const xi = Math.atan2(t, Math.cos(dl)), eta = Math.atanh(Math.sin(dl) / Math.sqrt(1 + t * t));
    let x = eta, y = xi;
    for (let j = 1; j <= 4; j++) {
      x += ALFA[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
      y += ALFA[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    }
    return [500000 + K0 * A_RECT * x, (sur ? 10000000 : 0) + K0 * A_RECT * y];
  }

  /** UTM [E, N] de `zona` → geográficas [lat, lon] en grados. */
  function utmAGeo(e, nn, zona, sur = false) {
    const xi = (nn - (sur ? 10000000 : 0)) / (K0 * A_RECT), eta = (e - 500000) / (K0 * A_RECT);
    let xp = xi, ep = eta;
    for (let j = 1; j <= 4; j++) {
      xp -= BETA[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
      ep -= BETA[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
    }
    const chi = Math.asin(Math.sin(xp) / Math.cosh(ep));
    let phi = chi;
    for (let j = 1; j <= 4; j++) phi += DELTA[j - 1] * Math.sin(2 * j * chi);
    const lon = meridiano(zona) + Math.atan2(Math.sinh(ep), Math.cos(xp));
    return [phi / RAD, lon / RAD];
  }

  // ── Web Mercator (EPSG:3857) ───────────────────────────────────────────────────────────────────────────────────────────────────
  const TESELA = 256;
  const R_MERC = 6378137;
  const LAT_MAX = 85.0511287798066;
  /** Píxel global [X, Y] del nivel z (el mundo mide 256·2^z px; Y hacia abajo; el píxel i va de i a i+1). */
  function geoAMercator(lat, lon, z) {
    const m = TESELA * 2 ** z, phi = Math.max(-LAT_MAX, Math.min(LAT_MAX, lat)) * RAD;
    return [((lon + 180) / 360) * m, (0.5 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / (2 * Math.PI)) * m];
  }
  function mercatorAGeo(x, y, z) {
    const m = TESELA * 2 ** z;
    return [(2 * Math.atan(Math.exp((0.5 - y / m) * 2 * Math.PI)) - Math.PI / 2) / RAD, (x / m) * 360 - 180];
  }
  /** Metros de terreno por píxel del nivel z a la latitud `lat` (Web Mercator: la esfera de 6 378 137 m por el coseno). */
  const resolucion = (lat, z) => (2 * Math.PI * R_MERC * Math.cos(lat * RAD)) / (TESELA * 2 ** z);
  /** El nivel más bajo cuyo píxel es igual o más fino que `metros` (así nunca se amplía la fuente para llegar a la calidad pedida). */
  const nivelPara = (lat, metros, zmin = 1, zmax = 21) =>
    Math.max(zmin, Math.min(zmax, Math.ceil(Math.log2((2 * Math.PI * R_MERC * Math.cos(lat * RAD)) / (TESELA * metros)) - 1e-9)));

  // ── El plan ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  /** Topes, con su motivo (satelite.js los dice tal cual). */
  const TOPES = {
    pixeles: 40e6,          // 40 millones de píxeles (≈ 6 300 × 6 300): lo que el navegador arma y codifica sin quedarse sin memoria
    lado: 12000,            // px por lado
    teselas: 2500,          // peticiones al proveedor por imagen
  };
  const BYTES_TESELA = 22000, BYTES_PIXEL = 0.32, TESELAS_POR_S = 30, PIXELES_POR_S = 5e6;

  /**
   * El plan de una imagen. `caja` [xmin, ymin, xmax, ymax] en metros de la zona; `metros` la calidad (m/px); `nivel`: forzado (si no, el
   * de la calidad). La extensión se ajusta a múltiplos del píxel: las esquinas caen en números redondos de la cuadrícula del plano.
   */
  function planear({ caja, margen = 0, metros, zona, sur = false, nivel = null, zmax = 21 }) {
    if (!(metros > 0)) throw new Error("La calidad debe ser mayor que cero.");
    const x0 = Math.floor((caja[0] - margen) / metros) * metros, y0 = Math.floor((caja[1] - margen) / metros) * metros;
    const x1 = Math.ceil((caja[2] + margen) / metros) * metros, y1 = Math.ceil((caja[3] + margen) / metros) * metros;
    const ancho = Math.max(1, Math.round((x1 - x0) / metros)), alto = Math.max(1, Math.round((y1 - y0) / metros));
    const centro = utmAGeo((x0 + x1) / 2, (y0 + y1) / 2, zona, sur);
    const z = nivel !== null ? nivel : nivelPara(centro[0], metros, 1, zmax);
    const teselas = teselasDe({ x0, y0, x1, y1, zona, sur }, z);
    const n = teselas.n;
    const px = ancho * alto;
    const plan = {
      x0, y0, x1, y1, ancho, alto, metros, zona, sur, z, teselas, centro,
      fuente_m: resolucion(centro[0], z),
      bytes_descarga: n * BYTES_TESELA, bytes_archivo: Math.round(px * BYTES_PIXEL),
      segundos: Math.ceil(n / TESELAS_POR_S + px / PIXELES_POR_S + 1),
      motivo: null,
    };
    if (px > TOPES.pixeles || ancho > TOPES.lado || alto > TOPES.lado) plan.motivo = "pixeles";
    else if (n > TOPES.teselas) plan.motivo = "teselas";
    return plan;
  }

  /** Las teselas del nivel z que cubren el rectángulo UTM (su contorno, muestreado, pasado a Mercator; +1 px por el remuestreo). */
  function teselasDe({ x0, y0, x1, y1, zona, sur }, z) {
    let mx0 = Infinity, my0 = Infinity, mx1 = -Infinity, my1 = -Infinity;
    const PASOS = 64;
    for (let i = 0; i <= PASOS; i++) {
      const f = i / PASOS;
      for (const [e, nn] of [[x0 + f * (x1 - x0), y0], [x0 + f * (x1 - x0), y1], [x0, y0 + f * (y1 - y0)], [x1, y0 + f * (y1 - y0)]]) {
        const [la, lo] = utmAGeo(e, nn, zona, sur);
        const [mx, my] = geoAMercator(la, lo, z);
        if (mx < mx0) mx0 = mx; if (mx > mx1) mx1 = mx;
        if (my < my0) my0 = my; if (my > my1) my1 = my;
      }
    }
    const ultima = 2 ** z - 1;
    const tx0 = Math.max(0, Math.floor((mx0 - 1) / TESELA)), tx1 = Math.min(ultima, Math.floor((mx1 + 1) / TESELA));
    const ty0 = Math.max(0, Math.floor((my0 - 1) / TESELA)), ty1 = Math.min(ultima, Math.floor((my1 + 1) / TESELA));
    return { tx0, tx1, ty0, ty1, n: (tx1 - tx0 + 1) * (ty1 - ty0 + 1) };
  }

  // ── La reproyección ────────────────────────────────────────────────────────────────────────────────────────────────────────────
  const PASO_MALLA = 32;
  /**
   * La malla gruesa: el píxel global de Mercator (nivel z) del centro de los píxeles de salida (i + ½, j + ½) cada PASO_MALLA px, exacta
   * (UTM → geográficas → Mercator). Entre nodos se interpola: la transformación es tan suave que el error es de milésimas de píxel
   * (lo mide la prueba), y así se calcula una vez cada mil píxeles en vez de en cada uno.
   */
  function malla(plan) {
    const { ancho, alto, x0, y1, metros, zona, sur, z } = plan;
    const cols = Math.ceil(ancho / PASO_MALLA) + 1, filas = Math.ceil(alto / PASO_MALLA) + 1;
    const mx = new Float64Array(cols * filas), my = new Float64Array(cols * filas);
    for (let r = 0; r < filas; r++) {
      const j = Math.min(r * PASO_MALLA, alto) + 0.5;
      for (let c = 0; c < cols; c++) {
        const i = Math.min(c * PASO_MALLA, ancho) + 0.5;
        const [la, lo] = utmAGeo(x0 + i * metros, y1 - j * metros, zona, sur);
        const [X, Y] = geoAMercator(la, lo, z);
        mx[r * cols + c] = X; my[r * cols + c] = Y;
      }
    }
    return { cols, filas, mx, my, paso: PASO_MALLA, ancho, alto };
  }
  /** El píxel de Mercator del centro del píxel de salida (i, j), interpolado en la malla. */
  function enMalla(m, i, j) {
    const ci = Math.min(Math.floor(i / m.paso), m.cols - 2), rj = Math.min(Math.floor(j / m.paso), m.filas - 2);
    const ia = ci * m.paso, ib = Math.min((ci + 1) * m.paso, m.ancho), ja = rj * m.paso, jb = Math.min((rj + 1) * m.paso, m.alto);
    const u = ib > ia ? (i - ia) / (ib - ia) : 0, v = jb > ja ? (j - ja) / (jb - ja) : 0;
    const k = rj * m.cols + ci;
    const b = (a) => (a[k] * (1 - u) + a[k + 1] * u) * (1 - v) + (a[k + m.cols] * (1 - u) + a[k + m.cols + 1] * u) * v;
    return [b(m.mx), b(m.my)];
  }

  // ── Archivos junto a la imagen ─────────────────────────────────────────────────────────────────────────────────────────────────
  /** Archivo de mundo (ESRI): A, D, B, E, C, F; C y F son el CENTRO del píxel superior izquierdo. Sin giro: la imagen va en la cuadrícula. */
  function mundo(plan) {
    const r = plan.metros, f = (v) => v.toFixed(6);
    return [f(r), f(0), f(0), f(-r), f(plan.x0 + r / 2), f(plan.y1 - r / 2)].join("\r\n") + "\r\n";
  }
  /** .prj (WKT de ESRI) de la zona: AutoCAD Map 3D, QGIS y ArcGIS lo leen. El datum de las teselas es WGS84 (≈ ITRF; con GRS80, < 0.1 mm). */
  function prj(zona, sur = false) {
    const lon0 = zona * 6 - 183;
    return `PROJCS["WGS_1984_UTM_Zone_${zona}${sur ? "S" : "N"}",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],` +
      `PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],` +
      `PARAMETER["False_Northing",${sur ? "10000000.0" : "0.0"}],PARAMETER["Central_Meridian",${lon0}.0],PARAMETER["Scale_Factor",0.9996],` +
      `PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]`;
  }

  const api = { geoAUtm, utmAGeo, geoAMercator, mercatorAGeo, resolucion, nivelPara, planear, teselasDe, malla, enMalla, mundo, prj, TOPES, TESELA, PASO_MALLA };
  if (typeof module === "object" && module.exports) module.exports = api;
  else raiz.TVSateliteGeo = Object.freeze(api);
})(typeof self !== "undefined" ? self : this);

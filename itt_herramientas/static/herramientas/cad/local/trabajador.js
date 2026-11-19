/* Trabajador de «Carpeta del proyecto» (CAD): lee y decodifica imágenes de la computadora fuera del hilo de la página.
 *
 * Mensajes (cada uno con `id`; responde `{id, ok, …}` o `{id, ok: false, error}`):
 *   {tipo: "iniciar", tiff}                         carga el lector TIFF (su dirección viene de la plantilla: nombres con huella).
 *   {tipo: "info", archivo}                         tamaño, formato, niveles y georreferencia; si conviene una pirámide local.
 *   {tipo: "region", archivo, rect, ancho, alto, factor?}
 *                                                   RGBA de la ventana `rect` [x0, y0, x1, y1] (píxeles del archivo) a ancho × alto.
 *                                                   Con `factor` (un mosaico del editor: 2^nivel píxeles del archivo por píxel), solo
 *                                                   de una fuente al menos así de fina y que cueste poco leer: si no la hay todavía
 *                                                   (la pirámide no está), `pendiente: true`, nunca algo más borroso. Sin `factor`,
 *                                                   `parcial: true` si salió de una vista más gruesa. `cancelada: true` si se cortó.
 *   {tipo: "piramide", archivo, teselas?}           arma la pirámide local (OPFS), avisa `{id, progreso, teselas?}` y termina
 *                                                   `{id, ok}`. Con `teselas` (la rejilla del editor y su nivel `desde`), cada aviso
 *                                                   trae los mosaicos de los niveles `desde`…raíz que cambiaron, sacados de la vista
 *                                                   general que se va llenando. Se puede cortar (`{tipo: "cancelar", objetivo}`) y se
 *                                                   retoma donde quedó; `cancelar` corta también una `region` en curso.
 *   {tipo: "borrar_piramides"}                      borra todas las pirámides de este navegador.
 *
 * Nada sale de la computadora: el trabajador lee `File`s locales y escribe solo en el almacenamiento privado del navegador (OPFS).
 */
"use strict";

const LADO = 256;                 // mosaicos de la pirámide local
const VISTA = 2048;               // lado mayor de la vista general que se guarda aparte
const LIMITE_LECTURA = 48e6;      // píxeles del original que una petición puede leer
const LIMITE_BYTES = 256e6;       // bytes del archivo que una petición puede leer
const UMBRAL_PIRAMIDE = 64e6;     // píxeles a partir de los cuales un archivo sin vistas generales necesita su pirámide
const UMBRAL_IMAGEN = 16e6;       // JPEG/PNG más grandes también la arman
const DIR = "tv-cad-piramides";

const abiertos = new Map();       // clave → {tipo, tif?, ancho, alto, piramide?}
const bloques = new Map();        // LRU de bloques TIFF decodificados: clave → {rgba, ancho, filas}
let bytesBloques = 0;
const MAX_BLOQUES = 128 * 1024 * 1024; // por trabajador
const cancelados = new Set();

const claveDe = (f) => `${f.name}|${f.size}|${f.lastModified}`;

function responder(id, datos, transferir) { postMessage({ id, ...datos }, transferir || []); }

self.onmessage = async (ev) => {
  const m = ev.data || {};
  try {
    switch (m.tipo) {
      case "iniciar": importScripts(m.tiff); return responder(m.id, { ok: true });
      case "info": return responder(m.id, { ok: true, ...(await info(m.archivo)) });
      case "region": {
        const r = await region(m.archivo, m.rect, m.ancho, m.alto, m.factor, m.id);
        cancelados.delete(m.id);
        return responder(m.id, { ok: true, ...r }, r.rgba ? [r.rgba.buffer] : []);
      }
      case "piramide": return responder(m.id, { ok: true, ...(await piramide(m.archivo, m.id, m.teselas)) });
      case "cancelar":
        if (cancelados.size > 4000) cancelados.clear();
        cancelados.add(m.objetivo);
        return; // sin respuesta: el id -1 no espera nada
      case "borrar_piramides": {
        const raiz = await navigator.storage.getDirectory();
        try { await raiz.removeEntry(DIR, { recursive: true }); } catch (_) { /* no había */ }
        abiertos.clear();
        return responder(m.id, { ok: true });
      }
      default: return responder(m.id, { ok: false, error: "mensaje desconocido" });
    }
  } catch (err) {
    responder(m.id, { ok: false, error: String((err && err.message) || err) });
  }
};

// ── Encabezados de imagen ───────────────────────────────────────────────────────────────────────────────────────────────────────

async function cabecera(archivo) {
  const b = new Uint8Array(await archivo.slice(0, 65536).arrayBuffer());
  const d = new DataView(b.buffer);
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) {
    const alfa = [4, 6].includes(b[25]) || false;
    return { formato: "png", ancho: d.getUint32(16), alto: d.getUint32(20), alfa };
  }
  if (b[0] === 0xff && b[1] === 0xd8) {
    let i = 2;
    while (i + 9 < b.length) {
      if (b[i] !== 0xff) { i++; continue; }
      const marca = b[i + 1];
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marca)) {
        return { formato: "jpeg", alto: d.getUint16(i + 5), ancho: d.getUint16(i + 7), alfa: false };
      }
      if (marca === 0xd8 || marca === 0x01 || (marca >= 0xd0 && marca <= 0xd7)) { i += 2; continue; }
      i += 2 + d.getUint16(i + 2);
    }
    // SOF más allá de los primeros 64 KB (miniatura EXIF grande): que lo diga el decodificador.
    const bmp = await createImageBitmap(archivo);
    const r = { formato: "jpeg", ancho: bmp.width, alto: bmp.height, alfa: false };
    bmp.close();
    return r;
  }
  if ((b[0] === 0x49 && b[1] === 0x49) || (b[0] === 0x4d && b[1] === 0x4d)) return { formato: "tiff" };
  if (b[0] === 0x42 && b[1] === 0x4d) return { formato: "bmp", ancho: d.getInt32(18, true), alto: Math.abs(d.getInt32(22, true)), alfa: false };
  if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return { formato: "gif", ancho: d.getUint16(6, true), alto: d.getUint16(8, true), alfa: true };
  if (b[0] === 0x52 && b[1] === 0x49 && b[8] === 0x57 && b[9] === 0x45) {
    const tipo = String.fromCharCode(b[12], b[13], b[14], b[15]);
    if (tipo === "VP8X") return { formato: "webp", ancho: 1 + (b[24] | (b[25] << 8) | (b[26] << 16)), alto: 1 + (b[27] | (b[28] << 8) | (b[29] << 16)), alfa: !!(b[20] & 16) };
    if (tipo === "VP8L") { const v = d.getUint32(21, true); return { formato: "webp", ancho: (v & 0x3fff) + 1, alto: ((v >> 14) & 0x3fff) + 1, alfa: true }; }
    if (tipo === "VP8 ") return { formato: "webp", ancho: d.getUint16(26, true) & 0x3fff, alto: d.getUint16(28, true) & 0x3fff, alfa: false };
  }
  throw new Error("Formato de imagen no reconocido");
}

async function abrir(archivo) {
  const clave = claveDe(archivo);
  let a = abiertos.get(clave);
  if (a) { a.archivo = archivo; return a; }
  const c = await cabecera(archivo);
  if (c.formato === "tiff") {
    const tif = await TVTiff.abrir(archivo);
    a = { clave, archivo, tipo: "tiff", tif, ancho: tif.ancho, alto: tif.alto, formato: "tiff", motivo: tif.motivo, geo: tif.geo,
          alfa: tif.niveles[0].alfa >= 0 || tif.niveles[0].spp === 2 };
  } else {
    a = { clave, archivo, tipo: "imagen", ancho: c.ancho, alto: c.alto, formato: c.formato, alfa: c.alfa, motivo: null, geo: null };
    if (c.ancho * c.alto > 268e6) a.motivo = "imagen de más de 268 millones de píxeles: conviértela a GeoTIFF en mosaicos";
  }
  a.piramide = await leerManifiesto(clave);
  abiertos.set(clave, a);
  return a;
}

function necesitaPiramide(a) {
  if (a.motivo) return false;
  if (a.tipo === "tiff") {
    const finos = a.tif.niveles;
    const grueso = finos[finos.length - 1];
    // Con vistas internas hasta ~2048 px no hace falta.
    return a.ancho * a.alto > UMBRAL_PIRAMIDE && Math.max(grueso.ancho, grueso.alto) > VISTA * 2;
  }
  return a.ancho * a.alto > UMBRAL_IMAGEN;
}

async function info(archivo) {
  const a = await abrir(archivo);
  const lista = a.tipo === "tiff" ? a.tif.niveles.map((n) => ({ ancho: n.ancho, alto: n.alto, mosaico: n.mosaico ? [n.tw, n.th] : null,
    filas_tira: n.mosaico ? null : n.th, compresion: n.compresion })) : [];
  return {
    formato: a.formato, ancho: a.ancho, alto: a.alto, alfa: a.alfa, motivo: a.motivo, geo: a.geo, niveles: lista, bytes: archivo.size,
    piramide: necesitaPiramide(a) ? (a.piramide && a.piramide.lista ? "lista" : "falta") : "no_hace_falta",
  };
}

// ── Regiones ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

/** Fuentes posibles de una región, cada una con su factor (píxeles del original por píxel de la fuente). */
function fuentes(a) {
  const out = [];
  if (a.tipo === "tiff" && !a.motivo) for (const n of a.tif.niveles) out.push({ tipo: "tiff", nivel: n, factor: n.factor });
  if (a.tipo === "imagen") out.push({ tipo: "imagen", factor: 1 });
  const p = a.piramide;
  if (p) {
    for (const n of p.niveles) if (n.lista) out.push({ tipo: "piramide", nivel: n, factor: n.factor });
    if (p.vista) out.push({ tipo: "vista", factor: p.vista.factor });
  }
  return out.sort((x, y) => x.factor - y.factor);
}

class Cancelada extends Error {}

async function region(archivo, rect, ancho, alto, factor, id) {
  const a = await abrir(archivo);
  if (a.motivo) return { pendiente: true, motivo: a.motivo };
  const [x0, y0, x1, y1] = rect;
  const estricto = factor > 0;
  const necesario = estricto ? factor : Math.min((x1 - x0) / ancho, (y1 - y0) / alto);
  const lista = fuentes(a);
  // La más gruesa que todavía da la resolución pedida; si leerla es demasiado, la siguiente más gruesa.
  let elegida = null, parcial = false;
  const finas = lista.filter((f) => f.factor <= necesario * 1.001);
  const costoDe = (f) => ((x1 - x0) / f.factor) * ((y1 - y0) / f.factor);
  // Demasiado para una petición: muchos píxeles, o (tiras anchas) muchos bytes del archivo para una ventana chica.
  const pesado = (f) => (f.tipo === "tiff" || f.tipo === "imagen") && (costoDe(f) > LIMITE_LECTURA
    || (f.tipo === "tiff" && TVTiff.costo(f.nivel, x0 / f.factor, y0 / f.factor, x1 / f.factor, y1 / f.factor) > LIMITE_BYTES));
  elegida = finas.length ? finas[finas.length - 1] : lista[0];
  if (estricto) {
    // Un mosaico: de la más gruesa que alcanza y se lee rápido; si no, todavía no (el editor dibuja su ancestro).
    const buenas = finas.filter((f) => !pesado(f));
    if (!buenas.length) return { pendiente: true };
    elegida = buenas[buenas.length - 1];
  } else if (elegida && pesado(elegida)) {
    const alternativa = lista.filter((f) => f.factor > elegida.factor && !pesado(f));
    if (!alternativa.length) return { pendiente: true };
    elegida = alternativa[0];
    parcial = true;
  }
  if (!elegida) return { pendiente: true };
  const lienzo = new OffscreenCanvas(ancho, alto);
  const ctx = lienzo.getContext("2d", { willReadFrequently: true });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  if (elegida.tipo === "imagen") {
    // Recorte y escala en la decodificación donde el navegador lo hace (Chrome, Firefox); si no, el lienzo escala.
    const bmp = await createImageBitmap(archivo, Math.floor(x0), Math.floor(y0), Math.max(1, Math.ceil(x1 - x0)), Math.max(1, Math.ceil(y1 - y0)),
      { resizeWidth: ancho, resizeHeight: alto, resizeQuality: "high" });
    ctx.drawImage(bmp, 0, 0, ancho, alto);
    bmp.close();
  } else if (elegida.tipo === "tiff") {
    try { await dibujarTiff(a, elegida.nivel, rect, ctx, ancho, alto, id); } catch (err) { if (err instanceof Cancelada) return { cancelada: true }; throw err; }
  } else if (elegida.tipo === "piramide") {
    await dibujarPiramide(a, elegida.nivel, rect, ctx, ancho, alto);
  } else {
    const bmp = await bitmapVista(a);
    const f = a.piramide.vista.factor;
    ctx.drawImage(bmp, x0 / f, y0 / f, (x1 - x0) / f, (y1 - y0) / f, 0, 0, ancho, alto);
  }
  const rgba = new Uint8Array(ctx.getImageData(0, 0, ancho, alto).data.buffer);
  return { rgba, ancho, alto, parcial, factor: elegida.factor };
}

async function bloqueTiff(a, n, indice) {
  const clave = `${a.clave}|${n.ancho}|${indice}`;
  const hit = bloques.get(clave);
  if (hit) { bloques.delete(clave); bloques.set(clave, hit); return hit; }
  const b = await TVTiff.bloque(a.tif, n, indice);
  bloques.set(clave, b);
  bytesBloques += b.rgba.length;
  while (bytesBloques > MAX_BLOQUES && bloques.size > 1) {
    const [k, v] = bloques.entries().next().value;
    bloques.delete(k);
    bytesBloques -= v.rgba.length;
  }
  return b;
}

async function dibujarTiff(a, n, rect, ctx, ancho, alto, id) {
  const f = n.factor;
  const [x0, y0, x1, y1] = rect.map((v) => v / f);
  const lx0 = Math.floor(x0), ly0 = Math.floor(y0), lx1 = Math.min(n.ancho, Math.ceil(x1)), ly1 = Math.min(n.alto, Math.ceil(y1));
  const w = Math.max(1, lx1 - lx0), h = Math.max(1, ly1 - ly0);
  const tmp = new OffscreenCanvas(w, h);
  const tctx = tmp.getContext("2d");
  const lista = TVTiff.bloquesDe(n, lx0, ly0, lx1, ly1);
  // Decodificar en paralelo (LZW y JPEG son lo caro), de a 16 para poder cortar si el mosaico salió de la vista.
  const decod = [];
  for (let i = 0; i < lista.length; i += 16) {
    if (id !== undefined && cancelados.has(id)) throw new Cancelada();
    decod.push(...(await Promise.all(lista.slice(i, i + 16).map((b) => bloqueTiff(a, n, b.indice)))));
  }
  if (id !== undefined && cancelados.has(id)) throw new Cancelada();
  lista.forEach((b, k) => {
    const d = decod[k];
    tctx.putImageData(new ImageData(new Uint8ClampedArray(d.rgba.buffer, d.rgba.byteOffset, d.ancho * d.filas * 4), d.ancho, d.filas), b.x - lx0, b.y - ly0);
  });
  ctx.drawImage(tmp, x0 - lx0, y0 - ly0, x1 - x0, y1 - y0, 0, 0, ancho, alto);
}

// ── Pirámide local (OPFS) ────────────────────────────────────────────────────────────────────────────────────────────────────────

async function carpetaDe(clave, crear) {
  const raiz = await navigator.storage.getDirectory();
  const base = await raiz.getDirectoryHandle(DIR, { create: crear });
  const nombre = await huella(clave);
  return base.getDirectoryHandle(nombre, { create: crear });
}
async function huella(texto) {
  const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
  return Array.from(new Uint8Array(h).slice(0, 16), (b) => b.toString(16).padStart(2, "0")).join("");
}
async function leerArchivo(dir, nombre) {
  try { return await (await dir.getFileHandle(nombre)).getFile(); } catch (_) { return null; }
}
async function escribirArchivo(dir, nombre, datos) {
  const fh = await dir.getFileHandle(nombre, { create: true });
  if (fh.createSyncAccessHandle) {
    const acceso = await fh.createSyncAccessHandle();
    try {
      const bytes = datos instanceof Blob ? new Uint8Array(await datos.arrayBuffer()) : datos;
      acceso.truncate(0);
      acceso.write(bytes, { at: 0 });
      acceso.flush();
    } finally { acceso.close(); }
  } else {
    const w = await fh.createWritable();
    await w.write(datos);
    await w.close();
  }
}
async function leerManifiesto(clave) {
  try {
    const dir = await carpetaDe(clave, false);
    const f = await leerArchivo(dir, "manifiesto.json");
    return f ? JSON.parse(await f.text()) : null;
  } catch (_) { return null; }
}
async function guardarManifiesto(a) {
  const dir = await carpetaDe(a.clave, true);
  await escribirArchivo(dir, "manifiesto.json", new TextEncoder().encode(JSON.stringify(a.piramide)));
}

const tilesAbiertos = new Map(); // nombre → ImageBitmap (LRU pequeño)
async function mosaicoPiramide(a, n, c, f) {
  const nombre = `${a.clave}|${n.factor}|${c}|${f}`;
  const hit = tilesAbiertos.get(nombre);
  if (hit) { tilesAbiertos.delete(nombre); tilesAbiertos.set(nombre, hit); return hit; }
  const dir = await carpetaDe(a.clave, false);
  const archivo = await leerArchivo(dir, `n${n.factor}_${c}_${f}.${n.ext}`);
  if (!archivo) return null;
  const bmp = await createImageBitmap(archivo);
  tilesAbiertos.set(nombre, bmp);
  while (tilesAbiertos.size > 400) {
    const [k, v] = tilesAbiertos.entries().next().value;
    v.close(); tilesAbiertos.delete(k);
  }
  return bmp;
}

async function dibujarPiramide(a, n, rect, ctx, ancho, alto) {
  const f = n.factor;
  const [x0, y0, x1, y1] = rect.map((v) => v / f);
  const c0 = Math.floor(x0 / LADO), c1 = Math.min(n.columnas - 1, Math.floor((x1 - 1e-9) / LADO));
  const f0 = Math.floor(y0 / LADO), f1 = Math.min(n.filas - 1, Math.floor((y1 - 1e-9) / LADO));
  const escala = ancho / (x1 - x0);
  const trabajos = [];
  for (let fi = f0; fi <= f1; fi++) for (let ci = c0; ci <= c1; ci++) trabajos.push([ci, fi]);
  const bmps = await Promise.all(trabajos.map(([ci, fi]) => mosaicoPiramide(a, n, ci, fi)));
  trabajos.forEach(([ci, fi], k) => {
    const bmp = bmps[k];
    if (!bmp) return;
    const dx = (ci * LADO - x0) * escala, dy = (fi * LADO - y0) * (alto / (y1 - y0));
    ctx.drawImage(bmp, dx, dy, bmp.width * escala, bmp.height * (alto / (y1 - y0)));
  });
}

let vistaBmp = null, vistaClave = "";
async function bitmapVista(a) {
  if (vistaBmp && vistaClave === a.clave) return vistaBmp;
  const dir = await carpetaDe(a.clave, false);
  const archivo = await leerArchivo(dir, "vista.png");
  if (!archivo) throw new Error("sin vista general");
  if (vistaBmp) vistaBmp.close();
  vistaBmp = await createImageBitmap(archivo);
  vistaClave = a.clave;
  return vistaBmp;
}

function promediar2(rgba, ancho, filas) {
  const w = Math.max(1, ancho >> 1), h = Math.max(1, filas >> 1);
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    const a0 = (2 * y) * ancho * 4, a1 = Math.min(2 * y + 1, filas - 1) * ancho * 4;
    for (let x = 0; x < w; x++) {
      const i = 2 * x * 4, j = Math.min(2 * x + 1, ancho - 1) * 4, o = (y * w + x) * 4;
      for (let k = 0; k < 4; k++) out[o + k] = (rgba[a0 + i + k] + rgba[a0 + j + k] + rgba[a1 + i + k] + rgba[a1 + j + k] + 2) >> 2;
    }
  }
  return { rgba: out, ancho: w, filas: h };
}

/** Lee la banda [y0, y1) del original a resolución completa como RGBA. */
async function bandaOriginal(a, y0, y1, bmpImagen) {
  const ancho = a.ancho, filas = y1 - y0;
  if (a.tipo === "imagen") {
    const c = new OffscreenCanvas(ancho, filas);
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(bmpImagen, 0, y0, ancho, filas, 0, 0, ancho, filas);
    return x.getImageData(0, 0, ancho, filas).data;
  }
  const n = a.tif.niveles[0];
  const out = new Uint8ClampedArray(ancho * filas * 4);
  for (const b of TVTiff.bloquesDe(n, 0, y0, ancho, y1)) {
    const d = await TVTiff.bloque(a.tif, n, b.indice);
    const yA = Math.max(y0, b.y), yB = Math.min(y1, b.y + d.filas);
    const w = Math.min(d.ancho, ancho - b.x);
    for (let y = yA; y < yB; y++) {
      out.set(d.rgba.subarray(((y - b.y) * d.ancho) * 4, ((y - b.y) * d.ancho + w) * 4), ((y - y0) * ancho + b.x) * 4);
    }
  }
  return out;
}

async function codificar(rgba, ancho, filas, conAlfa) {
  const c = new OffscreenCanvas(ancho, filas);
  c.getContext("2d").putImageData(new ImageData(rgba, ancho, filas), 0, 0);
  return c.convertToBlob(conAlfa ? { type: "image/png" } : { type: "image/jpeg", quality: 0.9 });
}

/** Pirámide local: nivel ×2 desde el original (por bandas, retomable), luego ×4, ×8… desde el nivel anterior (por mosaico,
 *  retomable) hasta que el lado mayor quepa en la vista general. La vista general se va llenando durante el primer paso. */
/** La rejilla de mosaicos del editor (local_raster.rs: Grid), para cortar los de la vista general. */
function rejilla(t) {
  const lado = (v, z) => Math.max(1, Math.ceil(v / 2 ** z));
  const bloque = (z, c, r) => {
    const lw = lado(t.ancho, z), lh = lado(t.alto, z), T = t.tile, g = t.gutter;
    const x0 = Math.min(c * T, lw), y0 = Math.min(r * T, lh), x1 = Math.min((c + 1) * T, lw), y1 = Math.min((r + 1) * T, lh);
    return [Math.max(0, x0 - g), Math.max(0, y0 - g), Math.min(lw, x1 + g), Math.min(lh, y1 + g)];
  };
  const archivoDe = (z, b) => [Math.min(t.ancho, b[0] * 2 ** z), Math.min(t.alto, b[1] * 2 ** z), Math.min(t.ancho, b[2] * 2 ** z), Math.min(t.alto, b[3] * 2 ** z)];
  const lista = [];
  for (let z = t.desde; z <= t.top; z++) {
    const cols = Math.ceil(lado(t.ancho, z) / t.tile), filas = Math.ceil(lado(t.alto, z) / t.tile);
    for (let r = 0; r < filas; r++) for (let c = 0; c < cols; c++) { const b = bloque(z, c, r); lista.push({ z, c, r, b, f: archivoDe(z, b) }); }
  }
  return lista;
}

async function piramide(archivo, id, teselas) {
  const a = await abrir(archivo);
  if (a.motivo) throw new Error(a.motivo);
  const conAlfa = !!a.alfa;
  const ext = conAlfa ? "png" : "jpg";
  if (!a.piramide || a.piramide.version !== 1) {
    const niveles = [];
    // JPEG/PNG grandes guardan también su nivel completo en mosaicos (decodificar el archivo entero cada vez sería lento).
    let factor = a.tipo === "imagen" ? 1 : 2;
    for (; ; factor *= 2) {
      const w = Math.ceil(a.ancho / factor), h = Math.ceil(a.alto / factor);
      niveles.push({ factor, ancho: w, alto: h, columnas: Math.ceil(w / LADO), filas: Math.ceil(h / LADO), ext, lista: false, bandas: 0 });
      if (Math.max(w, h) <= VISTA) break;
    }
    const fv = Math.max(1, Math.max(a.ancho, a.alto) / VISTA);
    a.piramide = { version: 1, clave: a.clave, ancho: a.ancho, alto: a.alto, niveles, vista: null, vistaFactor: fv, lista: false, inicio: Date.now() };
    await guardarManifiesto(a);
  }
  const p = a.piramide;
  const dir = await carpetaDe(a.clave, true);
  const primero = p.niveles[0];
  const fv = p.vistaFactor;
  const vw = Math.max(1, Math.round(a.ancho / fv)), vh = Math.max(1, Math.round(a.alto / fv));
  const vista = new OffscreenCanvas(vw, vh);
  const vctx = vista.getContext("2d");
  vctx.imageSmoothingQuality = "high";
  const previa = await leerArchivo(dir, "vista.png");
  if (previa) vctx.drawImage(await createImageBitmap(previa), 0, 0, vw, vh);
  const total = primero.filas + p.niveles.slice(1).reduce((s, n) => s + n.filas, 0);
  let hechas = primero.bandas + p.niveles.slice(1).filter((n) => n.lista).reduce((s, n) => s + n.filas, 0);
  let ultimoAviso = 0;
  // Los mosaicos del editor que salen de la vista general, y las filas del archivo que cambiaron desde el último aviso (al retomar,
  // todo: la vista guardada ya trae lo leído).
  const mosaicos = teselas ? rejilla(teselas) : [];
  let sucio = previa ? [0, a.alto] : null;
  const avisar = async (forzar) => {
    const ahora = Date.now();
    if (!forzar && ahora - ultimoAviso < 1500) return;
    ultimoAviso = ahora;
    const salen = [], transferir = [];
    if (sucio) {
      for (const m of mosaicos) {
        if (m.f[3] <= sucio[0] || m.f[1] >= sucio[1]) continue;
        const w = m.b[2] - m.b[0], h = m.b[3] - m.b[1];
        const c = new OffscreenCanvas(w, h);
        const x = c.getContext("2d", { willReadFrequently: true });
        x.imageSmoothingQuality = "high";
        x.drawImage(vista, m.f[0] / fv, m.f[1] / fv, (m.f[2] - m.f[0]) / fv, (m.f[3] - m.f[1]) / fv, 0, 0, w, h);
        const rgba = new Uint8Array(x.getImageData(0, 0, w, h).data.buffer);
        salen.push({ z: m.z, c: m.c, r: m.r, ancho: w, alto: h, rgba });
        transferir.push(rgba.buffer);
      }
      sucio = null;
    }
    postMessage({ id, progreso: hechas / total, teselas: salen, vistaLista: !!primero.lista }, transferir);
  };
  let bmpImagen = null;
  try {
    if (!primero.lista) {
      if (a.tipo === "imagen") bmpImagen = await createImageBitmap(archivo);
      const alto0 = LADO * primero.factor; // filas del original por banda
      for (let banda = primero.bandas; banda < primero.filas; banda++) {
        if (cancelados.has(id)) { cancelados.delete(id); await guardarManifiesto(a); return { cancelada: true, progreso: hechas / total }; }
        const y0 = banda * alto0, y1 = Math.min(a.alto, y0 + alto0);
        let d = { rgba: await bandaOriginal(a, y0, y1, bmpImagen), ancho: a.ancho, filas: y1 - y0 };
        if (primero.factor === 2) d = promediar2(d.rgba, d.ancho, d.filas);
        // mosaicos de la banda
        for (let c = 0; c < primero.columnas; c++) {
          const x0 = c * LADO, w = Math.min(LADO, d.ancho - x0);
          const t = new Uint8ClampedArray(w * d.filas * 4);
          for (let y = 0; y < d.filas; y++) t.set(d.rgba.subarray((y * d.ancho + x0) * 4, (y * d.ancho + x0 + w) * 4), y * w * 4);
          await escribirArchivo(dir, `n${primero.factor}_${c}_${banda}.${ext}`, await codificar(t, w, d.filas, conAlfa));
        }
        // la banda en la vista general
        const lb = new OffscreenCanvas(d.ancho, d.filas);
        lb.getContext("2d").putImageData(new ImageData(d.rgba, d.ancho, d.filas), 0, 0);
        vctx.drawImage(lb, 0, y0 / fv, a.ancho / fv, (y1 - y0) / fv);
        // Las filas de la vista general que cambiaron (con un píxel de margen por el suavizado).
        sucio = [Math.max(0, Math.min(sucio ? sucio[0] : y0, y0) - fv), Math.min(a.alto, Math.max(sucio ? sucio[1] : y1, y1) + fv)];
        primero.bandas = banda + 1;
        hechas++;
        if (banda % 8 === 7) {
          await escribirArchivo(dir, "vista.png", await vista.convertToBlob({ type: "image/png" }));
          await guardarManifiesto(a);
        }
        await avisar(false);
      }
      primero.lista = true;
      await escribirArchivo(dir, "vista.png", await vista.convertToBlob({ type: "image/png" }));
      p.vista = { factor: fv, ancho: vw, alto: vh };
      await guardarManifiesto(a);
      sucio = [0, a.alto];
      await avisar(true);
    }
    for (let k = 1; k < p.niveles.length; k++) {
      const n = p.niveles[k], prev = p.niveles[k - 1];
      if (n.lista) continue;
      for (let fi = 0; fi < n.filas; fi++) {
        if (cancelados.has(id)) { cancelados.delete(id); await guardarManifiesto(a); return { cancelada: true, progreso: hechas / total }; }
        for (let ci = 0; ci < n.columnas; ci++) {
          const nombre = `n${n.factor}_${ci}_${fi}.${ext}`;
          if (await leerArchivo(dir, nombre)) continue;
          const w = Math.min(LADO, n.ancho - ci * LADO), h = Math.min(LADO, n.alto - fi * LADO);
          const c = new OffscreenCanvas(w, h);
          const x = c.getContext("2d");
          x.imageSmoothingQuality = "high";
          for (const [dc, df] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
            const hijo = await leerArchivo(dir, `n${prev.factor}_${2 * ci + dc}_${2 * fi + df}.${ext}`);
            if (!hijo) continue;
            const bmp = await createImageBitmap(hijo);
            x.drawImage(bmp, (dc * LADO) / 2, (df * LADO) / 2, bmp.width / 2, bmp.height / 2);
            bmp.close();
          }
          await escribirArchivo(dir, nombre, await c.convertToBlob(conAlfa ? { type: "image/png" } : { type: "image/jpeg", quality: 0.9 }));
        }
        hechas++;
        await avisar(false);
      }
      n.lista = true;
      await guardarManifiesto(a);
    }
    p.lista = true;
    p.fin = Date.now();
    await guardarManifiesto(a);
    await avisar(true);
    return { lista: true, progreso: 1, segundos: (p.fin - p.inicio) / 1000 };
  } finally {
    if (bmpImagen) bmpImagen.close();
  }
}

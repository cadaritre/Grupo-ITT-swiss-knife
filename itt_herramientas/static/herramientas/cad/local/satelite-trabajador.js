/* Trabajador de «Imagen satelital desde una polilínea» (satelite.js): arma la imagen en la cuadrícula UTM del plano.
 *
 * La página baja las teselas (Web Mercator) directo del proveedor y se las pasa tal como llegaron (JPEG/PNG); aquí se decodifican por
 * bandas, se REPROYECTAN de Mercator a UTM (cada píxel de salida toma su lugar exacto en la fuente, con interpolación bilineal; no un
 * rectángulo estirado) y se codifica el JPEG final, con la atribución del proveedor en la esquina. Nada sale de aquí más que a la página.
 *
 * Mensajes: {tipo: "iniciar", plan, atribucion, geo} · {tipo: "fuente", clave: "z/x/y", bytes} · {tipo: "asignar", lista: [["x/y", clave|null]]}
 *           · {tipo: "armar", id} → {id, progreso} … {id, ok, jpeg, faltantes} · {tipo: "cancelar"}.
 */
"use strict";
// La geometría llega con su dirección de estáticos (con su huella en producción: nunca se deriva del nombre de este archivo).
let G = null;
const T = 256;   // TVSateliteGeo.TESELA
const GRIS = [205, 205, 205];
const BANDA = 256;

let plan = null, red = null, atribucion = "", cancelado = false;
const fuentes = new Map();     // "z/x/y" → bytes tal como llegaron
const asignadas = new Map();   // "x/y" del nivel del plan → "z/x/y" de su fuente (la misma tesela o una de un nivel más bajo), o null
const decodificadas = new Map();

self.onmessage = (ev) => {
  const m = ev.data;
  if (m.tipo === "iniciar") {
    if (!G) { self.importScripts(m.geo); G = self.TVSateliteGeo; }
    plan = m.plan; atribucion = m.atribucion || ""; cancelado = false;
    fuentes.clear(); asignadas.clear(); decodificadas.clear();
    red = G.malla(plan);
  } else if (m.tipo === "fuente") {
    fuentes.set(m.clave, m.bytes);
  } else if (m.tipo === "asignar") {
    for (const [k, v] of m.lista) asignadas.set(k, v);
  } else if (m.tipo === "cancelar") {
    cancelado = true;
  } else if (m.tipo === "armar") {
    armar(m.id).catch((err) => self.postMessage({ id: m.id, ok: false, error: String((err && err.message) || err) }));
  }
};

async function decodificar(clave) {
  if (decodificadas.has(clave)) return decodificadas.get(clave);
  const bytes = fuentes.get(clave);
  let datos = null;
  if (bytes) {
    try {
      const bmp = await createImageBitmap(new Blob([bytes]));
      const c = new OffscreenCanvas(T, T), x = c.getContext("2d", { willReadFrequently: true });
      x.drawImage(bmp, 0, 0, T, T);
      bmp.close && bmp.close();
      datos = x.getImageData(0, 0, T, T).data;
    } catch (_) { datos = null; }     // una tesela que no decodifica cuenta como faltante
  }
  decodificadas.set(clave, datos);
  return datos;
}

/** El rango de teselas del nivel del plan que toca la banda de filas [j0, j1): lo que cubre la malla en esas filas, con orilla. */
function rangoDeBanda(j0, j1) {
  const r0 = Math.floor(j0 / red.paso), r1 = Math.min(red.filas - 1, Math.ceil(j1 / red.paso));
  let xa = Infinity, xb = -Infinity, ya = Infinity, yb = -Infinity;
  for (let r = r0; r <= r1; r++) for (let c = 0; c < red.cols; c++) {
    const k = r * red.cols + c, X = red.mx[k], Y = red.my[k];
    if (X < xa) xa = X; if (X > xb) xb = X; if (Y < ya) ya = Y; if (Y > yb) yb = Y;
  }
  return { tx0: Math.floor((xa - 2) / T), tx1: Math.floor((xb + 2) / T), ty0: Math.floor((ya - 2) / T), ty1: Math.floor((yb + 2) / T) };
}

async function armar(id) {
  const { ancho, alto, z } = plan;
  const lienzo = new OffscreenCanvas(ancho, alto);
  const ctx = lienzo.getContext("2d");
  let faltantes = 0;
  for (const [, v] of asignadas) if (!v || !fuentes.has(v)) faltantes++;
  const paso = red.paso, cols = red.cols;
  const filaX = new Float64Array(cols), filaY = new Float64Array(cols);
  for (let j0 = 0; j0 < alto; j0 += BANDA) {
    if (cancelado) return self.postMessage({ id, ok: false, cancelado: true });
    const j1 = Math.min(alto, j0 + BANDA);
    // Una tabla densa de las teselas de la banda: datos decodificados, nivel y origen de cada una (sin cadenas por píxel).
    const { tx0, tx1, ty0, ty1 } = rangoDeBanda(j0, j1);
    const nx = tx1 - tx0 + 1, ny = ty1 - ty0 + 1;
    const tDatos = new Array(nx * ny).fill(null), tEsc = new Int32Array(nx * ny), tOx = new Int32Array(nx * ny), tOy = new Int32Array(nx * ny);
    const usadas = new Set();
    for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) { const f = asignadas.get(`${tx}/${ty}`); if (f) usadas.add(f); }
    // Se sueltan las decodificadas que esta banda ya no usa: la memoria queda en unas pocas filas de teselas.
    for (const k of [...decodificadas.keys()]) if (!usadas.has(k)) decodificadas.delete(k);
    for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) {
      const f = asignadas.get(`${tx}/${ty}`);
      if (!f) continue;
      const d = await decodificar(f);
      if (!d) continue;
      const [zf, fx, fy] = f.split("/").map(Number);
      const t = (ty - ty0) * nx + (tx - tx0);
      tDatos[t] = d; tEsc[t] = 2 ** (z - zf); tOx[t] = fx * T; tOy[t] = fy * T;
    }
    const sal = [0, 0, 0];
    // El valor del píxel global (px, py) del nivel z, de su tesela o de la de un nivel más bajo que la sustituye (gris si no hay).
    const valor = (px, py, k) => {
      const tx = Math.floor(px / T) - tx0, ty = Math.floor(py / T) - ty0;
      const t = tx >= 0 && ty >= 0 && tx < nx && ty < ny ? ty * nx + tx : -1;
      const d = t >= 0 ? tDatos[t] : null;
      if (!d) { sal[0] += GRIS[0] * k; sal[1] += GRIS[1] * k; sal[2] += GRIS[2] * k; return; }
      const s = tEsc[t];
      const lx = Math.min(T - 1, Math.floor(px / s) - tOx[t]), ly = Math.min(T - 1, Math.floor(py / s) - tOy[t]);
      const q = (ly * T + lx) * 4;
      sal[0] += d[q] * k; sal[1] += d[q + 1] * k; sal[2] += d[q + 2] * k;
    };
    const img = new ImageData(ancho, j1 - j0), px = img.data;
    for (let j = j0; j < j1; j++) {
      // La malla interpolada en la vertical para esta fila; en la horizontal, píxel por píxel.
      const rj = Math.min(Math.floor(j / paso), red.filas - 2), ja = rj * paso, jb = Math.min((rj + 1) * paso, alto);
      const v = jb > ja ? (j - ja) / (jb - ja) : 0;
      for (let c = 0; c < cols; c++) {
        const k = rj * cols + c;
        filaX[c] = red.mx[k] * (1 - v) + red.mx[k + cols] * v;
        filaY[c] = red.my[k] * (1 - v) + red.my[k + cols] * v;
      }
      for (let i = 0; i < ancho; i++) {
        const ci = Math.min(Math.floor(i / paso), cols - 2), ia = ci * paso, ib = Math.min((ci + 1) * paso, ancho);
        const u = ib > ia ? (i - ia) / (ib - ia) : 0;
        const X = filaX[ci] * (1 - u) + filaX[ci + 1] * u, Y = filaY[ci] * (1 - u) + filaY[ci + 1] * u;
        // Centro del píxel de la fuente en i + ½: bilineal entre los cuatro vecinos.
        const fx = X - 0.5, fy = Y - 0.5, ix = Math.floor(fx), iy = Math.floor(fy), a = fx - ix, b = fy - iy;
        sal[0] = sal[1] = sal[2] = 0;
        valor(ix, iy, (1 - a) * (1 - b)); valor(ix + 1, iy, a * (1 - b)); valor(ix, iy + 1, (1 - a) * b); valor(ix + 1, iy + 1, a * b);
        const o = ((j - j0) * ancho + i) * 4;
        px[o] = sal[0]; px[o + 1] = sal[1]; px[o + 2] = sal[2]; px[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, j0);
    self.postMessage({ id, progreso: j1 / alto });
  }
  decodificadas.clear();
  sellar(ctx, ancho, alto);
  if (cancelado) return self.postMessage({ id, ok: false, cancelado: true });
  const jpeg = await lienzo.convertToBlob({ type: "image/jpeg", quality: 0.9 });
  self.postMessage({ id, ok: true, jpeg, faltantes, ancho, alto });
}

/** La atribución del proveedor dentro de la imagen (abajo a la derecha): viaja con el archivo a donde vaya, también a AutoCAD. */
function sellar(ctx, ancho, alto) {
  if (!atribucion) return;
  try {
    const fs = Math.max(11, Math.min(40, Math.round(Math.min(ancho, alto) / 70)));
    ctx.font = `${fs}px sans-serif`;
    const w = Math.min(ancho - 4, ctx.measureText(atribucion).width + fs);
    const h = Math.round(fs * 1.6);
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(ancho - w - 2, alto - h - 2, w, h);
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.fillText(atribucion, ancho - w - 2 + fs / 2, alto - h / 2 - 2, w - fs);
  } catch (_) { /* sin texto en este navegador: queda el archivo de atribución junto a la imagen */ }
}

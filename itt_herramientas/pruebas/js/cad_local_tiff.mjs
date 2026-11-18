// Lector TIFF de «Carpeta del proyecto» (static/herramientas/cad/local/tiff.js) contra los TIFF sintéticos de cad_local_rasters.py.
// Uso: node cad_local_tiff.mjs <tiff.js> <casos.json>. Imprime una línea JSON por caso: {nombre, ok, error?}.
import fs from "node:fs";
import vm from "node:vm";

const [, , rutaJs, rutaCasos] = process.argv;
vm.runInThisContext(fs.readFileSync(rutaJs, "utf8"), { filename: rutaJs });
const TVTiff = globalThis.TVTiff;
const casos = JSON.parse(fs.readFileSync(rutaCasos, "utf8"));

const esperado = (x, y, spp, alfa) => {
  const r = x & 255, g = y & 255, b = (x ^ y) & 255;
  if (spp === 1) { const v = (r + g) >> 1; return [v, v, v, 255]; }
  return [r, g, b, alfa ? ((((x >> 6) + (y >> 6)) % 2 === 0) ? 255 : 128) : 255];
};

for (const c of casos) {
  try {
    const blob = await fs.openAsBlob(c.ruta);
    const tif = await TVTiff.abrir(blob);
    if (tif.motivo) throw new Error("no soportado: " + tif.motivo);
    if (tif.ancho !== c.ancho || tif.alto !== c.alto) throw new Error(`tamaño ${tif.ancho}x${tif.alto}`);
    if (tif.niveles.length !== 1 + (c.vistas || 0)) throw new Error(`niveles ${tif.niveles.length}`);
    const n = tif.niveles[0];
    let peor = 0, revisados = 0;
    // Cada bloque del nivel completo, píxeles de muestra.
    const total = n.columnas * n.filas;
    for (let i = 0; i < total; i += Math.max(1, Math.floor(total / 9))) {
      const b = await TVTiff.bloque(tif, n, i);
      const bx = (i % n.columnas) * n.tw, by = Math.floor(i / n.columnas) * n.th;
      for (let k = 0; k < 40; k++) {
        const x = (k * 37) % b.ancho, y = (k * 53) % b.filas;
        if (bx + x >= tif.ancho || by + y >= tif.alto) continue;
        const e = esperado(bx + x, by + y, c.spp, c.alfa);
        const o = (y * b.ancho + x) * 4;
        for (let ch = 0; ch < 4; ch++) peor = Math.max(peor, Math.abs(b.rgba[o + ch] - e[ch]));
        revisados++;
      }
    }
    if (peor > (c.tolerancia || 0)) throw new Error(`diferencia ${peor}`);
    // Vista general: el promedio del bloque.
    if (c.vistas) {
      const v = tif.niveles[1];
      if (v.factor !== 2) throw new Error(`factor ${v.factor}`);
      const b = await TVTiff.bloque(tif, v, 0);
      if (!(b.rgba[3] > 0)) throw new Error("vista general vacía");
    }
    if (c.geo) {
      const g = tif.geo;
      if (!g || g.epsg !== c.geo.epsg || g.pixel_is_point !== c.geo.puntual || Math.abs(g.tiepoints[3] - c.geo.x) > 1e-9) throw new Error("geo " + JSON.stringify(g));
    }
    console.log(JSON.stringify({ nombre: c.nombre, ok: true, revisados }));
  } catch (e) {
    console.log(JSON.stringify({ nombre: c.nombre, ok: false, error: String(e && e.message || e) }));
  }
}

// La geometría de «Imagen satelital» (static/herramientas/cad/local/satelite-geo.js) en Node: llama a sus funciones con lo que manda
// test_cad_satelite.py (stdin, JSON) y devuelve los resultados; el lado de Python los compara con pyproj y con cuentas a mano.
import fs from "node:fs";
import vm from "node:vm";

vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
const G = globalThis.TVSateliteGeo;
const { llamadas } = JSON.parse(fs.readFileSync(0, "utf8"));

/** El error máximo (px) de la malla interpolada contra la transformación exacta, en cada píxel de una cuadrícula de muestras. */
function errorMalla(plan) {
  const m = G.malla(plan);
  let peor = 0;
  for (let j = 0; j < plan.alto; j += 7) for (let i = 0; i < plan.ancho; i += 5) {
    const [X, Y] = G.enMalla(m, i + 0.0, j + 0.0);
    const [la, lo] = G.utmAGeo(plan.x0 + (i + 0.5) * plan.metros, plan.y1 - (j + 0.5) * plan.metros, plan.zona, plan.sur);
    const [x, y] = G.geoAMercator(la, lo, plan.z);
    peor = Math.max(peor, Math.hypot(X - x, Y - y));
  }
  return peor;
}

const salida = llamadas.map(([fn, args]) => (fn === "errorMalla" ? errorMalla(...args) : G[fn](...args)));
process.stdout.write(JSON.stringify({ salida, topes: G.TOPES, tesela: G.TESELA }));

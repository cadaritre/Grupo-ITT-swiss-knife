// Reglas para resolver referencias contra la carpeta del proyecto (static/herramientas/cad/local/carpeta.js).
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
const C = globalThis.TVCarpeta;

const rutas = [
  "Imagenes/Fachada_Norte/f001.png", "Imagenes/Fachada_Sur/f001.png", "Imagenes/Fachada_Norte/f002.jpg",
  "Ortofotos/Orto 2024.tif", "Ortofotos/Orto 2024.tfw", "Referencias/Marco_y_ejes.dxf", "otro/UNICO.PNG",
];
const ind = C.indice(rutas.map((ruta) => ({ ruta, nombre: ruta.split("/").pop() })));

assert.deepEqual(C.partes(String.raw`C:\PointCab\Proyecto\Imagenes\Fachada_Norte\f001.png`), ["PointCab", "Proyecto", "Imagenes", "Fachada_Norte", "f001.png"]);
assert.deepEqual(C.partes(String.raw`\\servidor\obras\Ortofotos\a.tif`), ["Ortofotos", "a.tif"]);
assert.deepEqual(C.partes("../Imagenes/./x.png"), ["Imagenes", "x.png"]);
assert.deepEqual(C.partes("/Users/alguien/Proyecto/a.png"), ["Users", "alguien", "Proyecto", "a.png"]);

// Ruta absoluta de Windows: coincide por la ruta relativa más larga, sin distinguir mayúsculas.
let r = C.resolver(ind, String.raw`C:\PointCab\Proyecto_Demo\IMAGENES\fachada_norte\F001.PNG`);
assert.equal(r.estado, "ruta"); assert.equal(r.entrada.ruta, "Imagenes/Fachada_Norte/f001.png"); assert.equal(r.coincide, 3);
// La misma con Fachada_Sur: el otro archivo del mismo nombre, sin confundirse.
r = C.resolver(ind, String.raw`D:\x\Imagenes\Fachada_Sur\f001.png`);
assert.equal(r.estado, "ruta"); assert.equal(r.entrada.ruta, "Imagenes/Fachada_Sur/f001.png");
// Solo el nombre, y hay dos: ambigua, con los candidatos.
r = C.resolver(ind, "f001.png");
assert.equal(r.estado, "ambigua"); assert.equal(r.candidatos.length, 2);
// Solo el nombre, único: por nombre.
r = C.resolver(ind, "/Volumes/Obra/fotos/unico.png");
assert.equal(r.estado, "nombre"); assert.equal(r.entrada.ruta, "otro/UNICO.PNG");
// Relativa con espacios; Mac.
assert.equal(C.resolver(ind, String.raw`..\Ortofotos\Orto 2024.tif`).estado, "ruta");
assert.equal(C.resolver(ind, "/Users/x/Proyecto/Referencias/Marco_y_ejes.dxf").entrada.ruta, "Referencias/Marco_y_ejes.dxf");
// Falta.
assert.equal(C.resolver(ind, String.raw`C:\otra\cosa.png`).estado, "falta");
assert.equal(C.resolver(ind, "").estado, "falta");
console.log("ok");

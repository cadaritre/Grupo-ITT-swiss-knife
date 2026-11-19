/* CAD · arma un archivo del editor nuevo con un parche desde la versión guardada en el equipo (instalador.js; docs/cad.md, «El editor
 * guardado en el equipo»). Formato TVP de cad/editor/preparar.py (bsdiff con la diferencia al final):
 *
 *   «TVPARCH1» · tamaño viejo · tamaño nuevo · registros · bytes extra · bytes diferencia (u32, little-endian)
 *   registros (x u32, y u32, z i32) · extra · diferencia
 *
 * Por registro: x bytes = viejo + diferencia (módulo 256), luego y bytes del extra, y el viejo avanza z. El parche llega comprimido por
 * HTTP (brotli o gzip, lo descomprime el navegador) y se lee de corrido: en memoria quedan el archivo viejo, el nuevo, los registros y el
 * extra (unos MB), nunca la diferencia entera. El resultado se compara con el SHA-256 del manifiesto antes de guardarlo.
 *
 * Mensaje: { id, viejo: {cache, url, sha256}, parche, destino: {cache, url}, sha256, bytes, cabeceras }
 * Responde { id, tipo: "avance", bytes } mientras arma y al final { id, ok, error? }.
 */
"use strict";

const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");

// Lee de un flujo de bytes: exacto(n) da n bytes nuevos; trozos(n, fn) pasa n bytes a fn de a pedazos, sin juntarlos.
class Lector {
  constructor(flujo) { this.r = flujo.getReader(); this.buf = new Uint8Array(0); this.i = 0; }
  async llenar() {
    const { value, done } = await this.r.read();
    if (done) throw new Error("El parche llegó incompleto.");
    this.buf = value; this.i = 0;
  }
  async exacto(n) {
    const salida = new Uint8Array(n);
    let k = 0;
    while (k < n) {
      if (this.i >= this.buf.length) await this.llenar();
      const m = Math.min(n - k, this.buf.length - this.i);
      salida.set(this.buf.subarray(this.i, this.i + m), k);
      this.i += m; k += m;
    }
    return salida;
  }
  async trozos(n, fn) {
    let k = 0;
    while (k < n) {
      if (this.i >= this.buf.length) await this.llenar();
      const m = Math.min(n - k, this.buf.length - this.i);
      fn(this.buf.subarray(this.i, this.i + m), k);
      this.i += m; k += m;
    }
  }
  async alFinal() {                                    // nada de más después del parche
    if (this.i < this.buf.length) return false;
    for (;;) {
      const { value, done } = await this.r.read();
      if (done) return true;
      if (value && value.length) return false;
    }
  }
}

async function aplicar(viejo, flujo, avance) {
  const lector = new Lector(flujo);
  const cab = await lector.exacto(28);
  if (new TextDecoder().decode(cab.subarray(0, 8)) !== "TVPARCH1") throw new Error("No es un parche del editor.");
  const v = new DataView(cab.buffer);
  const tamViejo = v.getUint32(8, true), tamNuevo = v.getUint32(12, true), n = v.getUint32(16, true), nExtra = v.getUint32(20, true), nDif = v.getUint32(24, true);
  if (tamViejo !== viejo.length) throw new Error("El parche es de otro archivo.");
  const registros = new DataView((await lector.exacto(12 * n)).buffer);
  const extra = await lector.exacto(nExtra);
  const nuevo = new Uint8Array(tamNuevo);
  let pn = 0, po = 0, pe = 0, pd = 0, ultimo = 0;
  for (let r = 0; r < n; r++) {
    const x = registros.getUint32(12 * r, true), y = registros.getUint32(12 * r + 4, true), z = registros.getInt32(12 * r + 8, true);
    if (pn + x + y > tamNuevo || pe + y > nExtra || pd + x > nDif) throw new Error("El parche está dañado.");
    const base = pn, ob = po;
    await lector.trozos(x, (trozo, k) => {
      for (let i = 0; i < trozo.length; i++) {
        const o = ob + k + i;
        nuevo[base + k + i] = (trozo[i] + (o >= 0 && o < tamViejo ? viejo[o] : 0)) & 255;
      }
    });
    pn += x; po += x; pd += x;
    nuevo.set(extra.subarray(pe, pe + y), pn);
    pn += y; pe += y; po += z;
    if (pn - ultimo > 4194304) { ultimo = pn; avance(pn); }
  }
  if (pn !== tamNuevo || pe !== nExtra || pd !== nDif || !(await lector.alFinal())) throw new Error("El parche está dañado.");
  return nuevo;
}

self.onmessage = async (ev) => {
  const m = ev.data;
  try {
    const vieja = await (await caches.open(m.viejo.cache)).match(m.viejo.url, { ignoreVary: true });
    if (!vieja) throw new Error("Falta el archivo de la versión guardada.");
    const viejo = new Uint8Array(await vieja.arrayBuffer());
    if (hex(await crypto.subtle.digest("SHA-256", viejo)) !== m.viejo.sha256) throw new Error("El archivo guardado no es el esperado.");
    const r = await fetch(m.parche, { credentials: "same-origin" });
    if (!r.ok || !r.body) throw new Error("No se pudo bajar el parche.");
    const nuevo = await aplicar(viejo, r.body, (bytes) => self.postMessage({ id: m.id, tipo: "avance", bytes }));
    if (nuevo.length !== m.bytes || hex(await crypto.subtle.digest("SHA-256", nuevo)) !== m.sha256) throw new Error("El archivo armado no coincide con el manifiesto.");
    const cache = await caches.open(m.destino.cache);
    await cache.put(m.destino.url, new Response(nuevo, { status: 200, headers: new Headers(m.cabeceras) }));
    self.postMessage({ id: m.id, ok: true });
  } catch (err) {
    self.postMessage({ id: m.id, ok: false, error: String((err && err.message) || err) });
  }
};

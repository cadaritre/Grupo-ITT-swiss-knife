/* Totales de una cotización en el navegador: la MISMA aritmética que `QuoteData` del dominio (Python).
 *
 * Python y JavaScript no redondean igual por omisión:
 *  - `round(x, 2)` de Python redondea el valor binario EXACTO del número y, en un empate exacto, al par.
 *    `toFixed` redondea el mismo valor exacto pero en un empate sube. Aquí se usa la expansión decimal exacta
 *    (`toFixed(100)`) para detectar el empate y resolverlo al par.
 *  - `sum()` de Python (3.12+) suma números flotantes con compensación de Neumaier; `reduce` no.
 * Una prueba compara este archivo con Python con miles de entradas al azar (pruebas/test_cotizaciones_totales.py).
 * Se puede cargar en el navegador (ITT.totales) y en Node (module.exports).
 */
(function (global) {
  "use strict";

  /** Equivalente a `round(x, 2)` de Python 3. */
  function redondear2(x) {
    if (!Number.isFinite(x)) return x;
    const negativo = x < 0 || Object.is(x, -0);
    const a = Math.abs(x);
    if (a >= 1e21) return x;                           // fuera de rango de toFixed; los límites de la API lo impiden
    const s = a.toFixed(100);
    const punto = s.indexOf(".");
    const dec = s.slice(punto + 1);
    let base = BigInt(s.slice(0, punto) + dec.slice(0, 2));
    const resto = dec.slice(2);
    const primero = resto.charCodeAt(0) - 48;
    const masAlla = /[1-9]/.test(resto.slice(1));
    if (primero > 5 || (primero === 5 && masAlla)) base += 1n;
    else if (primero === 5 && base % 2n === 1n) base += 1n;     // empate exacto: al par
    const texto = base.toString().padStart(3, "0");
    const r = Number(texto.slice(0, -2) + "." + texto.slice(-2));
    return negativo ? -r : r;
  }

  /** Equivalente a `sum(valores)` de CPython 3.12+ para flotantes (suma compensada de Neumaier). */
  function suma(valores) {
    let resultado = 0;
    let c = 0;
    for (const x of valores) {
      const t = resultado + x;
      if (Math.abs(resultado) >= Math.abs(x)) c += (resultado - t) + x;
      else c += (x - t) + resultado;
      resultado = t;
    }
    if (c !== 0 && Number.isFinite(c)) resultado += c;
    return resultado;
  }

  const numero = (v, porOmision) => (typeof v === "number" && Number.isFinite(v) ? v : porOmision);

  /** Importe de un concepto: `round(cantidad * precio, 2)`. */
  function importe(concepto) {
    return redondear2(numero(concepto.quantity, 1) * numero(concepto.unit_price, 0));
  }

  /** {subtotal, iva, total, anticipo, importes} de una cotización (objeto con el esquema `QuoteData`). */
  function totales(cotizacion) {
    const importes = (cotizacion.items || []).map(importe);
    const subtotal = redondear2(suma(importes));
    const iva = cotizacion.include_vat === false ? 0 : redondear2((subtotal * numero(cotizacion.vat_rate, 16)) / 100);
    const total = redondear2(subtotal + iva);
    const anticipo = redondear2((total * numero(cotizacion.advance_percent, 50)) / 100);
    return { subtotal, iva, total, anticipo, importes };
  }

  /** Igual que `f"$ {valor:,.2f}"` del PDF. */
  function dinero(valor) {
    const r = redondear2(valor);
    const [entero, decimales] = Math.abs(r).toFixed(2).split(".");
    return `${r < 0 ? "-" : ""}$ ${entero.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${decimales}`;
  }

  const API = { redondear2, suma, importe, totales, dinero };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  const ITT = (global.ITT = global.ITT || {});
  ITT.totales = API;
})(typeof self !== "undefined" ? self : this);

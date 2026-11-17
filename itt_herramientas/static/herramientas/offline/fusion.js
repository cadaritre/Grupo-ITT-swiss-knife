/* Fusión de tres vías, la misma del servidor (`comun/sincronizacion.py: fusionar`, con pruebas de que dan lo mismo).
 *
 *   ITT.fusionar(base, actual, nuevos, claves)  → el resultado, o lanza ITT.ChoqueDeFusion si los cambios se tocan.
 *
 * Los objetos se fusionan llave por llave; las listas cuentan como un solo valor, salvo las de `claves` ({ruta: llave}), cuyos elementos
 * son objetos con identidad (las fotos de un reporte por `sha256`) y se fusionan elemento por elemento.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const FALTA = Symbol("falta");

  class ChoqueDeFusion extends Error {}

  const esObjeto = (x) => x !== null && typeof x === "object" && !Array.isArray(x);

  function iguales(a, b) {
    if (a === b) return true;
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => iguales(x, b[i]));
    if (esObjeto(a) && esObjeto(b)) {
      const ka = Object.keys(a), kb = Object.keys(b);
      return ka.length === kb.length && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && iguales(a[k], b[k]));
    }
    return false;
  }
  const igual = (a, b) => (a === FALTA || b === FALTA ? a === b : iguales(a, b));
  const de = (o, k) => (Object.prototype.hasOwnProperty.call(o, k) ? o[k] : FALTA);

  function fusionar(base, actual, nuevos, claves, ruta) {
    claves = claves || {};
    ruta = ruta || "";
    if (igual(actual, nuevos)) return actual;
    if (igual(actual, base)) return nuevos;
    if (igual(nuevos, base)) return actual;
    if (esObjeto(base) && esObjeto(actual) && esObjeto(nuevos)) {
      const salida = {};
      for (const k of [...Object.keys(actual), ...Object.keys(nuevos).filter((k) => !Object.prototype.hasOwnProperty.call(actual, k))]) {
        const b = de(base, k), a = de(actual, k), n = de(nuevos, k);
        let r;
        if (igual(a, n)) r = a;
        else if (igual(a, b)) r = n;
        else if (igual(n, b)) r = a;
        else if (a === FALTA || n === FALTA || b === FALTA) throw new ChoqueDeFusion(k);
        else r = fusionar(b, a, n, claves, ruta ? `${ruta}.${k}` : k);
        if (r !== FALTA) salida[k] = r;
      }
      return salida;
    }
    const llave = claves[ruta];
    if (llave && Array.isArray(base) && Array.isArray(actual) && Array.isArray(nuevos)) return fusionarLista(base, actual, nuevos, llave, claves, ruta);
    throw new ChoqueDeFusion(ruta);
  }

  function fusionarLista(base, actual, nuevos, llave, claves, ruta) {
    const ident = (lista) => lista.map((x) => (esObjeto(x) && Object.prototype.hasOwnProperty.call(x, llave) && x[llave] !== null ? JSON.stringify(x[llave]) : null));
    const ib = ident(base), ia = ident(actual), inn = ident(nuevos);
    if ([...ib, ...ia, ...inn].includes(null) || new Set(ia).size !== ia.length || new Set(inn).size !== inn.length) throw new ChoqueDeFusion(ruta);
    const mapa = (ids, lista) => new Map(ids.map((k, i) => [k, lista[i]]));
    const pb = mapa(ib, base), pa = mapa(ia, actual), pn = mapa(inn, nuevos);
    const elementos = new Map();
    for (const k of new Set([...ib, ...ia, ...inn])) {
      const b = pb.has(k) ? pb.get(k) : FALTA, a = pa.has(k) ? pa.get(k) : FALTA, n = pn.has(k) ? pn.get(k) : FALTA;
      let r;
      if (igual(a, n)) r = a;
      else if (igual(a, b)) r = n;
      else if (igual(n, b)) r = a;
      else if (a === FALTA || n === FALTA || b === FALTA) throw new ChoqueDeFusion(ruta);     // uno lo quitó y el otro lo cambió
      else r = fusionar(b, a, n, claves, ruta);
      if (r !== FALTA) elementos.set(k, r);
    }
    const comunes = ib.filter((k) => elementos.has(k));
    const ordenA = ia.filter((k) => comunes.includes(k)), ordenN = inn.filter((k) => comunes.includes(k));
    if (!iguales(ordenA, comunes) && !iguales(ordenN, comunes) && !iguales(ordenA, ordenN)) throw new ChoqueDeFusion(ruta);   // los dos reordenaron distinto
    const orden = !iguales(ordenN, comunes)
      ? [...inn.filter((k) => elementos.has(k)), ...ia.filter((k) => elementos.has(k) && !inn.includes(k))]
      : [...ia.filter((k) => elementos.has(k)), ...inn.filter((k) => elementos.has(k) && !ia.includes(k))];
    return orden.map((k) => elementos.get(k));
  }

  ITT.fusionar = fusionar;
  ITT.ChoqueDeFusion = ChoqueDeFusion;
  if (typeof module !== "undefined" && module.exports) module.exports = { fusionar, ChoqueDeFusion };
})(typeof self !== "undefined" ? self : this);

/* Lector mínimo de EXIF de un JPEG: fecha de captura y GPS. Se ejecuta ANTES de reducir la imagen, porque el lienzo
 * quita los metadatos. Clásico: lo usan el worker (importScripts) y la página. Devuelve {} si no hay nada útil.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});

  function leer(buffer) {
    try { return interpretar(new DataView(buffer)); } catch (_) { return {}; }
  }

  function interpretar(v) {
    if (v.byteLength < 4 || v.getUint16(0) !== 0xffd8) return {};
    let pos = 2;
    while (pos + 4 < v.byteLength) {
      if (v.getUint8(pos) !== 0xff) return {};
      const marca = v.getUint8(pos + 1);
      const largo = v.getUint16(pos + 2);
      if (marca === 0xe1 && v.getUint32(pos + 4) === 0x45786966) return tiff(v, pos + 10, pos + 2 + largo);
      if (marca === 0xda) return {};
      pos += 2 + largo;
    }
    return {};
  }

  function tiff(v, base, fin) {
    const little = v.getUint16(base) === 0x4949;
    const u16 = (o) => v.getUint16(base + o, little);
    const u32 = (o) => v.getUint32(base + o, little);
    const texto = (o, n) => { let s = ""; for (let i = 0; i < n; i++) { const c = v.getUint8(base + o + i); if (!c) break; s += String.fromCharCode(c); } return s; };
    const ifd = (o) => {
      const n = u16(o);
      const campos = {};
      for (let i = 0; i < n; i++) {
        const e = o + 2 + i * 12;
        if (base + e + 12 > fin) break;
        campos[u16(e)] = { tipo: u16(e + 2), cuenta: u32(e + 4), valor: e + 8 };
      }
      return campos;
    };
    const dato = (c) => (c.tipo === 3 ? c.valor : (c.cuenta * ({ 1: 1, 2: 1, 3: 2, 4: 4, 5: 8 }[c.tipo] || 1) > 4 ? u32(c.valor) : c.valor));
    const racional = (c, i) => u32(dato(c) + i * 8) / (u32(dato(c) + i * 8 + 4) || 1);
    const principal = ifd(u32(4));
    const salida = {};
    const fecha = (c) => { const m = c && /^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/.exec(texto(dato(c), c.cuenta)); return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}` : ""; };
    if (principal[0x8769]) {
      const exif = ifd(u32(principal[0x8769].valor));
      salida.tomada = fecha(exif[0x9003]) || fecha(exif[0x9004]);
    }
    if (!salida.tomada) salida.tomada = fecha(principal[0x0132]);
    if (principal[0x0112]) salida.orientacion = u16(principal[0x0112].valor);
    if (principal[0x8825]) {
      const g = ifd(u32(principal[0x8825].valor));
      if (g[2] && g[4] && g[1] && g[3]) {
        const grados = (c) => racional(c, 0) + racional(c, 1) / 60 + racional(c, 2) / 3600;
        let lat = grados(g[2]);
        let lon = grados(g[4]);
        if (texto(g[1].valor, 1).toUpperCase() === "S") lat = -lat;
        if (texto(g[3].valor, 1).toUpperCase() === "W") lon = -lon;
        if (Number.isFinite(lat) && Number.isFinite(lon) && (lat || lon)) { salida.lat = Math.round(lat * 1e6) / 1e6; salida.lon = Math.round(lon * 1e6) / 1e6; }
      }
    }
    if (!salida.tomada) delete salida.tomada;
    return salida;
  }

  ITT.exif = { leer };
})(typeof self !== "undefined" ? self : this);

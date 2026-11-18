/* TVTiff: lector de TIFF, BigTIFF y GeoTIFF por partes, para las ortofotos que viven en la computadora (nunca se suben).
 *
 * Lee del archivo solo lo que se pide (`Blob.slice`, que funciona con archivos de varios GB): la cabecera, los directorios de imagen
 * (IFD) y luego los mosaicos o tiras que una vista necesita. Nunca carga el archivo entero.
 *
 * Soporta:
 *   - TIFF clásico y BigTIFF, en orden II y MM;
 *   - imagen en mosaicos (TileWidth/TileLength) o en tiras (RowsPerStrip), en un plano o en planos separados;
 *   - compresión: ninguna (1), LZW (5), Deflate (8, 32946), PackBits (32773) y JPEG (7, con JPEGTables; YCbCr o RGB);
 *     predictor horizontal (2) en 8 y 16 bits;
 *   - 8 bits por muestra (16 bits se reducen a 8): gris, RGB, RGBA, paleta;
 *   - vistas generales internas (COG o TIFF piramidal): los IFD siguientes con NewSubfileType de imagen reducida, y SubIFDs;
 *   - georreferencia: ModelPixelScale, ModelTiepoint, ModelTransformation, GeoKeyDirectory (tipo de píxel y EPSG).
 * No soporta (lo dice `soporte()`): JPEG antiguo (6), CCITT, flotantes, más de 16 bits, CMYK y Lab.
 *
 * Funciona en la página y en un trabajador (sin DOM: usa `createImageBitmap` y `OffscreenCanvas` solo para JPEG).
 * API: `TVTiff.abrir(blob)` → { ancho, alto, niveles: [nivel…], geo, soporte() }; `TVTiff.bloque(tif, nivel, indice)` → RGBA de ese
 * mosaico o tira; `TVTiff.region(tif, nivel, x0, y0, x1, y1)` → { rgba, ancho, alto } de esa ventana del nivel, a su resolución.
 */
(function (global) {
  "use strict";

  const T = {
    NewSubfileType: 254, ImageWidth: 256, ImageLength: 257, BitsPerSample: 258, Compression: 259, Photometric: 262,
    StripOffsets: 273, SamplesPerPixel: 277, RowsPerStrip: 278, StripByteCounts: 279, PlanarConfig: 284, Predictor: 317,
    ColorMap: 320, TileWidth: 322, TileLength: 323, TileOffsets: 324, TileByteCounts: 325, SubIFDs: 330, ExtraSamples: 338,
    SampleFormat: 339, JPEGTables: 347, YCbCrSubSampling: 530, ModelPixelScale: 33550, ModelTiepoint: 33922,
    ModelTransformation: 34264, GeoKeyDirectory: 34735, GdalNoData: 42113,
  };
  const TAMANO = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8 };

  async function leer(blob, desde, n) {
    return new Uint8Array(await blob.slice(desde, desde + n).arrayBuffer());
  }

  class Vista {
    constructor(bytes, le) { this.b = bytes; this.d = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); this.le = le; }
    u8(o) { return this.d.getUint8(o); }
    u16(o) { return this.d.getUint16(o, this.le); }
    u32(o) { return this.d.getUint32(o, this.le); }
    u64(o) { const a = this.d.getUint32(o, this.le), b = this.d.getUint32(o + 4, this.le); return this.le ? b * 4294967296 + a : a * 4294967296 + b; }
    f32(o) { return this.d.getFloat32(o, this.le); }
    f64(o) { return this.d.getFloat64(o, this.le); }
    i16(o) { return this.d.getInt16(o, this.le); }
    i32(o) { return this.d.getInt32(o, this.le); }
  }

  function valor(v, tipo, o) {
    switch (tipo) {
      case 1: case 2: case 7: return v.u8(o);
      case 3: return v.u16(o);
      case 4: case 13: return v.u32(o);
      case 5: return v.u32(o) / (v.u32(o + 4) || 1);
      case 6: return (v.u8(o) << 24) >> 24;
      case 8: return v.i16(o);
      case 9: return v.i32(o);
      case 10: return v.i32(o) / (v.i32(o + 4) || 1);
      case 11: return v.f32(o);
      case 12: return v.f64(o);
      case 16: case 18: return v.u64(o);
      case 17: return v.u64(o);
      default: return 0;
    }
  }

  /** Lee un IFD en `desde`; devuelve { etiquetas: Map(tag → valores), siguiente }. Los valores largos se leen aparte. */
  async function leerIfd(blob, cab, desde) {
    const { le, grande } = cab;
    const nCab = grande ? 8 : 2, tEntrada = grande ? 20 : 12, enLinea = grande ? 8 : 4;
    const cuenta = new Vista(await leer(blob, desde, nCab), le);
    const n = grande ? cuenta.u64(0) : cuenta.u16(0);
    if (n <= 0 || n > 4096) throw new Error("Directorio de imagen dañado");
    const cuerpo = new Vista(await leer(blob, desde + nCab, n * tEntrada + enLinea), le);
    const etiquetas = new Map();
    const pendientes = [];
    for (let i = 0; i < n; i++) {
      const o = i * tEntrada;
      const tag = cuerpo.u16(o), tipo = cuerpo.u16(o + 2);
      const cant = grande ? cuerpo.u64(o + 4) : cuerpo.u32(o + 4);
      const tam = TAMANO[tipo] || 1;
      const total = cant * tam;
      const vo = o + (grande ? 12 : 8);
      if (total <= enLinea) {
        const vals = [];
        for (let k = 0; k < cant; k++) vals.push(valor(cuerpo, tipo, vo + k * tam));
        etiquetas.set(tag, vals);
      } else {
        const off = grande ? cuerpo.u64(vo) : cuerpo.u32(vo);
        pendientes.push({ tag, tipo, cant, tam, off });
      }
    }
    const siguiente = grande ? cuerpo.u64(n * tEntrada) : cuerpo.u32(n * tEntrada);
    for (const p of pendientes) {
      // Solo lo que sirve: no leemos miniaturas EXIF, XMP ni notas largas.
      if (!QUERIDAS.has(p.tag)) continue;
      const bytes = await leer(blob, p.off, p.cant * p.tam);
      if (p.tag === T.JPEGTables) { etiquetas.set(p.tag, bytes); continue; }
      const v = new Vista(bytes, le);
      const vals = new Array(p.cant);
      for (let k = 0; k < p.cant; k++) vals[k] = valor(v, p.tipo, k * p.tam);
      etiquetas.set(p.tag, vals);
    }
    return { etiquetas, siguiente };
  }
  const QUERIDAS = new Set(Object.values(T));

  function nivelDe(et) {
    const g = (t, d) => (et.has(t) ? et.get(t)[0] : d);
    const ancho = g(T.ImageWidth, 0), alto = g(T.ImageLength, 0);
    const spp = g(T.SamplesPerPixel, 1);
    const bits = et.get(T.BitsPerSample) || [1];
    const mosaico = et.has(T.TileWidth);
    const tw = mosaico ? g(T.TileWidth, 256) : ancho;
    const th = mosaico ? g(T.TileLength, 256) : Math.min(alto, g(T.RowsPerStrip, alto) || alto);
    const offs = et.get(mosaico ? T.TileOffsets : T.StripOffsets) || [];
    const cuentas = et.get(mosaico ? T.TileByteCounts : T.StripByteCounts) || [];
    const planar = g(T.PlanarConfig, 1);
    const extra = et.get(T.ExtraSamples) || [];
    return {
      ancho, alto, spp, bits: bits[0], bitsTodos: bits, mosaico, tw, th,
      columnas: Math.ceil(ancho / tw), filas: Math.ceil(alto / th),
      offs, cuentas, planar,
      compresion: g(T.Compression, 1), foto: g(T.Photometric, spp >= 3 ? 2 : 1), predictor: g(T.Predictor, 1),
      formato: g(T.SampleFormat, 1), subtipo: g(T.NewSubfileType, 0),
      alfa: extra.length > 0 && (extra[0] === 1 || extra[0] === 2) ? 3 : (spp === 4 && !et.has(T.ExtraSamples) ? 3 : -1),
      tablasJpeg: et.get(T.JPEGTables) || null,
      paleta: et.get(T.ColorMap) || null,
    };
  }

  function geoDe(et) {
    if (!(et.has(T.ModelTransformation) || et.has(T.ModelTiepoint))) return null;
    const dir = et.get(T.GeoKeyDirectory) || [];
    let puntual = false, epsg = null, proyectado = null, geografico = null;
    const n = dir[3] || 0;
    for (let i = 0; i < n; i++) {
      const [id, lugar, , v] = dir.slice(4 + i * 4, 8 + i * 4);
      if (lugar !== 0) continue;
      if (id === 1025) puntual = v === 2;
      if (id === 3072 && v && v !== 32767) proyectado = v;
      if (id === 2048 && v && v !== 32767) geografico = v;
    }
    epsg = proyectado || geografico;
    return {
      pixel_scale: et.get(T.ModelPixelScale) || [],
      tiepoints: et.get(T.ModelTiepoint) || [],
      transformation: et.get(T.ModelTransformation) || [],
      pixel_is_point: puntual, epsg,
    };
  }

  function motivoNoSoportado(n) {
    if (n.compresion === 6) return "JPEG antiguo (compresión 6)";
    if ([2, 3, 4].includes(n.compresion)) return "fax (CCITT)";
    if (![1, 5, 7, 8, 32946, 32773].includes(n.compresion)) return `compresión ${n.compresion}`;
    if (n.formato === 3) return "muestras de punto flotante (modelo de elevaciones)";
    if (n.bits !== 8 && n.bits !== 16 && !(n.bits === 1 && n.spp === 1) && !(n.bits === 4 && n.foto === 3)) return `${n.bits} bits por muestra`;
    if (n.foto === 5) return "color CMYK";
    if (n.foto === 8 || n.foto === 9) return "color Lab";
    if (n.predictor === 3) return "predictor de punto flotante";
    if (!n.offs.length) return "sin datos de imagen";
    return null;
  }

  async function abrir(blob) {
    const c = new Vista(await leer(blob, 0, 16), true);
    const orden = String.fromCharCode(c.u8(0), c.u8(1));
    if (orden !== "II" && orden !== "MM") throw new Error("No es un TIFF");
    const le = orden === "II";
    const v = new Vista(c.b, le);
    const magia = v.u16(2);
    if (magia !== 42 && magia !== 43) throw new Error("No es un TIFF");
    const cab = { le, grande: magia === 43 };
    let ifd = cab.grande ? v.u64(8) : v.u32(4);
    const directorios = [];
    const vistos = new Set();
    while (ifd && !vistos.has(ifd) && directorios.length < 64) {
      vistos.add(ifd);
      const { etiquetas, siguiente } = await leerIfd(blob, cab, ifd);
      directorios.push(etiquetas);
      for (const sub of etiquetas.get(T.SubIFDs) || []) {
        if (vistos.has(sub) || directorios.length >= 64) continue;
        vistos.add(sub);
        try { directorios.push((await leerIfd(blob, cab, sub)).etiquetas); } catch (_) { /* SubIFD dañado: se ignora */ }
      }
      ifd = siguiente;
    }
    if (!directorios.length) throw new Error("TIFF sin imágenes");
    const principal = nivelDe(directorios[0]);
    // Vistas generales: imagen reducida (bit 0), no máscara (bit 2), con la misma forma, más pequeñas.
    const niveles = [principal];
    for (const et of directorios.slice(1)) {
      const n = nivelDe(et);
      if ((n.subtipo & 4) || !(n.subtipo & 1)) continue;
      if (n.ancho >= principal.ancho || n.ancho < 1) continue;
      if (Math.abs(n.ancho / n.alto - principal.ancho / principal.alto) > 0.02 * (principal.ancho / principal.alto) + 2 / n.alto) continue;
      if (motivoNoSoportado(n)) continue;
      n.tablasJpeg = n.tablasJpeg || principal.tablasJpeg;
      niveles.push(n);
    }
    niveles.sort((a, b) => b.ancho - a.ancho);
    for (const n of niveles) n.factor = principal.ancho / n.ancho;
    return {
      blob, le, grande: cab.grande, ancho: principal.ancho, alto: principal.alto, niveles,
      geo: geoDe(directorios[0]),
      motivo: motivoNoSoportado(principal),
      bytesSinComprimir: principal.ancho * principal.alto * Math.max(1, principal.spp) * Math.max(1, principal.bits / 8),
    };
  }

  // ── Descompresión ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

  function lzw(entrada, esperado) {
    const salida = new Uint8Array(esperado);
    let pos = 0;
    const tablaPref = new Int32Array(4096), tablaByte = new Uint8Array(4096), tablaLargo = new Uint16Array(4096);
    for (let i = 0; i < 256; i++) { tablaPref[i] = -1; tablaByte[i] = i; tablaLargo[i] = 1; }
    let siguiente = 258, ancho = 9, anterior = -1;
    const pila = new Uint8Array(4097);
    const escribir = (codigo) => {
      const largo = tablaLargo[codigo];
      let k = largo, c = codigo;
      while (k > 0) { pila[--k] = tablaByte[c]; c = tablaPref[c]; }
      const n = Math.min(largo, esperado - pos);
      salida.set(pila.subarray(0, n), pos);
      pos += n;
      return pila[0];
    };
    let acc = 0, accBits = 0, ip = 0;
    const largoEntrada = entrada.length;
    while (pos < esperado) {
      // Lectura MSB primero, de a un byte en un acumulador.
      while (accBits < ancho && ip < largoEntrada) { acc = ((acc << 8) | entrada[ip++]) >>> 0; accBits += 8; }
      if (accBits < ancho) break;
      const codigo = (acc >>> (accBits - ancho)) & ((1 << ancho) - 1);
      accBits -= ancho;
      acc &= (1 << accBits) - 1;
      if (codigo === 257) break;
      if (codigo === 256) { siguiente = 258; ancho = 9; anterior = -1; continue; }
      if (anterior === -1) { escribir(codigo); anterior = codigo; continue; }
      let primero;
      if (codigo < siguiente) {
        primero = escribir(codigo);
        if (siguiente < 4096) { tablaPref[siguiente] = anterior; tablaByte[siguiente] = primero; tablaLargo[siguiente] = tablaLargo[anterior] + 1; siguiente++; }
      } else {
        // KwKwK: el código nuevo es el anterior más su primer byte.
        let c = anterior; while (tablaPref[c] !== -1) c = tablaPref[c];
        primero = tablaByte[c];
        if (siguiente < 4096) { tablaPref[siguiente] = anterior; tablaByte[siguiente] = primero; tablaLargo[siguiente] = tablaLargo[anterior] + 1; siguiente++; }
        escribir(siguiente - 1);
      }
      anterior = codigo;
      // Cambio de ancho «temprano», como TIFF (un código antes que GIF).
      if (siguiente + 1 >= (1 << ancho) && ancho < 12) ancho++;
    }
    return salida;
  }

  function packbits(entrada, esperado) {
    const salida = new Uint8Array(esperado);
    let i = 0, o = 0;
    while (i < entrada.length && o < esperado) {
      const n = (entrada[i++] << 24) >> 24;
      if (n >= 0) { const k = Math.min(n + 1, esperado - o); salida.set(entrada.subarray(i, i + k), o); i += n + 1; o += k; }
      else if (n !== -128) { const k = Math.min(1 - n, esperado - o); salida.fill(entrada[i++], o, o + k); o += k; }
    }
    return salida;
  }

  async function inflar(entrada) {
    const flujo = new Blob([entrada]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(flujo).arrayBuffer());
  }

  /** JPEG en TIFF (compresión 7): tablas compartidas + datos del bloque, decodificado por el navegador. */
  async function jpeg(n, entrada, ancho, alto) {
    let datos = entrada;
    if (n.tablasJpeg && n.tablasJpeg.length > 4) {
      const t = n.tablasJpeg;
      // tablas sin su EOI (FFD9) + bloque sin su SOI (FFD8)
      const a = t.subarray(0, t.length - 2), b = entrada.subarray(2);
      datos = new Uint8Array(a.length + b.length);
      datos.set(a, 0); datos.set(b, a.length);
    }
    // RGB sin transformación (fotometría 2): el navegador asumiría YCbCr; un marcador Adobe con transformación 0 lo evita.
    if (n.foto === 2 && !tieneAdobe(datos)) datos = conAdobe(datos);
    const bmp = await createImageBitmap(new Blob([datos], { type: "image/jpeg" }), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
    const lienzo = new OffscreenCanvas(ancho, alto);
    const ctx = lienzo.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0);
    bmp.close && bmp.close();
    return { rgba: ctx.getImageData(0, 0, ancho, alto).data, yaRgba: true };
  }
  function tieneAdobe(d) {
    for (let i = 2; i + 4 < d.length && i < 65536;) {
      if (d[i] !== 0xff) return false;
      const m = d[i + 1];
      if (m === 0xee) return true;
      if (m === 0xda) return false;
      i += 2 + ((d[i + 2] << 8) | d[i + 3]);
    }
    return false;
  }
  function conAdobe(d) {
    const app14 = new Uint8Array([0xff, 0xee, 0, 14, 0x41, 0x64, 0x6f, 0x62, 0x65, 0, 100, 0, 0, 0, 0, 0]);
    const out = new Uint8Array(d.length + app14.length);
    out.set(d.subarray(0, 2), 0); out.set(app14, 2); out.set(d.subarray(2), 2 + app14.length);
    return out;
  }

  async function descomprimir(n, crudo, esperado) {
    switch (n.compresion) {
      case 1: return crudo;
      case 5: return lzw(crudo, esperado);
      case 8: case 32946: return inflar(crudo);
      case 32773: return packbits(crudo, esperado);
      default: throw new Error(`compresión ${n.compresion} no soportada`);
    }
  }

  function despredecir(n, datos, ancho, filas, muestras) {
    if (n.predictor !== 2) return;
    if (n.bits === 8) {
      for (let y = 0; y < filas; y++) {
        const fila = y * ancho * muestras;
        for (let x = muestras; x < ancho * muestras; x++) datos[fila + x] = (datos[fila + x] + datos[fila + x - muestras]) & 255;
      }
    } else if (n.bits === 16) {
      const v = new DataView(datos.buffer, datos.byteOffset, datos.byteLength);
      for (let y = 0; y < filas; y++) {
        const fila = y * ancho * muestras * 2;
        for (let x = muestras; x < ancho * muestras; x++) {
          const o = fila + x * 2;
          v.setUint16(o, (v.getUint16(o, n.le) + v.getUint16(o - muestras * 2, n.le)) & 65535, n.le);
        }
      }
    }
  }

  /** Muestras crudas (ya descomprimidas, en orden del archivo) → RGBA8. */
  function aRgba(n, planos, ancho, filas, le) {
    const salida = new Uint8ClampedArray(ancho * filas * 4);
    const spp = n.planar === 2 ? 1 : n.spp;
    const leer8 = (datos, i) => datos[i];
    const leer16 = (datos, i) => (le ? datos[i * 2 + 1] : datos[i * 2]);
    const leerM = n.bits === 16 ? leer16 : leer8;
    const total = ancho * filas;
    const muestra = (k, p) => (n.planar === 2 ? leerM(planos[k], p) : leerM(planos[0], p * spp + k));
    if (n.foto === 3 && n.paleta) {
      const pal = n.paleta, tam = pal.length / 3;
      for (let p = 0; p < total; p++) {
        let ix;
        if (n.bits === 4) { const b = planos[0][(p / 2) | 0]; ix = p % 2 ? b & 15 : b >> 4; } else ix = leerM(planos[0], p);
        salida[p * 4] = pal[ix] >> 8; salida[p * 4 + 1] = pal[tam + ix] >> 8; salida[p * 4 + 2] = pal[2 * tam + ix] >> 8; salida[p * 4 + 3] = 255;
      }
      return salida;
    }
    if (n.bits === 1) {
      const porFila = Math.ceil(ancho / 8);
      for (let y = 0; y < filas; y++) for (let x = 0; x < ancho; x++) {
        const bit = (planos[0][y * porFila + (x >> 3)] >> (7 - (x & 7))) & 1;
        const g = (n.foto === 0 ? !bit : bit) ? 255 : 0;
        const o = (y * ancho + x) * 4;
        salida[o] = salida[o + 1] = salida[o + 2] = g; salida[o + 3] = 255;
      }
      return salida;
    }
    if (n.spp >= 3) {
      for (let p = 0; p < total; p++) {
        const o = p * 4;
        salida[o] = muestra(0, p); salida[o + 1] = muestra(1, p); salida[o + 2] = muestra(2, p);
        salida[o + 3] = n.alfa === 3 ? muestra(3, p) : 255;
      }
    } else {
      const invertir = n.foto === 0;
      const conAlfa = n.spp === 2;
      for (let p = 0; p < total; p++) {
        let g = muestra(0, p); if (invertir) g = 255 - g;
        const o = p * 4;
        salida[o] = salida[o + 1] = salida[o + 2] = g;
        salida[o + 3] = conAlfa ? muestra(1, p) : 255;
      }
    }
    return salida;
  }

  /** RGBA de un mosaico o tira (`indice` en el orden del archivo). Ancho y alto completos del bloque (los del borde se rellenan). */
  async function bloque(tif, n, indice) {
    const ancho = n.tw;
    const filas = n.mosaico ? n.th : Math.min(n.th, n.alto - indice * n.th);
    const porPlano = n.columnas * n.filas;
    const planos = n.planar === 2 ? n.spp : 1;
    const sppBloque = n.planar === 2 ? 1 : n.spp;
    const porFila = n.bits === 1 ? Math.ceil(ancho / 8) : (n.bits === 4 ? Math.ceil(ancho / 2) : ancho * sppBloque * (n.bits / 8));
    const esperado = porFila * filas;
    if (n.compresion === 7) {
      const off = n.offs[indice], cuenta = n.cuentas[indice];
      if (!off || !cuenta) return { rgba: new Uint8ClampedArray(ancho * filas * 4), ancho, filas };
      const crudo = await leer(tif.blob, off, cuenta);
      const r = await jpeg(n, crudo, ancho, filas);
      return { rgba: r.rgba, ancho, filas };
    }
    const datos = [];
    for (let p = 0; p < planos; p++) {
      const k = indice + p * porPlano;
      const off = n.offs[k], cuenta = n.cuentas[k];
      if (!off || !cuenta) { datos.push(new Uint8Array(esperado)); continue; }
      const crudo = await leer(tif.blob, off, cuenta);
      let d = await descomprimir(n, crudo, esperado);
      if (d.length < esperado) { const r = new Uint8Array(esperado); r.set(d); d = r; }
      despredecir({ ...n, le: tif.le }, d, ancho, filas, sppBloque);
      datos.push(d);
    }
    return { rgba: aRgba(n, datos, ancho, filas, tif.le), ancho, filas };
  }

  /** Índices de los bloques que cubren la ventana [x0, x1) × [y0, y1) del nivel. */
  function bloquesDe(n, x0, y0, x1, y1) {
    const c0 = Math.max(0, Math.floor(x0 / n.tw)), c1 = Math.min(n.columnas - 1, Math.floor((x1 - 1) / n.tw));
    const f0 = Math.max(0, Math.floor(y0 / n.th)), f1 = Math.min(n.filas - 1, Math.floor((y1 - 1) / n.th));
    const out = [];
    for (let f = f0; f <= f1; f++) for (let c = c0; c <= c1; c++) out.push({ indice: f * n.columnas + c, x: c * n.tw, y: f * n.th });
    return out;
  }

  /** Bytes que habría que leer del archivo para la ventana (para decidir si conviene leer del original o esperar la vista general). */
  function costo(n, x0, y0, x1, y1) {
    let total = 0;
    for (const b of bloquesDe(n, x0, y0, x1, y1)) total += (n.cuentas[b.indice] || 0) * (n.planar === 2 ? n.spp : 1);
    return total;
  }

  global.TVTiff = { abrir, bloque, bloquesDe, costo, lzw, packbits, motivoNoSoportado, T };
})(typeof self !== "undefined" ? self : globalThis);

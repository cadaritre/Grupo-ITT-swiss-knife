/* TVCarpeta: la «carpeta del proyecto en esta computadora» de un plano CAD. Nada de lo que hay en ella se sube nunca.
 *
 * - Vincular: en Chrome y Edge (Windows y Mac) con `showDirectoryPicker`; el permiso se guarda en IndexedDB de este navegador por cuenta
 *   y plano, y tras reiniciar se pide de nuevo con un solo toque. En Safari y Firefox, la carpeta se elige (o se suelta) para la sesión:
 *   el navegador no deja recordarla.
 * - Índice: rutas relativas de los archivos que sirven (imágenes, archivos de mundo, DWG y DXF), sin leer su contenido.
 * - Resolver una referencia del plano (ruta absoluta de Windows o Mac, o relativa): primero por la ruta relativa más larga que coincida
 *   (sin distinguir mayúsculas), luego por el nombre del archivo. Dice cuál coincidió y si hubo varias con el mismo nombre.
 *
 * API pura (también en Node para las pruebas): TVCarpeta.partes(ref), indice(entradas), resolver(indice, ref).
 * API del navegador: TVCarpeta.soportaRecordar(), guardadas(clave), vincular(clave, {agregar}), permitir(clave), desvincular(clave),
 * desdeEntrada(fileList), desdeSoltar(dataTransfer), indexar(raices) → {indice, archivos, raices}.
 */
(function (global) {
  "use strict";

  const UTILES = /\.(tif|tiff|tifw|tfw|jpg|jpeg|jpgw|jgw|jpegw|png|pngw|pgw|wld|bmp|bpw|gif|gfw|webp|dwg|dxf|pdf)$/i;
  const MAX_ARCHIVOS = 200000;
  const MAX_PROFUNDIDAD = 16;

  /** Componentes de una referencia, sin unidad, servidor, «.» ni «..». */
  function partes(ref) {
    let s = String(ref || "").trim().replace(/^"+|"+$/g, "").replace(/\\/g, "/");
    s = s.replace(/^file:\/\/+/i, "/");
    s = s.replace(/^[a-z]:\//i, "/");          // C:/…
    s = s.replace(/^\/\/[^/]+\/[^/]+\//, "/");  // //servidor/recurso/…
    return s.split("/").filter((p) => p && p !== "." && p !== "..");
  }

  const clave = (componentes) => componentes.join("/").toLowerCase();

  /** `entradas`: [{ruta: "Imagenes/Fachada_Norte/f001.png", raiz, …}]. Índice por cada sufijo de ruta. */
  function indice(entradas) {
    const porSufijo = new Map();
    for (const e of entradas) {
      const p = e.ruta.split("/");
      for (let k = 1; k <= p.length; k++) {
        const s = clave(p.slice(p.length - k));
        let lista = porSufijo.get(s);
        if (!lista) porSufijo.set(s, (lista = []));
        lista.push(e);
      }
    }
    return { porSufijo, total: entradas.length };
  }

  /**
   * → {estado: "ruta" | "nombre" | "ambigua" | "falta", entrada, coincide (componentes que coincidieron), candidatos}
   * "ruta": coincidió más que el nombre (al menos su carpeta); "nombre": solo el nombre, y es el único con ese nombre;
   * "ambigua": varios archivos empatan en la coincidencia más larga (se usa el primero por orden alfabético y se avisa).
   */
  function resolver(ind, ref) {
    const p = partes(ref);
    if (!p.length || !ind) return { estado: "falta", entrada: null, coincide: 0, candidatos: [] };
    for (let k = p.length; k >= 1; k--) {
      const lista = ind.porSufijo.get(clave(p.slice(p.length - k)));
      if (!lista || !lista.length) continue;
      const orden = lista.slice().sort((a, b) => (a.ruta.length - b.ruta.length) || a.ruta.localeCompare(b.ruta));
      const estado = lista.length > 1 ? "ambigua" : (k >= 2 ? "ruta" : "nombre");
      return { estado, entrada: orden[0], coincide: k, candidatos: orden.slice(0, 8) };
    }
    return { estado: "falta", entrada: null, coincide: 0, candidatos: [] };
  }

  // ── Navegador ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  const soportaRecordar = () => typeof global.showDirectoryPicker === "function";

  function bd() {
    return new Promise((ok, mal) => {
      const r = indexedDB.open("tv-cad-carpetas", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("carpetas");
      r.onsuccess = () => ok(r.result);
      r.onerror = () => mal(r.error);
    });
  }
  async function bdHacer(modo, fn) {
    const base = await bd();
    try {
      return await new Promise((ok, mal) => {
        const t = base.transaction("carpetas", modo);
        const r = fn(t.objectStore("carpetas"));
        t.oncomplete = () => ok(r && r.result);
        t.onerror = () => mal(t.error);
      });
    } finally { base.close(); }
  }
  /** Carpetas guardadas para esta cuenta y plano: [{handle, nombre, desde}]. */
  async function guardadas(k) {
    try { return (await bdHacer("readonly", (s) => s.get(k))) || []; } catch (_) { return []; }
  }
  async function guardar(k, lista) { await bdHacer("readwrite", (s) => s.put(lista, k)); }

  /** Elige una carpeta (gesto de la persona). `agregar`: otra más (p. ej. las imágenes en otro disco). */
  async function vincular(k, { agregar = false, inicio } = {}) {
    const handle = await global.showDirectoryPicker({ id: "tv-cad-proyecto", mode: "read", ...(inicio ? { startIn: inicio } : {}) });
    const actuales = agregar ? await guardadas(k) : [];
    for (const c of actuales) if (await c.handle.isSameEntry(handle)) return actuales;
    const lista = [...actuales, { handle, nombre: handle.name, desde: Date.now() }];
    await guardar(k, lista);
    return lista;
  }
  /** Guarda una carpeta que llegó de otra forma (soltada sobre la página). */
  async function agregar(k, handle) {
    const actuales = await guardadas(k);
    for (const c of actuales) if (await c.handle.isSameEntry(handle)) return actuales;
    const lista = [...actuales, { handle, nombre: handle.name, desde: Date.now() }];
    await guardar(k, lista);
    return lista;
  }

  /** Estado del permiso de cada carpeta guardada, sin pedirlo. */
  async function permisos(lista) {
    const out = [];
    for (const c of lista) {
      let p = "prompt";
      try { p = await c.handle.queryPermission({ mode: "read" }); } catch (_) { p = "denied"; }
      out.push(p);
    }
    return out;
  }
  /** Pide el permiso (con el toque de la persona). */
  async function permitir(lista) {
    let todas = true;
    for (const c of lista) {
      try { if ((await c.handle.requestPermission({ mode: "read" })) !== "granted") todas = false; } catch (_) { todas = false; }
    }
    return todas;
  }
  async function desvincular(k) { await bdHacer("readwrite", (s) => s.delete(k)); }

  /** Recorre una carpeta con permiso (File System Access). */
  async function recorrer(raiz, n, salida, avisar) {
    const pila = [[raiz.handle, ""]];
    while (pila.length) {
      const [dir, prefijo] = pila.pop();
      for await (const [nombre, h] of dir.entries()) {
        if (salida.length >= MAX_ARCHIVOS) return;
        if (nombre.startsWith(".")) continue;
        const ruta = prefijo ? `${prefijo}/${nombre}` : nombre;
        if (h.kind === "directory") {
          if (ruta.split("/").length < MAX_PROFUNDIDAD && !/^(node_modules|\$recycle\.bin|system volume information)$/i.test(nombre)) pila.push([h, ruta]);
        } else if (UTILES.test(nombre)) {
          salida.push({ ruta, nombre, raiz: n, handle: h, carpeta: dir });
          if (avisar && salida.length % 500 === 0) avisar(salida.length);
        }
      }
    }
  }

  /** `raices`: [{handle}] (con permiso) o [{archivos: File[] con webkitRelativePath}]. */
  async function indexar(raices, avisar) {
    const entradas = [];
    for (let n = 0; n < raices.length; n++) {
      const r = raices[n];
      if (r.handle) await recorrer(r, n, entradas, avisar);
      else for (const f of r.archivos || []) {
        if (!UTILES.test(f.name)) continue;
        // webkitRelativePath trae la carpeta elegida delante: se quita para que las rutas sean relativas a ella.
        const rel = (f.webkitRelativePath || f.ruta || f.name).split("/");
        const ruta = rel.length > 1 && !f.ruta ? rel.slice(1).join("/") : rel.join("/");
        entradas.push({ ruta, nombre: f.name, raiz: n, archivo: f });
      }
    }
    return { indice: indice(entradas), entradas };
  }

  /** Archivos elegidos con <input webkitdirectory> → raíz de sesión. */
  function desdeEntrada(lista) {
    const archivos = Array.from(lista || []);
    const nombre = archivos.length ? (archivos[0].webkitRelativePath || "").split("/")[0] || "Carpeta" : "Carpeta";
    return { nombre, archivos, sesion: true };
  }

  /** Una carpeta soltada sobre la página → raíz (handle si el navegador lo da; si no, archivos de sesión). */
  async function desdeSoltar(dt) {
    const items = Array.from(dt.items || []);
    for (const it of items) {
      if (it.kind !== "file") continue;
      if (it.getAsFileSystemHandle) {
        const h = await it.getAsFileSystemHandle();
        if (h && h.kind === "directory") return { handle: h, nombre: h.name };
      }
      const entrada = it.webkitGetAsEntry && it.webkitGetAsEntry();
      if (entrada && entrada.isDirectory) {
        const archivos = [];
        const leerDir = (dir, prefijo) => new Promise((ok) => {
          const lector = dir.createReader();
          const pendientes = [];
          const tanda = () => lector.readEntries(async (lote) => {
            if (!lote.length) { await Promise.all(pendientes); return ok(); }
            for (const e of lote) {
              const ruta = prefijo ? `${prefijo}/${e.name}` : e.name;
              if (e.isDirectory) pendientes.push(leerDir(e, ruta));
              else if (UTILES.test(e.name)) pendientes.push(new Promise((r) => e.file((f) => { try { Object.defineProperty(f, "ruta", { value: ruta }); } catch (_) { /* */ } archivos.push(f); r(); }, () => r())));
            }
            tanda();
          }, () => ok());
          tanda();
        });
        await leerDir(entrada, "");
        return { nombre: entrada.name, archivos, sesion: true };
      }
    }
    return null;
  }

  async function archivoDe(entrada) {
    if (entrada.archivo) return entrada.archivo;
    return entrada.handle.getFile();
  }

  global.TVCarpeta = { partes, indice, resolver, soportaRecordar, guardadas, vincular, agregar, permisos, permitir, desvincular, indexar, desdeEntrada,
    desdeSoltar, archivoDe, UTILES };
})(typeof self !== "undefined" ? self : globalThis);

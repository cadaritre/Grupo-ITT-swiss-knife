/* Cola local en IndexedDB (arquitectura §18.3). Script clásico: lo cargan las páginas y el service worker.
 *
 * Una captura se escribe en UNA transacción y solo cuenta como guardada cuando la transacción termina
 * (`oncomplete`). Nada se borra de aquí hasta que el servidor confirma el mismo sha256 (ver subidor.js).
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const NOMBRE = "itt-cola";
  const VERSION = 2;
  let abierta = null;

  function abrir() {
    if (abierta) return abierta;
    abierta = new Promise((resolver, rechazar) => {
      const peticion = indexedDB.open(NOMBRE, VERSION);
      peticion.onupgradeneeded = () => {
        const db = peticion.result;
        if (!db.objectStoreNames.contains("items")) {
          const items = db.createObjectStore("items", { keyPath: "id" });
          items.createIndex("estado", "estado");
          items.createIndex("creada", "creada");
        }
        // El Blob de la foto vive en su propio almacén y NO se vuelve a escribir: en WebKit, regrabar un registro con un Blob
        // invalida los Blob que ya se habían leído («The object can not be found here») y la subida fallaba al reintentar.
        if (!db.objectStoreNames.contains("blobs")) db.createObjectStore("blobs", { keyPath: "id" });
        // Borradores de documentos (cotizaciones...): un registro por borrador, con su estado de sincronización.
        if (!db.objectStoreNames.contains("borradores")) db.createObjectStore("borradores", { keyPath: "id" }).createIndex("cuenta", "cuenta");
        if (!db.objectStoreNames.contains("ajustes")) db.createObjectStore("ajustes", { keyPath: "clave" });
      };
      peticion.onsuccess = () => {
        const db = peticion.result;
        db.onversionchange = () => { db.close(); abierta = null; };
        resolver(db);
      };
      peticion.onerror = () => { abierta = null; rechazar(peticion.error); };
      peticion.onblocked = () => rechazar(new Error("IndexedDB bloqueada por otra pestaña"));
    });
    return abierta;
  }

  /** Ejecuta `trabajo(almacen…)` en una transacción y resuelve cuando ESTA TERMINA (no cuando se pidió). */
  async function transaccion(nombres, modo, trabajo) {
    const db = await abrir();
    return new Promise((resolver, rechazar) => {
      const tx = db.transaction(nombres, modo);
      let resultado;
      tx.oncomplete = () => resolver(resultado);
      tx.onerror = () => rechazar(tx.error);
      tx.onabort = () => rechazar(tx.error || new Error("transacción abortada"));
      try {
        const almacenes = nombres.map((n) => tx.objectStore(n));
        const salida = trabajo(...almacenes);
        if (salida && typeof salida.then === "function") salida.then((v) => { resultado = v; }, rechazar);
        else resultado = salida;
      } catch (e) { try { tx.abort(); } catch (_) { /* ya terminó */ } rechazar(e); }
    });
  }

  const pedir = (peticion) => new Promise((ok, mal) => { peticion.onsuccess = () => ok(peticion.result); peticion.onerror = () => mal(peticion.error); });

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  function avisar() {
    try { new BroadcastChannel("itt-cola").postMessage({ tipo: "cambio" }); } catch (_) { /* sin BroadcastChannel */ }
  }

  const cola = {
    uuid,
    abrir,
    /** Guarda una captura. Resuelve con su id SOLO cuando la transacción (metadatos y foto) terminó. */
    async agregar(item) {
      const ahora = Date.now();
      const { blob, ...resto } = item;
      if (!(blob instanceof Blob)) throw new Error("La captura necesita un blob");
      const completo = Object.assign({ id: uuid(), estado: "en_cola", intentos: 0, progreso: 0, creada: ahora, actualizada: ahora, error: "" }, resto, { bytes: blob.size });
      await transaccion(["items", "blobs"], "readwrite", (items, blobs) => { items.add(completo); blobs.add({ id: completo.id, blob }); });
      avisar();
      return completo.id;
    },
    /** Metadatos de todas las capturas (sin las fotos), de la más vieja a la más nueva. */
    async listar() {
      const todos = await transaccion(["items"], "readonly", (items) => pedir(items.getAll()));
      return todos.sort((a, b) => a.creada - b.creada);
    },
    async obtener(id) { return transaccion(["items"], "readonly", (items) => pedir(items.get(id))); },
    /** La foto de una captura (se lee en cada uso; nunca se regraba). */
    async blob(id) { const fila = await transaccion(["blobs"], "readonly", (b) => pedir(b.get(id))); return fila ? fila.blob : null; },
    /** Mezcla `parche` en los metadatos. No toca la foto. */
    async actualizar(id, parche) {
      const nuevo = await transaccion(["items"], "readwrite", async (items) => {
        const actual = await pedir(items.get(id));
        if (!actual) return null;
        const fusionado = Object.assign(actual, parche, { actualizada: Date.now() });
        items.put(fusionado);
        return fusionado;
      });
      avisar();
      return nuevo;
    },
    async borrar(id) { await transaccion(["items", "blobs"], "readwrite", (items, blobs) => { items.delete(id); blobs.delete(id); }); avisar(); },
    async resumen() {
      const todos = await cola.listar();
      const r = { total: todos.length, en_cola: 0, subiendo: 0, error: 0, bytes: 0, items: todos, borradores: 0 };
      for (const i of todos) { r[i.estado] = (r[i.estado] || 0) + 1; r.bytes += i.bytes; }
      // Un borrador cuenta como pendiente mientras el servidor no tenga su última versión.
      const cuenta = await cola.ajuste("cuenta", null);
      for (const b of await cola.borradores()) if (b.sucio && (b.cuenta == null || cuenta == null || b.cuenta === cuenta)) r.borradores += 1;
      r.total += r.borradores;
      return r;
    },
    /** Borradores (de la cuenta indicada, o todos). */
    async borradores(cuenta) {
      const todos = await transaccion(["borradores"], "readonly", (b) => pedir(b.getAll()));
      return cuenta === undefined ? todos : todos.filter((b) => b.cuenta === cuenta);
    },
    async borrador(id) { return transaccion(["borradores"], "readonly", (b) => pedir(b.get(id))); },
    /** Escribe el borrador completo en UNA transacción (sale de aquí hasta que se resuelve, ya guardado). */
    async guardarBorrador(registro) {
      await transaccion(["borradores"], "readwrite", (b) => { b.put(registro); });
      avisar();
      return registro;
    },
    /** Lee-modifica-escribe en la misma transacción (así dos escrituras seguidas no se pisan). */
    async actualizarBorrador(id, cambiar) {
      const nuevo = await transaccion(["borradores"], "readwrite", async (b) => {
        const actual = await pedir(b.get(id));
        if (!actual) return null;
        const resultado = cambiar(actual);
        if (resultado === null) { b.delete(id); return null; }
        b.put(resultado || actual);
        return resultado || actual;
      });
      avisar();
      return nuevo;
    },
    async quitarBorrador(id) { await transaccion(["borradores"], "readwrite", (b) => { b.delete(id); }); avisar(); },
    async ajuste(clave, porOmision) {
      const fila = await transaccion(["ajustes"], "readonly", (a) => pedir(a.get(clave)));
      return fila ? fila.valor : porOmision;
    },
    async guardarAjuste(clave, valor) { await transaccion(["ajustes"], "readwrite", (a) => { a.put({ clave, valor }); }); },
  };
  ITT.cola = cola;
})(typeof self !== "undefined" ? self : this);

/* CAD · lo que queda en este navegador y qué pasa al cerrar sesión (auditoría A-1). Se carga en todas las páginas de Herramientas.
 *
 * - El editor deja copias de los planos en el almacenamiento del navegador (su lista de «recientes», su autoguardado y los nombres de
 *   su sesión). Al cerrar sesión se borran; también al arrancar el editor (cad/editor/arranque.js) y al salir de la página del plano.
 * - Planos «Disponibles sin conexión» (modo táctil, base `tv-cad-campo`): al cerrar sesión se borran las copias. Los cambios hechos sin
 *   conexión que aún no se subieron NO se borran sin avisar: antes de salir se ofrece descargarlos, y si la persona sale igual se quedan
 *   en este dispositivo, ocultos, hasta que vuelva a entrar la misma cuenta (como las fotos pendientes de Capturar). Mientras la sesión
 *   está cerrada, la página sin conexión no muestra nada.
 *
 * API: window.TVCadSesion = { abrir(): Promise<IDBDatabase|null>, pendientes(cuenta?), alSalir(): Promise<pendientes>, limpiarEditor(),
 *       descargar(cambio) }
 */
(function () {
  "use strict";
  if (window.TVCadSesion) return;
  const BASE = "tv-cad-campo", VERSION = 1;
  const COPIAS_DEL_EDITOR = ["opencadstudio-recent", "opencadstudio-autosave"];

  async function limpiarEditor() {
    try { localStorage.removeItem("opencadstudio.session"); } catch (_) { /* sin almacenamiento */ }
    try {
      if (!navigator.storage || !navigator.storage.getDirectory) return;
      const raiz = await navigator.storage.getDirectory();
      for (const n of COPIAS_DEL_EDITOR) { try { await raiz.removeEntry(n, { recursive: true }); } catch (_) { /* no existe */ } }
    } catch (_) { /* sin almacenamiento de archivos del navegador */ }
  }

  // La base del modo sin conexión. `crear: false` no la crea si nunca se usó (para no dejar una base vacía en cada teléfono).
  async function abrir({ crear = true } = {}) {
    if (!("indexedDB" in window)) return null;
    if (!crear && indexedDB.databases) {
      try { if (!(await indexedDB.databases()).some((d) => d.name === BASE)) return null; } catch (_) { /* se intenta abrir */ }
    }
    return new Promise((ok) => {
      let r;
      try { r = indexedDB.open(BASE, VERSION); } catch (_) { ok(null); return; }
      r.onupgradeneeded = () => {
        const db = r.result;
        // planos: la versión llevada al teléfono {id, cuenta, nombre, proyecto, formato, revision, bytes, tamano, guardado}
        if (!db.objectStoreNames.contains("planos")) db.createObjectStore("planos", { keyPath: "id" });
        // cambios: lo editado sin conexión {id, cuenta, nombre, formato, base, bytes, tamano, actualizado}; nunca es una versión
        if (!db.objectStoreNames.contains("cambios")) db.createObjectStore("cambios", { keyPath: "id" });
        // estado: {clave: "sesion", cuenta, cerrada}
        if (!db.objectStoreNames.contains("estado")) db.createObjectStore("estado", { keyPath: "clave" });
      };
      r.onsuccess = () => ok(r.result);
      r.onerror = () => ok(null);
      r.onblocked = () => ok(null);
    });
  }
  function tx(db, tienda, modo, fn) {
    return new Promise((ok, mal) => {
      const t = db.transaction(tienda, modo); let res;
      t.oncomplete = () => ok(res); t.onerror = () => mal(t.error); t.onabort = () => mal(t.error);
      res = fn(t.objectStore(tienda));
    });
  }
  const todos = (db, tienda) => new Promise((ok) => {
    const t = db.transaction(tienda, "readonly"), r = t.objectStore(tienda).getAll();
    r.onsuccess = () => ok(r.result || []); r.onerror = () => ok([]);
  });

  async function pendientes(cuenta) {
    const db = await abrir({ crear: false });
    if (!db) return [];
    try { return (await todos(db, "cambios")).filter((c) => cuenta === undefined || c.cuenta === cuenta); } finally { db.close(); }
  }

  async function alSalir() {
    await limpiarEditor();
    const db = await abrir({ crear: false });
    if (!db) return [];
    try {
      const cambios = await todos(db, "cambios");
      await tx(db, "planos", "readwrite", (s) => s.clear());                 // las copias sin cambios: fuera
      const previo = await new Promise((ok) => { const r = db.transaction("estado").objectStore("estado").get("sesion"); r.onsuccess = () => ok(r.result); r.onerror = () => ok(null); });
      await tx(db, "estado", "readwrite", (s) => s.put({ ...(previo || {}), clave: "sesion", cerrada: true }));
      return cambios;
    } finally { db.close(); }
  }

  function descargar(c) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([c.bytes], { type: "application/octet-stream" }));
    a.download = `${c.nombre || "plano"} (cambios sin conexión).${c.formato || "dwg"}`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }

  // Hoja «Antes de cerrar sesión» cuando hay cambios de planos sin subir. Devuelve true si la persona decide salir.
  function preguntar(cambios) {
    return new Promise((ok) => {
      const d = document.createElement("dialog");
      d.className = "tv-hoja tv-hoja--confirmar"; d.setAttribute("aria-labelledby", "cad-sesion-titulo");
      const cuerpo = document.createElement("div"); cuerpo.className = "tv-hoja__cuerpo";
      const h = document.createElement("h2"); h.id = "cad-sesion-titulo"; h.textContent = "Tienes cambios de planos sin subir";
      const p = document.createElement("p");
      p.textContent = `${cambios.length === 1 ? "Un plano tiene cambios hechos sin conexión" : `${cambios.length} planos tienen cambios hechos sin conexión`}. Descárgalos para tenerlos a mano. Si cierras sesión, se quedan en este dispositivo y se suben cuando vuelvas a entrar con la misma cuenta.`;
      const lista = document.createElement("ul"); lista.className = "cad-sesion__lista";
      for (const c of cambios) {
        const li = document.createElement("li");
        const b = document.createElement("button"); b.type = "button"; b.className = "tv-boton tv-boton--secundario";
        b.textContent = `Descargar «${c.nombre || "plano"}»`;
        b.addEventListener("click", () => descargar(c));
        li.append(b); lista.append(li);
      }
      const acciones = document.createElement("div"); acciones.className = "tv-hoja__acciones";
      const no = document.createElement("button"); no.type = "button"; no.className = "tv-boton tv-boton--secundario"; no.textContent = "Seguir en la app";
      const si = document.createElement("button"); si.type = "button"; si.className = "tv-boton"; si.textContent = "Cerrar sesión";
      acciones.append(no, si);
      cuerpo.append(h, p, lista, acciones); d.append(cuerpo); document.body.append(d);
      no.addEventListener("click", () => d.close("no"));
      si.addEventListener("click", () => d.close("si"));
      d.addEventListener("close", () => { d.remove(); ok(d.returnValue === "si"); }, { once: true });
      d.showModal();
    });
  }

  // Cerrar sesión desde el menú de la cuenta del armazón (#tv-salir): con el botón (evento submit) o desde la paleta de órdenes, que llama
  // a `form.submit()` (sin evento): por eso también se envuelve el `submit` de ese formulario. La de las páginas de campo (#itt-salir) la
  // lleva menu.js, que llama a `pendientes()`, `preguntar()` y `alSalir()` antes de enviar.
  const enviarDeVerdad = HTMLFormElement.prototype.submit;
  async function salirConCuidado(f) {
    const cambios = await pendientes();
    if (cambios.length && !(await preguntar(cambios))) return;
    await alSalir();
    enviarDeVerdad.call(f);
  }
  document.addEventListener("submit", (ev) => {
    const f = ev.target;
    if (!(f instanceof HTMLFormElement) || f.id !== "tv-salir") return;
    ev.preventDefault();
    salirConCuidado(f);
  }, true);
  function envolver() {
    const f = document.getElementById("tv-salir");
    if (f && !Object.prototype.hasOwnProperty.call(f, "submit")) Object.defineProperty(f, "submit", { value: () => salirConCuidado(f), configurable: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", envolver); else envolver();

  // Fuera de la página del plano no hay editor abierto en esta pestaña: lo que haya dejado en el navegador se borra al cargar cualquier
  // página de Herramientas (la limpieza al salir del plano puede no terminar si el navegador cierra la página antes).
  document.addEventListener("DOMContentLoaded", () => { if (!document.getElementById("cad-editor")) limpiarEditor(); });

  window.TVCadSesion = Object.freeze({ abrir, pendientes, alSalir, limpiarEditor, descargar, preguntar });
})();

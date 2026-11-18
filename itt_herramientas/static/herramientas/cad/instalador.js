/* CAD · el editor guardado en este equipo (docs/cad.md, «El editor guardado en el equipo»). Lo usan la página del plano (editor.js), el
 * CAD en el teléfono (campo.js) y la página sin conexión: un solo código para la computadora y el teléfono.
 *
 * - Cache Storage, una caché por versión fijada del editor (`itt-cad-editor-<versión>`), que sirve el service worker de Herramientas
 *   (offline/sw_cad_editor.js). Se pide `navigator.storage.persist()` para que el navegador no lo borre cuando le falte espacio.
 * - En la computadora (y la tableta) se guarda solo la primera vez que se abre un plano, antes de abrir el editor: es lo que el navegador
 *   ya bajaba, pero ahora se queda. «Quitar» lo borra y deja de guardarlo en este equipo hasta «Guardar el CAD en esta computadora». En el
 *   teléfono es a pedido («Descargar CAD en este teléfono», campo.js) y aquí solo se pone al día.
 * - Una versión nueva baja solo lo que cambió: lo que tiene el mismo SHA-256 que en la versión guardada se copia de allí; lo que cambió y
 *   trae parche desde la versión guardada (el .wasm, el .js) se arma en parche.worker.js con el parche (unos MB en vez de 33); lo demás
 *   se baja entero. Lo de la versión anterior se borra cuando la nueva está completa.
 * - Cada archivo se comprueba contra el SHA-256 de MANIFIESTO.json (lo da /cad/paquete-del-editor/) leyéndolo de vuelta de la caché, y
 *   se guarda con su tamaño y su SHA-256 en cabeceras (X-TV-Bytes, X-TV-SHA256) para que el service worker no sirva nada dañado. La
 *   versión cuenta como guardada solo con su marca completa, que se escribe al final (base `tv-cad-campo`, tienda `estado`, `editor:<v>`).
 * - Cerrar sesión no lo borra: es el programa, igual para todas las cuentas, sin datos de nadie.
 *
 * API: window.TVCadEditor = { estado(), preparar({decir, automatico}), descargar(alProgreso), quitar(), activado(), activar(si),
 *      versionInstalada(), versionesGuardadas(), uso(), equipo(), fallo(version), pintarMenu() }
 */
(function () {
  "use strict";
  if (window.TVCadEditor) return;
  const PREFIJO = "itt-cad-editor-", MARCA = "__tv_completo__.json", PREFERENCIA = "tv.cad.editor.guardar";
  const configEl = document.getElementById("cad-config");
  const C = configEl ? JSON.parse(configEl.textContent || "{}") : {};
  const T = C.tactil || {};
  // La página del plano trae `guardado`; la página sin conexión solo la versión fijada y la lista (modo táctil).
  const G = C.guardado || (T.version ? { version: T.version, descarga: T.descarga || 0, bytes: T.bytes || 0, urls: { paquete: T.urls && T.urls.paquete } } : {});
  const MB = (n) => `${(n / 1048576).toFixed(n >= 10485760 ? 0 : 1)} MB`;
  const hayCaches = () => "caches" in window && window.isSecureContext !== false;

  // Computadora, tableta o teléfono: cómo se nombra este equipo en los textos.
  function equipo() {
    try {
      if (matchMedia("(max-width: 767px) and (pointer: coarse)").matches) return "este teléfono";
      if (matchMedia("(pointer: coarse)").matches && !matchMedia("(any-pointer: fine)").matches) return "esta tableta";
    } catch (_) { /* computadora */ }
    return "esta computadora";
  }

  // ------------------------------------------------------------------------------------------------------------- la base del equipo
  const abrirDB = () => (window.TVCadSesion ? window.TVCadSesion.abrir() : Promise.resolve(null));
  const req = (r) => new Promise((ok, mal) => { r.onsuccess = () => ok(r.result); r.onerror = () => mal(r.error); });
  async function estadoDB(modo, fn) {
    const db = await abrirDB();
    if (!db) throw new Error("Este navegador no puede guardar el CAD.");
    try {
      const t = db.transaction("estado", modo);
      const fin = new Promise((ok, mal) => { t.oncomplete = ok; t.onerror = () => mal(t.error); t.onabort = () => mal(t.error); });
      const res = await fn(t.objectStore("estado"));
      await fin;
      return res;
    } finally { db.close(); }
  }

  // La marca de «versión completa» vive en la base del equipo (y, de antes, en la caché): WebKit no conserva entre páginas una respuesta
  // guardada con un cuerpo de texto hecho en la página.
  async function marcaDe(nombre) {
    const v = nombre.slice(PREFIJO.length);
    try {
      const x = await estadoDB("readonly", (e) => req(e.get(`editor:${v}`)));
      if (x && x.marca) return x.marca;
    } catch (_) { /* sin base */ }
    try {
      const r = await (await caches.open(nombre)).match(`/cad/editor/${v}/${MARCA}`, { ignoreVary: true });
      return r ? await r.json() : null;
    } catch (_) { return null; }
  }
  async function versionesGuardadas() {
    if (!hayCaches()) return [];
    const nombres = (await caches.keys()).filter((n) => n.startsWith(PREFIJO));
    const res = [];
    for (const n of nombres) res.push({ nombre: n, version: n.slice(PREFIJO.length), marca: await marcaDe(n) });
    return res;
  }
  async function versionInstalada() {
    const completas = (await versionesGuardadas()).filter((x) => x.marca);
    completas.sort((a, b) => String(b.marca.fecha).localeCompare(String(a.marca.fecha)));
    return completas[0] ? completas[0].marca : null;
  }
  async function estado() {
    const instal = await versionInstalada();
    let persistente = false;
    try { persistente = !!(navigator.storage && navigator.storage.persisted && (await navigator.storage.persisted())); } catch (_) { /* no se sabe */ }
    return { instalada: instal, fijada: G.version || null, alDia: !!(instal && G.version && instal.version === G.version), descarga: G.descarga || 0,
      ocupa: G.bytes || 0, persistente, activado: activado() };
  }
  async function uso() {
    let total = 0;
    for (const v of await versionesGuardadas()) if (v.marca) total += v.marca.bytes || 0;
    return total;
  }
  function activado() { try { return localStorage.getItem(PREFERENCIA) !== "no"; } catch (_) { return true; } }
  function activar(si) { try { if (si) localStorage.removeItem(PREFERENCIA); else localStorage.setItem(PREFERENCIA, "no"); } catch (_) { /* sin preferencia */ } }

  async function quitar() {
    if (!hayCaches()) return;
    for (const n of await caches.keys()) {
      if (!n.startsWith(PREFIJO)) continue;
      await estadoDB("readwrite", (e) => req(e.delete(`editor:${n.slice(PREFIJO.length)}`))).catch(() => {});
      await caches.delete(n);
    }
  }
  // La página no pudo arrancar el editor desde el equipo: esa versión se borra (la próxima apertura la vuelve a guardar).
  async function fallo(version) {
    if (!hayCaches()) return;
    await estadoDB("readwrite", (e) => req(e.delete(`editor:${version}`))).catch(() => {});
    await caches.delete(PREFIJO + version);
  }

  // ------------------------------------------------------------------------------------------------------------- comprobar y guardar
  async function sha256(buffer) {
    const h = new Uint8Array(await crypto.subtle.digest("SHA-256", buffer));
    return Array.from(h, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Lo guardado vale solo si se lee de vuelta entero y con el SHA-256 del manifiesto (en WebKit una escritura que no terminó se pierde).
  async function comprobado(cache, url, a) {
    const x = await cache.match(url, { ignoreVary: true });
    if (!x || x.headers.get("X-TV-SHA256") !== a.sha256 || Number(x.headers.get("X-TV-Bytes")) !== a.bytes) return false;
    const b = await x.arrayBuffer();
    return b.byteLength === a.bytes && (await sha256(b)) === a.sha256;
  }
  function cabecerasDe(origen, a) {
    const h = new Headers(origen);
    for (const k of ["content-encoding", "content-length", "set-cookie", "vary", "etag", "last-modified", "date", "age", "x-tv-bytes", "x-tv-sha256"]) h.delete(k);
    h.set("X-TV-Bytes", String(a.bytes));
    h.set("X-TV-SHA256", a.sha256);
    return h;
  }
  async function deLaRed(cache, url, a, alAvanzar) {
    // Lo que el navegador ya trajo al abrir el plano (los archivos con huella se guardan un año) no se baja otra vez.
    const resp = await fetch(url, { credentials: "same-origin" });
    if (!resp.ok || !resp.body) throw new Error("La descarga se interrumpió. Vuelve a intentarlo: lo ya bajado no se repite.");
    let llegaron = 0;
    const contar = new TransformStream({ transform(trozo, ctl) { llegaron += trozo.byteLength; if (alAvanzar) alAvanzar(trozo.byteLength); ctl.enqueue(trozo); } });
    await cache.put(url, new Response(resp.body.pipeThrough(contar), { status: 200, headers: cabecerasDe(resp.headers, a) }));
    if (llegaron !== a.bytes || !(await comprobado(cache, url, a))) { await cache.delete(url); throw new Error("Un archivo llegó incompleto. Vuelve a intentarlo."); }
  }
  async function copiar(cache, url, vieja, a) {
    const b = await vieja.arrayBuffer();
    if (b.byteLength !== a.bytes || (await sha256(b)) !== a.sha256) return false;
    await cache.put(url, new Response(b, { status: 200, headers: cabecerasDe(vieja.headers, a) }));
    if (await comprobado(cache, url, a)) return true;
    await cache.delete(url);
    return false;
  }
  // El parche se aplica en un trabajador: lee el archivo viejo de su caché, baja el parche (unos MB), arma el nuevo, comprueba su
  // SHA-256 y lo guarda. Aquí se vuelve a leer y comprobar.
  let trabajador = null, nTrabajo = 0;
  const pendientes = new Map();
  function enTrabajador(datos) {
    if (!trabajador) {
      trabajador = new Worker(G.urls.trabajador);
      trabajador.onmessage = (ev) => { const p = pendientes.get(ev.data.id); if (!p) return; if (ev.data.tipo === "avance") { if (p.avance) p.avance(ev.data.bytes); return; } pendientes.delete(ev.data.id); p.ok(ev.data); };
      trabajador.onerror = (ev) => { for (const p of pendientes.values()) p.ok({ ok: false, error: ev.message || "trabajador" }); pendientes.clear(); trabajador = null; };
    }
    nTrabajo += 1;
    const id = nTrabajo;
    return new Promise((ok) => { pendientes.set(id, { ok, avance: datos.avance }); trabajador.postMessage({ ...datos, avance: undefined, id }); });
  }
  function soltarTrabajador() { if (trabajador) { trabajador.terminate(); trabajador = null; } }
  async function conParche(cache, nombre, url, a, p, viejo, alAvanzar) {
    if (!G.urls || !G.urls.trabajador || typeof Worker === "undefined") return false;
    const vieja = await (await caches.open(viejo.nombre)).match(viejo.url, { ignoreVary: true });
    if (!vieja) return false;
    const r = await enTrabajador({ viejo: { cache: viejo.nombre, url: viejo.url, sha256: p.sha256 }, parche: p.url, destino: { cache: nombre, url },
      sha256: a.sha256, bytes: a.bytes, cabeceras: [...cabecerasDe(vieja.headers, a)], avance: alAvanzar });
    if (!r.ok) return false;
    if (await comprobado(cache, url, a)) return true;
    await cache.delete(url);
    return false;
  }

  // ------------------------------------------------------------------------------------------------------------- descargar
  let bajando = null;
  // Baja el editor fijado (o lo que cambió desde la versión guardada). alProgreso({hechos, total, bytes, totalBytes}). Una sola vez a la
  // vez en todo el navegador (varias pestañas abriendo planos a la vez): las demás esperan y encuentran la versión ya guardada.
  function descargar(alProgreso) {
    if (bajando) return bajando;
    const trabajo = () => descargarAhora(alProgreso);
    bajando = (navigator.locks && navigator.locks.request ? navigator.locks.request("tv-cad-editor", trabajo) : trabajo())
      .finally(() => { bajando = null; soltarTrabajador(); });
    return bajando;
  }
  async function descargarAhora(alProgreso) {
    if (!hayCaches()) throw new Error("Este navegador no puede guardar el CAD.");
    if (navigator.storage && navigator.storage.persist) { try { await navigator.storage.persist(); } catch (_) { /* se intenta igual */ } }
    // El service worker de Herramientas es quien sirve el editor guardado: se registra aquí si esta página aún no lo tenía.
    if ("serviceWorker" in navigator) { try { await navigator.serviceWorker.register("/sw.js", { scope: "/" }); } catch (_) { /* sin él, solo con conexión */ } }
    const r = await fetch(G.urls.paquete, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo saber qué descargar. Inténtalo de nuevo.");
    const paquete = await r.json();
    const nombre = PREFIJO + paquete.version;
    // Otra pestaña la terminó mientras esta esperaba. La marca vale solo con su caché: si el service worker la borró por algo dañado,
    // se rehace.
    const ya = (await caches.has(nombre)) ? await marcaDe(nombre) : null;
    if (ya && ya.version === paquete.version) return ya;
    const cache = await caches.open(nombre);
    const anteriores = (await versionesGuardadas()).filter((x) => x.nombre !== nombre && x.marca);
    // Lo que ya está en el equipo, por SHA-256 (en cualquier ruta de cualquier versión completa).
    const porSha = new Map();
    for (const ant of anteriores) for (const [ruta, sha] of Object.entries(ant.marca.archivos || {})) if (!porSha.has(sha)) porSha.set(sha, { nombre: ant.nombre, url: `/cad/editor/${ant.version}/${ruta}` });
    const https = location.protocol === "https:";
    const plan = [];
    for (const a of paquete.archivos) {
      const url = `${paquete.base}${a.ruta}`;
      // Ya guardado en una vuelta anterior que se cortó: vale solo si se comprueba (pudo quedar escrito sin comprobarse).
      if (await comprobado(cache, url, a).catch(() => false)) { plan.push({ a, url, como: "hecho", costo: 0 }); continue; }
      if (porSha.has(a.sha256)) { plan.push({ a, url, como: "copia", costo: 0, viejo: porSha.get(a.sha256) }); continue; }
      let par = null;
      for (const p of a.parches || []) {
        const ant = anteriores.find((x) => x.version === p.desde && x.marca.archivos && x.marca.archivos[p.ruta] === p.sha256);
        if (ant) { par = { p, viejo: { nombre: ant.nombre, url: `/cad/editor/${ant.version}/${p.ruta}` } }; break; }
      }
      if (par) plan.push({ a, url, como: "parche", costo: https ? par.p.descarga : par.p.descarga_gz, ...par });
      else plan.push({ a, url, como: "red", costo: a.descarga });
    }
    const total = plan.length;
    let totalBytes = plan.reduce((s, x) => s + x.costo, 0), hechos = 0, bytes = 0;
    const decir = () => { if (alProgreso) alProgreso({ hechos, total, bytes: Math.min(bytes, totalBytes), totalBytes }); };
    decir();
    for (const x of plan) {
      const antes = bytes;
      let listo = x.como === "hecho";
      if (x.como === "copia") listo = await copiar(cache, x.url, await (await caches.open(x.viejo.nombre)).match(x.viejo.url, { ignoreVary: true }), x.a).catch(() => false);
      if (x.como === "parche") {
        // Lo que avanza el trabajador se cuenta en proporción a lo que pesa el parche en el cable.
        listo = await conParche(cache, nombre, x.url, x.a, x.p, x.viejo, (n) => { bytes = antes + Math.round(x.costo * Math.min(1, n / x.a.bytes)); decir(); }).catch(() => false);
      }
      if (!listo) {
        // Sin copia ni parche (o falló): el archivo entero. Si se pensaba otra cosa, se suma lo que de verdad se baja.
        if (x.como !== "red") { totalBytes += x.a.descarga - x.costo; bytes = antes; }
        await deLaRed(cache, x.url, x.a, (n) => { bytes += n * (x.a.descarga / x.a.bytes); decir(); });
      }
      bytes = antes + (listo ? x.costo : x.a.descarga);
      hechos += 1;
      decir();
    }
    const marca = { version: paquete.version, fecha: new Date().toISOString(), bytes: paquete.bytes, descarga: paquete.descarga,
      archivos: Object.fromEntries(paquete.archivos.map((a) => [a.ruta, a.sha256])) };
    await estadoDB("readwrite", (e) => req(e.put({ clave: `editor:${paquete.version}`, marca })));
    // La versión nueva está completa: las anteriores se borran (y cualquier otra a medias).
    for (const ant of await versionesGuardadas()) {
      if (ant.nombre === nombre) continue;
      await caches.delete(ant.nombre);
      await estadoDB("readwrite", (e) => req(e.delete(`editor:${ant.version}`))).catch(() => {});
    }
    return marca;
  }

  // ------------------------------------------------------------------------------------------------------------- antes de abrir el editor
  // Antes de abrir el marco del editor: si hay que guardarlo o ponerlo al día, se hace ya (solo lo que cambió) para que el marco lo abra
  // del equipo. `automatico`: la computadora (se guarda solo); si no (teléfono), solo se pone al día una versión ya descargada. Sin
  // caché, sin service worker, sin red o si algo falla, el editor se abre de la red como siempre. Devuelve "equipo" o "red".
  async function swListo() {
    if (!("serviceWorker" in navigator)) return false;
    try {
      await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      return !!(await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 10000))]));
    } catch (_) { return false; }
  }
  async function preparar({ decir, automatico } = {}) {
    if (!G.version || !G.urls || !G.urls.paquete || !hayCaches()) return "red";
    const ed = await estado();
    if (ed.alDia) return (await swListo()) ? "equipo" : "red";
    if (!navigator.onLine) return "red";
    if (automatico ? !activado() : !ed.instalada) return "red";
    if (!(await swListo())) return "red";
    const donde = equipo();
    const texto = ed.instalada ? `Actualizando el CAD de ${donde}…` : `Guardando el CAD en ${donde}…`;
    if (decir) decir(texto);
    try {
      await descargar(({ bytes, totalBytes }) => { if (decir) decir(`${texto} ${MB(bytes)} de ${MB(totalBytes)}`); });
      return "equipo";
    } catch (_) { return "red"; }
  }

  // ------------------------------------------------------------------------------------------------------------- «Más acciones» del plano
  // En la computadora el menú dice qué ocupa: «CAD guardado en esta computadora · 123 MB» con «Quitar»; quitado, «Guardar el CAD en esta
  // computadora». En el teléfono lo dice la hoja «Sin conexión» del modo táctil (campo.js).
  const avisar = (t, tipo) => { if (window.tvAviso) window.tvAviso(t, tipo); };
  let ocupado = false;
  async function pintarMenu(progreso) {
    const caja = document.getElementById("cad-guardado");
    if (!caja || !G.version || !hayCaches() || !("serviceWorker" in navigator) || equipo() === "este teléfono") return;
    const texto = document.getElementById("cad-guardado-texto"), nota = document.getElementById("cad-guardado-nota");
    const quitarB = document.getElementById("cad-guardado-quitar"), guardarB = document.getElementById("cad-guardado-guardar");
    const ed = await estado();
    const donde = equipo();
    caja.hidden = false;
    guardarB.querySelector("span").textContent = `Guardar el CAD en ${donde}`;
    if (progreso) {
      texto.textContent = progreso; quitarB.hidden = guardarB.hidden = true; nota.hidden = true;
    } else if (ed.alDia) {
      texto.textContent = `CAD guardado en ${donde} · ${MB(ed.instalada.bytes || (await uso()))}`;
      nota.textContent = ed.persistente ? "Se abre sin descargar nada y se actualiza solo con lo que cambie." : "Se abre sin descargar nada. Si el navegador se queda sin espacio podría borrarlo; se vuelve a guardar solo.";
      nota.hidden = false; quitarB.hidden = false; guardarB.hidden = true;
    } else if (!ed.activado) {
      texto.textContent = "El CAD se descarga cada vez que abres un plano.";
      nota.hidden = true; quitarB.hidden = true; guardarB.hidden = false;
    } else {
      texto.textContent = `El CAD no está guardado en ${donde}.`;
      nota.hidden = true; quitarB.hidden = true; guardarB.hidden = false;
    }
    if (caja.dataset.listo) return;
    caja.dataset.listo = "1";
    quitarB.addEventListener("click", async () => {
      if (ocupado) return;
      ocupado = true;
      try { activar(false); await quitar(); avisar(`El CAD se quitó de ${equipo()}.`); } catch (_) { avisar("No se pudo quitar el CAD. Inténtalo de nuevo.", "error"); }
      finally { ocupado = false; pintarMenu(); }
    });
    guardarB.addEventListener("click", async () => {
      if (ocupado) return;
      ocupado = true;
      activar(true);
      const t = `Guardando el CAD en ${equipo()}…`;
      try {
        if (navigator.serviceWorker) await navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
        await descargar(({ bytes, totalBytes }) => pintarMenu(`${t} ${MB(bytes)} de ${MB(totalBytes)}`));
        avisar(`Listo: el CAD quedó guardado en ${equipo()}.`);
      } catch (err) { avisar((err && err.message) || "No se pudo guardar el CAD.", "error"); }
      finally { ocupado = false; pintarMenu(); }
    });
  }

  window.TVCadEditor = Object.freeze({ estado, preparar, descargar, quitar, activado, activar, versionInstalada, versionesGuardadas, uso, equipo, fallo, pintarMenu });
})();

/* Civil: herramientas de topografía al estilo CivilCAD dentro de CAD (docs/civil.md).
 *
 * La página lee el dibujo por window.TVCad (canal de control del editor), manda al servidor lo necesario (POST /cad/civil/<orden>/) y
 * escribe el resultado en el plano:
 *   - lo nuevo llega como DXF: se abre como dibujo temporal y se copia al plano con entities_copy_to (un paso de deshacer);
 *   - lo que se reemplaza o edita (triangulación anterior, curvas, invertir triángulos) se agrupa con `batch` cuando el editor lo tiene;
 *     sin él, cada operación es su propio paso de deshacer.
 * Elegir y señalar usan user_select y getpoint del editor; si el editor no los tiene (gui_required), Civil usa lo que la persona tenga
 * seleccionado y lo dice.
 */
(function () {
  "use strict";
  const C = JSON.parse(document.getElementById("civil-config").textContent);
  const $ = (id) => document.getElementById(id);
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const MARCA = "00000000-0000-0000-0000-000000000000";
  const LLAVE = `tv-civil-${C.plano}`;

  class ErrorCivil extends Error {}
  /** Lo que una hoja hace al abrirse (p. ej. contar los puntos que va a usar). */
  const alAbrir = {};

  // -- Estado de la extensión ---------------------------------------------------------------------------------------------------
  const est = { ocupado: false, escala: { denominador: 1000, alturas_mm: { ...C.alturas_mm }, zona: 14, hemisferio: "N" }, sinBatch: null, sinPicks: false, elegidos: {} };
  try { Object.assign(est.escala, JSON.parse(localStorage.getItem(LLAVE) || "{}").escala || {}); } catch (_) { /* sin almacenamiento */ }
  const guardarAjustes = () => { try { localStorage.setItem(LLAVE, JSON.stringify({ escala: est.escala })); } catch (_) { /* nada */ } };

  const avisar = (t, tipo) => (window.tvAviso ? window.tvAviso(t, tipo) : console.log(t));
  const icono = (n) => { const s = document.querySelector(`#civil-iconos [data-civil-icono="${n}"]`); return s ? s.innerHTML : ""; };
  const mil = (n, d = 2) => Number(n).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });

  // -- Editor ---------------------------------------------------------------------------------------------------------------------
  const pedir = (p, ms = 60000) => window.TVCad.pedir(p, { tiempoMs: ms });
  const codigo = (err) => (err && err.respuesta && err.respuesta.code) || (err && err.codigo) || "";

  async function documentoActivo() { return (await pedir({ op: "state" })).document_id; }

  /** Todas las entidades que cumplen el filtro (consulta paginada de 10 000 en 10 000). */
  async function consultar(filtro) {
    const salida = [];
    let offset = 0;
    for (;;) {
      let r;
      try { r = await pedir({ op: "query", limit: 10000, offset, ...filtro }, 120000); }
      catch (err) {
        // con `fields`, el editor rechaza la consulta cuando no hay ninguna entidad que la cumpla: es una lista vacía
        if (filtro.fields && /no matched entity/i.test(String((err && err.message) || ""))) return salida;
        throw err;
      }
      for (const e of r.entities || []) if (!e.space || e.space === "*Model_Space") salida.push(e);
      if (r.next_offset === null || r.next_offset === undefined) return salida;
      offset = r.next_offset;
    }
  }

  /** Un lote de operaciones del plano en un solo paso de deshacer (batch); sin batch, una por una. */
  async function enUnPaso(pasos, etiqueta = "Civil") {
    if (!pasos.length) return;
    if (pasos.length > 1 && est.sinBatch !== true) {
      try { await pedir({ op: "batch", steps: pasos, label: etiqueta }, 300000); est.sinBatch = false; return; }
      catch (err) { if (!["unknown_operation", "unknown_route"].includes(codigo(err))) throw err; est.sinBatch = true; }
    }
    for (const p of pasos) await pedir(p, 300000);
  }

  /** Inserta un DXF (base64) en el plano: dibujo temporal + entities_copy_to (un paso de deshacer). Devuelve los handles nuevos. */
  async function insertarDxf(b64, nombre = "civil.dxf") {
    // Ocupado mientras el dibujo temporal existe: la página no lo toma por un plano más (pestaña) ni guarda a media copia.
    window.TVCad.ocupar();
    try { return await insertarDxfLibre(b64, nombre); } finally { window.TVCad.soltar(); }
  }
  async function insertarDxfLibre(b64, nombre) {
    const plano = await documentoActivo();
    const antes = new Set(((await pedir({ op: "state" })).documents || []).map((d) => d.id));
    let temporal = null;
    try {
      // La respuesta de «open» puede traer un error que no es del dibujo (WebKit: el editor no logra guardar su lista de recientes en el
      // almacenamiento del navegador): cuenta que aparezca la pestaña nueva, como en editor.js.
      for (let i = 0; ; i++) {
        try { await pedir({ op: "open", name: nombre, data_base64: b64 }, 300000); break; }
        catch (err) { if (codigo(err) !== "busy") break; if (i > 40) throw err; await espera(250); }
      }
      for (let i = 0; i < 1200 && temporal === null; i++) {
        const s = await pedir({ op: "state" });
        const nuevo = (s.documents || []).find((d) => !antes.has(d.id));
        if (nuevo && s.document_id === nuevo.id && !s.operation) temporal = nuevo.id;
        else if (i > 30 && !nuevo && (s.documents || []).some((d) => d.id === plano && d.path === nombre))
          // un dibujo sin nombre y sin cambios se reemplaza al abrir otro; el plano de la página siempre tiene nombre, así que no debe pasar
          throw new ErrorCivil("El editor reemplazó el plano en lugar de abrir el resultado. Recarga la página.");
        else await espera(100);
      }
      if (temporal === null) throw new ErrorCivil("El editor no abrió el resultado. Inténtalo de nuevo.");
      const handles = (await consultar({ detail: "summary", document_id: temporal })).map((e) => e.handle);
      if (!handles.length) return [];
      const r = await pedir({ op: "entities_copy_to", handles, document_id: plano }, 300000);
      return (r.result && r.result.created) || [];
    } finally {
      await pedir({ op: "activate", document_id: plano }).catch(() => {});
      if (temporal !== null) await pedir({ op: "close", discard: true, document_id: temporal }).catch(() => {});
    }
  }

  // -- Colocar un resultado: la persona dice dónde ------------------------------------------------------------------------------
  // Todo lo que agrega un bloque de dibujo (perfil, secciones, curva masa, cuadros, tablas) se coloca como en CivilCAD: el resultado
  // ya calculado se abre en un dibujo temporal, se mide su caja y un rectángulo de su tamaño real sigue al puntero sobre el plano. Clic
  // (o «Poner aquí», que deja la vista previa donde está) lo pone; Esc o «Cancelar», no dibuja nada. La vista previa empieza en el
  // lugar sugerido (a un lado de lo dibujado). Se mueve solo lo que es del bloque (`mover`): la numeración de vértices o las claves en
  // el plano se quedan donde van. Un solo paso de deshacer: la copia al plano.
  const cajaDe = (e) => {
    const b = e.bounds || (e.properties && e.properties.bounds);
    if (!b) return null;
    if (Array.isArray(b) && b.length === 4 && typeof b[0] === "number") return b;
    if (b.min && b.max) return [b.min[0] ?? b.min.x, b.min[1] ?? b.min.y, b.max[0] ?? b.max.x, b.max[1] ?? b.max.y];
    if (Array.isArray(b) && b.length === 2) return [b[0][0], b[0][1], b[1][0], b[1][1]];
    return null;
  };
  const unirCajas = (cajas) => cajas.reduce((a, c) => (!c || !c.every(Number.isFinite) ? a : a ? [Math.min(a[0], c[0]), Math.min(a[1], c[1]), Math.max(a[2], c[2]), Math.max(a[3], c[3])] : c.slice()), null);

  /** La vista del editor en px de la página: la de `TVCad.vista()` (control.js), la misma que usa el modo táctil. */
  async function vistaEditor() {
    const m = await window.TVCad.vista();
    return { k: m.k, aPantalla: m.aPagina, aMundo: m.dePagina };
  }

  const ANCLAS = { arriba_izquierda: (c) => [c[0], c[3]], abajo_izquierda: (c) => [c[0], c[1]] };
  const fmtM = (v) => `${mil(v, v < 100 ? 1 : 0)} m`;

  /** Rectángulo de vista previa sobre el editor, siguiendo al puntero. Devuelve { poner(punto), fin(), actual() }. */
  function vistaPrevia(caja, ancla, titulo) {
    const capa = document.createElement("div");
    capa.className = "civil-previa";
    capa.setAttribute("aria-hidden", "true");
    capa.innerHTML = '<div class="civil-previa__caja"><span class="civil-previa__titulo"></span></div><span class="civil-previa__ancla"></span>';
    document.body.append(capa);
    const marco = capa.firstElementChild, punta = capa.lastElementChild;
    marco.firstElementChild.textContent = `${titulo} · ${fmtM(caja[2] - caja[0])} × ${fmtM(caja[3] - caja[1])}`;
    const a0 = ANCLAS[ancla](caja), dx0 = caja[0] - a0[0], dy0 = caja[3] - a0[1];     // del ancla a la esquina superior izquierda
    const ancho = caja[2] - caja[0], alto = caja[3] - caja[1];
    let vista = null, punto = a0.slice(), pantalla = null, vivo = true, releer = null;
    const pintar = () => {
      if (!vista || !vivo) return;
      const p = pantalla ? vista.aMundo(pantalla[0], pantalla[1]) : punto;
      punto = p;
      const [sx, sy] = vista.aPantalla(p[0] + dx0, p[1] + dy0), [ax, ay] = vista.aPantalla(p[0], p[1]);
      Object.assign(marco.style, { left: `${sx}px`, top: `${sy}px`, width: `${Math.max(2, ancho / vista.k)}px`, height: `${Math.max(2, alto / vista.k)}px` });
      Object.assign(punta.style, { left: `${ax}px`, top: `${ay}px` });
      const area = $("cad-editor").getBoundingClientRect();
      capa.style.clipPath = `inset(${area.top}px ${innerWidth - area.right}px ${innerHeight - area.bottom}px ${area.left}px)`;
    };
    const leer = async () => { try { vista = await vistaEditor(); pintar(); } catch (_) { /* el editor ocupado: la siguiente lectura */ } };
    const releerPronto = () => { clearTimeout(releer); releer = setTimeout(leer, 60); };
    let doc = null;
    const mover = (ev) => {
      const r = $("cad-editor").getBoundingClientRect();
      pantalla = [r.left + ev.clientX, r.top + ev.clientY];
      pintar();
    };
    try {
      doc = $("cad-editor").contentDocument;
      doc.addEventListener("pointermove", mover, { passive: true });
      doc.addEventListener("wheel", releerPronto, { passive: true });
      doc.addEventListener("pointerup", releerPronto, { passive: true });
    } catch (_) { doc = null; }
    const sondeo = setInterval(leer, 400);                     // pan con el botón de en medio, zoom por teclado: la vista se vuelve a leer
    leer();
    return {
      actual: () => punto.slice(),
      fin() {
        vivo = false; clearInterval(sondeo); clearTimeout(releer); capa.remove();
        if (doc) { doc.removeEventListener("pointermove", mover); doc.removeEventListener("wheel", releerPronto); doc.removeEventListener("pointerup", releerPronto); }
      },
    };
  }

  /**
   * Inserta el resultado `b64` donde la persona diga. `mover`: capas que forman el bloque (RegExp; por omisión todo). `ancla`: la esquina
   * que sigue al puntero ("arriba_izquierda" o "abajo_izquierda"). Devuelve los handles creados, o null si la persona canceló (no se
   * dibuja nada).
   */
  async function colocarDxf(b64, nombre, { titulo, mover = null, ancla = "arriba_izquierda" } = {}) {
    if (est.sinPicks) return insertarDxf(b64, nombre);               // sin getpoint en el editor no se puede preguntar: va al lugar sugerido
    const plano = await documentoActivo();
    const que = (titulo || "el resultado").toLowerCase();
    // 1. Medir: el resultado se abre, se mide su caja y se cierra (mientras la persona señala, en el editor solo está su plano)
    let medida;
    await conTemporal(b64, nombre, plano, async (temporal) => {
      const todas = await consultar({ detail: "full", fields: ["handle", "layer", "bounds", "space"], document_id: temporal });
      const bloque = mover ? todas.filter((e) => mover.test(e.layer || "")) : todas;
      medida = { vacio: !todas.length, caja: bloque.length ? unirCajas(bloque.map(cajaDe)) : null };
    });
    if (medida.vacio) return [];
    // 2. Dónde: vista previa de su tamaño real siguiendo al puntero; clic, «Poner aquí», o Esc / «Cancelar» (nada se dibuja)
    let destino = null;
    if (medida.caja) {
      const caja = medida.caja;
      destino = await conHojaCerrada(async () => {
        const previa = vistaPrevia(caja, ancla, titulo || "Resultado");
        let aqui = null;
        mostrarChip(`Señala dónde va ${que}`, false);
        chipPoner.hidden = false;
        cancelarPick = () => pedir({ op: "cancel" }).catch(() => {});
        ponerAqui = () => { aqui = previa.actual(); pedir({ op: "cancel" }).catch(() => {}); };
        try {
          const r = await pedir({ op: "getpoint", prompt: `Señala dónde va ${que}` }, 30 * 60000);
          if (r.result && !r.result.cancelled && r.result.point) return r.result.point;
          return aqui;
        } catch (err) {
          if (codigo(err) === "gui_required") { est.sinPicks = true; return ANCLAS[ancla](caja); }
          throw err;
        } finally { previa.fin(); chipPoner.hidden = true; ponerAqui = null; }
      });
      if (!destino) return null;
    }
    // 3. Poner: se abre otra vez, se mueve el bloque en el temporal y se copia de una vez (un solo paso de deshacer en el plano)
    return conTemporal(b64, nombre, plano, async (temporal) => {
      const todas = await consultar({ detail: "full", fields: ["handle", "layer", "bounds", "space"], document_id: temporal });
      if (destino) {
        const bloque = mover ? todas.filter((e) => mover.test(e.layer || "")) : todas;
        const a = ANCLAS[ancla](unirCajas(bloque.map(cajaDe))), v = [destino[0] - a[0], destino[1] - a[1], 0];
        if (Math.hypot(v[0], v[1]) > 1e-9) await pedir({ op: "entities_transform", action: "move", handles: bloque.map((e) => e.handle), vector: v, document_id: temporal }, 300000);
      }
      const r = await pedir({ op: "entities_copy_to", handles: todas.map((e) => e.handle), document_id: plano }, 300000);
      return (r.result && r.result.created) || [];
    });
  }

  /** Abre `b64` como dibujo temporal (queda activo), corre `fn(id)` y lo cierra sin guardar, volviendo al plano. */
  async function conTemporal(b64, nombre, plano, fn) {
    window.TVCad.ocupar();                          // como insertarDxf: el dibujo temporal no es una pestaña de la página
    try { return await conTemporalLibre(b64, nombre, plano, fn); } finally { window.TVCad.soltar(); }
  }
  async function conTemporalLibre(b64, nombre, plano, fn) {
    const antes = new Set(((await pedir({ op: "state" })).documents || []).map((d) => d.id));
    let temporal = null;
    try {
      for (let i = 0; ; i++) {
        try { await pedir({ op: "open", name: nombre, data_base64: b64 }, 300000); break; }
        catch (err) { if (codigo(err) !== "busy") break; if (i > 40) throw err; await espera(250); }
      }
      for (let i = 0; i < 1200 && temporal === null; i++) {
        const s = await pedir({ op: "state" });
        const nuevo = (s.documents || []).find((d) => !antes.has(d.id));
        if (nuevo && s.document_id === nuevo.id && !s.operation) temporal = nuevo.id;
        else await espera(100);
      }
      if (temporal === null) throw new ErrorCivil("El editor no abrió el resultado. Inténtalo de nuevo.");
      return await fn(temporal);
    } finally {
      await pedir({ op: "activate", document_id: plano }).catch(() => {});
      if (temporal !== null) await pedir({ op: "close", discard: true, document_id: temporal }).catch(() => {});
    }
  }

  async function borrar(handles) {
    const lista = (handles || []).filter(Boolean);
    if (lista.length) await pedir({ op: "entities_delete", handles: lista }, 300000);
  }

  // -- Elegir y señalar -----------------------------------------------------------------------------------------------------------
  let hojaAbierta = null;
  const chip = $("civil-pick"), chipTexto = $("civil-pick-texto"), chipListo = $("civil-pick-listo"), chipPoner = $("civil-pick-poner");
  let cancelarPick = null, ponerAqui = null;

  function mostrarChip(texto, conListo) {
    chipTexto.textContent = texto;
    chipListo.hidden = !conListo;
    chip.hidden = false;
    const lienzo = $("cad-lienzo");
    if (lienzo && chip.parentElement !== lienzo) lienzo.append(chip);
  }
  const ocultarChip = () => { chip.hidden = true; };
  $("civil-pick-cancelar").addEventListener("click", () => { if (cancelarPick) cancelarPick(); });
  chipPoner.addEventListener("click", () => { if (ponerAqui) ponerAqui(); });
  // Esc con el foco en la página (no en el editor) mientras Civil espera un punto o una elección: cancela eso, y nada más (en la pantalla
  // completa maximizada en la página, Esc también la quitaría). Va antes que los demás oyentes de la página.
  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !cancelarPick || chip.hidden) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    cancelarPick();
  }, true);

  async function conHojaCerrada(fn) {
    const hoja = hojaAbierta;
    if (hoja && hoja.open) hoja.close();
    window.TVCad.ocupar();
    try { return await fn(); }
    finally { window.TVCad.soltar(); ocultarChip(); cancelarPick = null; if (hoja) { hoja.showModal(); hojaAbierta = hoja; } }
  }

  /** Capas que dibuja Civil como resultado: nunca sirven como eje, límite o polígono (las marcas de estación tocan el eje). */
  const CAPAS_DE_RESULTADO = /^(EJE_ESTACIONES|PERFIL_|SECCION_|CURVAS_|CUADRO_|VOLUMEN_|VERTICES_POLIGONO|PUNTOS_|TRIANGULACION_REVISION|LADOS_|RETICULA_|MASA_|LOTES_|EJE_PUNTOS|EJE_TABLA)/;

  /** Entidades elegidas por la persona. `tipos`: nombres del editor (p. ej. ["Polyline", "Polyline3D"]). */
  async function elegir({ mensaje, tipo, capa, tipos, minimo = 1, maximo = Infinity, sinResultados = false }) {
    return conHojaCerrada(async () => {
      let entidades = null;
      if (!est.sinPicks) {
        mostrarChip(`${mensaje} · Enter para terminar, Esc para cancelar`, false);
        cancelarPick = () => pedir({ op: "cancel" }).catch(() => {});
        try {
          const r = await pedir({ op: "user_select", prompt: mensaje, detail: "full", clear: true, ...(tipo ? { type: tipo } : {}), ...(capa ? { layer: capa } : {}) }, 30 * 60000);
          if (!r.result || r.result.cancelled || r.status === "cancelled") return null;
          entidades = r.result.entities || [];
        } catch (err) {
          if (codigo(err) !== "gui_required") throw err;
          est.sinPicks = true;
        }
      }
      if (entidades === null) {
        // El editor no deja elegir desde aquí: se usa lo que la persona tenga seleccionado.
        let sel = (await pedir({ op: "state" })).selection || [];
        if (!sel.length) {
          mostrarChip(`${mensaje}: selecciónalo en el dibujo y pulsa «Listo»`, true);
          const listo = await new Promise((r) => { cancelarPick = () => r(false); chipListo.onclick = () => r(true); });
          if (!listo) return null;
          sel = (await pedir({ op: "state" })).selection || [];
        }
        entidades = sel.length ? (await pedir({ op: "query", handles: sel, detail: "full" })).entities || [] : [];
      }
      await pedir({ op: "select", clear: true }).catch(() => {});      // lo elegido ya se leyó: el dibujo queda sin selección, como al terminar una orden
      if (tipos) entidades = entidades.filter((e) => tipos.includes(e.type) || tipos.includes(e.display_type));
      if (sinResultados) entidades = entidades.filter((e) => !CAPAS_DE_RESULTADO.test(e.layer || ""));
      if (entidades.length < minimo) throw new ErrorCivil(minimo === 1 ? `No elegiste nada que sirva: ${mensaje.toLowerCase()}.` : `Elige al menos ${minimo}.`);
      if (entidades.length > maximo) throw new ErrorCivil(maximo === 1 ? "Elige solo uno." : `Elige como máximo ${maximo}.`);
      return entidades;
    });
  }

  /** Un punto señalado por la persona; null si cancela. Sin getpoint en el editor, lanza un ErrorCivil con la razón. */
  async function senalar(mensaje, sufijo = " · Esc para cancelar") {
    if (est.sinPicks) throw new ErrorCivil("Esta versión del editor no deja señalar puntos desde Civil.");
    return conHojaCerrada(async () => {
      mostrarChip(`${mensaje}${sufijo}`, false);
      cancelarPick = () => pedir({ op: "cancel" }).catch(() => {});
      try {
        const r = await pedir({ op: "getpoint", prompt: mensaje }, 30 * 60000);
        if (!r.result || r.result.cancelled) return null;
        return r.result.point;
      } catch (err) {
        if (codigo(err) === "gui_required") { est.sinPicks = true; throw new ErrorCivil("Esta versión del editor no deja señalar puntos desde Civil."); }
        throw err;
      }
    });
  }

  // -- Del dibujo a datos ---------------------------------------------------------------------------------------------------------
  const xyz = (p) => (Array.isArray(p) ? [p[0], p[1], p[2] ?? 0] : [p.x, p.y, p.z ?? 0]);

  function polilinea(e) {
    const p = e.properties || {};
    if (e.type === "Polyline3D" || e.display_type === "Polyline3D") {
      const v = (p.vertices || []).map((q) => xyz(q.position || q.location));
      return { vertices: v, combas: v.map(() => 0), cerrada: !!(p.flags && p.flags.closed), tres_d: true };
    }
    if (e.type === "Line") {
      const a = xyz(e.start || p.start), b = xyz(e.end || p.end);
      return { vertices: [a, b], combas: [0, 0], cerrada: false, tres_d: true };
    }
    const z = p.elevation || 0;
    const vs = (p.vertices || []).map((q) => [q.location.x, q.location.y, z]);
    return { vertices: vs, combas: (p.vertices || []).map((q) => q.bulge || 0), cerrada: !!p.is_closed, tres_d: false };
  }

  function cara(e) {
    const p = e.properties || e;
    return { handle: e.handle, vertices: [xyz(p.first_corner), xyz(p.second_corner), xyz(p.third_corner)] };
  }

  function punto(e) {
    if (e.type === "Point") return [...xyz(e.location || (e.properties && e.properties.location)), "", ""];
    const a = e.attributes || {};
    return [...xyz(e.position || (e.properties && e.properties.insert_point)), a.NUM || "", a.DESC || ""];
  }

  /** Un punto de Civil (bloque PT_*) como lo necesita «redibujar_puntos»: posición, tipo y atributos tal como están. */
  function puntoCivil(e) {
    const [x, y, z] = xyz(e.position || (e.properties && e.properties.insert_point));
    const bloque = e.block || (e.properties && (e.properties.block_name || e.properties.name)) || "";
    const tipo = bloque === C.bloques.proyecto || e.layer === C.capas.puntos.proyecto ? "proyecto" : "terreno";
    return { handle: e.handle, x, y, z, tipo, atributos: { ...(e.attributes || {}) } };
  }
  const esPuntoCivil = (e) => Object.values(C.bloques).includes(e.block) || Object.values(C.capas.puntos).includes(e.layer);

  async function insercionesDe(tipo) {
    const capas = tipo ? [C.capas.puntos[tipo]] : Object.values(C.capas.puntos);
    const salida = [];
    for (const capa of capas) {
      const ins = await consultar({ type: "INSERT", layer: capa, detail: "full", fields: ["handle", "block", "layer", "position", "attributes"] });
      salida.push(...ins.filter((e) => Object.values(C.bloques).includes(e.block)).map((e) => puntoCivil({ ...e, layer: e.layer || capa })));
    }
    return salida;
  }

  async function puntosDe(tipo) {
    const capa = C.capas.puntos[tipo];
    const ins = await consultar({ type: "INSERT", layer: capa, detail: "full", fields: ["handle", "block", "position", "attributes"] });
    const pts = await consultar({ type: "Point", layer: capa, detail: "geometry" });
    return [...ins.filter((e) => Object.values(C.bloques).includes(e.block)).map(punto), ...pts.map(punto)];
  }

  /** Caras 3D de la capa de una triangulación. `records` con proyección: ~360 bytes por cara en vez de ~1 kB de `query` completo. */
  async function carasDe(tipo) {
    const salida = [];
    const rutas = ["/first_corner", "/second_corner", "/third_corner"];
    let offset = 0;
    for (;;) {
      const r = await pedir({ op: "records", collection: "entities", type: "3DFACE", where: [{ path: "/common/layer", value: C.capas.tin[tipo] }], paths: rutas, limit: 10000, offset }, 120000);
      for (const e of r.records || []) {
        const v = e.values || {};
        if (v["/first_corner"]) salida.push({ handle: e.handle, vertices: rutas.map((k) => xyz(v[k])) });
      }
      if (r.next_offset === null || r.next_offset === undefined) return salida;
      offset = r.next_offset;
    }
  }

  // -- Servidor ---------------------------------------------------------------------------------------------------------------------
  function csrf() { const i = document.querySelector("input[name=csrfmiddlewaretoken]"); return i ? i.value : ""; }

  async function cuerpo(datos) {
    const texto = JSON.stringify(datos);
    if (typeof CompressionStream === "function" && texto.length > 20000) {
      const flujo = new Blob([texto]).stream().pipeThrough(new CompressionStream("gzip"));
      return { cuerpo: await new Response(flujo).blob(), gzip: true };
    }
    return { cuerpo: texto, gzip: false };
  }

  async function calcular(orden, datos, decir) {
    const { cuerpo: c, gzip } = await cuerpo({ ...datos, escala: est.escala });
    const r = await fetch(C.urls.orden.replace("__ORDEN__", orden), {
      method: "POST", credentials: "same-origin", body: c,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), ...(gzip ? { "Content-Encoding": "gzip" } : {}) },
    });
    let d = {};
    try { d = await r.json(); } catch (_) { /* nada */ }
    if (!r.ok && r.status !== 202) throw new ErrorCivil(d.error || "No se pudo calcular. Inténtalo de nuevo.");
    while (!d.terminada) {
      await espera(800);
      const t = await fetch(`${C.urls.tarea.replace(MARCA, d.tarea)}?formato=json`, { credentials: "same-origin" });
      d = { ...(await t.json()), tarea: d.tarea };
      if (!d.terminada && decir) decir(`${d.mensaje || "Calculando…"}`);
    }
    if (d.estado !== "completada") throw new ErrorCivil(d.error || "No se pudo calcular. Inténtalo de nuevo.");
    if (d.resultado && d.resultado.url) {
      const r2 = await fetch(d.resultado.url, { credentials: "same-origin" });
      if (!r2.ok) throw new ErrorCivil("El resultado ya no está. Vuelve a calcular.");
      return r2.json();
    }
    return d.resultado || {};
  }

  // -- Ocupado --------------------------------------------------------------------------------------------------------------------
  const puedeDibujar = () => {
    if (est.ocupado) { avisar("Espera a que termine lo anterior.", "error"); return false; }
    if (!window.TVCad.estado().editable) { avisar("Para dibujar, toma el control del plano.", "error"); return false; }
    return true;
  };

  /** Corre `fn` con la barra ocupada y los errores dichos con claridad. `soloLee`: no dibuja (exportar), no pide el control del plano. */
  async function trabajo(form, texto, fn, { soloLee = false } = {}) {
    if (est.ocupado) { avisar("Espera a que termine lo anterior.", "error"); return; }
    if (!soloLee && !window.TVCad.estado().editable) { avisar("Para dibujar, toma el control del plano.", "error"); return; }
    est.ocupado = true;
    window.TVCad.ocupar();
    const enviar = form && form.querySelector("button[type=submit]");
    const estado = form && form.querySelector(".civil-estado");
    const decir = (t) => { if (estado) { estado.hidden = !t; estado.textContent = t || ""; } };
    barra.setAttribute("aria-busy", "true");
    if (enviar) { enviar.setAttribute("aria-busy", "true"); enviar.disabled = true; }
    decir(texto);
    try { return await fn(decir); }
    catch (err) {
      const m = err instanceof ErrorCivil ? err.message : (err && err.codigo === "rechazada" ? `El editor no aceptó el cambio: ${err.message}` : "Algo salió mal. Inténtalo de nuevo.");
      decir(""); avisar(m, "error");
      if (!(err instanceof ErrorCivil)) console.error(err);
    } finally {
      est.ocupado = false;
      window.TVCad.soltar();
      barra.removeAttribute("aria-busy");
      if (enviar) { enviar.removeAttribute("aria-busy"); enviar.disabled = false; }
      decir("");                                                       // el estado de trabajo nunca se queda en la hoja
    }
  }

  function abrirHoja(id) {
    const h = $(id);
    document.querySelectorAll(".civil-menu[open]").forEach((d) => d.removeAttribute("open"));
    h.showModal(); hojaAbierta = h;
    return h;
  }
  const cerrarHoja = () => { if (hojaAbierta && hojaAbierta.open) hojaAbierta.close(); hojaAbierta = null; };

  // -- Formularios: campos que dependen de otros -------------------------------------------------------------------------------
  function enlazarDependientes(form) {
    const pintar = () => {
      form.querySelectorAll("[data-civil-si]").forEach((n) => {
        const [campo, valor] = n.dataset.civilSi.split("=");
        const c = form.elements[campo];
        let v = "";
        if (c instanceof RadioNodeList) v = c.value; else if (c && c.type === "checkbox") v = c.checked ? "1" : ""; else if (c) v = c.value;
        n.hidden = v !== valor;
      });
    };
    form.addEventListener("change", pintar); pintar();
  }
  const valor = (form, n) => { const c = form.elements[n]; if (!c) return undefined; if (c instanceof RadioNodeList) return c.value; if (c.type === "checkbox") return c.checked; return c.value; };
  const numero = (form, n, defecto) => { const v = parseFloat(String(valor(form, n) ?? "").replace(",", ".")); return Number.isFinite(v) ? v : defecto; };

  function leerEstacion(t) {
    const s = String(t || "").replace(/\s/g, "");
    const m = s.match(/^(-?)(\d+)\+(\d+(?:\.\d+)?)$/);
    if (m) return (m[1] ? -1 : 1) * (parseInt(m[2], 10) * 1000 + parseFloat(m[3]));
    const v = parseFloat(s.replace(",", "."));
    if (!Number.isFinite(v)) throw new ErrorCivil(`Estación no válida: «${t}». Escríbela como 0+120.00.`);
    return v;
  }

  // -- Escala ---------------------------------------------------------------------------------------------------------------------
  function pintarEscala() {
    const t = $("civil-escala-texto"); if (t) t.textContent = `1:${est.escala.denominador}`;
    const z = `zona ${est.escala.zona} ${est.escala.hemisferio === "S" ? "sur" : "norte"}`;
    document.querySelectorAll("[data-civil-zona-texto]").forEach((n) => { n.textContent = z; });
  }
  const zona = () => ({ zona: est.escala.zona, hemisferio: est.escala.hemisferio });

  async function detectarEscala() {
    // La escala viaja con el plano: los bloques de punto se insertan a escala E/1000.
    try {
      const r = await pedir({ op: "query", type: "INSERT", detail: "full", limit: 1, where: [{ path: "/block", op: "in", value: Object.values(C.bloques) }] });
      const e = (r.entities || [])[0];
      const s = e && e.properties && (e.properties.x_scale || (e.properties.scale && e.properties.scale.x));
      if (s > 0) { est.escala.denominador = Math.round(s * 1000 * 1000) / 1000; pintarEscala(); }
    } catch (_) { /* sin puntos de Civil: se queda la guardada */ }
  }

  function prepararEscala() {
    const f = document.querySelector('[data-civil-form="escala"]');
    enlazarDependientes(f);
    const llenar = () => {
      const den = String(est.escala.denominador);
      const sel = f.elements.denominador;
      if ([...sel.options].some((o) => o.value === den)) sel.value = den; else { sel.value = "otra"; f.elements.otra.value = `1:${den}`; }
      for (const [k, v] of Object.entries(est.escala.alturas_mm)) if (f.elements[`alt_${k}`]) f.elements[`alt_${k}`].value = v;
      const z = String(est.escala.zona);
      if ([...f.elements.zona.options].some((o) => o.value === z)) f.elements.zona.value = z; else { f.elements.zona.value = "otra"; f.elements.zona_otra.value = z; }
      f.elements.hemisferio.value = est.escala.hemisferio;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Aplicando…", async () => {
        let den = f.elements.denominador.value;
        if (den === "otra") den = String(f.elements.otra.value).replace(/^\s*1\s*[:/]\s*/, "");
        const n = parseFloat(String(den).replace(",", "."));
        if (!(n > 0)) throw new ErrorCivil("Escribe la escala como 1:750.");
        const z = parseInt(f.elements.zona.value === "otra" ? f.elements.zona_otra.value : f.elements.zona.value, 10);
        if (!(z >= 1 && z <= 60)) throw new ErrorCivil("La zona UTM va de 1 a 60.");
        est.escala.zona = z; est.escala.hemisferio = f.elements.hemisferio.value === "S" ? "S" : "N";
        const anterior = est.escala.denominador;
        est.escala.denominador = n;
        for (const k of Object.keys(est.escala.alturas_mm)) {
          const v = numero(f, `alt_${k}`, est.escala.alturas_mm[k]);
          if (!(v > 0)) throw new ErrorCivil("Las alturas deben ser mayores que cero.");
          est.escala.alturas_mm[k] = v;
        }
        guardarAjustes(); pintarEscala();
        if (valor(f, "redibujar") && anterior !== n) {
          // Los puntos se insertan a escala E/1000: se vuelven a dibujar a la escala nueva, con sus rótulos tal como estaban.
          const pts = await insercionesDe(null);
          if (pts.length) {
            const res = await calcular("redibujar_puntos", { puntos: pts });
            await insertarDxf(res.dxf, "puntos.dxf");
            await borrar(pts.map((e) => e.handle));
          }
        }
        cerrarHoja();
        avisar(`Escala 1:${n}`);
      });
    });
    return llenar;
  }

  // -- Archivos de puntos: delimitador, columnas en cualquier orden y formatos guardados ----------------------------------------
  // La hoja de importar muestra los primeros renglones como tabla y, sobre cada columna, una lista para decir qué es. Lo reconocido
  // llena todo; lo que la persona elige se respeta. Los formatos con nombre se guardan en el servidor, por cuenta (cad/civil/formatos/).
  const opcionesColumna = (conIgnorar) => (C.columnas || []).filter(([k]) => conIgnorar || k !== "-").map(([k, n]) => `<option value="${esc(k)}">${esc(n)}</option>`).join("");
  const retardo = (fn, ms = 300) => { let t = null; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  /** Lo que la persona eligió como delimitador: una de las opciones o el que escribió ("Otro…"). */
  function delimDe(f) { const t = valor(f, "delimitador_tipo"); return t === "otro" ? f.elements.delimitador_otro.value : t; }
  function ponerDelim(f, d) {
    const sel = f.elements.delimitador_tipo;
    const hay = [...sel.options].some((o) => o.value === d && o.value !== "otro");
    sel.value = hay ? d : "otro";
    f.elements.delimitador_otro.value = hay ? "" : d;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const decirDelim = (d) => ({ ",": "coma", ";": "punto y coma", "\t": "tabulador", " ": "espacios", "|": "barra" }[d] || `«${d}»`);
  const columnasDe = (cols, n) => Array.from({ length: n }, (_, i) => (cols && cols[i]) || "-");

  /** Formatos con nombre de esta hoja: la lista, guardar con nombre y borrar. `leer()` da el formato de la hoja; `aplicar(datos | null)` lo pone. */
  function prepararFormatos(f, uso, { leer, aplicar }) {
    const lista = f.querySelector("[data-civil-formato-lista]"), quitar = f.querySelector("[data-civil-formato-borrar]");
    const fila = f.querySelector("[data-civil-formato-guardar]"), nombre = f.elements.nombre_formato;
    let guardados = [], confirmarBorrado = null;
    const pintar = (elegido = "") => {
      lista.length = 1;
      for (const g of guardados) lista.add(new Option(g.nombre, g.nombre));
      lista.value = guardados.some((g) => g.nombre === elegido) ? elegido : "";
      quitar.hidden = !lista.value;
    };
    async function llamar(metodo, extra = "", cuerpoJson = null) {
      const r = await fetch(`${C.urls.formatos}?uso=${uso}${extra}`, { method: metodo, credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(), ...(cuerpoJson ? { "Content-Type": "application/json" } : {}) }, ...(cuerpoJson ? { body: JSON.stringify(cuerpoJson) } : {}) });
      let d = {};
      try { d = await r.json(); } catch (_) { /* nada */ }
      if (!r.ok) throw new ErrorCivil(d.error || "No se pudo guardar el formato. Inténtalo de nuevo.");
      return d;
    }
    const cerrarFila = () => { fila.hidden = true; };
    lista.addEventListener("change", () => {
      quitar.hidden = !lista.value; cerrarFila();
      aplicar(guardados.find((g) => g.nombre === lista.value)?.datos || null);
    });
    f.querySelector("[data-civil-formato-nuevo]").addEventListener("click", () => {
      fila.hidden = false; nombre.value = lista.value; nombre.focus();
      fila.scrollIntoView({ block: "nearest" });
    });
    f.querySelector("[data-civil-formato-cancelar]").addEventListener("click", cerrarFila);
    const guardar = async () => {
      try {
        const d = await llamar("POST", "", { nombre: nombre.value, datos: leer() });
        guardados = d.formatos; pintar(d.guardado.nombre); cerrarFila();
        avisar(`Formato «${d.guardado.nombre}» guardado`);
      } catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudo guardar el formato.", "error"); if (!(err instanceof ErrorCivil)) console.error(err); }
    };
    f.querySelector("[data-civil-formato-confirmar]").addEventListener("click", guardar);
    nombre.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); guardar(); } else if (ev.key === "Escape") { ev.stopPropagation(); cerrarFila(); } });
    // borrar pide un segundo toque (sin diálogos del navegador)
    quitar.addEventListener("click", async () => {
      if (!confirmarBorrado) {
        quitar.dataset.confirmar = "1"; quitar.setAttribute("data-tv-tooltip", "Toca otra vez para borrarlo");
        confirmarBorrado = setTimeout(() => { confirmarBorrado = null; delete quitar.dataset.confirmar; quitar.setAttribute("data-tv-tooltip", "Borrar este formato guardado"); }, 4000);
        return;
      }
      clearTimeout(confirmarBorrado); confirmarBorrado = null; delete quitar.dataset.confirmar;
      const n = lista.value;
      try { guardados = (await llamar("DELETE", `&nombre=${encodeURIComponent(n)}`)).formatos; pintar(""); aplicar(null); avisar(`Formato «${n}» borrado`); }
      catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudo borrar el formato.", "error"); }
    });
    return async function cargar() {
      cerrarFila();
      try { guardados = (await llamar("GET")).formatos; pintar(lista.value); } catch (_) { /* sin la lista se puede trabajar igual */ }
    };
  }

  // -- Puntos: importar ---------------------------------------------------------------------------------------------------------
  function prepararImportar() {
    const f = document.querySelector('[data-civil-form="importar"]');
    enlazarDependientes(f);
    const entrada = f.elements.archivo, vista = $("civil-imp-vista"), enviar = f.querySelector("[data-civil-enviar]");
    let archivoB64 = null, nombreArchivo = "", columnasManual = null, turno = 0, ultima = null, ultimaVista = null;
    const formato = () => ({ delimitador: delimDe(f), juntar: !!valor(f, "juntar"), calificador: valor(f, "calificador"), decimal: valor(f, "decimal"),
      saltar: parseInt(valor(f, "saltar"), 10) || 0, columnas: columnasManual ? columnasManual.slice() : [] });
    const opciones = () => ({ archivo: archivoB64, tipo: valor(f, "tipo"), prefijo: valor(f, "prefijo") || "", decimales_z: parseInt(valor(f, "decimales_z"), 10),
      numero_libre: est.libre || 1, formato: formato(), etiquetas: { numero: valor(f, "et_numero"), elevacion: valor(f, "et_elevacion"), descripcion: valor(f, "et_descripcion") } });

    function pintarTabla(r) {
      const n = Math.max(r.campos || 0, ...(r.tabla || []).map((t) => t.campos.length), 0);
      if (!n) return "";
      const cols = columnasDe(columnasManual || (r.formato && r.formato.columnas), n);
      const cabeza = cols.map((c, i) => `<th><select data-civil-col="${i}" aria-label="Qué es la columna ${i + 1}" class="${c === "-" ? "civil-col--ignorada" : ""}">${opcionesColumna(true)}</select></th>`).join("");
      const filas = r.tabla.slice(0, 8).map((t) => `<tr><td class="civil-mapa__linea">${t.linea}</td>${Array.from({ length: n }, (_, i) => `<td class="${cols[i] === "-" ? "civil-col--ignorada" : ""}">${esc(t.campos[i] ?? "")}</td>`).join("")}</tr>`).join("");
      const orden = `<select data-civil-orden aria-label="Poner un orden común"><option value="">Poner un orden común…</option>${Object.entries(C.ordenes || {}).map(([k, e]) => `<option value="${esc(k)}">${esc(k)} · ${esc(e)}</option>`).join("")}</select>`;
      return { n, cols, html: `<div class="civil-mapa__cabeza"><span class="civil-etiqueta">Qué es cada columna</span>${orden}</div>
        <div class="tv-tabla-envoltura"><table class="tv-tabla civil-tabla civil-mapa"><thead><tr><th class="civil-mapa__linea">Línea</th>${cabeza}</tr></thead><tbody>${filas}</tbody></table></div>` };
    }
    async function previa() {
      if (!archivoB64) return;
      const mio = ++turno;
      vista.hidden = false; if (!vista.innerHTML) vista.innerHTML = `<p class="tv-tenue">Leyendo…</p>`; enviar.disabled = true;
      vista.dataset.leyendo = "1";
      let r;
      try { r = await calcular("importar_puntos", { ...opciones(), solo_vista: true }); }
      catch (err) {
        if (mio !== turno) return;
        delete vista.dataset.leyendo;
        // un cambio a medias (p. ej. dos columnas de elevación) no quita la tabla: sigue ahí para corregirlo
        const t = ultimaVista ? pintarTabla(ultimaVista) : "";
        vista.innerHTML = `<p class="civil-aviso-linea civil-aviso-linea--error">${icono("circle-alert")} ${esc(err.message)}</p>${t ? t.html : ""}`;
        if (t) vista.querySelectorAll("[data-civil-col]").forEach((x) => { x.value = t.cols[+x.dataset.civilCol]; });
        return;
      }
      if (mio !== turno) return;                                         // llegó tarde: la persona ya cambió otra cosa
      delete vista.dataset.leyendo;
      ultima = r.formato || null; ultimaVista = r;
      if (r.formato) { f.elements.juntar.checked = !!r.formato.juntar; }
      const t = pintarTabla(r);
      let cabeza = "";
      if (r.error) cabeza = `<p class="civil-aviso-linea civil-aviso-linea--error">${icono("circle-alert")} ${esc(r.error)}</p>`;
      else {
        const s = r.resumen;
        const hechas = [`<strong>${s.puntos.toLocaleString("es-MX")} puntos</strong>`, `${s.orden} (por ${esc(s.orden_de)})`, `delimitados por ${esc(s.separador)}`, s.coma_decimal ? "coma decimal" : ""].filter(Boolean).join(" · ");
        const avisos = (r.avisos || []).filter((a) => !/renglones no se leyeron/.test(a)).map((a) => `<p class="civil-aviso-linea">${icono("triangle-alert")} ${esc(a)}</p>`).join("");
        const geo = s.geograficas ? `<p class="civil-nota">Latitud y longitud: se convierten a la zona UTM del plano (<span>${esc(est.escala.zona)}${est.escala.hemisferio === "S" ? " sur" : " norte"}</span>).</p>` : "";
        cabeza = `<p class="civil-vista__resumen">${hechas}</p>${avisos}${geo}`;
      }
      const desc = !r.error && r.descartados.length ? `<details class="civil-descartados"><summary>${r.descartados.length} renglones no se leyeron</summary><ul>${r.descartados.slice(0, 30).map((d) => `<li>Línea ${d.linea}: ${esc(d.motivo)}</li>`).join("")}</ul></details>` : "";
      vista.innerHTML = `${cabeza}${t ? t.html : ""}${desc}`;
      if (t) vista.querySelectorAll("[data-civil-col]").forEach((s) => { s.value = t.cols[+s.dataset.civilCol]; });
      enviar.disabled = !!r.error;
      enviar.querySelector("span").textContent = r.error ? "Importar" : `Importar ${r.resumen.puntos.toLocaleString("es-MX")} puntos`;
    }
    const previaPronta = retardo(previa);
    // qué es cada columna: la lista sobre cada una (o un orden común)
    vista.addEventListener("change", (ev) => {
      const col = ev.target.closest("[data-civil-col]"), orden = ev.target.closest("[data-civil-orden]");
      if (col) columnasManual = [...vista.querySelectorAll("[data-civil-col]")].map((s) => s.value);
      else if (orden && orden.value) { const n = vista.querySelectorAll("[data-civil-col]").length; columnasManual = columnasDe(orden.value.split(""), n); }
      else return;
      previa();
    });
    // delimitador, texto entre, decimal, renglones: se lee otra vez; las columnas elegidas se respetan
    for (const n of ["delimitador_tipo", "calificador", "decimal", "juntar", "tipo", "prefijo"]) { const c = f.elements[n]; (c instanceof RadioNodeList ? [...c] : [c]).forEach((x) => x.addEventListener("change", previa)); }
    for (const n of ["delimitador_otro", "saltar"]) f.elements[n].addEventListener("input", previaPronta);
    entrada.addEventListener("change", () => {
      const a = entrada.files[0];
      if (!a) return;
      nombreArchivo = a.name; if (!f.querySelector("[data-civil-formato-lista]").value) columnasManual = null; ultima = null; ultimaVista = null; vista.innerHTML = "";
      $("civil-imp-nombre").innerHTML = `<strong>${esc(a.name)}</strong><span class="tv-tenue">${(a.size / 1024).toFixed(0)} KB · cambiar</span>`;
      const lector = new FileReader();
      lector.onload = () => { archivoB64 = String(lector.result).split(",")[1] || ""; previa(); };
      lector.readAsDataURL(a);
    });
    const zona = f.querySelector(".civil-archivo");
    zona.addEventListener("dragover", (ev) => { ev.preventDefault(); zona.dataset.encima = "1"; });
    zona.addEventListener("dragleave", () => { delete zona.dataset.encima; });
    zona.addEventListener("drop", (ev) => { ev.preventDefault(); delete zona.dataset.encima; if (ev.dataTransfer.files.length) { entrada.files = ev.dataTransfer.files; entrada.dispatchEvent(new Event("change")); } });
    const cargarFormatos = prepararFormatos(f, "importar", {
      leer: () => { const fm = formato(); return { ...fm, columnas: columnasManual || [] }; },     // lo reconocido no se guarda: el formato vuelve a reconocer
      aplicar: (fm) => {
        const d = fm || { delimitador: "", juntar: false, calificador: '"', decimal: "", columnas: [], saltar: 0 };
        ponerDelim(f, d.delimitador || ""); f.elements.calificador.value = d.calificador ?? '"'; f.elements.decimal.value = d.decimal || "";
        f.elements.saltar.value = d.saltar || 0; f.elements.juntar.checked = !!d.juntar;
        columnasManual = d.columnas && d.columnas.length ? d.columnas.slice() : null;
        previa();
      },
    });
    alAbrir.importar = async () => {
      cargarFormatos();
      try { est.libre = Number(sigNumero(await insercionesDe())); } catch (_) { est.libre = 1; }
    };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Dibujando los puntos…", async () => {
        const r = await calcular("importar_puntos", opciones());
        await insertarDxf(r.dxf, nombreArchivo.replace(/\.[^.]+$/, "") + ".dxf");
        await pedir({ op: "action", name: "zoom_extents" }).catch(() => {});
        cerrarHoja();
        avisar(`${r.resumen.puntos.toLocaleString("es-MX")} puntos importados`);
        for (const a of (r.avisos || []).slice(0, 3)) avisar(a);
      });
    });
  }

  // -- Puntos: exportar ---------------------------------------------------------------------------------------------------------
  const PUNTOS_DE_EJEMPLO = [[500000.123, 2000000.456, 100.5, "1", "TN"], [500010.5, 2000020.25, 101.75, "2", "ESQ, NE"], [500025, 2000040, 102, "3", ""]];
  const texto64 = (b64) => new TextDecoder("utf-8").decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
  /** Hasta tres puntos de ese tipo del plano, para el ejemplo de la hoja (sin leerlos todos). */
  async function muestraDe(cuales) {
    if (cuales !== "terreno" && cuales !== "proyecto") return [];
    const capa = C.capas.puntos[cuales];
    try {
      const r = await pedir({ op: "query", type: "INSERT", layer: capa, detail: "full", fields: ["handle", "block", "position", "attributes"], limit: 3 });
      return (r.entities || []).filter((e) => Object.values(C.bloques).includes(e.block)).map(punto);
    } catch (_) { return []; }
  }

  function prepararExportar() {
    const f = document.querySelector('[data-civil-form="exportar"]');
    const caja = f.querySelector("[data-civil-columnas]"), ejemplo = f.querySelector("[data-civil-ejemplo]"), enviar = f.querySelector("button[type=submit]");
    enlazarDependientes(f);
    const geo = f.querySelector("[data-civil-geo]");
    let muestras = {}, turno = 0;
    const fila = (c, k) => `<div class="civil-columna"><span class="civil-columna__n" aria-hidden="true">${k + 1}</span><select data-civil-col aria-label="Columna ${k + 1}">${opcionesColumna(false)}</select>
      <button type="button" class="tv-boton tv-boton--fantasma tv-boton--icono" data-civil-columna-quitar data-tv-tooltip="Quitar la columna" aria-label="Quitar la columna ${k + 1}">${icono("x")}</button></div>`;
    const columnas = () => [...caja.querySelectorAll("[data-civil-col]")].map((s) => s.value);
    function ponerColumnas(cols) {
      caja.innerHTML = cols.map(fila).join("");
      caja.querySelectorAll("[data-civil-col]").forEach((s, i) => { s.value = cols[i]; });
      renumerar();
    }
    function renumerar() {
      const n = caja.children.length;
      [...caja.children].forEach((d, i) => { d.querySelector(".civil-columna__n").textContent = i + 1; d.querySelector("[data-civil-columna-quitar]").disabled = n <= 1; });
      const cols = columnas();
      geo.hidden = !(cols.includes("LAT") || cols.includes("LON"));
    }
    const formato = () => ({ delimitador: delimDe(f), calificador: valor(f, "calificador"), decimal: valor(f, "decimal"), columnas: columnas(), encabezado: !!valor(f, "encabezado"),
      decimales: parseInt(valor(f, "decimales"), 10), decimales_z: parseInt(valor(f, "decimales_z"), 10), decimales_geo: parseInt(valor(f, "decimales_geo"), 10) });
    const abierta = () => { const d = f.closest("dialog"); return !!(d && d.open); };
    async function verEjemplo() {
      if (!abierta()) return;                                             // la hoja cerrada no pide nada al servidor
      const mio = ++turno;
      const cuales = valor(f, "cuales");
      if (!(cuales in muestras)) muestras[cuales] = await muestraDe(cuales);
      const pts = muestras[cuales].length ? muestras[cuales] : PUNTOS_DE_EJEMPLO;
      try {
        const r = await calcular("exportar_puntos", { puntos: pts, formato: formato() });
        if (mio !== turno) return;
        ejemplo.textContent = texto64(r.archivo).replace(/\r/g, "").trimEnd() + (muestras[cuales].length ? "" : "\n(con puntos de ejemplo)");
        ejemplo.classList.remove("civil-texto--error"); enviar.disabled = false;
      } catch (err) {
        if (mio !== turno) return;
        ejemplo.textContent = err.message; ejemplo.classList.add("civil-texto--error"); enviar.disabled = true;
      }
    }
    const ejemploPronto = retardo(verEjemplo, 200);
    f.addEventListener("change", (ev) => { if (ev.target.name === "cuales") muestras = {}; if (ev.target.closest("[data-civil-col]")) renumerar(); if (!ev.target.name || ev.target.name !== "nombre_formato") ejemploPronto(); });
    f.elements.delimitador_otro.addEventListener("input", ejemploPronto);
    f.querySelector("[data-civil-columna-agregar]").addEventListener("click", () => {
      const usadas = columnas(), libre = ["P", "N", "E", "Z", "D"].find((c) => !usadas.includes(c)) || "D";
      caja.insertAdjacentHTML("beforeend", fila(libre, caja.children.length));
      caja.lastElementChild.querySelector("select").value = libre; renumerar(); ejemploPronto();
    });
    caja.addEventListener("click", (ev) => {
      const q = ev.target.closest("[data-civil-columna-quitar]");
      if (!q || caja.children.length <= 1) return;
      q.closest(".civil-columna").remove(); renumerar(); ejemploPronto();
    });
    f.elements.orden.addEventListener("change", () => { const o = f.elements.orden.value; if (o) { ponerColumnas(o.split("")); f.elements.orden.value = ""; ejemploPronto(); } });
    const aplicar = (fm) => {
      const d = { delimitador: ",", calificador: '"', decimal: ".", columnas: ["P", "E", "N", "Z", "D"], encabezado: false, decimales: 3, decimales_z: 3, decimales_geo: 8, ...(fm || {}) };
      ponerDelim(f, d.delimitador); f.elements.calificador.value = d.calificador; f.elements.decimal.value = d.decimal;
      f.elements.encabezado.checked = !!d.encabezado;
      for (const k of ["decimales", "decimales_z", "decimales_geo"]) f.elements[k].value = String(d[k]);
      ponerColumnas(d.columnas); ejemploPronto();
    };
    const cargarFormatos = prepararFormatos(f, "exportar", { leer: formato, aplicar });
    aplicar(null);
    alAbrir.exportar = () => { muestras = {}; cargarFormatos(); ejemploPronto(); };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo los puntos…", async () => {
        const cuales = valor(f, "cuales");
        let pts;
        if (cuales === "seleccion") {
          const e = await elegir({ mensaje: "Elige los puntos", tipos: ["Block Reference", "INSERT", "Insert", "Point"] });
          if (!e) return;
          pts = e.map(punto);
        } else pts = await puntosDe(cuales);
        if (!pts.length) throw new ErrorCivil("No hay puntos de ese tipo en el plano.");
        const fm = formato();
        const r = await calcular("exportar_puntos", { puntos: pts, formato: fm });
        const ext = fm.delimitador === "," && fm.decimal !== "," ? "csv" : "txt";
        descargar(r.archivo, `puntos ${cuales === "seleccion" ? "elegidos" : cuales} ${window.TVCad.estado().plano.nombre}.${ext}`, ext === "csv" ? "text/csv" : "text/plain");
        avisar(`${pts.length.toLocaleString("es-MX")} puntos exportados`);
      }, { soloLee: true });
    });
  }

  // -- Google Earth: importar KML o KMZ -----------------------------------------------------------------------------------------
  function prepararImportarKml() {
    const f = document.querySelector('[data-civil-form="importar_kml"]');
    const entrada = f.elements.archivo, vista = $("civil-kml-vista"), enviar = f.querySelector("[data-civil-enviar]");
    let archivoB64 = null, nombreArchivo = "", turno = 0;
    const opciones = () => ({ archivo: archivoB64, capas_por: valor(f, "capas_por"), puntos_como: valor(f, "puntos_como"), nombres: !!valor(f, "nombres"), altitudes: !!valor(f, "altitudes"),
      numero_libre: est.libre || 1 });
    const cuenta = (n, uno, varios) => `<span class="civil-ficha"><b>${n.toLocaleString("es-MX")}</b> ${n === 1 ? uno : varios}</span>`;
    async function previa() {
      if (!archivoB64) return;
      const mio = ++turno;
      vista.hidden = false; vista.innerHTML = `<p class="tv-tenue">Leyendo…</p>`; enviar.disabled = true;
      try {
        const r = await calcular("importar_kml", { ...opciones(), solo_vista: true });
        if (mio !== turno) return;
        const s = r.resumen;
        const fichas = [s.poligonos ? cuenta(s.poligonos, "polígono", "polígonos") : "", s.lineas ? cuenta(s.lineas, "línea", "líneas") : "", s.puntos ? cuenta(s.puntos, "punto", "puntos") : ""].join("");
        const capas = s.capas.length ? `<p class="civil-nota"><strong>${s.capas.length === 1 ? "Capa" : "Capas"}:</strong> ${s.capas.slice(0, 12).map(esc).join(", ")}${s.capas.length > 12 ? `… y ${s.capas.length - 12} más` : ""}</p>` : "";
        const avisos = (r.avisos || []).slice(0, 6).map((a) => `<p class="civil-aviso-linea">${icono("triangle-alert")} ${esc(a)}</p>`).join("");
        vista.innerHTML = `<p class="civil-vista__resumen"><strong>${esc(s.nombre || nombreArchivo)}</strong> · ${s.kmz ? "KMZ" : "KML"}</p><div class="civil-fichas">${fichas}</div>${s.huecos ? `<p class="civil-nota">${s.huecos} hueco${s.huecos === 1 ? "" : "s"} en polígonos.</p>` : ""}${capas}${avisos}`;
        enviar.disabled = false;
        enviar.querySelector("span").textContent = `Importar ${(s.poligonos + s.lineas + s.puntos).toLocaleString("es-MX")} elementos`;
      } catch (err) {
        if (mio === turno) vista.innerHTML = `<p class="civil-aviso-linea civil-aviso-linea--error">${icono("circle-alert")} ${esc(err.message)}</p>`;
      }
    }
    entrada.addEventListener("change", () => {
      const a = entrada.files[0];
      if (!a) return;
      nombreArchivo = a.name;
      $("civil-kml-nombre").innerHTML = `<strong>${esc(a.name)}</strong><span class="tv-tenue">${a.size >= 1048576 ? `${(a.size / 1048576).toFixed(1)} MB` : `${(a.size / 1024).toFixed(0)} KB`} · cambiar</span>`;
      const lector = new FileReader();
      lector.onload = () => { archivoB64 = String(lector.result).split(",")[1] || ""; previa(); };
      lector.readAsDataURL(a);
    });
    const zona = f.querySelector(".civil-archivo");
    zona.addEventListener("dragover", (ev) => { ev.preventDefault(); zona.dataset.encima = "1"; });
    zona.addEventListener("dragleave", () => { delete zona.dataset.encima; });
    zona.addEventListener("drop", (ev) => { ev.preventDefault(); delete zona.dataset.encima; if (ev.dataTransfer.files.length) { entrada.files = ev.dataTransfer.files; entrada.dispatchEvent(new Event("change")); } });
    for (const n of ["capas_por", "puntos_como", "nombres", "altitudes"]) { const c = f.elements[n]; (c instanceof RadioNodeList ? [...c] : [c]).forEach((x) => x.addEventListener("change", previa)); }
    alAbrir.importar_kml = async () => { try { est.libre = Number(sigNumero(await insercionesDe())); } catch (_) { est.libre = 1; } };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Dibujando…", async () => {
        const r = await calcular("importar_kml", opciones());
        await insertarDxf(r.dxf, nombreArchivo.replace(/\.[^.]+$/, "") + ".dxf");
        await pedir({ op: "action", name: "zoom_extents" }).catch(() => {});
        cerrarHoja();
        const s = r.resumen;
        avisar(`${(s.poligonos + s.lineas + s.puntos).toLocaleString("es-MX")} elementos importados`);
        for (const a of (r.avisos || []).slice(0, 3)) avisar(a);
      });
    });
  }

  // -- Google Earth: exportar KML o KMZ -----------------------------------------------------------------------------------------
  /** Textos (TEXT y MTEXT) dentro de una caja del plano: sus nombres posibles para los polígonos. */
  async function textosEnCaja(caja) {
    const salida = [];
    for (const tipo of ["TEXT", "MTEXT"]) {
      for (const e of await consultar({ type: tipo, detail: "full", bounds: caja })) {
        const p = e.properties || {};
        const alineado = e.type === "Text" && p.alignment_point && ((p.horizontal_alignment && p.horizontal_alignment !== "Left") || (p.vertical_alignment && p.vertical_alignment !== "Baseline"));
        const pos = alineado ? p.alignment_point : p.insertion_point;
        const valorT = String(e.value ?? p.value ?? "");
        if (pos && valorT.trim()) salida.push({ valor: valorT, posicion: [pos.x, pos.y], altura: Number(p.height || e.height || 0) });
      }
    }
    return salida;
  }

  function prepararExportarKml() {
    const f = document.querySelector('[data-civil-form="exportar_kml"]');
    const res = f.querySelector("[data-civil-resultado]");
    const resumenElegidas = (n, cerradas) => `${n} polilínea${n === 1 ? "" : "s"} · ${cerradas} cerrada${cerradas === 1 ? "" : "s"}`;
    prepararElecciones(f, null, { lineas: async (cuenta) => {
      const e = await elegir({ mensaje: "Elige las polilíneas (las cerradas salen como polígonos)", tipos: ["Polyline", "LwPolyline", "Polyline3D", "Line"], sinResultados: true });
      if (!e) return;
      est.elegidos.exportar_kml.lineas = e.map((x) => ({ ...polilinea(x), capa: x.layer || "" }));
      cuenta.textContent = resumenElegidas(e.length, est.elegidos.exportar_kml.lineas.filter((p) => p.cerrada).length);
      cuenta.classList.add("civil-elegido");
    } });
    enlazarDependientes(f);
    alAbrir.exportar_kml = () => { res.hidden = true; };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      res.hidden = true;
      trabajo(f, "Leyendo el plano…", async (decir) => {
        let polis;
        if (valor(f, "origen") === "cerradas") {
          polis = (await consultar({ type: "LWPOLYLINE", detail: "full" })).map((x) => ({ ...polilinea(x), capa: x.layer || "" })).filter((p) => p.cerrada && !CAPAS_DE_RESULTADO.test(p.capa));
        } else polis = ((est.elegidos.exportar_kml || {}).lineas || []);
        const cuales = valor(f, "puntos");
        const pts = [];
        if (cuales === "terreno" || cuales === "ambos") pts.push(...(await puntosDe("terreno")));
        if (cuales === "proyecto" || cuales === "ambos") pts.push(...(await puntosDe("proyecto")));
        if (!polis.length && !pts.length) throw new ErrorCivil(valor(f, "origen") === "cerradas" ? "No hay polilíneas cerradas en el plano." : "Elige las polilíneas que vas a exportar.");
        const nombres = valor(f, "nombres");
        let textos = [];
        const cerradas = polis.filter((p) => p.cerrada);
        if (nombres === "etiqueta" && cerradas.length) {
          decir("Buscando los nombres…");
          const caja = unirCajas(cerradas.map((p) => [Math.min(...p.vertices.map((v) => v[0])), Math.min(...p.vertices.map((v) => v[1])), Math.max(...p.vertices.map((v) => v[0])), Math.max(...p.vertices.map((v) => v[1]))]));
          textos = await textosEnCaja(caja);
        }
        decir("Convirtiendo a latitud y longitud…");
        const r = await calcular("exportar_kml", {
          polilineas: polis.map((p) => ({ vertices: p.vertices.map((v) => [v[0], v[1]]), combas: p.combas, cerrada: p.cerrada, capa: p.capa })), puntos: pts, textos,
          nombres: { modo: nombres, prefijo: valor(f, "prefijo") || "", inicio: parseInt(valor(f, "inicio"), 10) || 1 }, formato: valor(f, "formato"), nombre: nombrePlano(),
          estilo: { linea_color: valor(f, "linea_color"), linea_opacidad: numero(f, "linea_opacidad", 100), linea_ancho: numero(f, "linea_ancho", 2), relleno_color: valor(f, "relleno_color"), relleno_opacidad: numero(f, "relleno_opacidad", 25) } }, decir);
        descargar(r.archivo, `${nombrePlano()}.${r.extension}`, r.tipo);
        const s = r.resumen;
        res.hidden = false;
        res.innerHTML = `<dl class="civil-cifras"><div><dt>Polígonos</dt><dd>${s.poligonos.toLocaleString("es-MX")}</dd></div><div><dt>Líneas</dt><dd>${s.lineas.toLocaleString("es-MX")}</dd></div><div><dt>Puntos</dt><dd>${s.puntos.toLocaleString("es-MX")}</dd></div>${s.poligonos ? `<div><dt>Superficie</dt><dd>${mil(s.superficie, 2)} m²</dd></div>` : ""}</dl>${nombres === "etiqueta" && s.poligonos ? `<p class="civil-nota">${s.con_etiqueta} de ${s.poligonos} polígonos con el texto de adentro como nombre.</p>` : ""}`;
        avisar(`${nombrePlano()}.${r.extension} descargado`);
      }, { soloLee: true });
    });
  }

  // -- Superficies: cuál se usa (Terreno o Proyecto) --------------------------------------------------------------------------------
  /** Cuántos triángulos tiene cada triangulación del plano (sin leer sus vértices). */
  async function contarCaras(tipo) { return (await consultar({ type: "3DFACE", layer: C.capas.tin[tipo], detail: "summary" })).length; }

  /** La línea «Terreno · N triángulos / Proyecto · N triángulos» de una hoja y la frase de cuál superficie usa; con `con_superficie`, el interruptor. */
  function prepararSuperficies(f, frase, { excluye = null } = {}) {
    const caja = f.querySelector("[data-civil-superficies]");
    const interruptor = f.elements.con_superficie, uso = caja.querySelector("[data-civil-sup-uso]");
    let cuenta = null;
    const pintarUso = () => { uso.textContent = frase(!!(interruptor && interruptor.checked), !!(f.elements.con_rasante && f.elements.con_rasante.checked)); };
    if (interruptor) {
      interruptor.addEventListener("change", () => {
        if (interruptor.checked && excluye && f.elements[excluye].checked) { f.elements[excluye].checked = false; f.elements[excluye].dispatchEvent(new Event("change", { bubbles: true })); }
        pintarUso();
      });
      if (excluye) f.elements[excluye].addEventListener("change", () => {
        if (f.elements[excluye].checked && interruptor.checked) { interruptor.checked = false; interruptor.dispatchEvent(new Event("change", { bubbles: true })); }
        pintarUso();
      });
    }
    if (f.elements.con_rasante && !excluye) f.elements.con_rasante.addEventListener("change", pintarUso);
    pintarUso();
    return async function actualizar() {
      pintarUso();
      try { cuenta = { terreno: await contarCaras("terreno"), proyecto: await contarCaras("proyecto") }; } catch (_) { return; }
      for (const t of ["terreno", "proyecto"]) {
        const n = cuenta[t], chip = caja.querySelector(`[data-civil-sup="${t}"]`);
        chip.classList.toggle("civil-superficie--vacia", n === 0);
        chip.querySelector("i").textContent = n ? `${n.toLocaleString("es-MX")} triángulo${n === 1 ? "" : "s"}` : "Sin triangular";
      }
    };
  }
  /** Las caras de la superficie de proyecto cuando la hoja la pide (interruptor encendido); si no hay triangulación, dice qué hacer. */
  async function carasProyectoSi(f) {
    if (!valor(f, "con_superficie")) return null;
    const caras = await carasDe("proyecto");
    if (!caras.length) throw new ErrorCivil("No hay triangulación de proyecto. Importa los puntos de proyecto y triangúlalos como «Proyecto» (menú Triangulación).");
    return caras;
  }

  function descargar(b64, nombre, tipo) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: tipo }));
    const a = document.createElement("a");
    a.href = url; a.download = nombre.replace(/[\\/:*?"<>|]+/g, "-"); document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  function descargarDocumento(r, nombre) { descargar(r.archivo, `${nombre}.${r.extension}`, r.tipo); }
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // -- Elecciones de las hojas (eje, límite, quiebres…) ------------------------------------------------------------------------
  const ELECCIONES = {
    quiebres: { mensaje: "Elige las líneas de quiebre", tipos: ["Polyline", "LwPolyline", "Polyline3D", "Line"], maximo: Infinity, texto: (n) => `${n} línea${n === 1 ? "" : "s"}` },
    limite: { mensaje: "Elige el límite (polilínea cerrada)", tipos: ["Polyline", "LwPolyline", "Polyline3D"], maximo: 1, cerrada: true, texto: () => "Elegido" },
    islas: { mensaje: "Elige las islas (polilíneas cerradas)", tipos: ["Polyline", "LwPolyline", "Polyline3D"], maximo: Infinity, cerrada: true, texto: (n) => `${n} isla${n === 1 ? "" : "s"}` },
    poligono: { mensaje: "Elige el polígono (polilínea cerrada)", tipos: ["Polyline", "LwPolyline"], maximo: 1, cerrada: true, texto: (_, d) => `${d[0].vertices.length} vértices` },
    curvas: { mensaje: "Elige las polilíneas con curvas", tipos: ["Polyline", "LwPolyline"], maximo: Infinity,
      texto: (n, d) => { const k = d.reduce((t, p) => t + p.combas.slice(0, p.cerrada ? p.combas.length : p.combas.length - 1).filter((b) => b).length, 0); return `${n} polilínea${n === 1 ? "" : "s"} · ${k} curva${k === 1 ? "" : "s"}`; } },
    tangentes: { mensaje: "Elige la polilínea de las tangentes", tipos: ["Polyline", "LwPolyline"], maximo: 1, texto: (_, d) => `${d[0].vertices.length - 2} PI` },
    lineas: { mensaje: "Elige las polilíneas o líneas", tipos: ["Polyline", "LwPolyline", "Line", "Polyline3D"], maximo: Infinity, texto: (n) => `${n} elegida${n === 1 ? "" : "s"}` },
    eje: { mensaje: "Elige el eje (polilínea o línea)", tipos: ["Polyline", "LwPolyline", "Line", "Polyline3D"], maximo: 1, texto: (_, d) => `${d[0].vertices.length} vértices` },
  };

  function prepararElecciones(form, alElegir, especiales = {}) {
    const clave = form.dataset.civilForm;
    est.elegidos[clave] = est.elegidos[clave] || {};
    form.querySelectorAll("[data-civil-elegir]").forEach((b) => {
      b.addEventListener("click", async () => {
        const que = b.dataset.civilElegir;
        const cuenta = form.querySelector(`[data-civil-cuenta="${que}"]`);
        try {
          if (especiales[que]) { await especiales[que](cuenta); return; }
          if (que === "frente") {
            const p = await senalar("Señala el lado del frente (el que da a la calle)");
            if (!p) return;
            est.elegidos[clave].frente = p;
            cuenta.textContent = "Señalado"; cuenta.classList.add("civil-elegido");
            return;
          }
          if (que === "vertice") {
            const p = await senalar("Señala el vértice inicial");
            if (!p) return;
            est.elegidos[clave].vertice = p;
            cuenta.textContent = `${mil(p[0], 3)}, ${mil(p[1], 3)}`;
            return;
          }
          const def = ELECCIONES[que];
          const e = await elegir({ mensaje: def.mensaje, tipos: def.tipos, maximo: def.maximo, sinResultados: true });
          if (!e) return;
          const datos = e.map(polilinea);
          if (que === "eje") datos[0].definicion = await definicionEje(e[0].handle);
          if (def.cerrada && datos.some((d) => !d.cerrada && !(d.vertices.length > 2 && d.vertices[0][0] === d.vertices[d.vertices.length - 1][0] && d.vertices[0][1] === d.vertices[d.vertices.length - 1][1])))
            throw new ErrorCivil("La polilínea debe estar cerrada.");
          est.elegidos[clave][que] = datos;
          cuenta.textContent = que === "eje" && datos[0].definicion ? `Eje con espirales · ${datos[0].definicion.curvas.filter(Boolean).length} curvas` : def.texto(datos.length, datos);
          cuenta.classList.add("civil-elegido");
          if (alElegir) await alElegir(que, datos);
        } catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudo leer lo elegido.", "error"); if (!(err instanceof ErrorCivil)) console.error(err); }
      });
    });
  }
  function limpiarElecciones(form) {
    const clave = form.dataset.civilForm;
    est.elegidos[clave] = {};
    form.querySelectorAll("[data-civil-cuenta]").forEach((c) => { c.textContent = c.dataset.vacio; c.classList.remove("civil-elegido"); });
  }

  // -- Triangulación --------------------------------------------------------------------------------------------------------------
  /** Patrones de descripción («ARBOL, POSTE, BAR*»), como los usa el servidor (fnmatch, sin distinguir mayúsculas). */
  function patronesDescripcion(texto) {
    return String(texto || "").split(/[,;]/).map((t) => t.trim().toUpperCase()).filter(Boolean)
      .map((t) => new RegExp(`^${t.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")}$`));
  }
  const excluidoPor = (p, pats) => pats.length > 0 && pats.some((re) => re.test(String(p[4] || "").trim().toUpperCase()));

  /** Dentro del polígono (par-impar; un punto en la orilla cuenta como dentro). */
  function dentroDe(x, y, v) {
    let dentro = false;
    for (let i = 0, j = v.length - 1; i < v.length; j = i++) {
      const [xi, yi] = v[i], [xj, yj] = v[j];
      if (Math.abs((xj - xi) * (y - yi) - (yj - yi) * (x - xi)) < 1e-9 && Math.min(xi, xj) - 1e-9 <= x && x <= Math.max(xi, xj) + 1e-9 && Math.min(yi, yj) - 1e-9 <= y && y <= Math.max(yi, yj) + 1e-9) return true;
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dentro = !dentro;
    }
    return dentro;
  }

  /**
   * Elegir los puntos de una triangulación (Triangular, Insertar puntos), como en CivilCAD: todos los de la superficie, los que la persona
   * selecciona en el plano (clic, ventana o captura del editor) o los que caen dentro de un polígono. Cuántos hay se dice antes de correr,
   * con los que deja fuera el filtro de descripción. Devuelve { leer(): puntos a usar, contar() }.
   */
  function eleccionDePuntos(f, { todos = true, porTipo = true } = {}) {
    const clave = f.dataset.civilForm, conteo = f.querySelector("[data-civil-conteo]");
    const el = () => est.elegidos[clave] || (est.elegidos[clave] = {});
    // triangular: los puntos de la superficie elegida; insertar: los que la persona diga, de terreno o de proyecto
    const tipo = () => (porTipo ? valor(f, "tipo") : "todos");
    const usa = (t) => !porTipo || t === "Point" || t === tipo();
    let todosCache = null;
    const delPlano = async () => {
      if (!todosCache || todosCache.tipo !== tipo()) todosCache = { tipo: tipo(), pts: porTipo ? await puntosDe(tipo()) : [...(await puntosDe("terreno")), ...(await puntosDe("proyecto"))] };
      return todosCache.pts;
    };
    async function candidatos() {
      const modo = valor(f, "origen");
      if (modo === "seleccion") return el().puntos ? el().puntos.filter((x) => usa(x.tipo)).map((x) => x.p) : null;
      if (modo === "poligono") {
        if (!el().zona) return null;
        const v = el().zona.vertices;
        return (await delPlano()).filter((p) => dentroDe(p[0], p[1], v));
      }
      return delPlano();
    }
    async function contar() {
      if (!conteo) return;
      try {
        const c = await candidatos();
        if (!c) { conteo.textContent = ""; return; }
        const pats = patronesDescripcion(valor(f, "excluir"));
        const fuera = c.filter((p) => excluidoPor(p, pats)).length, sinZ = c.filter((p) => !Number.isFinite(p[2])).length;
        const n = c.length - fuera;
        conteo.textContent = `${n.toLocaleString("es-MX")} punto${n === 1 ? "" : "s"}${porTipo ? ` de ${tipo()}` : ""}` + (fuera ? ` · ${fuera.toLocaleString("es-MX")} fuera por su descripción` : "")
          + (sinZ ? ` · ${sinZ} sin elevación no se usan` : "");
        conteo.classList.toggle("civil-conteo--vacio", n === 0);
      } catch (_) { conteo.textContent = ""; }
    }
    const especiales = {
      async puntos(cuenta) {
        const e = await elegir({ mensaje: "Selecciona los puntos (clic, ventana o captura) y pulsa Enter", tipos: ["Block Reference", "INSERT", "Insert", "Point"] });
        if (!e) return;
        const pts = e.filter((x) => x.type === "Point" || esPuntoCivil(x)).map((x) => ({ tipo: x.type === "Point" ? "Point" : puntoCivil(x).tipo, p: punto(x) }));
        if (!pts.length) throw new ErrorCivil("Lo que seleccionaste no son puntos de Civil.");
        el().puntos = pts;
        const otros = pts.filter((x) => !usa(x.tipo)).length;
        cuenta.textContent = `${(pts.length - otros).toLocaleString("es-MX")} seleccionado${pts.length - otros === 1 ? "" : "s"}` + (otros ? ` · ${otros} de otra superficie` : "");
        cuenta.classList.add("civil-elegido");
        await contar();
      },
      async zona(cuenta) {
        const e = await elegir({ mensaje: "Elige el polígono (polilínea cerrada)", tipos: ["Polyline", "LwPolyline", "Polyline3D"], maximo: 1, sinResultados: true });
        if (!e) return;
        const d = polilinea(e[0]);
        const v = d.vertices.map((q) => [q[0], q[1]]);
        const cerrada = d.cerrada || (v.length > 3 && v[0][0] === v[v.length - 1][0] && v[0][1] === v[v.length - 1][1]);
        if (!cerrada || v.length < 3) throw new ErrorCivil("La polilínea debe estar cerrada.");
        el().zona = { vertices: v };
        cuenta.textContent = `${v.length} vértices`; cuenta.classList.add("civil-elegido");
        await contar();
      },
    };
    f.addEventListener("change", (ev) => { if (["origen", "tipo"].includes(ev.target.name)) contar(); });
    f.addEventListener("input", (ev) => { if (ev.target.name === "excluir") contar(); });
    return {
      especiales, contar,
      reiniciar() { todosCache = null; contar(); },
      async leer() {
        const modo = valor(f, "origen");
        if (modo === "seleccion" && !el().puntos) throw new ErrorCivil("Selecciona los puntos en el plano.");
        if (modo === "poligono" && !el().zona) throw new ErrorCivil("Elige el polígono.");
        if (!todos && modo === "capa") throw new ErrorCivil("Elige los puntos.");
        todosCache = null;
        return (await candidatos()) || [];
      },
    };
  }

  function prepararTriangular() {
    const f = document.querySelector('[data-civil-form="triangular"]');
    const eleccion = eleccionDePuntos(f);
    prepararElecciones(f, null, eleccion.especiales); enlazarDependientes(f);
    alAbrir.triangular = () => eleccion.reiniciar();
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo los puntos…", async (decir) => {
        const tipo = valor(f, "tipo"), el = est.elegidos.triangular;
        const pts = await eleccion.leer();
        if (pts.length < 3) throw new ErrorCivil(valor(f, "origen") === "capa" ? `No hay puntos de ${tipo} en el plano. Impórtalos primero.` : "Se necesitan al menos tres puntos.");
        const anteriores = valor(f, "reemplazar") ? (await consultar({ type: "3DFACE", layer: C.capas.tin[tipo], detail: "summary" })).map((e) => e.handle) : [];
        decir(`Triangulando ${pts.length.toLocaleString("es-MX")} puntos…`);
        const quiebres = (el.quiebres || []).map((q) => ({ vertices: q.vertices.map((v) => (q.tres_d ? v : [v[0], v[1], null])) }));
        const lim = el.limite && el.limite[0];
        const r = await calcular("triangular", {
          tipo, puntos: pts, quiebres,
          limite: lim ? { vertices: lim.vertices.map((v) => (lim.tres_d ? v : [v[0], v[1]])) } : null,
          islas: (el.islas || []).map((i) => ({ vertices: i.vertices.map((v) => [v[0], v[1]]) })),
          arista_maxima: numero(f, "arista_maxima", null) || null, excluir: valor(f, "excluir") || "",
        }, decir);
        decir("Dibujando la triangulación…");
        await insertarDxf(r.dxf, "triangulacion.dxf");
        await borrar(anteriores);
        cerrarHoja();
        avisar(`${r.resumen.triangulos.toLocaleString("es-MX")} triángulos con ${r.resumen.puntos.toLocaleString("es-MX")} puntos` + (r.resumen.excluidos ? ` · ${r.resumen.excluidos} fuera por su descripción` : ""));
        for (const a of r.avisos || []) avisar(a);
      });
    });
  }

  /** Los dos triángulos que comparten el lado más cercano a `p` (como al señalar el lado en CivilCAD). */
  async function parEnLado(p) {
    const r = Math.max(2, est.escala.denominador / 100);   // 10 m a 1:1000
    const ents = await consultar({ type: "3DFACE", detail: "full", bounds: [p[0] - r, p[1] - r, p[0] + r, p[1] + r] });
    const caras = ents.filter((e) => Object.values(C.capas.tin).includes(e.layer)).map(cara);
    const clave = (a, b) => { const k = [a, b].map((v) => `${v[0].toFixed(6)},${v[1].toFixed(6)}`).sort(); return k.join("|"); };
    const lados = new Map();
    for (const c of caras) for (let i = 0; i < 3; i++) {
      const a = c.vertices[i], b = c.vertices[(i + 1) % 3], k = clave(a, b);
      if (!lados.has(k)) lados.set(k, { a, b, caras: [] });
      lados.get(k).caras.push(c);
    }
    let mejor = null, dist = Infinity;
    for (const l of lados.values()) {
      if (l.caras.length !== 2) continue;
      const dx = l.b[0] - l.a[0], dy = l.b[1] - l.a[1], t = Math.max(0, Math.min(1, ((p[0] - l.a[0]) * dx + (p[1] - l.a[1]) * dy) / (dx * dx + dy * dy || 1)));
      const d = Math.hypot(l.a[0] + t * dx - p[0], l.a[1] + t * dy - p[1]);
      if (d < dist) { dist = d; mejor = l; }
    }
    return mejor && dist <= r ? mejor.caras : null;
  }

  async function invertirTriangulos() {
    if (!puedeDibujar()) return;
    let hechos = 0;
    for (;;) {
      let par = null;
      try {
        if (!est.sinPicks) {
          let p;
          try { p = await senalar("Señala el lado común de los dos triángulos"); }
          catch (err) { if (!est.sinPicks) throw err; }
          if (!est.sinPicks) {
            if (!p) break;
            par = await parEnLado(p);
            if (!par) { avisar("Ahí no hay un lado entre dos triángulos. Señala más cerca del lado.", "error"); continue; }
          }
        }
        if (!par) {
          // sin señalar en el dibujo: los dos triángulos seleccionados
          const e = await elegir({ mensaje: "Elige los dos triángulos que comparten el lado", tipos: ["3D Face", "3DFACE"], minimo: 2, maximo: 2 });
          if (!e) break;
          par = e.map(cara);
        }
      } catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudieron leer los triángulos.", "error"); if (!(err instanceof ErrorCivil)) console.error(err); break; }
      const [a, b] = par;
      const ok = await trabajo(null, "", async () => {
        const r = await calcular("invertir", { cara_a: a, cara_b: b });
        const pasos = [r.cara_a, r.cara_b].map((c) => ({
          op: "set_properties", collection: "entities", handle: c.handle,
          updates: ["first_corner", "second_corner", "third_corner", "fourth_corner"].map((k, i) => { const q = c.vertices[Math.min(i, 2)]; return { path: `/${k}`, value: { x: q[0], y: q[1], z: q[2] } }; }),
        }));
        await enUnPaso(pasos, "Civil: invertir triángulos");
        return true;
      });
      if (ok) hechos += 1;
      if (est.sinPicks) break;  // sin señalar en el dibujo no se puede repetir: una a la vez
    }
    if (hechos) avisar(`${hechos} ${hechos === 1 ? "inversión hecha" : "inversiones hechas"}. Actualiza las curvas.`);
  }

  function prepararBorrar() {
    const f = document.querySelector('[data-civil-form="borrar"]');
    enlazarDependientes(f);
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo la triangulación…", async () => {
        const tipo = valor(f, "tipo"), modo = valor(f, "modo");
        if (modo === "elegidos") {
          const capa = C.capas.tin[tipo];
          const e = await elegir({ mensaje: "Elige los triángulos que vas a borrar", tipos: ["3D Face", "3DFACE"] });
          if (!e) return;
          const handles = e.filter((x) => x.layer === capa).map((x) => x.handle);
          if (!handles.length) throw new ErrorCivil("Lo que elegiste no es de esta triangulación.");
          await borrar(handles);
          cerrarHoja();
          avisar(`${handles.length.toLocaleString("es-MX")} ${handles.length === 1 ? "triángulo borrado" : "triángulos borrados"}. Actualiza las curvas.`);
          return;
        }
        let contorno = null;
        if (modo !== "largos") {
          const e = await elegir({ mensaje: modo === "fuera" ? "Elige el límite" : "Elige el polígono", tipos: ["Polyline", "LwPolyline", "Polyline3D"], maximo: 1 });
          if (!e) return;
          contorno = polilinea(e[0]);
        }
        const caras = await carasDe(tipo);
        const r = await calcular("quitar_triangulos", { caras, modo, contorno, largo: numero(f, "largo", 50) });
        if (!r.borrar.length) { avisar("Ningún triángulo cumple."); return; }
        await borrar(r.borrar);
        cerrarHoja();
        avisar(`${r.resumen.borrados.toLocaleString("es-MX")} triángulos borrados`);
      });
    });
  }

  async function agregarTriangulo() {
    if (!puedeDibujar()) return;
    try {
      const vs = [];
      for (const n of ["primer", "segundo", "tercer"]) {
        const p = await senalar(`Señala el ${n} vértice del triángulo`);
        if (!p) return;
        vs.push(p);
      }
      await trabajo(null, "", async () => {
        // El triángulo va a la triangulación (terreno o proyecto) cuyos vértices se señalaron; la elevación sale de esos vértices.
        const cerca = (todos, p) => {
          let mejor = null, d = Infinity;
          for (const q of todos) { const dd = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2; if (dd < d) { d = dd; mejor = q; } }
          return d <= 1e-6 ? mejor : null;
        };
        let tipo = null, caras = null, conZ = null;
        for (const t of ["terreno", "proyecto"]) {
          const cs = await carasDe(t);
          const todos = cs.flatMap((c) => c.vertices);
          const zs = vs.map((p) => cerca(todos, p));
          if (zs.every(Boolean)) { tipo = t; caras = cs; conZ = zs; break; }
        }
        if (!tipo) throw new ErrorCivil("Señala tres vértices de una misma triangulación (usa la referencia a puntos finales).");
        const r = await calcular("agregar_triangulo", { caras, vertices: conZ, tipo });
        await insertarDxf(r.dxf, "triangulo.dxf");
        avisar("Triángulo agregado. Actualiza las curvas.");
      });
    } catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudo agregar el triángulo.", "error"); }
  }

  // -- Curvas ---------------------------------------------------------------------------------------------------------------------
  let ultimasCurvas = null;
  function datosCurvas(f) {
    return { tipo: valor(f, "tipo"), intervalo: numero(f, "intervalo", 1), maestra_cada: parseInt(valor(f, "maestra_cada"), 10), suavizado: parseInt(valor(f, "suavizado"), 10),
      etiquetas: { cuales: valor(f, "cuales"), separacion_mm: numero(f, "separacion_mm", 80), decimales: parseInt(valor(f, "decimales"), 10) }, tres_d: valor(f, "tres_d") };
  }
  async function dibujarCurvas(f, opciones, decir) {
    const caras = await carasDe(opciones.tipo);
    if (!caras.length) throw new ErrorCivil("No hay triangulación de ese tipo. Triangula primero.");
    const sufijo = opciones.tipo === "proyecto" ? "_PROYECTO" : "";
    const anteriores = [];
    for (const c of ["CURVAS_MAESTRAS", "CURVAS_INTERMEDIAS", "CURVAS_ETIQUETAS"]) anteriores.push(...(await consultar({ layer: c + sufijo, detail: "summary" })).map((e) => e.handle));
    decir && decir(`Calculando curvas de ${caras.length.toLocaleString("es-MX")} triángulos…`);
    const r = await calcular("curvas", { ...opciones, caras }, decir);
    decir && decir("Dibujando las curvas…");
    await insertarDxf(r.dxf, "curvas.dxf");
    await borrar(anteriores);
    ultimasCurvas = opciones;
    return r;
  }
  function prepararCurvas() {
    const f = document.querySelector('[data-civil-form="curvas"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const r = await dibujarCurvas(f, datosCurvas(f), decir);
        cerrarHoja();
        avisar(`${r.resumen.curvas.toLocaleString("es-MX")} curva${r.resumen.curvas === 1 ? "" : "s"}, ${r.resumen.maestras} maestra${r.resumen.maestras === 1 ? "" : "s"}`);
        for (const a of r.avisos || []) avisar(a);
      });
    });
  }
  async function actualizarCurvas() {
    const f = document.querySelector('[data-civil-form="curvas"]');
    await trabajo(null, "", async () => {
      const r = await dibujarCurvas(f, ultimasCurvas || datosCurvas(f));
      avisar(`Curvas actualizadas: ${r.resumen.curvas.toLocaleString("es-MX")}`);
    });
  }

  // -- Cuadro de construcción -----------------------------------------------------------------------------------------------------
  function datosCuadro(f) {
    const el = est.elegidos.cuadro || {};
    if (!el.poligono) throw new ErrorCivil("Elige el polígono.");
    const p = el.poligono[0];
    return {
      poligono: { vertices: p.vertices.map((v) => [v[0], v[1]]), combas: p.combas, cerrada: true },
      vertice_inicial: el.vertice || null,
      numerar_vertices: valor(f, "numerar_vertices"),
      opciones: { sentido: valor(f, "sentido"), numeracion: valor(f, "numeracion"), primer_numero: parseInt(valor(f, "primer_numero"), 10) || 0,
        azimut: valor(f, "azimut"), angulos_interiores: valor(f, "angulos_interiores"), separador_miles: valor(f, "separador_miles") ? "," : "", oeste: valor(f, "oeste_o") ? "O" : "W",
        decimales_distancia: +valor(f, "decimales_distancia"), decimales_coordenadas: +valor(f, "decimales_coordenadas"), decimales_segundos: +valor(f, "decimales_segundos"),
        decimales_superficie: +valor(f, "decimales_superficie"), titulo: valor(f, "titulo"), formato: valor(f, "formato") || "normal", ...zona() },
    };
  }
  function prepararCuadro() {
    const f = document.querySelector('[data-civil-form="cuadro"]');
    prepararElecciones(f); enlazarDependientes(f);
    const res = f.querySelector("[data-civil-resultado]");
    const mostrar = (r) => { res.hidden = false; res.innerHTML = `<p><strong>${mil(r.resumen.superficie, 3)} m²</strong> · ${esc(r.resumen.superficie_ha)} · perímetro ${mil(r.resumen.perimetro, 3)} m · ${r.resumen.lados} lados${r.resumen.curvas ? ` (${r.resumen.curvas} curvos)` : ""}</p>`; };
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Calculando…", async () => {
        const datos = datosCuadro(f);
        const r = await calcular("cuadro", datos);
        mostrar(r);
        if (!(await colocarDxf(r.dxf, "cuadro.dxf", { titulo: "El cuadro de construcción", mover: CAPAS_CUADRO }))) return cancelado();
        cerrarHoja();
        avisar(`Cuadro de construcción: ${mil(r.resumen.superficie, 3)} m²`);
      });
    });
    f.querySelector("[data-civil-descargar-dxf]").addEventListener("click", () => {
      trabajo(f, "Calculando…", async () => {
        const r = await calcular("cuadro", datosCuadro(f));
        mostrar(r);
        descargar(r.dxf, `Cuadro de construcción ${window.TVCad.estado().plano.nombre}.dxf`, "image/vnd.dxf");
      });
    });
  }

  /** Las capas de un cuadro (de construcción o de curvas) que forman la tabla: la numeración de vértices y las claves se quedan en el plano. */
  const CAPAS_CUADRO = /^CUADRO_(LINEAS|TEXTOS|TITULO)$/;
  const cancelado = () => avisar("Cancelado: no se dibujó nada.");

  /** Esquina superior izquierda libre a un lado de lo que ya hay en el plano: el lugar sugerido donde empieza la vista previa al colocar. */
  /**
   * La extensión real de lo dibujado en el modelo: la caja de cada objeto (como «Ver todo» del modo táctil). EXTMIN/EXTMAX del editor
   * pueden quedar viejos o incluir lo que no se ve (definiciones de bloque en el origen). null si el plano está vacío.
   */
  async function extensionDibujo() {
    const todas = await consultar({ detail: "full", fields: ["bounds", "space"] });
    return unirCajas(todas.map(cajaDe));
  }

  async function lugarLibre(donde) {
    try {
      const c = await extensionDibujo();
      if (!c) return null;
      const [x0, y0, x1, y1] = c;
      const margen = Math.max(x1 - x0, y1 - y0) * 0.05 + est.escala.denominador / 50;   // 20 m a 1:1000
      return donde === "debajo" ? [x0, y0 - margen] : [x1 + margen, y1];
    } catch (_) { return null; }
  }

  // -- Perfil y secciones -----------------------------------------------------------------------------------------------------------
  function prepararRasante(f) {
    const cuerpoTabla = f.querySelector("[data-civil-pivs]");
    if (!cuerpoTabla) return;
    const plantilla = cuerpoTabla.firstElementChild.cloneNode(true);
    f.querySelector("[data-civil-agregar-piv]").addEventListener("click", () => { const n = plantilla.cloneNode(true); n.querySelectorAll("input").forEach((i) => { i.value = i.name === "piv_l" ? "0" : ""; }); cuerpoTabla.append(n); n.querySelector("input").focus(); });
    cuerpoTabla.addEventListener("click", (ev) => { const b = ev.target.closest("[data-civil-quitar-piv]"); if (b && cuerpoTabla.children.length > 1) b.closest("tr").remove(); });
  }
  function rasante(f) {
    if (!valor(f, "con_rasante")) return null;
    const filas = [...f.querySelectorAll("[data-civil-pivs] tr")].map((tr) => {
      const [e, z, l] = ["piv_e", "piv_z", "piv_l"].map((n) => tr.querySelector(`[name=${n}]`).value.trim());
      if (!e && !z) return null;
      const zz = parseFloat(z.replace(",", "."));
      if (!Number.isFinite(zz)) throw new ErrorCivil("Cada punto de la rasante necesita su elevación.");
      return [leerEstacion(e), zz, parseFloat(l) || 0];
    }).filter(Boolean);
    if (filas.length < 2) throw new ErrorCivil("La rasante necesita al menos dos puntos.");
    return filas;
  }
  function datosEje(f) {
    const clave = f.dataset.civilForm;
    const el = est.elegidos[clave] || {};
    if (!el.eje) throw new ErrorCivil("Elige el eje.");
    const e = el.eje[0];
    // un eje que dibujó Civil con espirales viaja por su definición (PI y curvas); cualquier otra polilínea, por sus vértices y arcos
    const eje = e.definicion ? e.definicion : { vertices: e.vertices.map((v) => [v[0], v[1]]), combas: e.combas };
    return { eje, inicio: e.definicion ? e.definicion.inicio : leerEstacion(valor(f, "inicio")), intervalo: numero(f, "intervalo", 20) };
  }
  function prepararPerfil() {
    const f = document.querySelector('[data-civil-form="perfil"]');
    prepararElecciones(f); enlazarDependientes(f); prepararRasante(f);
    alAbrir.perfil = prepararSuperficies(f, (sup, ras) => (sup ? `El perfil muestra el terreno y la superficie de proyecto (su triangulación)${ras ? " y, aparte, la rasante" : ""}, con el espesor entre las dos.` : ras ? "El perfil muestra el terreno (su triangulación) y la rasante que escribes." : "El perfil sigue el terreno (su triangulación)."));
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const datos = { ...datosEje(f), exageracion: numero(f, "exageracion", 10), rasante: rasante(f) };
        datos.esquina_superior = await lugarLibre("debajo");          // el lugar sugerido: la persona lo confirma o lo cambia al colocarlo
        datos.caras = await carasDe("terreno");
        datos.caras_proyecto = await carasProyectoSi(f);
        decir("Calculando el perfil…");
        const r = await calcular("perfil", datos, decir);
        if (!(await colocarDxf(r.dxf, "perfil.dxf", { titulo: "El perfil", ancla: "abajo_izquierda" }))) return cancelado();
        cerrarHoja();
        avisar(`Perfil de ${mil(r.resumen.longitud, 2)} m, ${r.resumen.estaciones} estaciones`);
        for (const a of r.avisos || []) avisar(a);
      });
    });
  }
  /** La plantilla de la hoja: sencilla (corona y bombeo) o de carretera; los porcentajes de bombeo van hacia afuera (negativos). */
  function plantillaDe(f) {
    const comun = { talud_corte: numero(f, "talud_corte", 1), talud_terraplen: numero(f, "talud_terraplen", 1.5) };
    if (valor(f, "plantilla_tipo") === "carretera") {
      return { tipo: "carretera", ...comun, carriles_por_lado: parseInt(valor(f, "carriles_por_lado"), 10) || 1, ancho_carril: numero(f, "ancho_carril", 3.5),
        pendiente_carril: -Math.abs(numero(f, "pendiente_carril", 2)), acotamiento: numero(f, "acotamiento", 2.5), pendiente_acotamiento: -Math.abs(numero(f, "pendiente_acotamiento", 4)),
        cuneta_ancho: numero(f, "cuneta_ancho", 1), cuneta_profundidad: numero(f, "cuneta_profundidad", 0.5), espesor_pavimento: numero(f, "espesor_pavimento", 0.3), despalme: numero(f, "despalme", 0.2),
        curvas: valor(f, "sct") ? curvasSCT(f) : null };
    }
    return { ...comun, ancho_izq: numero(f, "ancho_izq", 3.5), ancho_der: numero(f, "ancho_der", 3.5), bombeo: numero(f, "bombeo", 2) };
  }

  /** Sobreelevación y ampliación en curvas (manual SCT): velocidad, Smáx, vehículo de proyecto y reparto de la transición. */
  function curvasSCT(f) {
    const c = { velocidad: +valor(f, "velocidad"), smax: +valor(f, "smax"), vehiculo: valor(f, "vehiculo"), reparto_tangente: +valor(f, "reparto_tangente") };
    if (c.vehiculo === "otro") {
      const li = String(valor(f, "veh_li") || "").split(/[,;\s]+/).map((x) => parseFloat(x.replace(",", "."))).filter((x) => x > 0);
      if (!li.length) throw new ErrorCivil("Escribe las distancias entre ejes del vehículo.");
      const a = numero(f, "veh_a", 0), l = numero(f, "veh_l", li[0]);
      Object.assign(c, { u: numero(f, "veh_u", 2.6), ev: numero(f, "veh_u", 2.6), suma_li2: li.reduce((t, x) => t + x * x, 0), q_vuelo: a * (2 * l + a) });
    }
    return c;
  }
  function tablaCurvas(curvas) {
    if (!curvas || !curvas.length) return "";
    const filas = curvas.map((c) => `<tr><td>${esc(estacion(c.pc))}</td><td>${esc(estacion(c.pt))}</td><td class="tv-num">${mil(c.radio, 2)}</td><td class="tv-num">${mil(c.grado, 2)}°</td><td>${c.lado}</td><td class="tv-num">${mil(c.sobreelevacion * 100, 1)} %</td><td class="tv-num">${mil(c.transicion, 1)}</td><td class="tv-num">${c.ampliacion ? mil(c.ampliacion, 1) : "—"}</td></tr>`).join("");
    return `<div class="tv-tabla-envoltura"><table class="tv-tabla civil-tabla"><thead><tr><th>PC</th><th>PT</th><th class="tv-num">Radio</th><th class="tv-num">Grado</th><th>Hacia</th><th class="tv-num">Sobreelev.</th><th class="tv-num">Transición (m)</th><th class="tv-num">Ampliación (m)</th></tr></thead><tbody>${filas}</tbody></table></div>`;
  }
  const estacion = (m) => { const km = Math.floor(Math.abs(m) / 1000), r = Math.abs(m) - km * 1000; return `${m < 0 ? "-" : ""}${km}+${r.toFixed(2).padStart(6, "0")}`; };

  function prepararMasa() {
    const f = document.querySelector('[data-civil-form="masa"]');
    prepararElecciones(f); enlazarDependientes(f); prepararRasante(f);
    f.elements.con_rasante.checked = true; f.elements.con_rasante.dispatchEvent(new Event("change", { bubbles: true }));
    alAbrir.masa = prepararSuperficies(f, (sup) => (sup ? "El proyecto sale de la superficie de proyecto (su triangulación): el corte y el terraplén son la diferencia con el terreno." : "El proyecto sale de la rasante y la sección que escribes, contra el terreno (su triangulación)."), { excluye: "con_rasante" });
    const res = f.querySelector("[data-civil-resultado]");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      res.hidden = true;
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const ras = rasante(f);
        if (!ras && !valor(f, "con_superficie")) throw new ErrorCivil("La curva masa necesita la rasante de proyecto o la superficie de proyecto.");
        const datos = { ...datosEje(f), izquierda: numero(f, "izquierda", 30), derecha: numero(f, "derecha", 30), rasante: ras, plantilla: plantillaDe(f),
          abundamiento: numero(f, "abundamiento", 1), acarreo_libre: numero(f, "acarreo_libre", 20), linea: valor(f, "linea_modo") === "dada" ? numero(f, "linea", 0) : "auto" };
        datos.esquina_superior = await lugarLibre("debajo");
        datos.caras = await carasDe("terreno");
        datos.caras_proyecto = await carasProyectoSi(f);
        decir("Calculando la curva masa…");
        const r = await calcular("curva_masa", datos, decir);
        if (!(await colocarDxf(r.dxf, "curva-masa.dxf", { titulo: "La curva masa", ancla: "abajo_izquierda" }))) return cancelado();
        const s = r.resumen;
        res.hidden = false;
        res.innerHTML = `<dl class="civil-cifras"><div><dt>Compensado</dt><dd>${mil(s.compensado, 0)} m³</dd></div><div><dt>Sobreacarreo</dt><dd>${mil(s.sobreacarreo, 1)} m³·hm</dd></div>
          <div><dt>Préstamo</dt><dd>${mil(s.prestamo, 0)} m³</dd></div><div><dt>Desperdicio</dt><dd>${mil(s.desperdicio, 0)} m³</dd></div></dl>
          <p class="civil-nota">Línea compensadora en ${mil(s.linea, 0)} m³ · ${s.puntos} puntos de compensación · corte ${mil(s.corte, 0)} m³, terraplén ${mil(s.terraplen, 0)} m³${s.despalme ? ` (con el relleno del despalme), despalme ${mil(s.despalme, 0)} m³` : ""}</p>${tablaCurvas(r.curvas)}`;
        for (const a of (r.avisos || []).slice(0, 3)) avisar(a);
      });
    });
  }

  function prepararLotes() {
    const f = document.querySelector('[data-civil-form="lotes"]');
    prepararElecciones(f); enlazarDependientes(f);
    const res = f.querySelector("[data-civil-resultado]");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      res.hidden = true;
      trabajo(f, "Partiendo la manzana…", async () => {
        const el = est.elegidos.lotes || {};
        if (!el.poligono) throw new ErrorCivil("Elige la manzana.");
        if (!el.frente) throw new ErrorCivil("Señala el lado del frente.");
        const p = el.poligono[0];
        const metodo = valor(f, "metodo");
        const v = { por_cantidad: numero(f, "cantidad", 0), por_superficie: numero(f, "superficie", 0), por_frente: numero(f, "frente", 0) }[metodo];
        const r = await calcular("lotificar", { poligono: { vertices: p.vertices.map((q) => [q[0], q[1]]), combas: p.combas, cerrada: true }, punto_frente: el.frente,
          metodo, valor: v, prefijo: valor(f, "prefijo") || "", primer_numero: parseInt(valor(f, "primer_numero"), 10) || 1, tabla: !!valor(f, "tabla") });
        if (!(await colocarDxf(r.dxf, "lotes.dxf", { titulo: "El cuadro de áreas", mover: /^LOTES_TABLA$/ }))) return cancelado();
        res.hidden = false;
        res.innerHTML = `<p><strong>${r.resumen.lotes} lotes</strong> · ${mil(r.resumen.superficie, 3)} m² en total</p>`;
        avisar(`${r.resumen.lotes} lotes`);
      });
    });
  }

  function prepararSecciones() {
    const f = document.querySelector('[data-civil-form="secciones"]');
    prepararElecciones(f); enlazarDependientes(f); prepararRasante(f);
    alAbrir.secciones = prepararSuperficies(f, (sup, ras) => (sup ? "Las secciones comparan el terreno con la superficie de proyecto (su triangulación): el corte y el terraplén salen de la diferencia entre las dos." : ras ? "Las secciones comparan el terreno (su triangulación) con la rasante y la sección de proyecto que escribes." : "Las secciones son del terreno (su triangulación)."), { excluye: "con_rasante" });
    const res = f.querySelector("[data-civil-resultado]");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const datos = { ...datosEje(f), izquierda: numero(f, "izquierda", 20), derecha: numero(f, "derecha", 20), rasante: rasante(f), tabla: valor(f, "tabla"),
          abundamiento: numero(f, "abundamiento", 1), plantilla: plantillaDe(f) };
        datos.esquina_superior = await lugarLibre("lado");
        datos.caras = await carasDe("terreno");
        datos.caras_proyecto = await carasProyectoSi(f);
        decir("Calculando las secciones…");
        const r = await calcular("secciones", datos, decir);
        if (!(await colocarDxf(r.dxf, "secciones.dxf", { titulo: "Las secciones" }))) return cancelado();
        if (r.resumen.corte !== undefined) { res.hidden = false; res.innerHTML = `<p>Corte <strong>${mil(r.resumen.corte)} m³</strong> · Terraplén <strong>${mil(r.resumen.terraplen)} m³</strong>${r.resumen.despalme ? ` · Despalme <strong>${mil(r.resumen.despalme)} m³</strong>` : ""}</p>${tablaCurvas(r.curvas)}`; }
        avisar(`${r.resumen.secciones} secciones`);
        for (const a of (r.avisos || []).slice(0, 3)) avisar(a);
        if (r.resumen.corte === undefined) cerrarHoja();
      });
    });
  }

  // -- Renumerar puntos --------------------------------------------------------------------------------------------------------
  function prepararRenumerar() {
    const f = document.querySelector('[data-civil-form="renumerar"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo los puntos…", async () => {
        const cuales = valor(f, "cuales");
        let pts;
        if (cuales === "seleccion") {
          const e = await elegir({ mensaje: "Elige los puntos", tipos: ["Block Reference", "INSERT", "Insert"] });
          if (!e) return;
          pts = e.filter(esPuntoCivil).map(puntoCivil);
        } else pts = await insercionesDe(cuales);
        if (!pts.length) throw new ErrorCivil("No hay puntos de Civil ahí. Impórtalos primero.");
        const inicio = parseInt(valor(f, "inicio"), 10), incremento = parseInt(valor(f, "incremento"), 10);
        if (!(inicio >= 0) || !(incremento >= 1)) throw new ErrorCivil("Revisa el primer número y el incremento.");
        const r = await calcular("redibujar_puntos", { puntos: pts, renumerar: { inicio, incremento, prefijo: valor(f, "prefijo") || "", orden: valor(f, "orden") } });
        await insertarDxf(r.dxf, "puntos.dxf");
        await borrar(pts.map((p) => p.handle));
        cerrarHoja();
        avisar(`${r.resumen.puntos.toLocaleString("es-MX")} puntos renumerados`);
      });
    });
  }

  // -- Revisar triangulación ----------------------------------------------------------------------------------------------------
  const CAPA_REVISION = "TRIANGULACION_REVISION";
  function prepararRevisar() {
    const f = document.querySelector('[data-civil-form="revisar"]');
    const res = f.querySelector("[data-civil-resultado]");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      res.hidden = true;
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const tipo = valor(f, "tipo");
        const caras = await carasDe(tipo);
        if (!caras.length) throw new ErrorCivil("No hay triangulación de ese tipo. Triangula primero.");
        const anteriores = (await consultar({ layer: CAPA_REVISION, detail: "summary" })).map((e) => e.handle);
        decir(`Revisando ${caras.length.toLocaleString("es-MX")} triángulos…`);
        const r = await calcular("revisar_tin", { caras, angulo_minimo: numero(f, "angulo_minimo", 2) }, decir);
        await borrar(anteriores);
        const s = r.resumen;
        res.hidden = false;
        if (!s.total) {
          res.classList.remove("civil-resultado--aviso");
          res.innerHTML = `<p><strong>Sin problemas</strong> · ${caras.length.toLocaleString("es-MX")} triángulos revisados</p>`;
          return;
        }
        if (r.dxf) await insertarDxf(r.dxf, "revision.dxf");
        if (r.handles.length) await pedir({ op: "select", clear: true, handles: r.handles }).catch(() => {});
        const filas = [[s.repetidas, "repetidos"], [s.encimadas, "encimados"], [s.sin_superficie, "sin superficie"], [s.lados_de_mas, "lados con más de dos triángulos"], [s.alargadas, "muy delgados"]]
          .filter(([n]) => n).map(([n, t]) => `<li><strong>${n.toLocaleString("es-MX")}</strong> ${t}</li>`).join("");
        res.classList.add("civil-resultado--aviso");
        res.innerHTML = `<p>Marcados en rojo y seleccionados:</p><ul class="civil-lista">${filas}</ul>${r.graves.length ? `<p class="civil-nota">Bórralos o vuelve a triangular; luego actualiza las curvas.</p>` : ""}`;
      });
    });
  }

  // -- Cuadro de curvas ---------------------------------------------------------------------------------------------------------
  function prepararCuadroCurvas() {
    const f = document.querySelector('[data-civil-form="cuadro_curvas"]');
    prepararElecciones(f);
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Calculando…", async () => {
        const el = est.elegidos.cuadro_curvas || {};
        if (!el.curvas) throw new ErrorCivil("Elige las polilíneas con curvas.");
        const datos = {
          polilineas: el.curvas.map((p) => ({ vertices: p.vertices.map((v) => [v[0], v[1]]), combas: p.combas, cerrada: p.cerrada })),
          rotular: !!valor(f, "rotular"),
          opciones: { primer_numero: parseInt(valor(f, "primer_numero"), 10) || 1, decimales_distancia: +valor(f, "decimales_distancia"), decimales_coordenadas: +valor(f, "decimales_coordenadas"),
            decimales_segundos: +valor(f, "decimales_segundos"), separador_miles: valor(f, "separador_miles") ? "," : "", oeste: valor(f, "oeste_o") ? "O" : "W", titulo: valor(f, "titulo") },
        };
        datos.esquina = await lugarLibre("lado");             // sugerido: a un lado de todo lo dibujado, nunca encima de otro cuadro
        const r = await calcular("cuadro_curvas", datos);
        if (!(await colocarDxf(r.dxf, "cuadro-curvas.dxf", { titulo: "El cuadro de curvas", mover: CAPAS_CUADRO }))) return cancelado();
        cerrarHoja();
        avisar(`Cuadro de curvas: ${r.resumen.curvas} ${r.resumen.curvas === 1 ? "curva" : "curvas"}`);
      });
    });
  }

  // -- v1.1: localizar, reporte, insertar puntos, anotar lados, numerar vértices, memoria, cuadrícula --------------------------
  const polisDe = (lista) => (lista || []).map((p) => ({ vertices: p.vertices.map((v) => [v[0], v[1]]), combas: p.combas, cerrada: p.cerrada }));
  const nombrePlano = () => (window.TVCad.estado().plano || {}).nombre || "Plano";

  /** Acerca la vista a un punto (una ventana de unos 4 cm de papel) y lo deja seleccionado. */
  async function irA(x, y, handle) {
    const r = est.escala.denominador / 50;
    await pedir({ op: "run", cmd: `ZOOM W ${x - r},${y - r} ${x + r},${y + r}` });
    const s = await pedir({ op: "state" });
    if (s.command) await pedir({ op: "cancel" }).catch(() => {});
    if (handle) await pedir({ op: "select", clear: true, handles: [handle] }).catch(() => {});
  }

  function prepararLocalizar() {
    const f = document.querySelector('[data-civil-form="localizar"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Buscando…", async () => {
        const buscado = String(valor(f, "numero") || "").trim().toUpperCase();
        if (!buscado) throw new ErrorCivil("Escribe el número del punto.");
        const pts = await insercionesDe(null);
        const p = pts.find((q) => String(q.atributos.NUM || "").trim().toUpperCase() === buscado);
        if (!p) throw new ErrorCivil(`No hay un punto ${valor(f, "numero")} en el plano.`);
        cerrarHoja();
        await irA(p.x, p.y, p.handle);
        avisar(`Punto ${p.atributos.NUM}: Y ${mil(p.y, 3)} · X ${mil(p.x, 3)}${p.atributos.ELEV ? ` · Z ${p.atributos.ELEV}` : ""}`);
      }, { soloLee: true });
    });
  }

  function prepararReporte() {
    const f = document.querySelector('[data-civil-form="reporte"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo los puntos…", async () => {
        const cuales = valor(f, "cuales");
        let pts;
        if (cuales === "seleccion") {
          const e = await elegir({ mensaje: "Elige los puntos", tipos: ["Block Reference", "INSERT", "Insert", "Point"] });
          if (!e) return;
          pts = e.map(punto);
        } else pts = await puntosDe(cuales);
        if (!pts.length) throw new ErrorCivil("No hay puntos de ese tipo en el plano.");
        const datos = [["Proyecto", valor(f, "proyecto") || ""], ["Plano", nombrePlano()]];
        const r = await calcular("reporte_puntos", { puntos: pts, decimales: parseInt(valor(f, "decimales"), 10), titulo: valor(f, "titulo"), datos, formato: valor(f, "formato"),
          ...(valor(f, "geograficas") ? zona() : {}) });
        descargarDocumento(r, `Reporte de puntos ${nombrePlano()}`);
        avisar(`Reporte de ${r.resumen.puntos.toLocaleString("es-MX")} puntos`);
      }, { soloLee: true });
    });
  }

  function prepararInsertar() {
    const f = document.querySelector('[data-civil-form="insertar"]');
    const eleccion = eleccionDePuntos(f, { todos: false, porTipo: false });
    prepararElecciones(f, null, eleccion.especiales); enlazarDependientes(f);
    alAbrir.insertar = () => eleccion.reiniciar();
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo la triangulación…", async (decir) => {
        const tipo = valor(f, "tipo");
        const pats = patronesDescripcion(valor(f, "excluir"));
        const pts = (await eleccion.leer()).filter((p) => Number.isFinite(p[2]) && !excluidoPor(p, pats));
        if (!pts.length) throw new ErrorCivil("Ninguno de esos puntos tiene elevación para insertar.");
        const caras = await carasDe(tipo);
        if (!caras.length) throw new ErrorCivil("No hay triangulación de ese tipo. Triangula primero.");
        decir(`Insertando ${pts.length.toLocaleString("es-MX")} puntos…`);
        const r = await calcular("insertar_puntos", { caras, puntos: pts, tipo, ajustar: !!valor(f, "ajustar") }, decir);
        await insertarDxf(r.dxf, "triangulos.dxf");
        await borrar(r.borrar);
        cerrarHoja();
        avisar(`${r.resumen.insertados} punto${r.resumen.insertados === 1 ? "" : "s"} insertado${r.resumen.insertados === 1 ? "" : "s"}. Actualiza las curvas.`);
        for (const a of r.avisos || []) avisar(a);
      });
    });
  }

  function opcionesLados(f) {
    return { decimales_distancia: +valor(f, "decimales_distancia"), decimales_segundos: +valor(f, "decimales_segundos"),
      separador_miles: valor(f, "separador_miles") ? "," : "", oeste: valor(f, "oeste_o") ? "O" : "W" };
  }
  function prepararRumbos() {
    const f = document.querySelector('[data-civil-form="rumbos"]');
    prepararElecciones(f);
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Calculando…", async () => {
        const el = est.elegidos.rumbos || {};
        if (!el.lineas) throw new ErrorCivil("Elige las polilíneas o líneas.");
        const r = await calcular("anotar_lados", { polilineas: polisDe(el.lineas), numerar_vertices: !!valor(f, "numerar_vertices"), opciones: opcionesLados(f) });
        await insertarDxf(r.dxf, "lados.dxf");
        cerrarHoja();
        avisar(`${r.resumen.lados} lado${r.resumen.lados === 1 ? "" : "s"} anotado${r.resumen.lados === 1 ? "" : "s"}`);
      });
    });
  }
  function prepararNumerar() {
    const f = document.querySelector('[data-civil-form="numerar"]');
    prepararElecciones(f); enlazarDependientes(f);
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Numerando…", async () => {
        const el = est.elegidos.numerar || {};
        if (!el.lineas) throw new ErrorCivil("Elige las polilíneas o líneas.");
        const r = await calcular("numerar_vertices", { polilineas: polisDe(el.lineas), opciones: { numeracion: valor(f, "numeracion"), primer_numero: parseInt(valor(f, "primer_numero"), 10) || 1 } });
        await insertarDxf(r.dxf, "vertices.dxf");
        cerrarHoja();
        avisar(`${r.resumen.vertices} vértices numerados`);
      });
    });
  }

  function prepararMemoria() {
    const f = document.querySelector('[data-civil-form="memoria"]');
    const caja = f.querySelector("[data-civil-colindancias]"), lista = f.querySelector("[data-civil-colindancias-lista]");
    const datosMemoria = () => {
      const el = est.elegidos.memoria || {};
      if (!el.poligono) throw new ErrorCivil("Elige el polígono.");
      const p = el.poligono[0];
      return { poligono: { vertices: p.vertices.map((v) => [v[0], v[1]]), combas: p.combas, cerrada: true },
        opciones: { sentido: valor(f, "sentido"), formato: valor(f, "formato_cuadro"), ...zona() } };
    };
    async function lados() {
      // los lados en el orden del cuadro (cambia con el sentido): una colindancia por lado, conservando lo ya escrito
      const previas = Object.fromEntries([...lista.querySelectorAll("input")].map((i) => [i.dataset.lado, i.value]));
      const r = await calcular("memoria", datosMemoria());
      const filas = r.documento.filas.filter((x) => x[0] && x[1] && x[0] !== "CURVA");
      lista.innerHTML = filas.map((x, k) => { const clave = `${x[0]}-${x[1]}`; return `<div class="tv-campo"><label for="civil-mem-col-${k}">Lado ${esc(x[0])}–${esc(x[1])}</label><input id="civil-mem-col-${k}" data-lado="${esc(clave)}" maxlength="200" autocomplete="off" placeholder="Colindancia" value="${esc(previas[clave] || "")}"></div>`; }).join("");
      caja.hidden = !filas.length;
    }
    prepararElecciones(f, async () => { await lados(); });
    f.addEventListener("change", (ev) => { if (["sentido", "formato_cuadro"].includes(ev.target.name) && (est.elegidos.memoria || {}).poligono) lados().catch(() => {}); });
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Redactando…", async () => {
        const d = datosMemoria();
        d.colindancias = [...lista.querySelectorAll("input")].map((i) => i.value.trim());
        d.datos = [["Predio", valor(f, "predio") || ""], ["Ubicación", valor(f, "ubicacion") || ""], ["Propietario", valor(f, "propietario") || ""]];
        d.formato = valor(f, "formato");
        const r = await calcular("memoria", d);
        descargarDocumento(r, `Memoria descriptiva ${valor(f, "predio") || nombrePlano()}`);
        avisar("Memoria descriptiva lista");
      }, { soloLee: true });
    });
  }

  function prepararCuadricula() {
    const f = document.querySelector('[data-civil-form="cuadricula"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Calculando…", async () => {
        let caja;
        if (valor(f, "zona_dibujo") === "senalar") {
          const a = await senalar("Señala una esquina de la cuadrícula");
          if (!a) return;
          const b = await senalar("Señala la esquina opuesta");
          if (!b) return;
          caja = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
        } else {
          caja = await extensionDibujo();
          if (!caja) throw new ErrorCivil("El plano está vacío. Señala las esquinas.");
        }
        const anteriores = [];
        if (valor(f, "reemplazar")) for (const capa of ["RETICULA_UTM", "RETICULA_MARCO", "RETICULA_TEXTOS"]) anteriores.push(...(await consultar({ layer: capa, detail: "summary" })).map((e) => e.handle));
        const r = await calcular("cuadricula", { caja, intervalo: numero(f, "intervalo", 0), tipo: valor(f, "tipo"), marco: !!valor(f, "marco"),
          ...(valor(f, "geograficas") ? zona() : {}) });
        await insertarDxf(r.dxf, "cuadricula.dxf");
        await borrar(anteriores);
        cerrarHoja();
        avisar(`Cuadrícula UTM cada ${mil(r.resumen.intervalo, 0)} m`);
      });
    });
  }

  // -- Puntos: dibujar, editar, etiquetas y convertir ------------------------------------------------------------------------
  const elevTexto = (z, dec) => (z === null || z === undefined || z === "" || !Number.isFinite(+z) ? "" : (+z).toFixed(dec));
  const sigNumero = (pts) => {
    let n = 0;
    for (const p of pts) { const m = String(p.atributos.NUM || "").match(/(\d+)\s*$/); if (m) n = Math.max(n, parseInt(m[1], 10)); }
    return String(n + 1);
  };
  async function redibujar(pts, borrarAntes = [], extra = {}) {
    const r = await calcular("redibujar_puntos", { puntos: pts, ...extra });
    await insertarDxf(r.dxf, "puntos.dxf");
    await borrar(borrarAntes);
    return r;
  }
  function datosPunto(f) {
    const num = String(valor(f, "numero") || "").trim();
    if (!num) throw new ErrorCivil("Escribe el número del punto.");
    const zTxt = String(valor(f, "elevacion") ?? "").trim();
    const z = zTxt === "" ? null : parseFloat(zTxt.replace(",", "."));
    if (zTxt !== "" && !Number.isFinite(z)) throw new ErrorCivil("Revisa la elevación.");
    return { num, z, desc: String(valor(f, "descripcion") || "").trim(), dec: parseInt(valor(f, "decimales_z"), 10) };
  }

  function prepararPuntoNuevo() {
    const f = document.querySelector('[data-civil-form="punto_nuevo"]');
    enlazarDependientes(f);
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Dibujando…", async () => {
        const d = datosPunto(f);
        const existentes = await insercionesDe(null);
        if (existentes.some((p) => String(p.atributos.NUM || "").trim().toUpperCase() === d.num.toUpperCase())) throw new ErrorCivil(`Ya hay un punto ${d.num}.`);
        let x, y;
        if (valor(f, "donde") === "coordenadas") {
          x = numero(f, "x", NaN); y = numero(f, "y", NaN);
          if (!Number.isFinite(x) || !Number.isFinite(y)) throw new ErrorCivil("Escribe las coordenadas X y Y.");
        } else {
          const q = await senalar("Señala dónde va el punto");
          if (!q) return;
          [x, y] = q;
        }
        await redibujar([{ x, y, z: d.z ?? 0, tipo: valor(f, "tipo"), atributos: { NUM: d.num, ELEV: elevTexto(d.z, d.dec), DESC: d.desc } }]);
        f.elements.numero.value = (() => { const m = d.num.match(/^(.*?)(\d+)$/); return m ? `${m[1]}${parseInt(m[2], 10) + 1}` : ""; })();
        f.elements.elevacion.value = "";
        avisar(`Punto ${d.num} dibujado`);
      });
    });
    return async () => {   // al abrir: el siguiente número libre
      if (!f.elements.numero.value) { try { f.elements.numero.value = sigNumero(await insercionesDe(null)); } catch (_) { /* nada */ } }
    };
  }

  function prepararPuntoEditar() {
    const f = document.querySelector('[data-civil-form="punto_editar"]');
    enlazarDependientes(f);
    const cuenta = f.querySelector('[data-civil-cuenta="punto"]');
    let actual = null;
    f.querySelector('[data-civil-elegir="punto"]').addEventListener("click", async () => {
      try {
        const e = await elegir({ mensaje: "Elige el punto", tipos: ["Block Reference", "INSERT", "Insert"], maximo: 1 });
        if (!e) return;
        if (!esPuntoCivil(e[0])) throw new ErrorCivil("Eso no es un punto de Civil.");
        actual = puntoCivil(e[0]);
        f.elements.numero.value = actual.atributos.NUM || "";
        f.elements.elevacion.value = actual.atributos.ELEV ? actual.z : "";
        f.elements.descripcion.value = actual.atributos.DESC || "";
        f.elements.x.value = actual.x.toFixed(4); f.elements.y.value = actual.y.toFixed(4);
        const dec = ((actual.atributos.ELEV || "").split(".")[1] || "").length;
        if ([...f.elements.decimales_z.options].some((o) => +o.value === dec)) f.elements.decimales_z.value = String(dec);
        cuenta.textContent = `Punto ${actual.atributos.NUM || "sin número"} · ${actual.tipo}`; cuenta.classList.add("civil-elegido");
        f.elements.elegido.value = "1"; f.dispatchEvent(new Event("change"));
      } catch (err) { avisar(err instanceof ErrorCivil ? err.message : "No se pudo leer el punto.", "error"); }
    });
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Guardando…", async () => {
        if (!actual) throw new ErrorCivil("Elige el punto.");
        const d = datosPunto(f);
        const x = numero(f, "x", actual.x), y = numero(f, "y", actual.y);
        await redibujar([{ x, y, z: d.z ?? actual.z, tipo: actual.tipo, atributos: { NUM: d.num, ELEV: elevTexto(d.z, d.dec), DESC: d.desc } }], [actual.handle]);
        actual = null; f.elements.elegido.value = ""; cuenta.textContent = cuenta.dataset.vacio; cuenta.classList.remove("civil-elegido"); f.dispatchEvent(new Event("change"));
        cerrarHoja();
        avisar(`Punto ${d.num} actualizado`);
      });
    });
  }

  function prepararEtiquetasPuntos() {
    const f = document.querySelector('[data-civil-form="etiquetas_puntos"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Aplicando…", async () => {
        const capas = { ver_numero: "PUNTOS_NUMERO", ver_elevacion: "PUNTOS_ELEVACION", ver_descripcion: "PUNTOS_DESCRIPCION" };
        const pasos = [];
        for (const [campo, capa] of Object.entries(capas)) pasos.push({ op: "set_properties", collection: "layers", name: capa, updates: [{ path: "/flags/off", value: !valor(f, campo) }] });
        for (const p of pasos) await pedir(p).catch(() => {});            // una capa que aún no existe no es un error
        const dec = valor(f, "decimales_z");
        let n = 0;
        if (dec !== "") {
          const pts = (await insercionesDe(null)).filter((p) => p.atributos.ELEV);
          if (pts.length) {
            await redibujar(pts.map((p) => ({ ...p, atributos: { ...p.atributos, ELEV: elevTexto(p.z, +dec) } })), pts.map((p) => p.handle));
            n = pts.length;
          }
        }
        cerrarHoja();
        avisar(n ? `Etiquetas aplicadas; ${n.toLocaleString("es-MX")} elevaciones con ${dec} decimal${+dec === 1 ? "" : "es"}` : "Etiquetas aplicadas");
      });
    });
  }

  function prepararConvertirPuntos() {
    const f = document.querySelector('[data-civil-form="convertir_puntos"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "", async () => {
        const a = valor(f, "a");
        const e = await elegir({ mensaje: `Elige los puntos que pasan a ${a}`, tipos: ["Block Reference", "INSERT", "Insert"] });
        if (!e) return;
        const pts = e.filter(esPuntoCivil).map(puntoCivil).filter((p) => p.tipo !== a);
        if (!pts.length) throw new ErrorCivil(`Esos puntos ya son de ${a}.`);
        await redibujar(pts.map((p) => ({ ...p, tipo: a })), pts.map((p) => p.handle));
        cerrarHoja();
        avisar(pts.length === 1 ? `1 punto pasó a ${a}` : `${pts.length.toLocaleString("es-MX")} puntos pasaron a ${a}`);
      });
    });
  }

  // -- Curvas ya dibujadas: etiquetar y suavizar --------------------------------------------------------------------------------
  const CAPA_CURVA = /^CURVAS_(MAESTRAS|INTERMEDIAS)(_PROYECTO)?$/;
  const curvaDe = (e) => { const p = polilinea(e); return { handle: e.handle, capa: e.layer, vertices: p.vertices.map((v) => [v[0], v[1]]), elevacion: p.vertices.length ? p.vertices[0][2] : 0, cerrada: p.cerrada, tres_d: p.tres_d }; };
  const CAPAS_ETIQUETAS_CURVAS = ["CURVAS_ETIQUETAS", "CURVAS_ETIQUETAS_PROYECTO"];
  /** Las etiquetas de curvas que ya están en el plano (TEXT de versiones anteriores o MTEXT con fondo): para reescribirlas al unir. */
  async function etiquetasDelPlano() {
    const salida = [];
    for (const capa of CAPAS_ETIQUETAS_CURVAS) {
      for (const e of await consultar({ layer: capa, detail: "full" })) {
        if (e.type !== "Text" && e.type !== "MText") continue;
        const p = e.properties || {};
        // TEXT alineado: su punto es el de alineación; MTEXT: su punto de inserción (el motor las escribe centradas en la curva)
        const alineado = e.type === "Text" && p.alignment_point && ((p.horizontal_alignment && p.horizontal_alignment !== "Left") || (p.vertical_alignment && p.vertical_alignment !== "Baseline"));
        const pos = alineado ? p.alignment_point : p.insertion_point;
        const texto = String(e.value ?? p.value ?? "").replace(/\\P/g, " ").replace(/[{}]/g, "").trim();
        if (!pos || !texto) continue;
        salida.push({ handle: e.handle, capa: e.layer || capa, valor: texto, posicion: [pos.x, pos.y],
          rotacion: (Number(p.rotation || 0) * 180) / Math.PI, altura: Number(p.height || e.height || 0) });   // el editor da la rotación en radianes
      }
    }
    return salida;
  }

  async function curvasDelPlano() {
    const salida = [];
    for (const capa of ["CURVAS_MAESTRAS", "CURVAS_INTERMEDIAS", "CURVAS_MAESTRAS_PROYECTO", "CURVAS_INTERMEDIAS_PROYECTO"]) salida.push(...(await consultar({ layer: capa, detail: "full" })).map(curvaDe));
    return salida.filter((c) => c.vertices.length > 1);
  }
  async function elegirCurvas(mensaje) {
    const e = await elegir({ mensaje, tipos: ["Polyline", "LwPolyline", "Polyline3D"] });
    if (!e) return null;
    const curvas = e.filter((x) => CAPA_CURVA.test(x.layer || "")).map(curvaDe);
    if (!curvas.length) throw new ErrorCivil("Lo que elegiste no son curvas de nivel de Civil.");
    return curvas;
  }

  function prepararEtiquetarCurvas() {
    const f = document.querySelector('[data-civil-form="etiquetar_curvas"]');
    enlazarDependientes(f);
    const panel = f.querySelector("[data-civil-cortes]");
    let pendiente = null;                         // lo ya elegido mientras la persona decide si une las curvas cortadas
    const ocultar = () => { panel.hidden = true; pendiente = null; };
    f.addEventListener("change", ocultar);
    alAbrir.etiquetar_curvas = ocultar;
    async function etiquetar(datos, unir) {
      if (unir) datos = { ...datos, unir: true };
      const r = await calcular("etiquetar_curvas", datos);
      await insertarDxf(r.dxf, "etiquetas.dxf");
      await borrar(r.borrar);
      ocultar();
      cerrarHoja();
      const n = r.resumen.etiquetas;
      avisar(`${n} etiqueta${n === 1 ? "" : "s"}` + (r.resumen.cortes_unidos ? ` · ${r.resumen.cortes_unidos} corte${r.resumen.cortes_unidos === 1 ? "" : "s"} unido${r.resumen.cortes_unidos === 1 ? "" : "s"}` : ""));
    }
    f.querySelector("[data-civil-cortes-si]").addEventListener("click", () => { const d = pendiente; if (d) trabajo(f, "Uniendo y etiquetando…", () => etiquetar(d, true)); });
    f.querySelector("[data-civil-cortes-no]").addEventListener("click", () => { const d = pendiente; if (d) trabajo(f, "Etiquetando…", () => etiquetar(d, false)); });
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      ocultar();
      trabajo(f, "", async (decir) => {
        const datos = { decimales: +valor(f, "decimales") };
        if (valor(f, "modo") === "largo") {
          const c = await elegirCurvas("Elige las curvas");
          if (!c) return;
          Object.assign(datos, { polilineas: c, separacion_mm: numero(f, "separacion_mm", 80) });
        } else {
          const puntos = [];
          for (;;) { const q = await senalar(`Señala dónde va la etiqueta${puntos.length ? ` (${puntos.length})` : ""}`, " · Esc para terminar"); if (!q) break; puntos.push(q); }
          if (!puntos.length) return;
          datos.puntos = puntos;
          datos.polilineas = await curvasDelPlano();
          if (!datos.polilineas.length) throw new ErrorCivil("No hay curvas de nivel en el plano.");
        }
        // ¿curvas cortadas por una versión anterior? Se ofrece unirlas antes: una etiqueta nunca debe ir sobre una curva partida
        decir("Revisando las curvas…");
        datos.etiquetas = await etiquetasDelPlano();
        const cuenta = await calcular("unir_curvas", { polilineas: datos.polilineas, etiquetas: datos.etiquetas, solo_contar: true });
        if (cuenta.resumen.cortes) {
          pendiente = datos;
          const n = cuenta.resumen.cortes;
          f.querySelector("[data-civil-cortes-texto]").textContent = `Estas curvas están cortadas en ${n.toLocaleString("es-MX")} lugar${n === 1 ? "" : "es"}.`;
          panel.hidden = false;
          return;
        }
        decir("Etiquetando…");
        await etiquetar(datos, false);
      });
    });
  }

  function prepararUnirCurvas() {
    const f = document.querySelector('[data-civil-form="unir_curvas"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo las curvas…", async (decir) => {
        const c = valor(f, "cuales") === "elegidas" ? await elegirCurvas("Elige las curvas cortadas") : await curvasDelPlano();
        if (!c) return;
        if (!c.length) throw new ErrorCivil("No hay curvas de nivel en el plano.");
        decir("Uniendo…");
        const tol = numero(f, "tolerancia", 0);
        const r = await calcular("unir_curvas", { polilineas: c, etiquetas: await etiquetasDelPlano(), sin_etiquetas: !!valor(f, "sin_etiquetas"), ...(tol > 0 ? { tolerancia: tol } : {}) });
        await insertarDxf(r.dxf, "curvas.dxf");
        await borrar(r.borrar);
        cerrarHoja();
        const { cortes, antes, despues } = r.resumen;
        avisar(`${cortes.toLocaleString("es-MX")} corte${cortes === 1 ? "" : "s"} unido${cortes === 1 ? "" : "s"}: ${antes.toLocaleString("es-MX")} piezas quedaron en ${despues.toLocaleString("es-MX")} curvas`);
      });
    });
  }

  function prepararSuavizarCurvas() {
    const f = document.querySelector('[data-civil-form="suavizar_curvas"]');
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "", async (decir) => {
        const c = await elegirCurvas("Elige las curvas que vas a suavizar");
        if (!c) return;
        decir("Suavizando…");
        const elegidas = new Set(c.map((x) => x.handle));
        const fijas = (await curvasDelPlano()).filter((x) => !elegidas.has(x.handle));
        const r = await calcular("suavizar_curvas", { polilineas: c, fijas, iteraciones: +valor(f, "iteraciones") });
        await insertarDxf(r.dxf, "curvas.dxf");
        await borrar(r.borrar);
        cerrarHoja();
        avisar(`${r.resumen.suavizadas} curva${r.resumen.suavizadas === 1 ? "" : "s"} suavizada${r.resumen.suavizadas === 1 ? "" : "s"}`);
        for (const a of r.avisos || []) avisar(a);
      });
    });
  }

  // -- Eje con espirales ------------------------------------------------------------------------------------------------------
  /** La definición del eje (PI y curvas) que Civil guarda en el XDATA del eje que dibuja; null si es una polilínea cualquiera. */
  async function definicionEje(handle) {
    try {
      const r = await pedir({ op: "xdata_get", handles: [handle], app: "TVCIVIL" });
      const datos = (((r.items || r.result && r.result.items || [])[0] || {}).xdata || {}).TVCIVIL || [];
      const valores = datos.map((d) => (d && typeof d === "object" ? d.value : d));
      if (valores[0] !== "eje_espiral") return null;
      return JSON.parse(valores.slice(1).join(""));
    } catch (_) { return null; }
  }

  function prepararEjeEspirales() {
    const f = document.querySelector('[data-civil-form="eje_espirales"]');
    const caja = f.querySelector("[data-civil-pis]"), cuerpo = f.querySelector("[data-civil-pis-filas]"), res = f.querySelector("[data-civil-resultado]");
    const deflexion = (a, b, c) => {
      const r1 = Math.atan2(b[1] - a[1], b[0] - a[0]), r2 = Math.atan2(c[1] - b[1], c[0] - b[0]);
      let d = r2 - r1; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI;
      return d * 180 / Math.PI;
    };
    const dms = (g) => { const a = Math.abs(g), gr = Math.floor(a), m = Math.floor((a - gr) * 60), s = Math.round(((a - gr) * 60 - m) * 60); return `${gr}°${String(m).padStart(2, "0")}'${String(s).padStart(2, "0")}"`; };
    prepararElecciones(f, async (_, datos) => {
      const v = datos[0].vertices.map((q) => [q[0], q[1]]);
      if (v.length < 3) throw new ErrorCivil("La polilínea necesita al menos un vértice interior (un PI).");
      cuerpo.innerHTML = v.slice(1, -1).map((p, k) => { const d = deflexion(v[k], p, v[k + 2]); return `<tr><td>PI ${k + 1}</td><td class="tv-num">${dms(d)} ${d > 0 ? "izq." : "der."}</td>
        <td class="tv-num"><input name="pi_radio" type="number" min="1" step="any" value="300" inputmode="decimal" aria-label="Radio del PI ${k + 1}"></td>
        <td class="tv-num"><input name="pi_espiral" value="Mínima" autocomplete="off" aria-label="Espiral del PI ${k + 1}"></td></tr>`; }).join("");
      caja.hidden = false; res.hidden = true;
    });
    const datos = () => {
      const el = est.elegidos.eje_espirales || {};
      if (!el.tangentes) throw new ErrorCivil("Elige la polilínea de las tangentes.");
      const curvas = [...cuerpo.querySelectorAll("tr")].map((tr) => {
        const r = parseFloat(String(tr.querySelector("[name=pi_radio]").value).replace(",", "."));
        const e = String(tr.querySelector("[name=pi_espiral]").value).trim().toLowerCase();
        if (!(r > 0)) throw new ErrorCivil("Cada PI necesita su radio.");
        const le = e === "" || e.startsWith("m") ? "auto" : parseFloat(e.replace(",", "."));
        if (le !== "auto" && !(le >= 0)) throw new ErrorCivil("La espiral es «Mínima», 0 o una longitud en metros.");
        return { radio: r, espiral: le };
      });
      return { pi: el.tangentes[0].vertices.map((q) => [q[0], q[1]]), curvas, velocidad: +valor(f, "velocidad"), smax: +valor(f, "smax"), inicio: leerEstacion(valor(f, "inicio")) };
    };
    const mostrar = (r) => {
      res.hidden = false;
      res.innerHTML = `<p><strong>${r.resumen.curvas} curva${r.resumen.curvas === 1 ? "" : "s"}</strong> · eje de ${mil(r.resumen.longitud, 2)} m, hasta ${estacion(r.resumen.fin)}</p>
        <div class="tv-tabla-envoltura"><table class="tv-tabla civil-tabla"><thead><tr><th>PI</th><th class="tv-num">Rc</th><th class="tv-num">le</th><th class="tv-num">θe</th><th class="tv-num">STe</th><th>TE</th><th>EC</th><th>CE</th><th>ET</th></tr></thead><tbody>
        ${r.elementos.map((e) => `<tr><td>${e.pi}</td><td class="tv-num">${mil(e.radio, 2)}</td><td class="tv-num">${mil(e.le, 2)}</td><td class="tv-num">${dms(e.theta_e)}</td><td class="tv-num">${mil(e.ste, 3)}</td><td>${estacion(e.te)}</td><td>${estacion(e.ec)}</td><td>${estacion(e.ce)}</td><td>${estacion(e.et)}</td></tr>`).join("")}</tbody></table></div>`;
    };
    f.querySelector("[data-civil-calcular-eje]").addEventListener("click", () => {
      trabajo(f, "Calculando…", async () => { mostrar(await calcular("eje_espirales", { ...datos(), tabla: false })); }, { soloLee: true });
    });
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Dibujando el eje…", async () => {
        const r = await calcular("eje_espirales", datos());
        mostrar(r);
        if (!(await colocarDxf(r.dxf, "eje.dxf", { titulo: "La tabla de curvas del eje", mover: /^EJE_TABLA$/ }))) return cancelado();
        avisar(`Eje con ${r.resumen.curvas} curva${r.resumen.curvas === 1 ? "" : "s"}: elígelo en el perfil, las secciones o la curva masa`);
      });
    });
  }

  // -- Volumen entre superficies -------------------------------------------------------------------------------------------------
  function prepararVolumen() {
    const f = document.querySelector('[data-civil-form="volumen"]');
    alAbrir.volumen = prepararSuperficies(f, () => "El volumen compara la triangulación de Terreno con la de Proyecto: lo que el terreno tiene de más es corte; lo que le falta, terraplén.");
    const res = f.querySelector("[data-civil-resultado]");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      trabajo(f, "Leyendo las dos triangulaciones…", async (decir) => {
        const [t, p] = [await carasDe("terreno"), await carasDe("proyecto")];
        if (!t.length) throw new ErrorCivil("Falta la triangulación del terreno.");
        if (!p.length) throw new ErrorCivil("Falta la triangulación de proyecto.");
        const datos = { caras_terreno: t, caras_proyecto: p, linea_cero: !!valor(f, "linea_cero") };
        const enPlano = valor(f, "ubicacion") === "senalar";
        if (enPlano) datos.posicion = await lugarLibre("lado");
        decir("Calculando el volumen…");
        const r = await calcular("volumen_superficies", datos, decir);
        res.hidden = false;
        res.innerHTML = `<dl class="civil-cifras"><div><dt>Corte</dt><dd>${mil(r.resumen.corte)} m³</dd></div><div><dt>Terraplén</dt><dd>${mil(r.resumen.terraplen)} m³</dd></div>
          <div><dt>Neto</dt><dd>${mil(r.resumen.neto)} m³</dd></div><div><dt>Zona común</dt><dd>${mil(r.resumen.area_comun)} m²</dd></div></dl>`;
        if (enPlano) { if (!(await colocarDxf(r.dxf, "volumen.dxf", { titulo: "La tabla de volúmenes", mover: /^VOLUMEN_TABLA$/ }))) return cancelado(); }
        else if (valor(f, "linea_cero")) await insertarDxf(r.dxf, "volumen.dxf");
      });
    });
  }

  // -- Barra y menús --------------------------------------------------------------------------------------------------------------
  let barra = null;
  const ACCIONES = {
    escala: () => { llenarEscala(); abrirHoja("civil-hoja-escala"); },
    importar: () => { abrirHoja("civil-hoja-importar"); alAbrir.importar(); },
    exportar: () => { abrirHoja("civil-hoja-exportar"); alAbrir.exportar(); },
    importar_kml: () => { abrirHoja("civil-hoja-importar-kml"); alAbrir.importar_kml(); },
    exportar_kml: () => { abrirHoja("civil-hoja-exportar-kml"); alAbrir.exportar_kml(); },
    renumerar: () => abrirHoja("civil-hoja-renumerar"),
    revisar: () => abrirHoja("civil-hoja-revisar"),
    cuadro_curvas: () => abrirHoja("civil-hoja-cuadro-curvas"),
    punto_nuevo: async () => { abrirHoja("civil-hoja-punto-nuevo"); await alAbrirPuntoNuevo(); },
    punto_editar: () => abrirHoja("civil-hoja-punto-editar"),
    etiquetas_puntos: () => abrirHoja("civil-hoja-etiquetas-puntos"),
    convertir_puntos: () => abrirHoja("civil-hoja-convertir-puntos"),
    etiquetar_curvas: () => { abrirHoja("civil-hoja-etiquetar-curvas"); alAbrir.etiquetar_curvas(); },
    unir_curvas: () => abrirHoja("civil-hoja-unir-curvas"),
    suavizar_curvas: () => abrirHoja("civil-hoja-suavizar-curvas"),
    localizar: () => { abrirHoja("civil-hoja-localizar"); $("civil-loc-num").focus(); },
    reporte: () => abrirHoja("civil-hoja-reporte"),
    insertar: () => { abrirHoja("civil-hoja-insertar"); alAbrir.insertar(); },
    rumbos: () => abrirHoja("civil-hoja-rumbos"),
    numerar: () => abrirHoja("civil-hoja-numerar"),
    memoria: () => abrirHoja("civil-hoja-memoria"),
    cuadricula: () => abrirHoja("civil-hoja-cuadricula"),
    masa: () => { abrirHoja("civil-hoja-masa"); alAbrir.masa(); },
    eje_espirales: () => abrirHoja("civil-hoja-eje-espirales"),
    lotes: () => abrirHoja("civil-hoja-lotes"),
    triangular: () => { abrirHoja("civil-hoja-triangular"); alAbrir.triangular(); },
    invertir: invertirTriangulos,
    borrar: () => abrirHoja("civil-hoja-borrar"),
    agregar: agregarTriangulo,
    curvas: () => abrirHoja("civil-hoja-curvas"),
    actualizar_curvas: actualizarCurvas,
    cuadro: () => abrirHoja("civil-hoja-cuadro"),
    perfil: () => { abrirHoja("civil-hoja-perfil"); alAbrir.perfil(); },
    secciones: () => { abrirHoja("civil-hoja-secciones"); alAbrir.secciones(); },
    volumen: () => { abrirHoja("civil-hoja-volumen"); alAbrir.volumen(); },
  };
  let llenarEscala = () => {};
  let alAbrirPuntoNuevo = async () => {};

  /** La lista de un menú va fija (la barra se desplaza a lo ancho): bajo su botón, sin salirse de la ventana. */
  function colocarMenu(menu) {
    const lista = menu.querySelector(".civil-menu__lista");
    const b = menu.querySelector("summary").getBoundingClientRect();
    const ancho = lista.offsetWidth || 256;
    const x = Math.max(8, Math.min(b.left, window.innerWidth - ancho - 8));
    lista.style.setProperty("--civil-menu-x", `${Math.round(x)}px`);
    lista.style.setProperty("--civil-menu-y", `${Math.round(b.bottom + 4)}px`);
  }

  function montar() {
    barra = window.TVCad.barra();
    if (!barra) return;
    barra.classList.add("civil-barra");
    barra.append($("civil-barra").content.cloneNode(true));
    barra.addEventListener("click", (ev) => {
      const s = ev.target.closest(".civil-menu > summary");
      if (s) colocarMenu(s.parentElement);   // antes de abrir: la lista aparece ya en su lugar
      const b = ev.target.closest("[data-civil]");
      if (!b) return;
      ev.preventDefault();
      const d = b.closest("details"); if (d) d.removeAttribute("open");
      const accion = ACCIONES[b.dataset.civil];
      if (accion) accion();
    });
    // un menú abierto a la vez; Esc y clic fuera lo cierran
    barra.addEventListener("toggle", (ev) => {
      if (!ev.target.open) return;
      barra.querySelectorAll(".civil-menu[open]").forEach((d) => { if (d !== ev.target) d.removeAttribute("open"); });
      colocarMenu(ev.target);
    }, true);
    barra.addEventListener("scroll", () => barra.querySelectorAll(".civil-menu[open]").forEach((d) => d.removeAttribute("open")), { passive: true });
    window.addEventListener("resize", () => barra.querySelectorAll(".civil-menu[open]").forEach(colocarMenu));
    document.addEventListener("click", (ev) => { if (!ev.target.closest(".civil-menu")) barra.querySelectorAll(".civil-menu[open]").forEach((d) => d.removeAttribute("open")); });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") barra.querySelectorAll(".civil-menu[open]").forEach((d) => d.removeAttribute("open")); });
    document.querySelectorAll("[data-civil-cuenta]").forEach((c) => { c.dataset.vacio = c.textContent; });
    llenarEscala = prepararEscala();
    prepararImportar(); prepararExportar(); prepararImportarKml(); prepararExportarKml(); prepararTriangular(); prepararBorrar(); prepararCurvas(); prepararCuadro(); prepararCuadroCurvas(); prepararRenumerar(); prepararRevisar(); prepararLocalizar(); alAbrirPuntoNuevo = prepararPuntoNuevo(); prepararPuntoEditar(); prepararEtiquetasPuntos(); prepararConvertirPuntos(); prepararEtiquetarCurvas(); prepararUnirCurvas(); prepararSuavizarCurvas(); prepararReporte(); prepararInsertar(); prepararRumbos(); prepararNumerar(); prepararMemoria(); prepararCuadricula(); prepararMasa(); prepararLotes(); prepararEjeEspirales(); prepararPerfil(); prepararSecciones(); prepararVolumen();
    pintarEscala();
    const editable = () => barra.toggleAttribute("data-solo-lectura", !window.TVCad.estado().editable);
    editable();
    window.TVCad.on("cargado", () => { editable(); detectarEscala(); });
    detectarEscala();
  }

  window.TVCad.listo.then(montar);
  window.TVCivil = Object.freeze({ version: 1, insertarDxf, calcular, consultar, enUnPaso, lugarLibre });
})();

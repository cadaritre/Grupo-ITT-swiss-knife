/* CAD · «Modo táctil» para teléfono y tableta (detrás del ajuste del servidor TV_CAD_TACTIL).
 *
 * Una capa táctil en nuestra página, encima del editor (Open CAD Studio web en su marco del mismo origen), que lo maneja por su canal de
 * control (`window.TVCad`, control.js). El editor sigue siendo el dueño del dibujo; la página (editor.js o, sin conexión, campo.js)
 * sigue siendo la dueña de guardar, versiones, bloqueo y borradores. Esta capa solo cambia cómo se toca:
 *
 *  - Navegar «transformar ahora, fijar al soltar»: durante el gesto el marco del editor se mueve y escala en el compositor del navegador
 *    (sin esperar al editor); al soltar, un `ZOOM W` exacto (f64) lleva el editor a la vista que se ve y la transformación se quita
 *    cuando el editor ya pintó. Un dedo mueve, dos dedos acercan alrededor de los dedos. Toques (una sola máquina, «racha»): un toque
 *    elige al momento; toque y tocar otra vez en el mismo lugar y arrastrar = recuadro de selección; tres toques = ver todo (lo que
 *    cambiaron los primeros toques se deshace antes).
 *  - Puntero con desfase: la punta queda 88 px arriba del asa, el dedo nunca tapa el punto. Referencias a objetos con `snap` del editor
 *    (el punto exacto en f64; el juego de referencias de la persona va en `modes`), lectura X/Y en vivo y «Poner punto» para confirmar.
 *  - Entrada dinámica: tocar un valor de la lectura (X, Y, distancia, azimut, radio, ancho…) lo escribe con el teclado propio; la punta
 *    va ahí mientras se escribe y el valor queda fijo hasta poner el punto.
 *  - Herramientas: cada figura o edición es UN paso de deshacer (`batch`, `entities_*`, `explode`, `trim`, `extend`). Las mediciones no
 *    tocan el dibujo.
 *
 * Contrato con la página (el mismo en plano.html y en la página sin conexión): #cad-config (JSON, con `tactil`), #cad-lienzo con
 * #cad-editor, #cad-estado[data-estado] y #cad-estado-texto, #cad-guardar, #cad-volver, y los eventos `tvcad:*` de control.js.
 * Opcionales: #cad-boton-versiones, #cad-descargar, #cad-tomar. Ver docs/cad.md, «Modo táctil».
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const raiz = document.documentElement;
  const C = JSON.parse(($("cad-config") || {}).textContent || "{}");
  const T = C.tactil;
  const tt = $("tt");
  if (!T || !tt || !$("cad-editor")) return;
  const CAMPO = raiz.dataset.cadCampo === "1";                               // página sin conexión: siempre táctil
  const zona = $("tt-zona"), capa = $("tt-capa"), iframe = $("cad-editor");
  const NS = "http://www.w3.org/2000/svg";
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const guardado = {
    leer(k, d) { try { const v = localStorage.getItem(`tv.cad.${k}`); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    poner(k, v) { try { localStorage.setItem(`tv.cad.${k}`, JSON.stringify(v)); } catch (_) { /* sin almacenamiento: solo esta vez */ } },
  };

  // ---------------------------------------------------------------------------------------------------------------- preferencias
  // Las referencias que el editor puede dar (los nombres de su respuesta `snap`), en el orden de la hoja.
  const REFS = [["Endpoint", "Extremo"], ["Midpoint", "Punto medio"], ["Intersection", "Intersección"], ["ApparentIntersection", "Intersección aparente"],
    ["Center", "Centro"], ["GeometricCenter", "Centro geométrico"], ["Quadrant", "Cuadrante"], ["Perpendicular", "Perpendicular"], ["Tangent", "Tangente"], ["Node", "Nodo"],
    ["Insertion", "Inserción"], ["Nearest", "Cercano"]];
  const TIPOS_SNAP = REFS.map(([k]) => k);
  const REFS_OMISION = ["Endpoint", "Midpoint", "Intersection", "ApparentIntersection", "Center", "Perpendicular", "Node", "Quadrant", "Insertion"];
  function refsGuardadas() {
    const v2 = guardado.leer("referencias.v2", null);
    if (Array.isArray(v2)) return v2.filter((k) => TIPOS_SNAP.includes(k));
    // La lista de la primera versión juntaba la intersección aparente con la intersección.
    const v1 = guardado.leer("referencias", null);
    if (!Array.isArray(v1)) return REFS_OMISION.slice();
    const r = v1.filter((k) => TIPOS_SNAP.includes(k));
    if (r.includes("Intersection")) r.push("ApparentIntersection");
    return r;
  }
  const pref = {
    decimales: guardado.leer("decimales", 3),
    zurdo: guardado.leer("zurdo", false),
    refs: refsGuardadas(),
    refsOn: guardado.leer("referencias.encendidas", true),
  };
  const NOMBRES_SNAP = Object.fromEntries(REFS);

  // ---------------------------------------------------------------------------------------------------------------- canal del editor
  const ed = () => iframe.contentWindow;
  async function pedir(q, tiempoMs = 30000) {
    const r = await window.TVCad.pedir(q, { tiempoMs, sondeoMs: 4 });
    return r && r.result !== undefined && r.result !== null ? r.result : r;
  }
  const pedirSuave = (q, ms) => pedir(q, ms).catch(() => null);
  const ejecutar = (cmd, ms) => pedir({ op: "run", cmd }, ms);
  const pintado = () => new Promise((ok) => { const w = ed(); if (!w) return ok(); w.requestAnimationFrame(() => w.requestAnimationFrame(ok)); });

  // ---------------------------------------------------------------------------------------------------------------- vista: pantalla ↔ mundo
  // Coordenadas de pantalla: píxeles CSS de #tt-zona. El área de dibujo del editor (su lienzo) empieza en (ox, oy) y mide w × h.
  // k = metros por píxel; (cx, cy) = el mundo en el centro del área.
  const vista = { cx: 0, cy: 0, k: 1, w: 1, h: 1, ox: 0, oy: 0 };
  const vistaEd = { ...vista };
  // La cuenta es la de TVCad.vistaDe (control.js), la misma que usa Civil: nunca difieren.
  function deEstado(v, st) {
    const m = window.TVCad.vistaDe(st);
    v.w = m.w; v.h = m.h; v.ox = m.ox; v.oy = m.oy; v.cx = m.cx; v.cy = m.cy; v.k = m.k;
    return v;
  }
  const aMundo = (sx, sy, v = vista) => [v.cx + (sx - v.ox - v.w / 2) * v.k, v.cy - (sy - v.oy - v.h / 2) * v.k];
  const aPantalla = (x, y, v = vista) => [v.ox + v.w / 2 + (x - v.cx) / v.k, v.oy + v.h / 2 - (y - v.cy) / v.k];
  async function leerVista() {
    const st = await pedir({ op: "state" });
    deEstado(vistaEd, st);
    Object.assign(vista, vistaEd);
    aplicarTransformacion(); pintar();
    return st;
  }
  function aplicarTransformacion() {
    const a = vistaEd.k / vista.k;
    const tx = (vista.ox + vista.w / 2) * (1 - a) + (vistaEd.cx - vista.cx) / vista.k;
    const ty = (vista.oy + vista.h / 2) * (1 - a) - (vistaEd.cy - vista.cy) / vista.k;
    iframe.style.transform = Math.abs(a - 1) < 1e-9 && Math.abs(tx) < 0.01 && Math.abs(ty) < 0.01 ? "" : `translate(${tx}px, ${ty}px) scale(${a})`;
    // Si el marco se va lejos (un arrastre largo, el recuadro que corre el plano), se fija la vista sin esperar a soltar: WebKit deja
    // de pintar un marco que quedó fuera de la pantalla y el editor dejaba de responder hasta que volvía.
    if (gesto && !fijando && (Math.abs(tx) > vista.w * 0.4 || Math.abs(ty) > vista.h * 0.4 || a > 2.5 || a < 0.4)) fijarVista();
  }
  const navegando = () => gesto && (gesto.tipo === "mover" || gesto.tipo === "pellizco" || ((gesto.tipo === "ventana" || gesto.tipo === "cerca") && !!auto));
  let fijando = null;
  // Lleva el editor a la vista que se ve con una sola petición; si el gesto siguió mientras tanto, vuelve a fijar al terminar.
  function fijarVista() {
    if (fijando) { fijando.otra = true; return fijando.promesa; }
    fijando = { otra: false };
    fijando.promesa = (async () => {
      try {
        do {
          fijando.otra = false;
          const v = { ...vista };
          const x1 = v.cx - (v.w / 2) * v.k, x2 = v.cx + (v.w / 2) * v.k, y1 = v.cy - (v.h / 2) * v.k, y2 = v.cy + (v.h / 2) * v.k;
          // view_set (f64, sin entrar en «Zoom anterior»); con un editor sin esa op, ZOOM Ventana, igual de exacto.
          const ok = await pedir({ op: "view_set", center: [v.cx, v.cy], height: v.h * v.k }).then(() => true, () => false);
          if (!ok) await ejecutar(`ZOOM W ${x1},${y1} ${x2},${y2}`);
          await pintado();
          const real = deEstado({ ...vista }, await pedir({ op: "state" }));
          Object.assign(vistaEd, real);
          if (!navegando() && !fijando.otra) Object.assign(vista, real);
          aplicarTransformacion(); pintar();
        } while (fijando.otra && !navegando());
      } catch (_) { /* el editor ocupado: el siguiente gesto vuelve a fijar */ }
      finally { fijando = null; }
    })();
    return fijando.promesa;
  }
  // Ver todo: la extensión real de lo dibujado en el modelo (la caja de cada objeto), con un margen, en el área que se ve. ZOOM Extensión
  // del editor usa la extensión guardada en el plano, que puede quedar vieja o no venir (en un plano chico el círculo salía cortado).
  async function extensionReal() {
    let caja = null, offset = 0;
    for (let i = 0; i < 30; i++) {
      const r = await pedirSuave({ op: "query", detail: "full", fields: ["bounds", "space"], limit: 10000, offset }, 60000);
      if (!r) return caja;
      for (const e of lista(r)) {
        if (e.space !== "*Model_Space") continue;
        const c = cajaDe(e);
        if (!c || !c.every(Number.isFinite)) continue;
        caja = caja ? [Math.min(caja[0], c[0]), Math.min(caja[1], c[1]), Math.max(caja[2], c[2]), Math.max(caja[3], c[3])] : c.slice();
      }
      if (r.next_offset === null || r.next_offset === undefined) break;
      offset = r.next_offset;
    }
    return caja;
  }
  async function verTodo() {
    try {
      const caja = est.presentacion ? null : await extensionReal();
      if (caja) {
        const st = deEstado({}, await pedir({ op: "state" }));
        const ancho = Math.max(caja[2] - caja[0], 1e-6), alto = Math.max(caja[3] - caja[1], 1e-6);
        const altura = Math.max(alto, (ancho * st.h) / Math.max(1, st.w)) * 1.12;      // 6 % de margen por lado
        const ok = await pedir({ op: "view_set", center: [(caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2], height: altura }).then(() => true, () => false);
        if (!ok) await ejecutar("ZOOM E");
      } else await ejecutar("ZOOM E");
      await pintado(); await leerVista();
    } catch (_) { /* nada que ver */ }
    if (puntero.visible) consultarSnap();
  }
  // Lleva a la vista un punto (o caja) que quedó fuera, sin cambiar la escala salvo que no quepa.
  async function enfocar(x1, y1, x2 = x1, y2 = y1) {
    const [a, b] = aPantalla(x1, y2), [c, d] = aPantalla(x2, y1);
    // Abajo, además, la lectura (la punta no debe quedar debajo de ella).
    const abajo = $("tt-lectura").hidden ? 20 : $("tt-lectura").offsetHeight + 28;
    const dentro = a > vista.ox + 20 && b > vista.oy + 20 && c < vista.ox + vista.w - 20 && d < vista.oy + vista.h - abajo;
    if (dentro) return;
    const ancho = Math.max(vista.w * vista.k, (x2 - x1) * 1.4), alto = Math.max(vista.h * vista.k, (y2 - y1) * 1.4);
    const esc = Math.max(ancho / vista.w, alto / vista.h);
    vista.cx = (x1 + x2) / 2; vista.cy = (y1 + y2) / 2; vista.k = esc;
    aplicarTransformacion(); pintar();
    await fijarVista();
  }

  // ---------------------------------------------------------------------------------------------------------------- tamaño del área de dibujo
  // El editor (#cad-lienzo) se coloca exactamente debajo de #tt-zona. Mientras el editor no deje ocultar su barra de estado, el marco se
  // alarga esa altura y la barra queda fuera de la vista (paso intermedio hasta la opción «solo lienzo» del editor).
  function medirZona() {
    const r = zona.getBoundingClientRect();
    raiz.style.setProperty("--tt-z-arriba", `${r.top}px`);
    raiz.style.setProperty("--tt-z-izq", `${r.left}px`);
    raiz.style.setProperty("--tt-z-ancho", `${r.width}px`);
    raiz.style.setProperty("--tt-z-alto", `${r.height}px`);
    raiz.style.setProperty("--tt-abajo-alto", `${Math.max(0, innerHeight - r.bottom)}px`);
    $("tt-svg").setAttribute("viewBox", `0 0 ${Math.max(1, r.width)} ${Math.max(1, r.height)}`);
  }
  async function ajustarRecorte() {
    for (let i = 0; i < 4; i++) {
      const st = await pedir({ op: "state" });
      const v = deEstado({}, st);
      const falta = zona.clientHeight - (v.oy + v.h);
      if (Math.abs(falta) < 1) return;
      const actual = parseFloat(raiz.style.getPropertyValue("--tt-recorte")) || 0;
      raiz.style.setProperty("--tt-recorte", `${Math.max(0, Math.min(200, actual + falta))}px`);
      await espera(200);
    }
  }
  let releer = null;
  function alCambiarTamano() {
    medirZona();
    if (!activo || !listo) return;
    clearTimeout(releer);
    // Cambió el tamaño del área (la barra de la herramienta en lugar de las pestañas, la barra de Safari, el teclado): lo dibujado no se
    // mueve. Queda en su lugar la esquina de arriba a la izquierda y la escala es la misma. Antes se fijaba con el alto viejo: la vista
    // se corría y se acercaba un poco sola mientras se movía el puntero.
    releer = setTimeout(async () => {
      try {
        const izq = vista.cx - (vista.w / 2) * vista.k, arr = vista.cy + (vista.h / 2) * vista.k;
        await ajustarRecorte();
        const ahora = deEstado({}, await pedir({ op: "state" }));
        Object.assign(vistaEd, ahora);
        Object.assign(vista, { w: ahora.w, h: ahora.h, ox: ahora.ox, oy: ahora.oy });
        vista.cx = izq + (vista.w / 2) * vista.k; vista.cy = arr - (vista.h / 2) * vista.k;
        aplicarTransformacion(); pintar();
        await fijarVista();
      } catch (_) { /* siguiente cambio */ }
    }, 180);
  }
  new ResizeObserver(alCambiarTamano).observe(zona);

  // ---------------------------------------------------------------------------------------------------------------- gestos
  // Una sola máquina para los toques de un dedo. Cada toque de una «racha» (toques seguidos en el mismo lugar, ≤ RACHA_MS entre uno y
  // otro y ≤ RACHA_PX del primero):
  //   1.º toque           → actúa al momento (elegir un objeto, llevar la punta, recortar…). Antes se guarda cómo estaba todo.
  //   2.º toque y arrastre → recuadro de selección desde ahí (antes se deshace lo que cambió el 1.º toque); suma a la selección.
  //   2.º toque y soltar  → actúa otra vez (como un toque).
  //   3.er toque          → ver todo; se deshace lo que cambiaron los dos primeros (nunca edita).
  // Un toque tarde o lejos empieza otra racha. Arrastrar sin la racha mueve el plano; dos dedos acercan; mantener pulsado = el puntero.
  // En «Recortar» y «Alargar» un dedo que arrastra traza una línea que cruza lo que se corta (dos dedos mueven el plano).
  const dedos = new Map();
  let gesto = null;          // {tipo: "pendiente"|"mover"|"pellizco"|"asa"|"ventana"|"cerca"}
  let racha = null;          // {n, t, x, y, antes, deshacer}
  const TOQUE_MS = 280, TOQUE_PX = 9, LARGO_MS = 450, RACHA_MS = 320, RACHA_PX = 40, BORDE_PX = 32;
  let modoVentana = false;
  let cola = Promise.resolve();
  const encolar = (fn) => (cola = cola.then(fn).catch((e) => console.warn(e)));

  function punto(ev) { const r = zona.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; }
  capa.addEventListener("pointerdown", (ev) => {
    if (!listo) return;
    try { capa.setPointerCapture(ev.pointerId); } catch (_) { /* puntero sintético */ }
    const [x, y] = punto(ev);
    const ahora = performance.now();
    dedos.set(ev.pointerId, { x, y, x0: x, y0: y, t0: ahora });
    cerrarTarjetaSiMueve = true;
    if (dedos.size === 1) {
      if (puntero.visible) {
        // El asa, o el astil entre el asa y la punta: arrastrar ahí mueve el puntero, nunca el dibujo (con el dedo un poco fuera del asa
        // la vista se corría mientras se movía el puntero).
        const [ax, ay] = posAsa(), [px, py] = posPunta();
        const L2 = (px - ax) ** 2 + (py - ay) ** 2, t = L2 ? Math.max(0, Math.min(1, ((x - ax) * (px - ax) + (y - ay) * (py - ay)) / L2)) : 0;
        const enAstil = Math.hypot(x - (ax + t * (px - ax)), y - (ay + t * (py - ay))) < 22 && t < 0.8;
        if (Math.hypot(x - ax, y - ay) < 46 || enAstil) { racha = null; gesto = { tipo: "asa", dx: ax - x, dy: ay - y }; return; }
      }
      if (modoVentana) { racha = null; empezarVentana(x, y); return; }
      const sigue = racha && ahora - racha.t <= RACHA_MS && Math.hypot(x - racha.x, y - racha.y) <= RACHA_PX;
      if (!sigue) racha = null;
      gesto = { tipo: "pendiente", n: sigue ? racha.n + 1 : 1, largo: setTimeout(() => pulsacionLarga(ev.pointerId), LARGO_MS) };
    } else if (dedos.size === 2) {
      racha = null;
      if (gesto && gesto.largo) clearTimeout(gesto.largo);
      if (gesto && (gesto.tipo === "ventana" || gesto.tipo === "cerca")) { pararAuto(); ocultarVentana(); ocultarCerca(); }
      const [a, b] = [...dedos.values()];
      gesto = { tipo: "pellizco", cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, sep: Math.hypot(a.x - b.x, a.y - b.y) };
    }
  });
  capa.addEventListener("pointermove", (ev) => {
    const f = dedos.get(ev.pointerId);
    if (!f || !gesto) return;
    const [x, y] = punto(ev);
    f.x = x; f.y = y;
    if (gesto.tipo === "asa") { moverPunteroPorAsa(x + gesto.dx, y + gesto.dy); return; }
    if (gesto.tipo === "ventana" || gesto.tipo === "cerca") { gesto.x = x; gesto.y = y; bordeAuto(x, y); pintarVentana(); pintarCerca(); return; }
    if (gesto.tipo === "pendiente" && Math.hypot(f.x - f.x0, f.y - f.y0) > TOQUE_PX) {
      clearTimeout(gesto.largo);
      const n = gesto.n;
      if (n === 2 && !herr && racha) {
        // Toque, otra vez en el mismo lugar y arrastre: recuadro de selección. Lo que cambió el primer toque se deshace antes.
        const r = racha; racha = null;
        gesto = null;
        encolar(() => restaurar(r));
        empezarVentana(f.x0, f.y0);
        gesto.x = x; gesto.y = y; pintarVentana();
        return;
      }
      racha = null;
      if (herr && herr.def.corte && n === 1) { gesto = { tipo: "cerca", m0: aMundo(f.x0, f.y0), x, y }; pintarCerca(); return; }
      gesto = { tipo: "mover", x: f.x0, y: f.y0 };
    }
    if (gesto.tipo === "mover") {
      prever(f.x - gesto.x, f.y - gesto.y, 1, 0, 0);
      gesto.x = f.x; gesto.y = f.y;
    } else if (gesto.tipo === "pellizco" && dedos.size >= 2) {
      const [a, b] = [...dedos.values()];
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2, sep = Math.hypot(a.x - b.x, a.y - b.y);
      prever(cx - gesto.cx, cy - gesto.cy, 1, cx, cy);
      if (gesto.sep > 4 && sep > 4) {
        const factor = sep / gesto.sep;
        if (Math.abs(factor - 1) > 0.002) { prever(0, 0, factor, cx, cy); gesto.sep = sep; }
      }
      gesto.cx = cx; gesto.cy = cy;
    }
  });
  function finDedo(ev) {
    const f = dedos.get(ev.pointerId);
    dedos.delete(ev.pointerId);
    if (!f || !gesto) return;
    const g = gesto;
    if (g.tipo === "pendiente") {
      clearTimeout(g.largo);
      gesto = null;
      const ahora = performance.now();
      if (ev.type !== "pointerup" || ahora - f.t0 >= TOQUE_MS) { racha = null; return; }
      const [tx, ty] = [f.x0, f.y0];
      if (g.n === 1) {
        racha = { n: 1, t: ahora, x: tx, y: ty, antes: instantanea(), deshacer: 0 };
        const r = racha;
        encolar(() => toque(tx, ty, r));
      } else if (g.n === 2 && racha) {
        racha.n = 2; racha.t = ahora;
        const r = racha;
        // En «Recortar» y «Alargar» el segundo toque de una racha no edita: podría ser el comienzo de tres toques.
        if (!(herr && herr.def.corte)) encolar(() => toque(tx, ty, r));
      } else if (g.n >= 3 && racha) {
        const r = racha; racha = null;
        encolar(async () => { await restaurar(r); await verTodo(); });
      }
    } else if (g.tipo === "mover" || (g.tipo === "pellizco" && dedos.size === 0)) {
      gesto = null;
      fijarVista().then(() => { if (puntero.visible) consultarSnap(); });
    } else if (g.tipo === "pellizco" && dedos.size === 1) {
      const [h] = [...dedos.values()];
      gesto = { tipo: "mover", x: h.x, y: h.y };          // queda un dedo: sigue moviendo, sin salto
    } else if (g.tipo === "asa") {
      gesto = null;
      consultarSnap();
    } else if (g.tipo === "ventana") {
      gesto = null; pararAuto();
      encolar(() => terminarVentana(g));
    } else if (g.tipo === "cerca") {
      gesto = null; pararAuto(); ocultarCerca();
      const [ax, ay] = aPantalla(g.m0[0], g.m0[1]);
      if (Math.hypot(g.x - ax, g.y - ay) >= 12) { const b = aMundo(g.x, g.y); encolar(() => accionCorte({ fence: [g.m0.slice(), b] })); }
      if (fijandoAuto) { fijandoAuto = false; fijarVista(); }
    }
  }
  capa.addEventListener("pointerup", finDedo);
  capa.addEventListener("pointercancel", finDedo);
  capa.addEventListener("contextmenu", (ev) => ev.preventDefault());
  // Cómo estaba todo antes del primer toque de una racha, para deshacer lo que cambiaron sus toques.
  function instantanea() {
    return { sel: new Map(seleccion), punt: { visible: puntero.visible, mundo: puntero.mundo.slice() }, herr };
  }
  async function restaurar(r) {
    if (!r || !r.antes) return;
    for (let i = 0; i < r.deshacer; i++) await pedirSuave({ op: "undo" });
    r.deshacer = 0;
    const a = r.antes;
    const igual = a.sel.size === seleccion.size && [...a.sel.keys()].every((k) => seleccion.has(k));
    if (!igual) { seleccion.clear(); for (const [k, v] of a.sel) seleccion.set(k, v); await reflejarSeleccion(); }
    if (herr && herr === a.herr) { puntero.mundo = a.punt.mundo.slice(); puntero.snap = null; pintar(); consultarSnap(); }
    else if (!herr && a.punt.visible && !puntero.visible) { puntero.mundo = a.punt.mundo.slice(); mostrarPuntero(); }
  }
  // Rueda y trackpad (tableta con teclado): acercar alrededor del cursor, mover con dos dedos del trackpad.
  let ruedaT = null;
  capa.addEventListener("wheel", (ev) => {
    if (!listo) return;
    ev.preventDefault();
    const [x, y] = punto(ev);
    if (ev.ctrlKey || Math.abs(ev.deltaY) > Math.abs(ev.deltaX) * 2 && ev.deltaMode === 0 && !ev.shiftKey && Math.abs(ev.deltaY) >= 40) {
      prever(0, 0, Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0025)), x, y);
    } else prever(-ev.deltaX, -ev.deltaY, 1, 0, 0);
    clearTimeout(ruedaT);
    ruedaT = setTimeout(() => fijarVista().then(() => { if (puntero.visible) consultarSnap(); }), 140);
  }, { passive: false });

  // Durante el gesto la vista se predice aquí: el editor solo se entera al soltar.
  function prever(dx, dy, factor, px, py) {
    vista.cx -= dx * vista.k; vista.cy += dy * vista.k;
    if (factor !== 1) {
      const [wx, wy] = aMundo(px, py);
      vista.k = Math.min(Math.max(vista.k / factor, 1e-7), 1e7);
      vista.cx = wx - (px - vista.ox - vista.w / 2) * vista.k; vista.cy = wy + (py - vista.oy - vista.h / 2) * vista.k;
    }
    aplicarTransformacion();
    pintar();
  }
  // Recuadro o línea de corte con el dedo junto al borde: el plano se corre solo, así cabe algo más grande que la pantalla.
  let auto = null, fijandoAuto = false;
  function bordeAuto(x, y) {
    const W = zona.clientWidth, H = zona.clientHeight;
    const v = (d) => (d < BORDE_PX ? (BORDE_PX - Math.max(0, d)) / BORDE_PX : 0);
    const vx = v(x) - v(W - x), vy = v(y) - v(H - y);
    if (!vx && !vy) { pararAuto(); return; }
    if (!auto) { auto = { vx, vy }; requestAnimationFrame(pasoAuto); } else { auto.vx = vx; auto.vy = vy; }
  }
  function pasoAuto() {
    if (!auto || !gesto || (gesto.tipo !== "ventana" && gesto.tipo !== "cerca")) { auto = null; return; }
    prever(auto.vx * 14, auto.vy * 14, 1, 0, 0);
    fijandoAuto = true;
    pintarVentana(); pintarCerca();
    requestAnimationFrame(pasoAuto);
  }
  function pararAuto() { auto = null; }
  async function toque(x, y, r) {
    if (herr) {
      if (herr.def.corte) {
        const cambio = await accionCorte({ point: aMundo(x, y) });
        if (cambio && r) r.deshacer += 1;
        return;
      }
      moverPuntaA(x, y);
      return;
    }
    if (puntero.visible) { ocultarPuntero(); return; }
    if (cerrarTarjeta()) return;
    await seleccionarEn(x, y);
  }
  function pulsacionLarga(id) {
    const f = dedos.get(id);
    if (!f || !gesto || gesto.tipo !== "pendiente") return;
    racha = null;
    if (navigator.vibrate) { try { navigator.vibrate(10); } catch (_) { /* sin vibración */ } }
    // El asa aparece bajo el dedo (ya en arrastre) y la punta arriba: «¿qué hay aquí?».
    const [dx, dy] = desfase();
    puntero.mundo = aMundo(f.x - dx, f.y - dy);
    mostrarPuntero();
    const [ax, ay] = posAsa();
    gesto = { tipo: "asa", dx: ax - f.x, dy: ay - f.y };
  }

  // ---------------------------------------------------------------------------------------------------------------- puntero con desfase
  const puntero = { visible: false, mundo: [0, 0], snap: null, cands: [], indice: 0 };
  const desfase = () => {
    const d = vista.w >= 700 ? 96 : 88;
    return pref.zurdo ? [52, Math.round(d * 0.8)] : [0, d];                    // la punta queda arriba (a la izquierda para zurdos)
  };
  function posPunta() { return aPantalla(puntero.mundo[0], puntero.mundo[1]); }
  function posAsa() { const [px, py] = posPunta(), [dx, dy] = desfase(); return [px + dx, py + dy]; }
  function mostrarPuntero() {
    puntero.visible = true;
    $("tt-puntero").setAttribute("visibility", "visible");
    $("tt-lectura").hidden = false;
    pintar(); consultarSnap();
  }
  function ocultarPuntero() {
    puntero.visible = false; puntero.snap = null; puntero.cands = [];
    cerrarDinamica();
    $("tt-puntero").setAttribute("visibility", "hidden");
    // La marca de la referencia es hija del puntero, pero en SVG un hijo con visibility="visible" se sigue viendo aunque el grupo esté
    // oculto: se apaga aparte (si no, quedaba en su lugar de la pantalla tras «Terminar» o al guardar).
    $("tt-marca").setAttribute("visibility", "hidden");
    $("tt-lectura-snap").hidden = true;
    $("tt-lectura").hidden = true;
    pintar();
  }
  function limitar(px, py) {
    const W = zona.clientWidth, H = zona.clientHeight;
    return [Math.min(Math.max(px, 6), W - 6), Math.min(Math.max(py, 6), H - 6)];
  }
  function moverPunteroPorAsa(ax, ay) {
    const [dx, dy] = desfase();
    const [px, py] = limitar(ax - dx, ay - dy);
    puntero.mundo = aMundo(px, py); puntero.snap = null;
    pintar(); consultarSnap();
  }
  function moverPuntaA(px, py) {
    [px, py] = limitar(px, py);
    puntero.mundo = aMundo(px, py); puntero.snap = null;
    if (!puntero.visible) mostrarPuntero(); else { pintar(); consultarSnap(); }
  }
  // Una consulta a la vez; si el puntero se movió mientras tanto, se pregunta por la última posición.
  // Con un editor que respeta `modes` (tv-v2026.45-9), el juego de la persona va en la petición y el editor elige la mejor referencia
  // permitida (y con `limit`, las siguientes en `hits`). Con uno anterior (o una copia vieja guardada en el teléfono) se filtra aquí.
  let enVuelo = false, otraConsulta = false;
  const refsActivas = () => (pref.refsOn ? pref.refs.filter((k) => TIPOS_SNAP.includes(k)) : []);
  async function consultarSnap() {
    if (!puntero.visible || navegando()) return;
    if (enVuelo) { otraConsulta = true; return; }
    enVuelo = true;
    try {
      do {
        otraConsulta = false;
        const m = puntero.mundo.slice();
        const modos = refsActivas();
        if (!modos.length) { puntero.snap = null; puntero.cands = []; pintar(); break; }
        const ultimo = herr && herr.puntos.length ? herr.puntos[herr.puntos.length - 1] : null;
        let hits;
        if (capac.modos) {
          const r = await pedirSuave({ op: "snap", point: m, modes: modos, limit: 4, ...(ultimo ? { from: ultimo } : {}) }, 5000);
          hits = r ? (Array.isArray(r.hits) ? r.hits : (r.snap ? [r] : [])) : [];
        } else {
          // Primero la referencia del punto (extremo, intersección, medio, centro…); la perpendicular desde el último punto solo si no
          // hay una de esas (con `from`, ese editor la prefiere aunque la punta esté justo en un extremo).
          let r = await pedirSuave({ op: "snap", point: m }, 5000);
          const fuerte = r && ["Endpoint", "Intersection", "ApparentIntersection", "Midpoint", "Center", "Node", "Insertion", "Quadrant"].includes(r.snap) && permitida(r.snap);
          if (!fuerte && ultimo && modos.includes("Perpendicular")) {
            const p2 = await pedirSuave({ op: "snap", point: m, from: ultimo }, 5000);
            if (p2 && p2.snap === "Perpendicular") r = p2;
          }
          hits = r && r.snap ? [r] : [];
        }
        if (otraConsulta || !puntero.visible) continue;
        puntero.cands = hits.map((h) => ({ tipo: h.snap || h.kind, mundo: h.world || h.point }))
          .filter((h) => h.tipo && Array.isArray(h.mundo) && permitida(h.tipo)).map((h) => ({ tipo: h.tipo, mundo: [h.mundo[0], h.mundo[1]] }));
        puntero.indice = 0;
        puntero.snap = puntero.cands[0] || null;
        pintar();
      } while (otraConsulta);
    } finally { enVuelo = false; }
  }
  const permitida = (tipo) => refsActivas().includes(tipo);
  // La siguiente referencia que el editor ofreció en el mismo lugar (como Tab en CAD): se elige en la etiqueta de la lectura.
  function siguienteReferencia() {
    if (!puntero.cands || puntero.cands.length < 2) return;
    puntero.indice = (puntero.indice + 1) % puntero.cands.length;
    puntero.snap = puntero.cands[puntero.indice];
    pintar();
  }
  // El punto que se pondría: la referencia (o la punta) con los valores escritos aplicados.
  function puntoActual() { return restringir(puntero.snap ? puntero.snap.mundo.slice() : puntero.mundo.slice()); }
  function formaSnap(tipo) {
    const s = 8;
    switch (tipo) {
      case "Endpoint": return `M${-s},${-s}h${2 * s}v${2 * s}h${-2 * s}z`;
      case "Midpoint": return `M0,${-s - 1}L${s + 1},${s}H${-s - 1}z`;
      case "Intersection": case "ApparentIntersection": return `M${-s},${-s}L${s},${s}M${s},${-s}L${-s},${s}`;
      case "Center": return `M${s},0A${s},${s} 0 1 1 ${-s},0A${s},${s} 0 1 1 ${s},0`;
      case "Perpendicular": return `M${-s},${s}H${s}M${-s},${s}V${-s}M${-s},0H0V${s}`;
      case "Nearest": return `M${-s},${-s}H${s}L${-s},${s}H${s}z`;
      case "GeometricCenter": return `M0,${-s}L${s},${-s / 3}L${s * 0.62},${s}H${-s * 0.62}L${-s},${-s / 3}zM-1.5,0h3`;
      case "Quadrant": return `M0,${-s}L${s},0L0,${s}L${-s},0z`;
      case "Tangent": return `M${s},2A${s - 2},${s - 2} 0 1 1 ${-s + 4},2M${-s},${-s + 1}H${s}`;
      case "Node": return `M${-s},${-s}L${s},${s}M${s},${-s}L${-s},${s}M${s},0A${s},${s} 0 1 1 ${-s},0A${s},${s} 0 1 1 ${s},0`;
      default: return `M${-s},${-s}h${2 * s}v${2 * s}h${-2 * s}zM0,${-s}V${s}`;
    }
  }

  // ---------------------------------------------------------------------------------------------------------------- formato de números
  function fmt(v, dec = pref.decimales) {
    if (!Number.isFinite(v)) return "—";
    const neg = v < 0; const [e, d] = Math.abs(v).toFixed(dec).split(".");
    return (neg ? "−" : "") + e.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (d ? "." + d : "");
  }
  const crudo = (v, dec = Math.max(pref.decimales, 3)) => (Number.isFinite(v) ? v.toFixed(dec) : "");
  const grados = (r) => (r * 180) / Math.PI;
  const norm360 = (g) => ((g % 360) + 360) % 360;
  const azimut = (a, b) => norm360(90 - grados(Math.atan2(b[1] - a[1], b[0] - a[0])));
  function fmtAng(g) { return `${fmt(g, 4)}°`; }
  function gms(g) {
    const t = norm360(g); let d = Math.floor(t), m = Math.floor((t - d) * 60), s = ((t - d) * 60 - m) * 60;
    if (s >= 59.995) { s = 0; m += 1; } if (m >= 60) { m = 0; d += 1; }
    return `${d}° ${String(m).padStart(2, "0")}′ ${s.toFixed(2).padStart(5, "0")}″`;
  }

  // ---------------------------------------------------------------------------------------------------------------- pintar la capa
  function el(tag, attrs = {}, padre) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (padre) padre.append(e);
    return e;
  }
  const P = (m) => aPantalla(m[0], m[1]);
  let pintarPendiente = false;
  function pintar() {
    if (pintarPendiente) return;
    pintarPendiente = true;
    requestAnimationFrame(() => { pintarPendiente = false; pintarYa(); });
  }
  function pintarYa() {
    const vert = $("tt-vertices"), previa = $("tt-previa"), sel = $("tt-sel");
    vert.replaceChildren(); previa.replaceChildren(); sel.replaceChildren();
    // Selección: la caja de cada objeto (el editor además los resalta).
    for (const s of seleccion.values()) {
      if (!s.caja) continue;
      const [a, b] = P([s.caja[0], s.caja[3]]), [c, d] = P([s.caja[2], s.caja[1]]);
      el("rect", { x: a - 3, y: b - 3, width: Math.max(6, c - a + 6), height: Math.max(6, d - b + 6), rx: 3 }, sel);
    }
    const cursor = puntero.visible ? puntoActual() : null;
    if (herr) {
      for (const p of herr.puntos) { const [x, y] = P(p); el("circle", { class: "tt-vertice", r: 5, cx: x, cy: y }, vert); }
      if (herr.def.previa) { try { herr.def.previa(previa, herr.puntos, cursor, herr); } catch (_) { /* previa incompleta */ } }
    }
    if (!puntero.visible) { $("tt-marca").setAttribute("visibility", "hidden"); $("tt-fijo").setAttribute("visibility", "hidden"); return; }
    const [px, py] = posPunta(), [ax, ay] = posAsa();
    const ang = Math.atan2(py - ay, px - ax);
    $("tt-astil").setAttribute("x1", ax + Math.cos(ang) * 26); $("tt-astil").setAttribute("y1", ay + Math.sin(ang) * 26);
    $("tt-astil").setAttribute("x2", px - Math.cos(ang) * 16); $("tt-astil").setAttribute("y2", py - Math.sin(ang) * 16);
    $("tt-mira").setAttribute("transform", `translate(${px},${py})`);
    $("tt-asa").setAttribute("cx", ax); $("tt-asa").setAttribute("cy", ay);
    $("tt-asa-flecha").setAttribute("d", `M0,-12l-7,9h14z`);
    $("tt-asa-flecha").setAttribute("transform", `translate(${ax},${ay}) rotate(${grados(ang) + 90})`);
    const marca = $("tt-marca"), chip = $("tt-lectura-snap"), fijo = $("tt-fijo");
    const conValores = hayFijos();
    if (puntero.snap && !conValores) {
      const [sx, sy] = P(puntero.snap.mundo);
      marca.setAttribute("transform", `translate(${sx},${sy})`);
      $("tt-marca-forma").setAttribute("d", formaSnap(puntero.snap.tipo));
      marca.setAttribute("visibility", "visible");
      const n = puntero.cands.length;
      chip.hidden = false;
      chip.textContent = (NOMBRES_SNAP[puntero.snap.tipo] || "Referencia") + (n > 1 ? ` · ${puntero.indice + 1} de ${n}` : "");
      chip.disabled = n < 2;
      chip.setAttribute("aria-label", n > 1 ? `${NOMBRES_SNAP[puntero.snap.tipo]}. Tocar para la siguiente referencia` : (NOMBRES_SNAP[puntero.snap.tipo] || "Referencia"));
    } else { marca.setAttribute("visibility", "hidden"); chip.hidden = true; }
    const m = puntoActual();
    // Con valores escritos, el punto que se pondrá (fijo) se marca aparte de la punta.
    if (conValores) { const [fx, fy] = P(m); fijo.setAttribute("transform", `translate(${fx},${fy})`); fijo.setAttribute("visibility", "visible"); }
    else fijo.setAttribute("visibility", "hidden");
    $("tt-imanes").dataset.apagado = refsActivas().length ? "0" : "1";
    pintarLectura(m);
  }
  // Ayudas para las vistas previas de las herramientas.
  function linea(g, pts, cerrada) {
    if (pts.length < 2) return;
    el(cerrada ? "polygon" : "polyline", { points: pts.map((p) => P(p).join(",")).join(" ") }, g);
  }
  function circulo(g, c, r) { const [x, y] = P(c); el("circle", { cx: x, cy: y, r: Math.max(0, r / vista.k) }, g); }
  function arcoPath(g, c, r, a0, a1) {
    // a0 → a1 en sentido antihorario (grados, como en el dibujo)
    let barrido = norm360(a1 - a0); if (barrido === 0) barrido = 360;
    const pt = (a) => P([c[0] + r * Math.cos((a * Math.PI) / 180), c[1] + r * Math.sin((a * Math.PI) / 180)]);
    const [x0, y0] = pt(a0), [x1, y1] = pt(a0 + barrido);
    const R = r / vista.k;
    el("path", { d: `M${x0},${y0}A${R},${R} 0 ${barrido > 180 ? 1 : 0} 0 ${x1},${y1}` }, g);
  }
  function texto(g, m, t) { const [x, y] = P(m); const e = el("text", { x: x + 8, y: y - 8, class: "tt-cota-texto" }, g); e.textContent = t; }

  // ---------------------------------------------------------------------------------------------------------------- entrada dinámica
  // La lectura encima de «Poner punto» muestra X, Y y, con la herramienta en marcha, los valores que importan (distancia y azimut desde
  // el último punto, radio, ancho y alto, ángulo de giro, distancia del desfase). Tocar uno abre el teclado propio debajo (el plano y la
  // punta siguen a la vista): lo escrito mueve la punta en vivo y queda fijo (candado) hasta poner el punto, como la entrada dinámica de
  // CAD. X/Y y los demás no se mezclan: fijar uno de un grupo suelta los del otro.
  const din = { fijos: {}, abierto: false, campo: null, texto: "", nuevo: true, firma: "" };
  const XY = ["x", "y"];
  const rad = (g) => (g * Math.PI) / 180;
  const norm180 = (g) => { const v = norm360(g); return v > 180 ? v - 360 : v; };
  function camposHerr() {
    if (!herr) return [];
    const { id, puntos } = herr, n = puntos.length, ult = puntos[n - 1];
    if (["linea", "polilinea", "mover", "copiar"].includes(id) && n >= 1)
      return [{ k: "d", et: "Distancia", v: (p) => dist(ult, p) }, { k: "az", et: "Azimut", ang: true, v: (p) => azimut(ult, p) }];
    if (id === "circulo" && n === 1) return [{ k: "r", et: "Radio", v: (p) => dist(puntos[0], p) }, { k: "diam", et: "Diámetro", v: (p) => 2 * dist(puntos[0], p) }];
    if (id === "rectangulo" && n === 1) return [{ k: "ancho", et: "Ancho", v: (p) => p[0] - puntos[0][0] }, { k: "alto", et: "Alto", v: (p) => p[1] - puntos[0][1] }];
    if (id === "girar" && n >= 1) return [{ k: "ang", et: "Ángulo", ang: true, v: (p) => norm180(n === 1 ? grados(Math.atan2(p[1] - puntos[0][1], p[0] - puntos[0][0])) : anguloGiro([...puntos, p])) }];
    if (id === "desfasar_lado") return [{ k: "dist", et: "Distancia", v: (p) => distanciaA(herr.extra.objeto.e, p) }];
    return [];
  }
  const campoXY = [{ k: "x", et: "X", v: (p) => p[0] }, { k: "y", et: "Y", v: (p) => p[1] }];
  const camposTodos = () => [...campoXY, ...camposHerr()];
  const hayFijos = () => Object.keys(din.fijos).length > 0;
  // El punto con los valores escritos aplicados (b: la referencia o la punta).
  function restringir(b) {
    const f = din.fijos;
    if (!hayFijos()) return b;
    const p = [f.x !== undefined ? f.x : b[0], f.y !== undefined ? f.y : b[1]];
    if (!herr) return p;
    const { id, puntos } = herr, n = puntos.length, ult = puntos[n - 1];
    const unidad = (a, c) => { const L = dist(a, c); return L > 0 ? [(c[0] - a[0]) / L, (c[1] - a[1]) / L] : [1, 0]; };
    if ((f.d !== undefined || f.az !== undefined) && ult) {
      const u = f.az !== undefined ? [Math.sin(rad(f.az)), Math.cos(rad(f.az))] : unidad(ult, b);
      const L = f.d !== undefined ? f.d : Math.max(0, (b[0] - ult[0]) * u[0] + (b[1] - ult[1]) * u[1]);
      return [ult[0] + L * u[0], ult[1] + L * u[1]];
    }
    if (id === "circulo" && n === 1 && (f.r !== undefined || f.diam !== undefined)) {
      const R = f.r !== undefined ? f.r : f.diam / 2, u = unidad(puntos[0], b);
      return [puntos[0][0] + R * u[0], puntos[0][1] + R * u[1]];
    }
    if (id === "rectangulo" && n === 1 && (f.ancho !== undefined || f.alto !== undefined)) {
      const a = puntos[0];
      return [f.ancho !== undefined ? a[0] + f.ancho : b[0], f.alto !== undefined ? a[1] + f.alto : b[1]];
    }
    if (id === "girar" && n >= 1 && f.ang !== undefined) {
      const c = puntos[0], base = n === 1 ? 0 : Math.atan2(puntos[1][1] - c[1], puntos[1][0] - c[0]);
      const L = Math.max(dist(c, b), (n === 2 ? dist(c, puntos[1]) : 0), vista.k * 60), a = base + rad(f.ang);
      return [c[0] + L * Math.cos(a), c[1] + L * Math.sin(a)];
    }
    return p;
  }
  function fijar(k, v) {
    const xy = XY.includes(k);
    for (const otro of Object.keys(din.fijos)) if (XY.includes(otro) !== xy) delete din.fijos[otro];
    if (k === "r") delete din.fijos.diam;
    if (k === "diam") delete din.fijos.r;
    din.fijos[k] = v;
  }
  function soltarValor(k) {
    delete din.fijos[k];
    if (din.campo === k) { din.texto = ""; din.nuevo = true; }
    pintar(); consultarSnap();
  }
  function soltarTodo() { din.fijos = {}; din.texto = ""; din.nuevo = true; }
  function abrirDinamica(k) {
    if (!puntero.visible) return;
    if (!camposTodos().some((c) => c.k === k)) return;
    cerrarTarjeta();
    din.abierto = true; din.campo = k; din.texto = ""; din.nuevo = true;
    $("tt-din").hidden = false; $("tt-accion").hidden = true; $("tt-pestanas").hidden = true; $("tt-seleccion").hidden = true;
    $("tt-din-poner").textContent = herr ? $("tt-confirmar-texto").textContent : "Listo";
    pintarSelectorDin();
    pintar();
    // El teclado ocupa el lugar de la barra y el área de dibujo se achica: la punta debe quedar a la vista.
    setTimeout(() => { if (din.abierto) { const m = puntoActual(); enfocar(m[0], m[1]); } }, 450);
  }
  function cerrarDinamica() {
    if (!din.abierto) return;
    din.abierto = false; din.campo = null;
    $("tt-din").hidden = true;
    if (herr) $("tt-accion").hidden = false;
    else if (seleccion.size) $("tt-seleccion").hidden = false;
    else $("tt-pestanas").hidden = false;
    pintar();
  }
  function teclaDinamica(t) {
    if (!din.abierto) return;
    if (t === "poner") { if (herr) confirmarPunto(); else cerrarDinamica(); return; }
    if (t === "cerrar") { cerrarDinamica(); return; }
    if (t === "soltar") { soltarValor(din.campo); return; }
    if (t === "siguiente") {
      const ks = camposTodos().map((c) => c.k), i = ks.indexOf(din.campo);
      din.campo = ks[(i + 1) % ks.length]; din.texto = ""; din.nuevo = true; pintar(); return;
    }
    let v = din.nuevo ? "" : din.texto;
    din.nuevo = false;
    if (/^\d$/.test(t)) v += t;
    else if (t === ".") { if (!v.includes(".")) v = (v.replace("-", "") ? v : v + "0") + "."; }
    else if (t === "borrar") v = v.slice(0, -1);
    else if (t === "signo") v = v.startsWith("-") ? v.slice(1) : "-" + v;
    else return;
    din.texto = v.slice(0, 18);
    const num = Number(din.texto);
    if (/\d/.test(din.texto) && Number.isFinite(num)) fijar(din.campo, num); else delete din.fijos[din.campo];
    // La punta va al punto escrito (y la vista lo sigue si sale de la pantalla).
    const m = puntoActual();
    puntero.mundo = m.slice();
    pintar();
    enfocar(m[0], m[1]).then(() => consultarSnap());
  }
  // Tras poner un punto: los valores fijos se sueltan; el teclado sigue abierto en el primer valor de la herramienta.
  function despuesDePoner() {
    soltarTodo();
    if (!din.abierto) return;
    if (!herr) { cerrarDinamica(); return; }
    const ks = camposTodos().map((c) => c.k);
    if (!ks.includes(din.campo) || camposHerr().length) din.campo = (camposHerr()[0] || campoXY[0]).k;
  }
  // Las fichas de la lectura: se rehacen solo si cambia cuáles hay; los números se cambian en cada cuadro.
  function pintarLectura(m) {
    const cs = camposTodos();
    const firma = cs.map((c) => c.k).join(",");
    const fila = $("tt-valores");
    if (firma !== din.firma) {
      din.firma = firma;
      pintarSelectorDin();
      $("tt-valores-xy").replaceChildren(...cs.filter((c) => XY.includes(c.k)).map(ficha));
      fila.replaceChildren(...cs.filter((c) => !XY.includes(c.k)).map(ficha));
    }
    fila.hidden = !fila.childElementCount;
    for (const c of cs) {
      const f = document.querySelector(`.tt-valor[data-campo="${c.k}"]`);
      if (!f) continue;
      const fijo = din.fijos[c.k] !== undefined, editando = din.abierto && din.campo === c.k;
      f.dataset.fijo = fijo ? "1" : "0"; f.dataset.editando = editando ? "1" : "0";
      const texto = editando && !din.nuevo ? (din.texto || " ") : (c.ang ? `${fmt(fijo ? din.fijos[c.k] : c.v(m), 4)}°` : fmt(fijo ? din.fijos[c.k] : c.v(m)));
      f.querySelector(".tt-valor__v").textContent = texto;
      f.querySelector(".tt-valor__quitar").hidden = !fijo;
    }
    if (din.abierto) {
      $("tt-din-soltar").disabled = din.fijos[din.campo] === undefined;
      for (const b of $("tt-din-campos").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.campo === din.campo));
    }
    const delta = $("tt-delta");
    const ult = herr && herr.puntos.length ? herr.puntos[herr.puntos.length - 1] : null;
    if (ult && herr.def.delta !== false && !camposHerr().length) {
      delta.hidden = false;
      delta.textContent = `Δ ${fmt(Math.hypot(m[0] - ult[0], m[1] - ult[1]))} m · ${fmt(azimut(ult, m), 2)}°`;
    } else delta.hidden = true;
  }
  function ficha(c) {
    const f = nodo("span", "tt-valor"); f.dataset.campo = c.k;
    const b = nodo("button", "tt-valor__b"); b.type = "button";
    b.setAttribute("aria-label", `Escribir ${c.et}`);
    const v = nodo("span", "tt-valor__v");
    if (c.k === "x") v.id = "tt-x"; if (c.k === "y") v.id = "tt-y";
    b.append(nodo("span", "tt-et", c.et), v, icono("lock"));
    b.addEventListener("click", () => abrirDinamica(c.k));
    const q = nodo("button", "tt-valor__quitar"); q.type = "button"; q.hidden = true;
    q.setAttribute("aria-label", `Soltar ${c.et}`); q.append(icono("x"));
    q.addEventListener("click", () => soltarValor(c.k));
    f.append(b, q);
    return f;
  }
  // En el teclado, el selector de qué valor se escribe.
  function pintarSelectorDin() {
    const cont = $("tt-din-campos");
    cont.replaceChildren(...camposTodos().map((c) => {
      const b = nodo("button", "", c.et); b.type = "button"; b.dataset.campo = c.k;
      b.addEventListener("click", () => { din.campo = c.k; din.texto = ""; din.nuevo = true; pintar(); });
      return b;
    }));
  }
  $("tt-din").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-d]"); if (!b) return;
    teclaDinamica(b.dataset.d);
  });
  $("tt-lectura-snap").addEventListener("click", siguienteReferencia);
  $("tt-imanes").addEventListener("click", hojaReferencias);
  document.addEventListener("keydown", (ev) => {
    if (!din.abierto || num) return;
    const mapa = { Backspace: "borrar", Enter: "poner", Tab: "siguiente", "-": "signo", ".": ".", ",": ".", Escape: "cerrar" };
    const t = /^\d$/.test(ev.key) ? ev.key : mapa[ev.key];
    if (!t) return;
    ev.preventDefault(); ev.stopPropagation();
    teclaDinamica(t);
  }, true);
  // Distancia de un punto a un objeto (para el desfase en vivo).
  function distanciaA(e, p) {
    const t = tipoDe(e), g = e.geometry || e;
    if (t === "Circle" || t === "Arc") return Math.abs(dist(p, g.center) - g.radius);
    const seg = segmentoCercano(e, p);
    return seg ? dist(p, pie(p, seg[0], seg[1])) : NaN;
  }

  // ---------------------------------------------------------------------------------------------------------------- avisos y tarjetas
  let avisoT = null;
  function aviso(t, tipo, ms = 2800) {
    const a = $("tt-aviso"); a.textContent = t; a.dataset.tipo = tipo || ""; a.hidden = false;
    clearTimeout(avisoT); avisoT = setTimeout(() => { a.hidden = true; }, ms);
  }
  let cerrarTarjetaSiMueve = false, tarjetaTexto = "";
  function tarjeta(titulo, filas) {
    $("tt-tarjeta-titulo").textContent = titulo;
    const dl = $("tt-tarjeta-datos"); dl.replaceChildren();
    for (const [k, v] of filas) { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = k; dd.textContent = v; dl.append(dt, dd); }
    tarjetaTexto = [titulo, ...filas.map(([k, v]) => `${k}: ${v}`)].join("\n");
    $("tt-tarjeta").hidden = false; $("tt-lectura").hidden = true;
  }
  function cerrarTarjeta() {
    if ($("tt-tarjeta").hidden) return false;
    $("tt-tarjeta").hidden = true; if (puntero.visible) $("tt-lectura").hidden = false;
    return true;
  }
  $("tt-tarjeta-cerrar").addEventListener("click", cerrarTarjeta);
  $("tt-tarjeta-copiar").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(tarjetaTexto); aviso("Copiado"); }
    catch (_) { aviso("No se pudo copiar en este navegador.", "error"); }
  });
  function guia(t) { const g = $("tt-guia"); g.hidden = !t; g.textContent = t || ""; }
  function icono(nombre) {
    if (nombre.startsWith("#")) {
      const s = document.createElementNS(NS, "svg"); s.setAttribute("class", "tv-icono"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
      const u = document.createElementNS(NS, "use"); u.setAttribute("href", nombre); s.append(u); return s;
    }
    const p = document.querySelector(`#tt-iconos [data-tt-icono="${nombre}"] svg`);
    return p ? p.cloneNode(true) : document.createTextNode("");
  }

  // ---------------------------------------------------------------------------------------------------------------- estado del plano
  const est = { editable: false, presentacion: false, documento: null };
  function reflejarEstado() {
    const chip = $("cad-estado"), texto = $("cad-estado-texto"), g = $("cad-guardar");
    if (chip) tt.dataset.estado = chip.dataset.estado || "cargando";
    if (texto) $("tt-sub-texto").textContent = texto.textContent;
    const nombre = $("cad-nombre");
    if (nombre) $("tt-nombre").textContent = nombre.textContent;
    $("tt-guardar").disabled = !g || g.disabled;
    const lectura = !est.editable;
    for (const b of document.querySelectorAll(".tt-pestanas button")) {
      const solo = ["dibujar", "editar", "cota", "texto"].includes(b.dataset.grupo);
      b.setAttribute("aria-disabled", String(solo && (lectura || est.presentacion)));
    }
    $("tt-deshacer").disabled = lectura || !listo;
    $("tt-rehacer").disabled = lectura || !listo;
  }
  const observador = new MutationObserver(reflejarEstado);
  for (const id of ["cad-estado", "cad-estado-texto", "cad-guardar", "cad-nombre"]) {
    const e = $(id); if (e) observador.observe(e, { attributes: true, childList: true, characterData: true, subtree: true });
  }
  document.addEventListener("tvcad:editable", (ev) => {
    est.editable = !!(ev.detail && ev.detail.editable);
    if (!est.editable && herr && herr.def.edita) { cancelarHerramienta(); aviso("Ya no puedes editar este plano: lo que estabas dibujando se canceló.", "error", 5000); }
    reflejarEstado();
  });

  // ---------------------------------------------------------------------------------------------------------------- entrar y salir del modo
  let activo = false, listo = false, preparando = null;
  const TABLETA = matchMedia("(min-width: 768px)");
  const TACTIL_SOLO = matchMedia("(pointer: coarse)").matches && !matchMedia("(any-pointer: fine)").matches;
  function quiereTactil() {
    if (CAMPO) return true;
    const v = guardado.leer("tactil", null);
    return v === null ? TACTIL_SOLO : !!v;
  }
  // El marco del editor en modo táctil: solo el área de dibujo («solo lienzo» del editor, op `canvas_only`, build tv-v2026.45-6). Es un
  // estado de esta sesión que el editor nunca guarda: el editor completo en este dispositivo queda como estaba. (Ocultar sus barras con
  // órdenes como MENUBAR o FILETAB sí se guardaría y se colaría en el editor completo: no se usan.) El área de dibujo del editor ya es
  // oscura (sigue su tema), como pidió el dueño.
  // El editor puede abrir un aviso propio (p. ej. «sin GPU»): en modo táctil no se puede tocar, así que se cierra.
  async function cerrarAvisoDelEditor() {
    const st = await pedirSuave({ op: "state" }, 3000);
    if (st && st.modal) await pedirSuave({ op: "action", name: "close_modal" }, 3000);
  }
  setInterval(() => { if (activo && listo && document.visibilityState === "visible") cerrarAvisoDelEditor(); }, 1500);
  async function prepararEditor() {
    await cerrarAvisoDelEditor();
    const r = await pedirSuave({ op: "canvas_only", on: true });
    if (!r) console.warn("El editor fijado no tiene la opción «solo lienzo» (hace falta tv-v2026.45-6 o posterior).");
    medirZona();
    await espera(120);
    await ajustarRecorte().catch(() => {});
  }
  async function restaurarEditor() {
    await pedirSuave({ op: "canvas_only", on: false });
  }
  // Lo que sabe hacer el editor en uso. Las referencias nunca tocan OSMODE (cambiar esa variable marcaría el plano «Sin guardar»): van
  // en cada petición `snap` (`modes`) o, con un editor que no las entiende, se filtran aquí. Explotar, recortar, alargar y las cotas a la
  // escala de la vista solo se ofrecen si el editor tiene sus ops (el de una copia vieja guardada en el teléfono puede no tenerlas).
  const capac = { modos: false, explotar: false, recortar: false, alargar: false, cotas: false };
  const tieneOp = async (peticion) => {
    try { await pedir(peticion, 8000); return true; }
    catch (err) { return !(err && err.codigo === "rechazada" && err.respuesta && err.respuesta.code === "unknown_operation") && !!(err && err.codigo === "rechazada"); }
  };
  async function leerCapacidades() {
    const st = await pedirSuave({ op: "state" }, 5000);
    const lista = new Set((st && st.capabilities) || []);
    // `modes`: el editor que lo entiende responde `hits` cuando se pide `limit` (también sin referencia cerca).
    if (lista.has("snap_modes")) capac.modos = true;
    else {
      const v = st ? deEstado({}, st) : vista;
      const r = await pedirSuave({ op: "snap", point: [v.cx, v.cy], modes: ["Endpoint"], limit: 2 }, 5000);
      capac.modos = !!(r && Array.isArray(r.hits));
    }
    // Las demás: una petición que un editor con la op rechaza por sus datos (nunca cambia el dibujo) y uno sin ella, por desconocida.
    capac.explotar = lista.has("explode") || await tieneOp({ op: "explode", handles: ["zz"] });
    capac.recortar = lista.has("trim") || await tieneOp({ op: "trim", point: "?" });
    capac.alargar = lista.has("extend") || await tieneOp({ op: "extend", point: "?" });
    capac.cotas = lista.has("dim_create") || (await tieneOp({ op: "dim_style" }) && await tieneOp({ op: "dim_create", kind: "?" }));
    for (const b of document.querySelectorAll("[data-requiere]")) b.hidden = !capac[b.dataset.requiere];
  }
  async function entrar(recordar) {
    if (recordar) guardado.poner("tactil", true);
    if (activo) return;
    activo = true;
    raiz.classList.add("cad-tactil");
    tt.hidden = false;
    medirZona();
    reflejarEstado();
    await window.TVCad.listo;
    if (!activo) return;
    if (!preparando) preparando = (async () => {
      const st = window.TVCad.estado();
      est.editable = !!st.editable;
      await prepararEditor();
      await leerCapacidades();
      await verTodo();
      await fijarVista();                // primer cuadro: el editor vuelve a pintar a su tamaño final (en vertical quedaba en blanco)
      listo = true;
      reflejarEstado();
      await leerPresentacion();
      tt.dataset.listo = "1";
    })().finally(() => { preparando = null; });
    await preparando;
  }
  async function salir(recordar) {
    if (recordar) guardado.poner("tactil", false);
    if (!activo) return;
    cancelarHerramienta(); quitarSeleccion(); ocultarPuntero(); cerrarHojas();
    activo = false; listo = false;
    delete tt.dataset.listo;
    tt.hidden = true;
    raiz.classList.remove("cad-tactil");
    iframe.style.transform = "";
    raiz.style.removeProperty("--tt-recorte");
    await restaurarEditor();
  }
  document.addEventListener("tvcad:cargado", async () => {
    // Al reabrir el plano (tomar el control, restaurar, recuperar) el editor es otro: se vuelve a preparar.
    if (!activo) return;
    listo = false; delete tt.dataset.listo; estiloCota = null;
    cancelarHerramienta(); quitarSeleccion(); ocultarPuntero();
    await espera(50);
    activo = false; await entrar(false);
  });

  // ---------------------------------------------------------------------------------------------------------------- barra superior
  $("tt-cerrar").addEventListener("click", () => { const v = $("cad-volver"); if (v) v.click(); });
  $("tt-deshacer").addEventListener("click", async () => {
    if (herr && herr.puntos.length) { herr.puntos.pop(); actualizarHerramienta(); return; }
    const r = await pedirSuave({ op: "undo" });
    if (!r) aviso("No hay nada que deshacer."); else { quitarSeleccion(); pintar(); }
  });
  $("tt-rehacer").addEventListener("click", async () => {
    const r = await pedirSuave({ op: "redo" });
    if (!r) aviso("No hay nada que rehacer.");
  });
  $("tt-guardar").addEventListener("click", () => {
    if (herr && herr.def.edita && herr.puntos.length) { aviso("Termina o cancela lo que estás dibujando antes de guardar."); return; }
    const g = $("cad-guardar"); if (g && !g.disabled) g.click();
  });
  $("tt-mas").addEventListener("click", hojaMas);

  // ---------------------------------------------------------------------------------------------------------------- hojas
  let hojaAbierta = null;
  function abrirHoja(titulo, contenido, { grupo } = {}) {
    cerrarHojas();
    $("tt-hoja-titulo").textContent = titulo;
    $("tt-hoja-cuerpo").replaceChildren(...[].concat(contenido));
    $("tt-hoja").hidden = false; $("tt-velo").hidden = false;
    hojaAbierta = grupo || titulo;
    for (const b of document.querySelectorAll(".tt-pestanas button")) b.setAttribute("aria-expanded", String(b.dataset.grupo === grupo));
    const primero = $("tt-hoja-cuerpo").querySelector("button:not(:disabled), input");
    if (primero && matchMedia("(any-pointer: fine)").matches) primero.focus();
  }
  function cerrarHojas() {
    $("tt-hoja").hidden = true; $("tt-velo").hidden = true; hojaAbierta = null;
    for (const b of document.querySelectorAll(".tt-pestanas button")) b.setAttribute("aria-expanded", "false");
    if (num) cerrarNumerica(null);
  }
  $("tt-velo").addEventListener("click", cerrarHojas);
  document.querySelector("#tt-hoja [data-tt-cerrar]").addEventListener("click", cerrarHojas);
  document.addEventListener("keydown", (ev) => {
    if (!activo) return;
    if (ev.key === "Escape") { if (!$("tt-hoja").hidden || num) cerrarHojas(); else if (herr) cancelarHerramienta(); else if (seleccion.size) quitarSeleccion(); }
  });
  function nodo(tag, clase, texto) { const e = document.createElement(tag); if (clase) e.className = clase; if (texto !== undefined) e.textContent = texto; return e; }
  function boton(et, ic, fn, { clase = "tt-herr", deshabilitado = false, nota } = {}) {
    const b = nodo("button", clase); b.type = "button";
    if (ic) b.append(icono(ic));
    b.append(nodo("span", "", et));
    if (deshabilitado) { b.disabled = true; if (nota) b.title = nota; }
    b.addEventListener("click", fn);
    return b;
  }
  function rejilla(botones) { const r = nodo("div", "tt-rejilla"); r.append(...botones); return r; }

  for (const b of document.querySelectorAll(".tt-pestanas button")) {
    b.addEventListener("click", () => {
      if (b.getAttribute("aria-disabled") === "true") {
        aviso(est.presentacion ? "En una presentación solo se ve y se mide. Vuelve a Modelo para dibujar." : (lecturaTexto() || "Este plano está en solo lectura."));
        return;
      }
      if (hojaAbierta === b.dataset.grupo) { cerrarHojas(); return; }
      abrirGrupo(b.dataset.grupo);
    });
  }
  function lecturaTexto() { const t = $("cad-aviso-lectura-texto"); return t && t.textContent.trim(); }
  function abrirGrupo(g) {
    if (g === "capas") return hojaCapas();
    const lista = Object.entries(HERR).filter(([, d]) => d.grupo === g && (!d.requiere || capac[d.requiere]));
    const botones = lista.map(([id, d]) => {
      const falta = d.seleccion && !seleccion.size;
      return boton(d.et, d.ic, () => { cerrarHojas(); empezar(id); }, { deshabilitado: falta, nota: falta ? "Primero toca un objeto para elegirlo" : "" });
    });
    const titulos = { dibujar: "Dibujar", editar: "Editar", medir: "Medir", cota: "Cota", texto: "Texto" };
    const cont = [rejilla(botones)];
    if (g === "editar" && !seleccion.size) cont.unshift(nodo("p", "tt-nota-hoja", "Toca un objeto para elegirlo, o usa «Seleccionar área». Luego elige qué hacer."));
    if (g === "medir") cont.push(filaDecimales());
    abrirHoja(titulos[g], cont, { grupo: g });
  }
  function filaDecimales() {
    const cont = nodo("div");
    cont.append(nodo("h3", "", "Decimales"));
    const seg = nodo("div", "tt-segmentado");
    for (let d = 0; d <= 4; d++) {
      const b = nodo("button", "", String(d)); b.type = "button"; b.setAttribute("aria-pressed", String(pref.decimales === d));
      b.addEventListener("click", () => { pref.decimales = d; guardado.poner("decimales", d); seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); pintar(); });
      seg.append(b);
    }
    cont.append(seg);
    return cont;
  }

  // ---------------------------------------------------------------------------------------------------------------- hoja numérica
  // pedirValores({titulo, campos: [{k, et, sugerido, tipo: "num"|"texto"|"ang"}], listo, validar}) → Promise<{k: valor}|null>
  let num = null;
  function pedirValores({ titulo, campos, listo: textoListo = "Listo", validar }) {
    cerrarHojas();
    return new Promise((resolver) => {
      num = { campos: Object.fromEntries(campos.filter((c) => c.tipo !== "texto").map((c) => [c.k, c.valor !== undefined ? String(c.valor) : ""])), defs: campos,
        actual: (campos.find((c) => c.tipo !== "texto") || {}).k, resolver, validar };
      $("tt-num-titulo").textContent = titulo;
      $("tt-num-listo").textContent = textoListo;
      const textoDef = campos.find((c) => c.tipo === "texto");
      $("tt-texto-campo").hidden = !textoDef;
      $("tt-num").classList.toggle("tt-num--texto", !!textoDef && campos.length === 1);
      if (textoDef) { $("tt-texto").value = textoDef.valor || ""; $("tt-texto-campo").querySelector("label").textContent = textoDef.et; }
      $("tt-campos").replaceChildren(...campos.filter((c) => c.tipo !== "texto").map((c) => {
        const b = nodo("button", "tt-campo"); b.type = "button"; b.dataset.campo = c.k;
        b.append(nodo("span", "tt-campo__et", c.et));
        const v = nodo("span", "tt-campo__v"); v.dataset.vacio = c.sugerido !== undefined ? String(c.sugerido) : "";
        b.append(v);
        b.addEventListener("click", () => { num.actual = c.k; pintarNum(); });
        return b;
      }));
      $("tt-num-error").hidden = true;
      $("tt-num").hidden = false; $("tt-velo").hidden = false;
      pintarNum();
      if (textoDef) setTimeout(() => $("tt-texto").focus(), 60);
    });
  }
  function pintarNum() {
    document.querySelectorAll(".tt-campo").forEach((b) => {
      b.setAttribute("aria-current", String(b.dataset.campo === num.actual && $("tt-texto") !== document.activeElement));
      b.querySelector(".tt-campo__v").textContent = num.campos[b.dataset.campo];
    });
  }
  function cerrarNumerica(valor) {
    if (!num) return;
    const r = num.resolver; num = null;
    $("tt-num").hidden = true; if ($("tt-hoja").hidden) $("tt-velo").hidden = true;
    $("tt-texto").blur();
    r(valor);
  }
  $("tt-num-cerrar").addEventListener("click", () => cerrarNumerica(null));
  $("tt-texto").addEventListener("focus", () => num && pintarNum());
  $("tt-texto").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); aceptarNum(); } });
  $("tt-teclado").addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b || !num) return;
    const t = b.dataset.t;
    if (t === "listo") { aceptarNum(); return; }
    if (!num.actual) return;
    if (document.activeElement === $("tt-texto")) $("tt-texto").blur();
    const k = num.actual; let v = num.campos[k];
    if (/^\d$/.test(t)) v += t;
    else if (t === ".") { if (!v.includes(".")) v = (v.replace("-", "") ? v : v + "0") + "."; }
    else if (t === "borrar") v = v.slice(0, -1);
    else if (t === "signo") v = v.startsWith("-") ? v.slice(1) : "-" + v;
    else if (t === "siguiente") {
      const ks = Object.keys(num.campos); num.actual = ks[(ks.indexOf(k) + 1) % ks.length]; pintarNum(); return;
    }
    num.campos[k] = v.slice(0, 18); pintarNum();
    $("tt-num-error").hidden = true;
  });
  // Teclado físico (tableta con teclado): números, punto, menos, borrar, Tab y Enter.
  document.addEventListener("keydown", (ev) => {
    if (!num || document.activeElement === $("tt-texto")) return;
    const mapa = { Backspace: "borrar", Enter: "listo", Tab: "siguiente", "-": "signo", ".": ".", ",": "." };
    const t = /^\d$/.test(ev.key) ? ev.key : mapa[ev.key];
    if (!t) return;
    ev.preventDefault();
    const b = $("tt-teclado").querySelector(`[data-t="${t === "." ? "." : t}"]`);
    if (b) b.click();
  });
  function aceptarNum() {
    const valores = {};
    for (const c of num.defs) {
      if (c.tipo === "texto") { valores[c.k] = $("tt-texto").value.trim(); continue; }
      const s = num.campos[c.k];
      valores[c.k] = s === "" || s === "-" ? (c.sugerido !== undefined && c.sugerido !== "" ? Number(String(c.sugerido).replace(/[  ]/g, "").replace("−", "-")) : NaN) : Number(s);
    }
    const err = num.validar ? num.validar(valores) : null;
    if (err) {
      const [campo, texto] = Array.isArray(err) ? err : [null, err];
      $("tt-num-error").textContent = texto; $("tt-num-error").hidden = false;
      if (campo && campo in num.campos) { num.actual = campo; pintarNum(); }
      return;
    }
    cerrarNumerica(valores);
  }

  // ---------------------------------------------------------------------------------------------------------------- geometría
  const resta = (a, b) => [a[0] - b[0], a[1] - b[1]];
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  function circunferencia(a, b, c) {
    const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
    if (Math.abs(d) < 1e-12) return null;
    const a2 = a[0] ** 2 + a[1] ** 2, b2 = b[0] ** 2 + b[1] ** 2, c2 = c[0] ** 2 + c[1] ** 2;
    // Centrado en el primer punto para no perder precisión con coordenadas UTM.
    const [ox, oy] = a, A = [0, 0], B = [b[0] - ox, b[1] - oy], Cc = [c[0] - ox, c[1] - oy];
    const D = 2 * (A[0] * (B[1] - Cc[1]) + B[0] * (Cc[1] - A[1]) + Cc[0] * (A[1] - B[1]));
    if (Math.abs(D) < 1e-12) return null;
    const B2 = B[0] ** 2 + B[1] ** 2, C2 = Cc[0] ** 2 + Cc[1] ** 2;
    const ux = (B2 * (Cc[1] - A[1]) + C2 * (A[1] - B[1])) / D, uy = (B2 * (A[0] - Cc[0]) + C2 * (B[0] - A[0])) / D;
    void a2; void b2; void c2;
    return { c: [ux + ox, uy + oy], r: Math.hypot(ux, uy) };
  }
  function arco3(a, b, c) {
    const k = circunferencia(a, b, c);
    if (!k) return null;
    const ang = (p) => norm360(grados(Math.atan2(p[1] - k.c[1], p[0] - k.c[0])));
    const aa = ang(a), ab = ang(b), ac = ang(c);
    const ccw = norm360(ab - aa) < norm360(ac - aa);                  // b está entre a y c yendo antihorario
    return ccw ? { ...k, ini: aa, fin: ac } : { ...k, ini: ac, fin: aa };
  }
  function areaPerimetro(pts) {
    let a = 0, p = 0; const [ox, oy] = pts[0];
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
      a += (x1 - ox) * (y2 - oy) - (x2 - ox) * (y1 - oy);
      p += Math.hypot(x2 - x1, y2 - y1);
    }
    return { area: Math.abs(a) / 2, perimetro: p };
  }
  function pie(p, a, b) {
    const [dx, dy] = resta(b, a); const L2 = dx * dx + dy * dy;
    if (L2 === 0) return a.slice();
    const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2;
    return [a[0] + t * dx, a[1] + t * dy];
  }

  // ---------------------------------------------------------------------------------------------------------------- objetos del dibujo
  const NOMBRE_TIPO = { Line: "Línea", Circle: "Círculo", Arc: "Arco", LwPolyline: "Polilínea", Polyline: "Polilínea", Point: "Punto", Text: "Texto",
    MText: "Texto de párrafo", Insert: "Bloque", Dimension: "Cota", Hatch: "Sombreado", Spline: "Curva", Ellipse: "Elipse", Solid: "Sólido",
    Leader: "Directriz", Face3d: "Cara 3D" };
  function tipoDe(e) {
    const t = String(e.type || e.kind || "").replace(/\s+/g, "");
    if (/^(lw)?polyline$/i.test(t)) return "LwPolyline";                    // el editor nombra «Polyline» a la polilínea 2D
    for (const k of Object.keys(NOMBRE_TIPO)) if (t.toLowerCase() === k.toLowerCase()) return k;
    if (/polyline/i.test(t)) return "LwPolyline";
    if (/dimension/i.test(t)) return "Dimension";
    if (/text/i.test(t)) return /m/i.test(t[0]) ? "MText" : "Text";
    return t;
  }
  const lista = (r) => (Array.isArray(r) ? r : (r && (r.entities || r.items || r.results)) || []);
  const cajaDe = (e) => {
    const b = e.bounds || (e.properties && e.properties.bounds);
    if (!b) return null;
    if (Array.isArray(b) && b.length === 4 && typeof b[0] === "number") return b;
    if (b.min && b.max) return [b.min[0], b.min[1], b.max[0], b.max[1]];
    if (Array.isArray(b) && b.length === 2) return [b[0][0], b[0][1], b[1][0], b[1][1]];
    return null;
  };
  // El objeto más cercano a un punto de la pantalla, dentro de una tolerancia en píxeles.
  // `query near` devuelve los objetos por distancia (con `distance`), también los que viven dentro de las definiciones de bloques: solo
  // cuentan los del espacio que se ve.
  const delEspacio = (e) => !!(e && e.handle && (est.presentacion ? /^\*Paper_Space/i.test(e.space || "") : e.space === "*Model_Space"));
  async function objetoEn(m, tolPx = 14, detalle = "geometry") {
    const t = tolPx * vista.k;
    const r = await pedirSuave({ op: "query", near: [m[0], m[1]], bounds: [m[0] - t, m[1] - t, m[0] + t, m[1] + t], limit: 12, detail: "summary" });
    const e = lista(r).find((x) => delEspacio(x) && (x.distance === undefined || x.distance <= t));
    if (!e) return null;
    if (detalle === "summary") return e;
    const [d] = await detalleDe([e.handle], detalle);
    return d || e;
  }
  // Longitud y área que calcula el editor (`measure`): {length, area} o null.
  async function medida(handle) {
    const m = await pedirSuave({ op: "measure", handles: [handle] });
    const x = m && m.measurements && m.measurements[0];
    const c = x && (x.curve || x);
    return c ? { length: c.length, area: c.bounded || c.closed ? c.area : undefined, closed: !!c.closed } : null;
  }
  async function detalleDe(handles, detalle = "full") {
    const r = await pedirSuave({ op: "query", handles, detail: detalle, limit: Math.max(1, handles.length) });
    return lista(r);
  }

  // ---------------------------------------------------------------------------------------------------------------- selección
  const seleccion = new Map();     // handle → {handle, tipo, caja, capa}
  async function seleccionarEn(x, y) {
    const e = await objetoEn(aMundo(x, y), 14, "full");
    if (!e) { if (seleccion.size) quitarSeleccion(); return; }
    if (seleccion.has(e.handle)) seleccion.delete(e.handle);
    else seleccion.set(e.handle, { handle: e.handle, tipo: tipoDe(e), caja: cajaDe(e), capa: e.layer, e });
    await reflejarSeleccion();
  }
  async function reflejarSeleccion() {
    await pedirSuave({ op: "select", handles: [...seleccion.keys()], clear: true });
    const n = seleccion.size;
    $("tt-seleccion").hidden = !n || !!herr || din.abierto;
    $("tt-pestanas").hidden = (!!n || !!herr || din.abierto);
    $("tt-seleccion-cuenta").textContent = n === 1 ? `1 objeto elegido · ${NOMBRE_TIPO[[...seleccion.values()][0].tipo] || "Objeto"}` : `${n} objetos elegidos`;
    for (const b of document.querySelectorAll("#tt-seleccion-fila [data-editar]")) {
      const unico = b.dataset.editar === "desfasar" || b.dataset.editar === "info";
      if (b.dataset.requiere) b.hidden = !capac[b.dataset.requiere];
      b.disabled = !n || (unico && n !== 1) || (!est.editable && !["info"].includes(b.dataset.editar));
    }
    pintar();
  }
  function quitarSeleccion() {
    if (!seleccion.size) { $("tt-seleccion").hidden = true; if (!herr) $("tt-pestanas").hidden = false; return; }
    seleccion.clear();
    pedirSuave({ op: "select", handles: [], clear: true });
    $("tt-seleccion").hidden = true; if (!herr) $("tt-pestanas").hidden = false;
    pintar();
  }
  $("tt-seleccion-quitar").addEventListener("click", quitarSeleccion);
  for (const b of document.querySelectorAll("#tt-seleccion-fila [data-editar]")) {
    b.addEventListener("click", () => {
      const a = b.dataset.editar;
      if (a === "borrar") return borrarSeleccion();
      if (a === "propiedades") return hojaPropiedades();
      if (a === "info") return infoSeleccion();
      if (a === "explotar") return explotar();
      empezar(a);
    });
  }
  // Seleccionar por recuadro: izquierda → derecha, solo lo que queda dentro (azul, lleno); derecha → izquierda, lo que toca (verde,
  // punteado), como en CAD. La esquina de inicio queda fija en el dibujo: si el plano se corre solo junto al borde, el recuadro crece.
  function empezarVentana(x, y) { gesto = { tipo: "ventana", m0: aMundo(x, y), x, y }; pintarVentana(); }
  function pintarVentana() {
    const r = $("tt-ventana");
    if (!gesto || gesto.tipo !== "ventana") { r.setAttribute("visibility", "hidden"); return; }
    const [x0, y0] = aPantalla(gesto.m0[0], gesto.m0[1]);
    const x = Math.min(x0, gesto.x), y = Math.min(y0, gesto.y);
    r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("width", Math.abs(gesto.x - x0)); r.setAttribute("height", Math.abs(gesto.y - y0));
    r.dataset.cruce = gesto.x < x0 ? "1" : "0";
    r.setAttribute("visibility", "visible");
  }
  function ocultarVentana() { $("tt-ventana").setAttribute("visibility", "hidden"); }
  // La línea que se traza en «Recortar» y «Alargar».
  function pintarCerca() {
    const l = $("tt-cerca");
    if (!gesto || gesto.tipo !== "cerca") { l.setAttribute("visibility", "hidden"); return; }
    const [x0, y0] = aPantalla(gesto.m0[0], gesto.m0[1]);
    l.setAttribute("x1", x0); l.setAttribute("y1", y0); l.setAttribute("x2", gesto.x); l.setAttribute("y2", gesto.y);
    l.setAttribute("visibility", "visible");
  }
  function ocultarCerca() { $("tt-cerca").setAttribute("visibility", "hidden"); }
  function modoArea(si) {
    modoVentana = si;
    guia(si ? "Arrastra un rectángulo. De izquierda a derecha: lo que queda dentro. De derecha a izquierda: lo que toca." : "");
  }
  async function terminarVentana(g) {
    ocultarVentana(); if (modoVentana) modoArea(false);
    if (fijandoAuto) { fijandoAuto = false; await fijarVista(); }
    const [x0, y0] = aPantalla(g.m0[0], g.m0[1]);
    if (Math.abs(g.x - x0) < 6 || Math.abs(g.y - y0) < 6) return;
    const fin = aMundo(g.x, g.y);
    const a = [Math.min(g.m0[0], fin[0]), Math.min(g.m0[1], fin[1])], b = [Math.max(g.m0[0], fin[0]), Math.max(g.m0[1], fin[1])];
    const cruce = g.x < x0;
    const r = await pedirSuave({ op: "query", bounds: [a[0], a[1], b[0], b[1]], detail: "full", limit: 5000 }, 60000);
    let n = 0;
    for (const e of lista(r)) {
      if (!delEspacio(e)) continue;
      const caja = cajaDe(e);
      if (!cruce && caja && !(caja[0] >= a[0] && caja[1] >= a[1] && caja[2] <= b[0] && caja[3] <= b[1])) continue;
      if (!seleccion.has(e.handle)) n += 1;
      seleccion.set(e.handle, { handle: e.handle, tipo: tipoDe(e), caja, capa: e.layer, e });
    }
    if (!n) aviso("No hay más objetos en esa área.");
    await reflejarSeleccion();
  }
  async function borrarSeleccion() {
    if (!est.editable) return;
    const handles = [...seleccion.keys()];
    try {
      await pedir({ op: "entities_delete", handles });
      aviso(handles.length === 1 ? "Objeto borrado" : `${handles.length} objetos borrados`);
      seleccion.clear(); await reflejarSeleccion();
    } catch (_) { aviso("No se pudo borrar. Alguno de los objetos está bloqueado.", "error"); }
  }
  async function infoSeleccion() {
    const [s] = seleccion.values();
    if (!s) return;
    await mostrarInfo(s.handle);
  }
  async function mostrarInfo(handle) {
    const [e] = await detalleDe([handle]);
    if (!e) { aviso("No se encontró el objeto.", "error"); return; }
    const tipo = tipoDe(e), filas = [["Tipo", NOMBRE_TIPO[tipo] || tipo], ["Capa", e.layer || "0"]];
    const color = (e.color !== undefined ? e.color : e.properties && e.properties.common && e.properties.common.color);
    filas.push(["Color", nombreColor(color)]);
    const g = e.geometry || e;
    const med = await medida(handle);
    if (tipo === "Circle") { filas.push(["Radio", `${fmt(g.radius)} m`], ["Centro X", fmt(g.center[0])], ["Centro Y", fmt(g.center[1])]); }
    if (tipo === "Arc") { filas.push(["Radio", `${fmt(g.radius)} m`]); }
    if (tipo === "Line" && g.start && g.end) { filas.push(["Azimut", gms(azimut(g.start, g.end))]); }
    if (tipo === "Text" || tipo === "MText") { filas.push(["Texto", String(e.text || e.value || "")]); }
    if (med && Number.isFinite(med.length)) filas.push([tipo === "Arc" ? "Longitud de arco" : "Longitud", `${fmt(med.length)} m`]);
    if (med && Number.isFinite(med.area) && med.area > 0) filas.push(["Área", `${fmt(med.area)} m²`]);
    tarjeta("Info del objeto", filas);
  }

  // ---------------------------------------------------------------------------------------------------------------- colores (ACI)
  // Paleta de colores del dibujo (ACI 0–255): 6 cifras hexadecimales por color.
  const TABLA_ACI = "000000ff0000ffff0000ff0000ffff0000ffff00ffffffff808080c0c0c0ff0000ff7f7fa50000a552527f00007f3f3f4c00004c2626260000261313ff3f00ff9f7f" +
    "a52900a567527f1f007f4f3f4c13004c2f26260900261713ff7f00ffbf7fa55200a57c527f3f007f5f3f4c26004c3926261300261c13ffbf00ffdf7fa57c00a59152" +
    "7f5f007f6f3f4c39004c4226261c00262113ffff00ffff7fa5a500a5a5527f7f007f7f3f4c4c004c4c26262600262613bfff00dfff7f7ca50091a5525f7f006f7f3f" +
    "394c00424c261c26002126137fff00bfff7f52a5007ca5523f7f005f7f3f264c00394c261326001c26133fff009fff7f29a50067a5521f7f004f7f3f134c002f4c26" +
    "09260017261300ff007fff7f00a50052a552007f003f7f3f004c00264c2600260013261300ff3f7fff9f00a52952a567007f1f3f7f4f004c13264c2f002609135817" +
    "00ff7f7fffbf00a55252a57c007f3f3f7f5f004c26264c3900261313581c00ffbf7fffdf00a57c52a591007f5f3f7f6f004c39264c4200261c13585800ffff7fffff" +
    "00a5a552a5a5007f7f3f7f7f004c4c264c4c00262613585800bfff7fdfff007ca55291a5005f7f3f6f7f00394c26427e001c26135858007fff7fbfff0052a5527ca5" +
    "003f7f3f5f7f00264c26397e001326131c58003fff7f9fff0029a55267a5001f7f3f4f7f00134c262f7e0009261317580000ff7f7fff0000a55252a500007f3f3f7f" +
    "00004c26267e0000261313583f00ff9f7fff2900a56752a51f007f4f3f7f13004c2f267e0900261713587f00ffbf7fff5200a57c52a53f007f5f3f7f26004c39267e" +
    "1300261c1358bf00ffdf7fff7c00a59152a55f007f6f3f7f39004c42264c1c0026581358ff00ffff7fffa500a5a552a57f007f7f3f7f4c004c4c264c260026581358" +
    "ff00bfff7fdfa5007ca552917f005f7f3f6f4c00394c264226001c581358ff007fff7fbfa50052a5527c7f003f7f3f5f4c00264c263926001358131cff003fff7f9f" +
    "a50029a552677f001f7f3f4f4c00134c262f260009581317000000656565666666999999ccccccffffff";
  let tablaACI = null;
  function aciRGB(i) {
    if (!tablaACI) tablaACI = TABLA_ACI.match(/.{6}/g);
    return `#${tablaACI[i] || "ffffff"}`;
  }
  function nombreColor(c) {
    if (c === undefined || c === null) return "Por capa";
    if (typeof c === "string") return /bylayer/i.test(c) ? "Por capa" : /byblock/i.test(c) ? "Por bloque" : c;
    if (typeof c === "number") return c === 256 ? "Por capa" : c === 0 ? "Por bloque" : `Color ${c}`;
    if (c.Index !== undefined) return nombreColor(c.Index);
    if (c.Rgb) return `RGB ${c.Rgb.r}, ${c.Rgb.g}, ${c.Rgb.b}`;
    return "Por capa";
  }
  // Una muestra de color como SVG con `fill` (atributo de presentación): la CSP de la página no deja estilos en línea y WebKit los
  // rechaza también cuando se ponen desde el script.
  function muestra(color, clase = "tt-color") {
    const s = document.createElementNS(NS, "svg"); s.setAttribute("class", clase); s.setAttribute("viewBox", "0 0 10 10"); s.setAttribute("aria-hidden", "true");
    const r = document.createElementNS(NS, "rect"); r.setAttribute("width", "10"); r.setAttribute("height", "10"); r.setAttribute("rx", "2"); r.setAttribute("fill", color);
    s.append(r); return s;
  }
  let colorNuevo = null;                  // color de lo que se dibuja (null = por capa)
  function paleta(actual, alElegir) {
    const cont = nodo("div");
    const seg = nodo("div", "tt-segmentado");
    for (const [v, et] of [[256, "Por capa"], [0, "Por bloque"]]) {
      const b = nodo("button", "", et); b.type = "button"; b.setAttribute("aria-pressed", String(actual === v));
      b.addEventListener("click", () => alElegir(v)); seg.append(b);
    }
    cont.append(seg, nodo("h3", "", "Colores"));
    const p = nodo("div", "tt-paleta");
    const indices = [1, 2, 3, 4, 5, 6, 7, 8, 9, 250, ...Array.from({ length: 24 }, (_, i) => 10 + i * 10), ...Array.from({ length: 24 }, (_, i) => 14 + i * 10), 251, 252, 253, 254, 255, 30, 40, 50, 60, 70, 80].slice(0, 70);
    for (const i of [...new Set(indices)]) {
      const b = nodo("button"); b.type = "button"; b.append(muestra(aciRGB(i))); b.setAttribute("aria-label", `Color ${i}`); b.title = `Color ${i}`;
      b.setAttribute("aria-pressed", String(actual === i));
      b.addEventListener("click", () => alElegir(i)); p.append(b);
    }
    cont.append(p);
    return cont;
  }

  // ---------------------------------------------------------------------------------------------------------------- capas
  let estadoCapasAnterior = null;
  async function leerCapas() {
    const r = await pedirSuave({ op: "layers" });
    const capas = (Array.isArray(r) ? r : (r && (r.layers || r.items)) || []).map((c) => ({
      nombre: c.name, encendida: !(c.off || c.on === false || c.is_off), congelada: !!(c.frozen || c.is_frozen), color: c.color && c.color.Index !== undefined ? c.color.Index : (typeof c.color === "number" ? c.color : 7),
      actual: !!c.current,
    }));
    const sv = await pedirSuave({ op: "sysvar", get: ["clayer"] });
    const clayer = sv && sv.values ? sv.values.clayer : null;
    if (clayer) for (const c of capas) c.actual = c.nombre.toLowerCase() === String(clayer).toLowerCase();
    return capas;
  }
  const nombreCmd = (n) => (/[\s,;"]/.test(n) ? `"${n.replace(/"/g, "")}"` : n);
  async function capaEncender(nombre, si) { return ejecutar(`-LAYER ${si ? "ON" : "OFF"} ${nombreCmd(nombre)} `); }
  async function hojaCapas() {
    const capas = await leerCapas();
    const ul = nodo("ul", "tt-lista");
    for (const c of capas) {
      const li = nodo("li", "tt-fila"); if (c.actual) li.setAttribute("aria-current", "true");
      const m = muestra(aciRGB(c.color), "tt-muestra");
      const txt = nodo("button", "tt-fila__texto tt-fila--boton"); txt.type = "button";
      txt.append(nodo("strong", "", c.nombre), nodo("span", "", c.actual ? "Capa actual: lo nuevo se dibuja aquí" : (c.encendida ? "Toca para dibujar en esta capa" : "Apagada")));
      txt.disabled = !est.editable;
      txt.addEventListener("click", async () => {
        await guardarCapasAnterior(capas);
        const ok = await pedirSuave({ op: "sysvar", set: { clayer: c.nombre } });
        if (!ok) await pedirSuave({ op: "run", cmd: `-LAYER SET ${nombreCmd(c.nombre)} ` });
        if (!c.encendida) await capaEncender(c.nombre, true);
        aviso(`Capa actual: ${c.nombre}`); hojaCapas();
      });
      const ojo = nodo("button", "tt-ib"); ojo.type = "button"; ojo.append(icono(c.encendida ? "eye" : "eye-off"));
      ojo.setAttribute("aria-label", c.encendida ? `Apagar ${c.nombre}` : `Encender ${c.nombre}`);
      ojo.disabled = c.actual && c.encendida;
      ojo.addEventListener("click", async () => { await guardarCapasAnterior(capas); await capaEncender(c.nombre, !c.encendida); hojaCapas(); });
      li.append(m, txt, ojo); ul.append(li);
    }
    const acciones = nodo("div", "tt-acciones");
    acciones.append(
      boton("Nueva capa", "plus", nuevaCapa, { clase: "tt-boton", deshabilitado: !est.editable }),
      boton("Apagar las demás", "eye-off", async () => { await guardarCapasAnterior(capas); for (const c of capas) if (!c.actual && c.encendida) await capaEncender(c.nombre, false); hojaCapas(); }, { clase: "tt-boton tt-boton--secundario" }),
      boton("Encender todas", "eye", async () => { await guardarCapasAnterior(capas); for (const c of capas) if (!c.encendida) await capaEncender(c.nombre, true); hojaCapas(); }, { clase: "tt-boton tt-boton--secundario" }),
      boton("Como estaban", "history", async () => {
        if (!estadoCapasAnterior) return;
        const previo = estadoCapasAnterior; estadoCapasAnterior = null;
        for (const c of previo) await capaEncender(c.nombre, c.encendida);
        hojaCapas();
      }, { clase: "tt-boton tt-boton--secundario", deshabilitado: !estadoCapasAnterior }),
      boton(colorNuevo === null ? "Color de lo nuevo: por capa" : `Color de lo nuevo: ${nombreColor(colorNuevo)}`, "palette", hojaColorNuevo, { clase: "tt-boton tt-boton--secundario" }),
    );
    abrirHoja("Capas", [ul, acciones], { grupo: "capas" });
  }
  async function guardarCapasAnterior(capas) { estadoCapasAnterior = capas.map((c) => ({ nombre: c.nombre, encendida: c.encendida })); }
  async function nuevaCapa() {
    const v = await pedirValores({ titulo: "Nueva capa", campos: [{ k: "nombre", et: "Nombre de la capa", tipo: "texto" }], listo: "Crear",
      validar: (x) => (!x.nombre ? "Escribe un nombre." : /[<>\/\\":;?*|=,`]/.test(x.nombre) ? "El nombre no puede llevar < > / \\ \" : ; ? * | = ," : null) });
    if (!v) return hojaCapas();
    try {
      await ejecutar(`-LAYER M ${nombreCmd(v.nombre)} `);
      aviso(`Capa «${v.nombre}» creada; lo nuevo se dibuja en ella.`);
    } catch (_) { aviso("No se pudo crear la capa.", "error"); }
    hojaCapas();
  }
  function hojaColorNuevo() {
    abrirHoja("Color de lo que dibujas", [nodo("p", "tt-nota-hoja", "Los objetos nuevos llevan este color. «Por capa» usa el color de la capa."),
      paleta(colorNuevo === null ? 256 : colorNuevo, (i) => { colorNuevo = i === 256 ? null : i; aviso(`Color de lo nuevo: ${nombreColor(i)}`); cerrarHojas(); })]);
  }
  async function hojaPropiedades() {
    const capas = await leerCapas();
    const handles = [...seleccion.keys()];
    const ul = nodo("ul", "tt-lista");
    for (const c of capas) {
      const li = nodo("li", "tt-fila");
      const b = nodo("button", "tt-fila__texto tt-fila--boton"); b.type = "button";
      const m = muestra(aciRGB(c.color), "tt-muestra");
      b.append(nodo("strong", "", c.nombre));
      b.addEventListener("click", async () => {
        try { await propiedad(handles, "layer", c.nombre); aviso(`Ahora en la capa ${c.nombre}`); cerrarHojas(); await refrescarSeleccion(); }
        catch (_) { aviso("No se pudo cambiar la capa.", "error"); }
      });
      li.append(m, b); ul.append(li);
    }
    abrirHoja(handles.length === 1 ? "Capa y color del objeto" : `Capa y color de ${handles.length} objetos`, [nodo("h3", "", "Capa"), ul, nodo("h3", "", "Color"),
      paleta(null, async (i) => {
        try { await propiedad(handles, "color", i === 256 ? "ByLayer" : i === 0 ? "ByBlock" : i); aviso(`Color: ${nombreColor(i)}`); cerrarHojas(); }
        catch (_) { aviso("No se pudo cambiar el color.", "error"); }
      })]);
  }
  // Una propiedad de los objetos elegidos (un paso de deshacer): el editor la cambia en su selección, como su panel de Propiedades.
  async function propiedad(handles, campo, valor) {
    await pedir({ op: "select", handles, clear: true });
    await pedir({ op: "property", field: campo, value: valor });
  }
  async function refrescarSeleccion() {
    const handles = [...seleccion.keys()];
    if (!handles.length) return;
    const es = await detalleDe(handles);
    seleccion.clear();
    for (const e of es) if (e.handle) seleccion.set(e.handle, { handle: e.handle, tipo: tipoDe(e), caja: cajaDe(e), capa: e.layer, e });
    await reflejarSeleccion();
  }

  // ---------------------------------------------------------------------------------------------------------------- presentaciones
  // Modelo y presentaciones: `layouts` (en el orden de sus pestañas, Modelo primero, y `current`) y `layout_set` por nombre (con
  // espacios). Cada entrada puede venir como nombre o como {name, model, current}.
  const esModelo = (n) => /^model(o)?$/i.test(String(n || ""));
  function listaPresentaciones(r) {
    const actual = r.current && (r.current.name || r.current);
    return (r.layouts || []).map((l) => {
      const name = typeof l === "string" ? l : l.name;
      return { name, model: typeof l === "object" && l.model !== undefined ? !!l.model : esModelo(name),
        current: typeof l === "object" && l.current !== undefined ? !!l.current : String(name).toLowerCase() === String(actual || "").toLowerCase() };
    });
  }
  async function leerPresentacion() {
    if (est.sinPresentaciones) return null;
    const r = await pedirSuave({ op: "layouts" });
    if (!r || !r.layouts) { est.sinPresentaciones = true; return null; }      // un editor anterior a tv-v2026.45-7 no las lista
    const lista = listaPresentaciones(r);
    const actual = lista.find((l) => l.current);
    est.presentacion = !!(actual && !actual.model);
    reflejarEstado();
    return lista;
  }
  async function hojaPresentaciones() {
    const lista = (await leerPresentacion()) || [];
    const ul = nodo("ul", "tt-lista");
    for (const l of lista) {
      const li = nodo("li", "tt-fila"); if (l.current) li.setAttribute("aria-current", "true");
      const b = nodo("button", "tt-fila__texto tt-fila--boton"); b.type = "button";
      b.append(nodo("strong", "", l.model ? "Modelo" : l.name), nodo("span", "", l.current ? "Es la que ves" : (l.model ? "El dibujo" : "Presentación para imprimir")));
      b.addEventListener("click", async () => {
        cerrarHojas(); cancelarHerramienta(); quitarSeleccion();
        const ok = await pedirSuave({ op: "layout_set", name: l.name });
        if (!ok) { aviso("No se pudo mostrar esa presentación.", "error"); return; }
        await pintado(); await leerVista(); await leerPresentacion();
        aviso(l.model ? "Modelo" : `Presentación «${l.name}»: solo ver y medir`);
      });
      li.append(icono(l.model ? "pen-line" : "layout-template"), b); ul.append(li);
    }
    if (lista.length < 2) ul.append(nodo("li", "tt-nota-hoja", "Este plano no tiene presentaciones: solo el modelo."));
    abrirHoja("Modelo y presentaciones", ul);
  }

  // ---------------------------------------------------------------------------------------------------------------- hoja «Más»
  function interruptor(et, valor, fn) {
    const f = nodo("label", "tt-interruptor"); f.append(nodo("span", "", et));
    const i = nodo("input", "tv-interruptor"); i.type = "checkbox"; i.setAttribute("role", "switch"); i.checked = !!valor;
    i.addEventListener("change", () => fn(i.checked)); f.append(i); return f;
  }
  function hojaMas() {
    const filas = [];
    const fila = (et, ic, fn, extra) => { const b = boton(et, ic, () => { cerrarHojas(); fn(); }, { clase: "tt-fila tt-fila--boton", ...(extra || {}) }); filas.push(b); };
    if ($("cad-boton-versiones")) fila("Versiones", "history", () => $("cad-boton-versiones").click());
    fila("Ver todo el plano · tres toques", "maximize", verTodo);
    fila("Seleccionar área · dos toques y arrastra", "scan", () => { quitarSeleccion(); modoArea(true); });
    if (!est.sinPresentaciones) fila("Modelo y presentaciones", "layout-template", hojaPresentaciones);
    fila("Referencias a objetos", "magnet", hojaReferencias);
    if (window.TVCadCampo) fila("Sin conexión", "cloud-download", () => window.TVCadCampo.hoja({ abrirHoja, nodo, boton, interruptor, aviso, icono, cerrarHojas }));
    if ($("cad-descargar")) fila("Descargar el plano", "download", () => $("cad-descargar").click());
    if (window.TVCadPantalla) fila(window.TVCadPantalla.completa() ? "Salir de pantalla completa" : "Pantalla completa", "maximize", () => window.TVCadPantalla.alternar());
    if (!CAMPO) fila(TABLETA.matches ? "Modo completo" : "Modo completo (pensado para pantallas grandes)", "monitor", () => salir(true));
    const cont = nodo("div"); cont.append(...filas);
    cont.append(interruptor("Puntero para zurdos", pref.zurdo, (si) => { pref.zurdo = si; guardado.poner("zurdo", si); pintar(); }));
    cont.append(filaDecimales());
    abrirHoja("Más", cont);
  }
  // Referencias a objetos: un interruptor general, «Todas» / «Ninguna» y cada tipo con su marca. Se recuerdan en este dispositivo.
  function hojaReferencias() {
    const cont = nodo("div");
    const guardar = () => { guardado.poner("referencias.v2", pref.refs); guardado.poner("referencias.encendidas", pref.refsOn); pintar(); consultarSnap(); };
    const general = interruptor("Referencias a objetos", pref.refsOn, (si) => { pref.refsOn = si; lista.dataset.apagada = si ? "0" : "1"; guardar(); });
    general.classList.add("tt-interruptor--general");
    cont.append(nodo("p", "tt-nota-hoja", "La punta salta a estos puntos cuando está cerca."), general);
    const acciones = nodo("div", "tt-segmentado");
    const lista = nodo("div", "tt-refs"); lista.dataset.apagada = pref.refsOn ? "0" : "1";
    const pintarLista = () => {
      lista.replaceChildren(...REFS.map(([k, et]) => {
        const fila = interruptor(et, pref.refs.includes(k), (si) => {
          pref.refs = si ? [...new Set([...pref.refs, k])] : pref.refs.filter((x) => x !== k);
          if (si && !pref.refsOn) { pref.refsOn = true; general.querySelector("input").checked = true; lista.dataset.apagada = "0"; }
          guardar();
        });
        fila.dataset.ref = k;
        const m = document.createElementNS(NS, "svg"); m.setAttribute("class", "tt-ref-marca"); m.setAttribute("viewBox", "-12 -12 24 24"); m.setAttribute("aria-hidden", "true");
        const p = document.createElementNS(NS, "path"); p.setAttribute("d", formaSnap(k)); m.append(p);
        fila.prepend(m);
        return fila;
      }));
    };
    acciones.append(
      boton("Todas", "", () => { pref.refs = TIPOS_SNAP.slice(); pref.refsOn = true; general.querySelector("input").checked = true; lista.dataset.apagada = "0"; guardar(); pintarLista(); }, { clase: "" }),
      boton("Ninguna", "", () => { pref.refs = []; guardar(); pintarLista(); }, { clase: "" }));
    pintarLista();
    cont.append(acciones, lista);
    abrirHoja("Referencias a objetos", cont);
  }

  // ---------------------------------------------------------------------------------------------------------------- herramientas
  // Cada definición: grupo, et (nombre), ic (icono), n (puntos que pide) o multi (sin límite, con «Terminar»), min, guia(n),
  // previa(g, puntos, cursor), fin(puntos) → crea o mide. `edita`: cambia el dibujo (no en solo lectura). `seleccion`: necesita objetos.
  const ent = (e) => (colorNuevo === null ? e : { ...e, color: colorNuevo });
  async function crear(etiqueta, entidades, mensaje) {
    try {
      await pedir({ op: "batch", label: etiqueta, steps: [{ op: "entities_create", entities: entidades.map(ent) }] }, 60000);
      aviso(mensaje || `${etiqueta} dibujada`);
      return true;
    } catch (err) {
      const r = err && err.respuesta;
      aviso(r && /locked/i.test(String(r.code || r.error)) ? "La capa actual está bloqueada." : `No se pudo dibujar: ${etiqueta.toLowerCase()}.`, "error");
      return false;
    }
  }
  const HERR = {
    polilinea: { grupo: "dibujar", et: "Polilínea", ic: "#tt-s-polilinea", edita: true, multi: true, min: 2, cerrar: true,
      guia: (n) => (n === 0 ? "Lleva la punta al primer punto y toca «Poner punto»" : `${n} ${n === 1 ? "punto" : "puntos"} · sigue o toca «Terminar»`),
      previa: (g, p, c) => linea(g, c ? [...p, c] : p, false),
      fin: (p, h) => crear("Polilínea", [{ type: "LwPolyline", vertices: p, closed: !!h.cerrada }], h.cerrada ? "Polilínea cerrada dibujada" : "Polilínea dibujada") },
    linea: { grupo: "dibujar", et: "Línea", ic: "#tt-s-linea", edita: true, multi: true, min: 2,
      guia: (n) => (n === 0 ? "Pon el inicio de la línea" : n === 1 ? "Pon el final" : `${n - 1} ${n === 2 ? "línea" : "líneas"} · sigue o toca «Terminar»`),
      previa: (g, p, c) => linea(g, c ? [...p, c] : p, false),
      fin: (p) => crear("Líneas", p.slice(1).map((b, i) => ({ type: "Line", start: p[i], end: b })), p.length === 2 ? "Línea dibujada" : `${p.length - 1} líneas dibujadas`) },
    rectangulo: { grupo: "dibujar", et: "Rectángulo", ic: "#tt-s-rectangulo", edita: true, n: 2,
      guia: (n) => (n === 0 ? "Pon una esquina" : "Pon la esquina opuesta"),
      previa: (g, p, c) => { if (p.length && c) linea(g, [p[0], [c[0], p[0][1]], c, [p[0][0], c[1]]], true); },
      valido: (p) => (Math.abs(p[1][0] - p[0][0]) > 0 && Math.abs(p[1][1] - p[0][1]) > 0 ? null : "Las esquinas no pueden estar en la misma línea."),
      fin: ([a, b]) => crear("Rectángulo", [{ type: "LwPolyline", vertices: [a, [b[0], a[1]], b, [a[0], b[1]]], closed: true }], "Rectángulo dibujado") },
    circulo: { grupo: "dibujar", et: "Círculo", ic: "#tt-s-circulo", edita: true, n: 2,
      guia: (n) => (n === 0 ? "Pon el centro (toca X o Y para escribirlo)" : "Lleva la punta al borde, o toca «Radio» para escribirlo"),
      previa: (g, p, c) => { if (p.length && c) { circulo(g, p[0], dist(p[0], c)); texto(g, c, `R ${fmt(dist(p[0], c))}`); } },
      valido: (p, h) => ((h.extra.radio !== undefined ? h.extra.radio : dist(p[0], p[1])) > 0 ? null : "El radio debe ser mayor que cero."),
      fin: ([c, b], h) => { const r = h.extra.radio !== undefined ? h.extra.radio : dist(c, b); return crear("Círculo", [{ type: "Circle", center: c, radius: r }], `Círculo de radio ${fmt(r)} m`); } },
    arco: { grupo: "dibujar", et: "Arco por 3 puntos", ic: "#tt-s-arco", edita: true, n: 3,
      guia: (n) => ["Pon el inicio del arco", "Pon un punto del arco", "Pon el final del arco"][n],
      previa: (g, p, c) => {
        const q = c ? [...p, c] : p;
        if (q.length === 2) linea(g, q, false);
        if (q.length === 3) { const a = arco3(...q); if (a) arcoPath(g, a.c, a.r, a.ini, a.fin); }
      },
      valido: (p) => (arco3(...p) ? null : "Los tres puntos no pueden estar en línea recta."),
      fin: (p) => { const a = arco3(...p); return crear("Arco", [{ type: "Arc", center: a.c, radius: a.r, start_angle_deg: a.ini, end_angle_deg: a.fin }], `Arco de radio ${fmt(a.r)} m`); } },
    punto: { grupo: "dibujar", et: "Punto", ic: "circle-dot", edita: true, n: 1, repite: true,
      guia: () => "Lleva la punta (o toca X o Y para escribir la coordenada) y toca «Poner punto». Sigue o toca «Cancelar»",
      fin: ([p]) => crear("Punto", [{ type: "Point", location: [p[0], p[1], 0] }], `Punto en X ${fmt(p[0])}, Y ${fmt(p[1])}`) },
    perpendicular: { grupo: "dibujar", et: "Perpendicular", ic: "#tt-s-perpendicular", edita: true, n: 1, elegir: true,
      guia: (n, h) => (n === 0 ? "Pon el punto de partida" : "Lleva la punta sobre la línea y toca «Elegir»"),
      previa: (g, p, c, h) => { if (p.length && h.pie) { linea(g, [p[0], h.pie], false); } else if (p.length && c) linea(g, [p[0], c], false); },
      fin: (p, h) => perpendicularFin(p, h) },
    texto: { grupo: "texto", et: "Texto", ic: "type", edita: true, n: 1, delta: false,
      guia: () => "Lleva la punta a donde empieza el texto y toca «Poner punto»",
      fin: ([p]) => textoFin(p) },
    cota_lineal: { grupo: "cota", et: "Cota horizontal o vertical", ic: "#tt-s-cota", edita: true, n: 3,
      guia: (n) => ["Pon el primer punto a medir", "Pon el segundo punto", "Pon dónde va la línea de la cota"][n],
      previa: (g, p, c) => previaCota(g, c ? [...p, c] : p, false),
      fin: (p) => cotaFin(p, false) },
    cota_alineada: { grupo: "cota", et: "Cota alineada", ic: "#tt-s-cota-alineada", edita: true, n: 3,
      guia: (n) => ["Pon el primer punto a medir", "Pon el segundo punto", "Pon a qué distancia va la cota"][n],
      previa: (g, p, c) => previaCota(g, c ? [...p, c] : p, true),
      fin: (p) => cotaFin(p, true) },
    // Medir: nada cambia en el dibujo.
    medir_id: { grupo: "medir", et: "Coordenadas de un punto", ic: "locate-fixed", n: 1, repite: true, confirmar: "Ver coordenadas", delta: false,
      guia: () => "Lleva la punta al punto y toca «Ver coordenadas»",
      fin: ([p]) => { tarjeta("Coordenadas", [["Este (X)", fmt(p[0])], ["Norte (Y)", fmt(p[1])]]); tarjetaTexto = `${crudo(p[0])}, ${crudo(p[1])}`; } },
    medir_dist: { grupo: "medir", et: "Distancia", ic: "ruler", n: 2, repite: true,
      guia: (n) => (n === 0 ? "Pon el primer punto" : "Pon el segundo punto"),
      previa: (g, p, c) => { if (p.length && c) { linea(g, [p[0], c], false); texto(g, c, `${fmt(dist(p[0], c))} m`); } },
      fin: ([a, b]) => tarjeta("Distancia", [["Distancia", `${fmt(dist(a, b))} m`], ["ΔX (Este)", fmt(b[0] - a[0])], ["ΔY (Norte)", fmt(b[1] - a[1])], ["Azimut", gms(azimut(a, b))]]) },
    medir_continua: { grupo: "medir", et: "Distancia continua", ic: "#tt-s-continua", multi: true, min: 2,
      guia: (n) => (n < 2 ? "Pon los puntos uno tras otro" : `Total ${fmt(longitud(herr.puntos))} m · sigue o toca «Terminar»`),
      previa: (g, p, c) => { linea(g, c ? [...p, c] : p, false); if (p.length && c) texto(g, c, `${fmt(longitud([...p, c]))} m`); },
      fin: (p) => tarjeta("Distancia continua", [["Total", `${fmt(longitud(p))} m`], ["Tramos", String(p.length - 1)]]) },
    medir_area: { grupo: "medir", et: "Área y perímetro", ic: "#tt-s-area", multi: true, min: 3, extra: ["Tocar un objeto", "objeto"],
      guia: (n) => (n < 3 ? "Pon los vértices del área, o toca «Tocar un objeto» para medir uno cerrado" : `${n} vértices · sigue o toca «Terminar»`),
      previa: (g, p, c) => { const q = c ? [...p, c] : p; if (q.length >= 3) { el("polygon", { class: "tt-sombra", points: q.map((x) => P(x).join(",")).join(" ") }, g); } linea(g, q, q.length >= 3); },
      fin: (p) => { const { area, perimetro } = areaPerimetro(p); tarjeta("Área", [["Área", `${fmt(area)} m²`], ["Hectáreas", fmt(area / 10000, 4)], ["Perímetro", `${fmt(perimetro)} m`]]); } },
    medir_angulo: { grupo: "medir", et: "Ángulo", ic: "#tt-s-angulo", n: 3, delta: false,
      guia: (n) => ["Pon un punto del primer lado", "Pon el vértice", "Pon un punto del segundo lado"][n],
      previa: (g, p, c) => linea(g, c ? [...p, c] : p, false),
      fin: ([a, v, b]) => {
        const g1 = Math.atan2(a[1] - v[1], a[0] - v[0]), g2 = Math.atan2(b[1] - v[1], b[0] - v[0]);
        let ang = Math.abs(grados(g2 - g1)); if (ang > 180) ang = 360 - ang;
        tarjeta("Ángulo", [["Ángulo", fmtAng(ang)], ["En grados, minutos y segundos", gms(ang)], ["Su complemento a 360°", fmtAng(360 - ang)]]);
      } },
    medir_objeto: { grupo: "medir", et: "Info del objeto", ic: "info", n: 0, elegir: true, repite: true,
      guia: () => "Lleva la punta sobre un objeto y toca «Elegir»" },
    medir_arco: { grupo: "medir", et: "Longitud de arco o de línea", ic: "#tt-s-arco-largo", n: 0, elegir: true, repite: true,
      guia: () => "Lleva la punta sobre un arco, línea o polilínea y toca «Elegir»" },
    // Editar: necesitan objetos elegidos.
    area: { grupo: "editar", et: "Seleccionar área", ic: "scan", hoja: () => { quitarSeleccion(); modoArea(true); } },
    borrar: { grupo: "editar", et: "Borrar", ic: "trash-2", seleccion: true, edita: true, hoja: borrarSeleccion },
    mover: { grupo: "editar", et: "Mover", ic: "move", seleccion: true, edita: true, n: 2,
      guia: (n) => (n === 0 ? "Pon el punto desde donde mueves" : "Pon a dónde va ese punto"),
      previa: (g, p, c) => previaDesplazar(g, p, c),
      fin: ([a, b]) => transformar("move", { vector: [b[0] - a[0], b[1] - a[1], 0] }, "Mover", "Movido") },
    copiar: { grupo: "editar", et: "Copiar", ic: "copy", seleccion: true, edita: true, n: 2, repite: true, quedarPrimero: true,
      guia: (n) => (n === 0 ? "Pon el punto de referencia" : "Pon dónde va la copia (puedes seguir copiando)"),
      previa: (g, p, c) => previaDesplazar(g, p, c),
      fin: ([a, b]) => transformar("copy", { vector: [b[0] - a[0], b[1] - a[1], 0] }, "Copiar", "Copiado") },
    girar: { grupo: "editar", et: "Girar", ic: "rotate-cw", seleccion: true, edita: true, n: 3,
      guia: (n) => ["Pon el centro del giro", "Pon un punto de referencia, o toca «Ángulo» para escribir el giro", "Pon hacia dónde gira, o toca «Ángulo»"][n],
      previa: (g, p, c) => { const q = c ? [...p, c] : p; if (q.length >= 2) linea(g, [q[1], q[0], ...(q[2] ? [q[2]] : [])], false); if (q.length === 3) texto(g, q[2], fmtAng(anguloGiro(q))); },
      fin: (p) => transformar("rotate", { center: [p[0][0], p[0][1], 0], angle_deg: anguloGiro(p) }, "Girar", `Girado ${fmtAng(anguloGiro(p))}`) },
    desfasar: { grupo: "editar", et: "Desfasar", ic: "#tt-s-desfasar", seleccion: true, edita: true, hoja: desfasar },
    propiedades: { grupo: "editar", et: "Capa y color", ic: "palette", seleccion: true, edita: true, hoja: hojaPropiedades },
    info: { grupo: "editar", et: "Info", ic: "info", seleccion: true, hoja: infoSeleccion },
    explotar: { grupo: "editar", et: "Explotar", ic: "ungroup", seleccion: true, edita: true, requiere: "explotar", hoja: explotar },
    recortar: { grupo: "editar", et: "Recortar", ic: "scissors", edita: true, requiere: "recortar", corte: "trim", n: 0, delta: false, confirmar: "Recortar aquí",
      guia: () => "Toca la parte que quieres quitar, o arrastra una línea que cruce las partes. Dos dedos mueven el plano" },
    alargar: { grupo: "editar", et: "Alargar", ic: "arrow-right-to-line", edita: true, requiere: "alargar", corte: "extend", n: 0, delta: false, confirmar: "Alargar aquí",
      guia: () => "Toca cerca del extremo que quieres alargar, o arrastra una línea que cruce los extremos. Dos dedos mueven el plano" },
  };
  const longitud = (p) => p.slice(1).reduce((s, b, i) => s + dist(p[i], b), 0);
  function anguloGiro(p) { if (p.angulo !== undefined) return p.angulo; const [c, a, b] = p; return grados(Math.atan2(b[1] - c[1], b[0] - c[0]) - Math.atan2(a[1] - c[1], a[0] - c[0])); }
  function previaDesplazar(g, p, c) {
    if (!p.length || !c) return;
    linea(g, [p[0], c], false);
    const dx = c[0] - p[0][0], dy = c[1] - p[0][1];
    for (const s of seleccion.values()) if (s.caja) linea(g, [[s.caja[0] + dx, s.caja[1] + dy], [s.caja[2] + dx, s.caja[1] + dy], [s.caja[2] + dx, s.caja[3] + dy], [s.caja[0] + dx, s.caja[3] + dy]], true);
  }
  async function transformar(accion, params, etiqueta, hecho) {
    const handles = [...seleccion.keys()];
    try {
      await pedir({ op: "entities_transform", handles, action: accion, ...params }, 60000);
      aviso(handles.length === 1 ? `${hecho}` : `${hecho}: ${handles.length} objetos`);
      if (accion !== "copy") await refrescarSeleccion();
      return true;
    } catch (_) { aviso(`No se pudo ${etiqueta.toLowerCase()}.`, "error"); return false; }
  }
  // Tamaño de cotas y textos según la vista (pedido del dueño 17-11): el texto se lee igual en la pantalla, sea lo medido chico o
  // grande. Alto ≈ 3 % del ancho visible en un teléfono (2.5 % en una tableta), redondeado a 1, 2, 2.5 o 5 × 10ⁿ, así las cotas
  // puestas con un zoom parecido quedan iguales. La cota lleva su propia escala (DIMSCALE = alto / DIMTXT del estilo, `dim_create`); el
  // estilo del plano y las cotas que ya hay no cambian.
  function altoSegunVista() {
    const px = vista.w, frac = px <= 430 ? 0.03 : px >= 768 ? 0.025 : 0.03 - ((px - 430) / (768 - 430)) * 0.005;
    const h = Math.max(vista.w * vista.k * frac, 1e-6);
    const e = Math.floor(Math.log10(h)), base = 10 ** e;
    let mejor = base, md = Infinity;
    for (const m of [1, 2, 2.5, 5, 10]) { const d = Math.abs(Math.log(h / (m * base))); if (d < md) { md = d; mejor = m * base; } }
    return Number(mejor.toPrecision(3));
  }
  let estiloCota = null;
  async function dimtxt() {
    if (estiloCota === null) {
      const r = await pedirSuave({ op: "dim_style" }, 8000);
      const v = r && (r.dimtxt ?? r.DIMTXT ?? (r.style && (r.style.dimtxt ?? r.style.DIMTXT)) ?? (r.values && (r.values.dimtxt ?? r.values.DIMTXT)));
      estiloCota = Number.isFinite(v) && v > 0 ? v : 2.5;
    }
    return estiloCota;
  }
  function textoCota(g, m, t, alto) {
    if (!capac.cotas) { texto(g, m, t); return; }
    const [x, y] = P(m);
    const e = el("text", { x, y: y - (alto / vista.k) * 0.4, class: "tt-cota-escala", "font-size": Math.max(4, alto / vista.k), "text-anchor": "middle" }, g);
    e.textContent = t;
  }
  function previaCota(g, q, alineada) {
    if (q.length < 2) { linea(g, q, false); return; }
    const [a, b] = q, c = q[2] || null, alto = altoSegunVista();
    if (!c) { linea(g, [a, b], false); texto(g, b, `${fmt(dist(a, b))} m`); return; }
    if (alineada) {
      const [ux, uy] = [b[0] - a[0], b[1] - a[1]]; const L = Math.hypot(ux, uy) || 1; const nx = -uy / L, ny = ux / L;
      const d = (c[0] - a[0]) * nx + (c[1] - a[1]) * ny;
      const a2 = [a[0] + nx * d, a[1] + ny * d], b2 = [b[0] + nx * d, b[1] + ny * d];
      linea(g, [a, a2], false); linea(g, [b, b2], false); linea(g, [a2, b2], false); textoCota(g, [(a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2], fmt(L), alto);
    } else {
      const horizontal = Math.abs(c[1] - (a[1] + b[1]) / 2) >= Math.abs(c[0] - (a[0] + b[0]) / 2);
      const a2 = horizontal ? [a[0], c[1]] : [c[0], a[1]], b2 = horizontal ? [b[0], c[1]] : [c[0], b[1]];
      linea(g, [a, a2], false); linea(g, [b, b2], false); linea(g, [a2, b2], false);
      textoCota(g, [(a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2], fmt(horizontal ? Math.abs(b[0] - a[0]) : Math.abs(b[1] - a[1])), alto);
    }
  }
  async function cotaFin([a, b, c], alineada) {
    if (dist(a, b) <= 0) { aviso("Los dos puntos no pueden ser el mismo.", "error"); return false; }
    const pt = (p) => `${p[0]},${p[1]}`;
    if (capac.cotas) {
      try {
        const escala = altoSegunVista() / await dimtxt();
        await pedir({ op: "dim_create", kind: alineada ? "aligned" : "linear", p1: [a[0], a[1]], p2: [b[0], b[1]], at: [c[0], c[1]], dimscale: escala }, 60000);
        aviso("Cota dibujada"); return true;
      } catch (err) {
        const r = err && err.respuesta;
        aviso(r && /locked/i.test(String(r.code || r.error)) ? "La capa actual está bloqueada." : "No se pudo dibujar la cota.", "error"); return false;
      }
    }
    try {
      const r = await pedir({ op: "batch", label: "Cota", steps: [{ op: "run", cmd: `${alineada ? "DIMALIGNED" : "DIMLINEAR"} ${pt(a)} ${pt(b)} ${pt(c)}` }] }, 60000);
      void r; aviso("Cota dibujada"); return true;
    } catch (_) { aviso("No se pudo dibujar la cota.", "error"); return false; }
  }
  async function textoFin(p) {
    const v = await pedirValores({ titulo: "Texto", listo: "Poner texto",
      campos: [{ k: "texto", et: "Texto", tipo: "texto", valor: "" }, { k: "altura", et: "Altura", sugerido: crudo(altoSegunVista(), 3).replace(/\.?0+$/, "") }, { k: "giro", et: "Giro (grados)", sugerido: "0" }],
      validar: (x) => (!x.texto ? "Escribe el texto." : !(x.altura > 0) ? ["altura", "La altura debe ser mayor que cero."] : !Number.isFinite(x.giro) ? ["giro", "Revisa el giro."] : null) });
    if (!v) return false;
    return crear("Texto", [{ type: "Text", value: v.texto, position: [p[0], p[1], 0], height: v.altura, rotation_deg: v.giro }], "Texto puesto");
  }
  async function perpendicularFin(p, h) {
    let e = await objetoEn(puntoActual(), 16, "geometry");
    if (e && tipoDe(e) === "LwPolyline") { const med = await medida(e.handle); e = { ...e, closed: !!(med && med.closed) }; }
    const seg = e && segmentoCercano(e, puntoActual());
    if (!seg) { aviso("Lleva la punta sobre una línea o polilínea.", "error"); return "seguir"; }
    const f = pie(p[0], seg[0], seg[1]);
    if (dist(f, p[0]) <= 0) { aviso("El punto ya está sobre la línea.", "error"); return "seguir"; }
    h.pie = f; pintar();
    return crear("Perpendicular", [{ type: "Line", start: p[0], end: f }], `Perpendicular de ${fmt(dist(p[0], f))} m`);
  }
  function verticesDe(e) {
    const g = e.geometry || e;
    if (g.start && g.end) return { v: [g.start, g.end], cerrada: false };
    const v = g.vertices || g.points;
    if (Array.isArray(v)) return { v: v.map((x) => (Array.isArray(x) ? x : [x.x, x.y])), cerrada: !!(g.closed || g.is_closed) };
    return null;
  }
  function segmentoCercano(e, m) {
    const d = verticesDe(e);
    if (!d || d.v.length < 2) return null;
    const v = d.v, n = d.cerrada ? v.length : v.length - 1;
    let mejor = null, md = Infinity;
    for (let i = 0; i < n; i++) {
      const a = v[i], b = v[(i + 1) % v.length], f = pie(m, a, b);
      const L = dist(a, b), t = L ? ((f[0] - a[0]) * (b[0] - a[0]) + (f[1] - a[1]) * (b[1] - a[1])) / (L * L) : 0;
      const q = t < 0 ? a : t > 1 ? b : f, dd = dist(m, q);
      if (dd < md) { md = dd; mejor = [a, b]; }
    }
    return mejor;
  }
  async function desfasar() {
    if (seleccion.size !== 1) { aviso("Elige un solo objeto para desfasarlo."); return; }
    const [s] = seleccion.values();
    if (!["Line", "Circle", "Arc", "LwPolyline"].includes(s.tipo)) { aviso("Se puede desfasar una línea, polilínea, círculo o arco.", "error"); return; }
    // La consulta no dice si una polilínea es cerrada; lo dice la medida del editor.
    const med = s.tipo === "LwPolyline" ? await medida(s.handle) : null;
    const [geo] = await detalleDe([s.handle], "geometry");
    const objeto = { ...s, e: { ...(geo || s.e), closed: med ? med.closed : false } };
    empezar("desfasar_lado", { objeto });
    // Como en CAD, la última distancia queda puesta (con su candado); se suelta para desfasar hasta la punta.
    const ultima = guardado.leer("desfase", null);
    if (Number.isFinite(ultima) && ultima > 0) { fijar("dist", ultima); pintar(); }
  }
  const distanciaDesfase = (h, p) => (h.extra.distancia !== undefined ? h.extra.distancia : din.fijos.dist !== undefined ? din.fijos.dist : distanciaA(h.extra.objeto.e, p));
  HERR.desfasar_lado = { grupo: "-", et: "Desfasar", edita: true, n: 1, delta: false,
    guia: () => "Lleva la punta al lado hacia donde va la copia (a esa distancia, o toca «Distancia» para escribirla) y toca «Poner punto»",
    previa: (g, p, c, h) => { if (c) { const d = distanciaDesfase(h, c); const e = d > 0 && desfaseDe(h.extra.objeto.e, d, c); if (e) previaEntidad(g, e); } },
    valido: (p, h) => (distanciaDesfase(h, p[0]) > 0 ? null : "La distancia debe ser mayor que cero."),
    fin: async ([p], h) => {
      const d = distanciaDesfase(h, p);
      const e = desfaseDe(h.extra.objeto.e, d, p);
      if (!e) { aviso("No se pudo desfasar ese objeto.", "error"); return false; }
      guardado.poner("desfase", d);
      const capaObj = h.extra.objeto.capa;
      return crear("Desfasar", [{ ...e, ...(capaObj ? { layer: capaObj } : {}) }], `Desfasado ${fmt(d)} m`);
    } };
  function previaEntidad(g, e) {
    if (e.type === "Circle") circulo(g, e.center, e.radius);
    else if (e.type === "Arc") arcoPath(g, e.center, e.radius, e.start_angle_deg, e.end_angle_deg);
    else if (e.type === "Line") linea(g, [e.start, e.end], false);
    else if (e.type === "LwPolyline") linea(g, e.vertices, e.closed);
  }
  // Desfase propio para línea, círculo, arco y polilínea de tramos rectos (inglete en las esquinas), en f64 centrado en el objeto.
  function desfaseDe(e, d, lado) {
    const t = tipoDe(e), g = e.geometry || e;
    if (t === "Circle" || t === "Arc") {
      const r = Math.hypot(lado[0] - g.center[0], lado[1] - g.center[1]) > g.radius ? g.radius + d : g.radius - d;
      if (r <= 0) return null;
      return t === "Circle" ? { type: "Circle", center: g.center.slice(0, 2), radius: r }
        : { type: "Arc", center: g.center.slice(0, 2), radius: r, start_angle_deg: g.start_angle_deg ?? g.start_angle, end_angle_deg: g.end_angle_deg ?? g.end_angle };
    }
    const vs = verticesDe(e); if (!vs) return null;
    const v = vs.v.map((p) => [p[0], p[1]]), cerrada = vs.cerrada;
    if (v.length < 2) return null;
    // Lado: según el tramo más cercano al punto.
    const seg = segmentoCercano(e, lado);
    const [a, b] = seg; const cruz = (b[0] - a[0]) * (lado[1] - a[1]) - (b[1] - a[1]) * (lado[0] - a[0]);
    const s = cruz > 0 ? 1 : -1;                   // izquierda del sentido del dibujo = +
    const n = v.length, tramos = cerrada ? n : n - 1;
    const normal = (i) => { const p = v[i], q = v[(i + 1) % n]; const L = dist(p, q) || 1; return [-(q[1] - p[1]) / L * s * d, (q[0] - p[0]) / L * s * d]; };
    const lineas = []; for (let i = 0; i < tramos; i++) { const [nx, ny] = normal(i); const p = v[i], q = v[(i + 1) % n]; lineas.push([[p[0] + nx, p[1] + ny], [q[0] + nx, q[1] + ny]]); }
    const corte = (l1, l2) => {
      const [p, p2] = l1, [q, q2] = l2; const r = resta(p2, p), u = resta(q2, q); const den = r[0] * u[1] - r[1] * u[0];
      if (Math.abs(den) < 1e-12) return p2;
      const tt2 = ((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0]) / den; return [p[0] + tt2 * r[0], p[1] + tt2 * r[1]];
    };
    const out = [];
    for (let i = 0; i < (cerrada ? n : n); i++) {
      if (!cerrada && i === 0) { out.push(lineas[0][0]); continue; }
      if (!cerrada && i === n - 1) { out.push(lineas[tramos - 1][1]); continue; }
      const prev = lineas[(i - 1 + tramos) % tramos], sig = lineas[i % tramos];
      out.push(corte(prev, sig));
    }
    if (t === "Line") return { type: "Line", start: out[0], end: out[1] };
    return { type: "LwPolyline", vertices: out, closed: cerrada };
  }

  // -- Motor de las herramientas ------------------------------------------------------------------------------------------------
  let herr = null;               // {id, def, puntos, extra, cerrada, pie}
  function empezar(id, extra) {
    const def = HERR[id];
    if (!def) return;
    if (def.edita && !est.editable) { aviso(lecturaTexto() || "Este plano está en solo lectura."); return; }
    if (def.seleccion && !seleccion.size) { aviso("Primero toca un objeto para elegirlo."); return; }
    if (def.hoja) { def.hoja(); return; }
    cerrarTarjeta(); soltarTodo();
    if (def.corte && seleccion.size) quitarSeleccion();
    herr = { id, def, puntos: [], extra: extra || {} };
    $("tt-pestanas").hidden = true; $("tt-seleccion").hidden = true; $("tt-accion").hidden = false;
    if (!puntero.visible) {
      const [px, py] = [vista.ox + vista.w / 2, vista.oy + vista.h / 2 - 30];
      puntero.mundo = aMundo(px, py); mostrarPuntero();
    }
    actualizarHerramienta();
  }
  function actualizarHerramienta() {
    if (!herr) return;
    const { def, puntos } = herr, n = puntos.length;
    guia(def.guia ? def.guia(n, herr) : "");
    $("tt-atras").disabled = n === 0;
    $("tt-atras").hidden = !!(def.n !== undefined && def.n <= 1);
    $("tt-terminar").hidden = !def.multi;
    $("tt-terminar").disabled = n < (def.min || 2);
    $("tt-cerrar-figura").hidden = !def.cerrar || n < 3;
    // Coordenadas, distancias, radios y ángulos se escriben en la lectura; aquí solo queda «Tocar un objeto» del área.
    $("tt-coord").hidden = !(def.extra && def.extra[1] === "objeto" && n === 0);
    if (!$("tt-coord").hidden) $("tt-coord-texto").textContent = def.extra[0];
    // «Recortar» y «Alargar» no dejan nada a medias: se terminan con «Listo».
    $("tt-cancelar-texto").textContent = def.corte ? "Listo" : "Cancelar";
    $("tt-cancelar").replaceChildren(icono(def.corte ? "check" : "x"), $("tt-cancelar-texto"));
    $("tt-confirmar-texto").textContent = def.elegir && n >= (def.n || 0) ? "Elegir" : (def.confirmar || "Poner punto");
    if (din.abierto) $("tt-din-poner").textContent = $("tt-confirmar-texto").textContent;
    pintar(); consultarSnap();
  }
  function cancelarHerramienta() {
    if (!herr) return;
    herr = null; guia(""); soltarTodo(); cerrarDinamica();
    $("tt-accion").hidden = true;
    if (seleccion.size) { $("tt-seleccion").hidden = false; $("tt-pestanas").hidden = true; } else { $("tt-pestanas").hidden = false; }
    ocultarPuntero(); pintar();
  }
  $("tt-cancelar").addEventListener("click", cancelarHerramienta);
  $("tt-atras").addEventListener("click", () => { if (herr && herr.puntos.length) { herr.puntos.pop(); herr.pie = null; actualizarHerramienta(); } });
  $("tt-terminar").addEventListener("click", () => terminar());
  $("tt-cerrar-figura").addEventListener("click", () => { if (herr) { herr.cerrada = true; terminar(); } });
  $("tt-confirmar").addEventListener("click", () => confirmarPunto());
  $("tt-coord").addEventListener("click", () => botonObjeto());
  let ocupado = false;
  async function confirmarPunto() {
    if (!herr || ocupado) return;
    const { def } = herr;
    if (def.elegir && herr.puntos.length >= (def.n || 0)) return elegirObjeto();
    if (def.corte) { const m = puntoActual(); return accionCorte({ point: m }); }
    colocar(puntoActual());
  }
  async function colocar(p) {
    if (!herr || ocupado) return;
    const { def } = herr;
    const ult = herr.puntos[herr.puntos.length - 1];
    if (ult && dist(ult, p) === 0 && herr.id !== "punto") { aviso("Ese punto ya está puesto."); return; }
    // Girar con el ángulo escrito: gira ya (desde el este si aún no hay referencia, o desde la referencia).
    if (herr.id === "girar" && din.fijos.ang !== undefined && herr.puntos.length >= 1) {
      const c = herr.puntos[0], a = din.fijos.ang;
      ocupado = true; window.TVCad.ocupar();
      try { await transformar("rotate", { center: [c[0], c[1], 0], angle_deg: a }, "Girar", `Girado ${fmtAng(a)}`); } finally { ocupado = false; window.TVCad.soltar(); }
      cancelarHerramienta(); return;
    }
    if (herr.id === "circulo" && herr.puntos.length === 1 && (din.fijos.r !== undefined || din.fijos.diam !== undefined)) {
      herr.extra.radio = din.fijos.r !== undefined ? din.fijos.r : din.fijos.diam / 2;
    } else if (herr.id === "circulo") delete herr.extra.radio;
    if (herr.id === "desfasar_lado") herr.extra.distancia = din.fijos.dist !== undefined ? din.fijos.dist : undefined;
    herr.puntos.push([p[0], p[1]]);
    despuesDePoner();
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) { /* nada */ } }
    if (def.n !== undefined && def.n > 0 && herr.puntos.length >= def.n && !def.elegir) return terminar();
    actualizarHerramienta();
  }
  async function terminar() {
    if (!herr || ocupado) return;
    const h = herr, def = h.def;
    if (def.multi && h.puntos.length < (def.min || 2)) return;
    const err = def.valido && def.valido(h.puntos, h);
    if (err) { aviso(err, "error"); h.puntos.pop(); actualizarHerramienta(); return; }
    ocupado = true;
    window.TVCad.ocupar && def.edita && window.TVCad.ocupar();
    let r;
    try { r = await def.fin(h.puntos, h); }
    finally { ocupado = false; window.TVCad.soltar && def.edita && window.TVCad.soltar(); }
    if (herr !== h) return;
    if (r === false || r === "seguir") { if (r === false && def.n) h.puntos.pop(); actualizarHerramienta(); return; }
    if (def.repite) { h.puntos = def.quedarPrimero ? [h.puntos[0]] : []; h.cerrada = false; actualizarHerramienta(); return; }
    cancelarHerramienta();
  }
  async function elegirObjeto() {
    const h = herr;
    if (h.def.fin && h.id === "perpendicular") return terminar();
    const e = await objetoEn(puntoActual(), 16, "summary");
    if (!e) { aviso("No hay un objeto en la punta. Acércate más."); return; }
    if (h.id === "medir_objeto") return mostrarInfo(e.handle);
    if (h.id === "medir_arco") {
      const med = await medida(e.handle);
      if (!med || !Number.isFinite(med.length)) { aviso("Ese objeto no tiene longitud.", "error"); return; }
      tarjeta(`Longitud · ${NOMBRE_TIPO[tipoDe(e)] || "objeto"}`, [["Longitud", `${fmt(med.length)} m`]]);
    }
  }
  async function botonObjeto() {
    if (!herr) return;
    const e = await objetoEn(puntoActual(), 16, "summary");
    if (!e) { aviso("Lleva la punta sobre un objeto cerrado y vuelve a tocar «Tocar un objeto»."); return; }
    const med = await medida(e.handle);
    if (!med || !(med.area > 0)) { aviso("Ese objeto no encierra un área.", "error"); return; }
    tarjeta(`Área · ${NOMBRE_TIPO[tipoDe(e)] || "objeto"}`, [["Área", `${fmt(med.area)} m²`], ["Hectáreas", fmt(med.area / 10000, 4)], ...(Number.isFinite(med.length) ? [["Perímetro", `${fmt(med.length)} m`]] : [])]);
  }

  // -- Explotar, recortar y alargar (ops del editor desde tv-v2026.45-9; sin ellas, las herramientas no se muestran) ----------------
  const RAZONES = { locked_layer: "está en una capa bloqueada" };
  async function explotar() {
    if (!est.editable || !capac.explotar) return;
    const handles = [...seleccion.keys()];
    if (!handles.length) return;
    let r;
    window.TVCad.ocupar();
    try { r = await pedir({ op: "explode", handles }, 60000); }
    catch (err) { aviso("No se pudo explotar.", "error"); return; }
    finally { window.TVCad.soltar(); }
    const creados = r.created || [], saltados = r.skipped || [];
    seleccion.clear();
    if (creados.length) for (const e of await detalleDe(creados)) if (e.handle) seleccion.set(e.handle, { handle: e.handle, tipo: tipoDe(e), caja: cajaDe(e), capa: e.layer, e });
    await reflejarSeleccion();
    const nada = saltados.length === 1 ? "1 no se pudo explotar" : `${saltados.length} no se pudieron explotar`;
    if (!creados.length) {
      const motivo = saltados.length === 1 && RAZONES[saltados[0].reason];
      aviso(`No se pudo explotar: ${motivo || (saltados.length > 1 ? "ninguno de estos objetos se puede explotar" : saltados.length ? "ese objeto no se puede explotar" : "no había nada que explotar")}.`, "error", 4000);
    } else aviso(`Explotado: ${creados.length === 1 ? "1 objeto nuevo" : `${creados.length} objetos nuevos`}${saltados.length ? ` · ${nada}` : ""}`, "", saltados.length ? 4500 : 2800);
  }
  // Un toque, «Recortar aquí» o una línea que cruza: cada vez es un paso de deshacer. Devuelve si cambió algo.
  async function accionCorte(param) {
    if (!herr || !herr.def.corte || ocupado) return false;
    const op = herr.def.corte, h = herr;
    let r;
    ocupado = true; window.TVCad.ocupar();
    try { r = await pedir({ op, ...param }, 60000); }
    catch (err) {
      const c = err && err.respuesta && String(err.respuesta.code || "");
      aviso(/locked/i.test(c) ? "Ese objeto está en una capa bloqueada." : (op === "trim" ? "No se pudo recortar ahí." : "No se pudo alargar ahí."), "error");
      return false;
    } finally { ocupado = false; window.TVCad.soltar(); }
    if (herr !== h) return !!(r && r.changed);
    if (!r || !r.changed) {
      aviso(op === "trim" ? "Ahí no hay nada que recortar. Toca justo sobre la parte que sobra." : "No hay hacia dónde alargar: no hay otro objeto en esa dirección.");
      return false;
    }
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) { /* nada */ } }
    const n = (r.removed || []).length + (r.modified || []).length + (r.created || []).length;
    aviso(op === "trim" ? (param.fence && n > 1 ? `Recortado (${n} partes)` : "Recortado") : (param.fence && n > 1 ? `Alargado (${n} objetos)` : "Alargado"), "", 1600);
    return true;
  }

  // ---------------------------------------------------------------------------------------------------------------- interruptor de la cabecera
  const entrarBoton = $("cad-tactil-entrar");
  if (entrarBoton) {
    entrarBoton.hidden = false;
    entrarBoton.addEventListener("click", () => entrar(true));
  }
  window.TVCadTactil = Object.freeze({ entrar: () => entrar(true), salir: () => salir(true), activo: () => activo, listo: () => listo,
    // Para las pruebas y para depurar en el teléfono: la vista calculada aquí y la conversión pantalla ↔ mundo.
    vista: () => ({ ...vista }), aMundo: (x, y) => aMundo(x, y), aPantalla: (x, y) => aPantalla(x, y), herramienta: () => herr && herr.id,
    puntos: () => (herr ? herr.puntos.map((p) => p.slice()) : []), puntero: () => ({ visible: puntero.visible, mundo: puntoActual(), snap: puntero.snap && puntero.snap.tipo }),
    seleccion: () => [...seleccion.keys()], capacidades: () => ({ ...capac }), releer: () => leerVista(), fijos: () => ({ ...din.fijos }),
    referencias: () => ({ encendidas: pref.refsOn, tipos: pref.refs.slice(), candidatos: (puntero.cands || []).length }), objetoEn: (x, y) => objetoEn(aMundo(x, y), 14, "summary"),
    cercanos: (x, y) => { const m = aMundo(x, y), t = 14 * vista.k; return pedir({ op: "query", near: m, bounds: [m[0] - t, m[1] - t, m[0] + t, m[1] + t], limit: 12, detail: "summary" }); } });

  // ---------------------------------------------------------------------------------------------------------------- arranque
  // La ventana visible también cambia sin que cambie el área (Chrome al cerrar su diálogo de instalar, el teclado, el zoom de la
  // página): se vuelve a medir y se fija la vista, que además hace que el editor vuelva a pintar (en Android quedaba una copia vieja
  // de la barra y de la lectura a media pantalla).
  if (window.visualViewport) visualViewport.addEventListener("resize", alCambiarTamano);
  addEventListener("resize", alCambiarTamano);
  addEventListener("pageshow", alCambiarTamano);
  medirZona();
  if (CAMPO) {
    // Página sin conexión: primero la lista de planos del teléfono; el modo táctil entra al abrir uno.
    document.addEventListener("tvcad:cargado", () => entrar(false), { once: true });
  } else if (quiereTactil()) {
    if (!CAMPO && guardado.leer("tactil", null) === null) {
      // Primera vez en un dispositivo táctil: se abre en modo táctil y se dice cómo cambiarlo.
      window.TVCad.listo.then(() => setTimeout(() => aviso("Modo táctil activado. Puedes cambiarlo en «Más».", "", 5000), 1200));
    }
    entrar(false);
  }
})();

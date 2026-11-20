/* CAD: el árbol de proyectos y carpetas, las migas y el explorador de la biblioteca (docs/cad.md, «Proyectos y carpetas»).
 *
 * Lo usan «Planos» (lista.js), «Mover a…», «Abrir otro plano» (editor.js) y «Desde plantilla › De la biblioteca» (plantillas.js): la misma
 * forma de ver y recorrer la biblioteca en todas partes.
 *
 *   TVCadArbol.carpetas(url)                 → {carpetas, raiz, total} (lista plana del servidor; se guarda en la página con su ETag)
 *   TVCadArbol.arbol(caja, datos, opciones)  → árbol con teclado (role="tree"): ↑ ↓ ← → Inicio Fin Intro; recuerda lo abierto en este equipo
 *       opciones: {actual, enlace(id) → url, alElegir(id), deshabilitadas: Set, raiz: "Planos", clave, soltar(id, ev), id}
 *   TVCadArbol.migas(caja, ruta, {enlace, alElegir, raiz})
 *   TVCadArbol.explorador({...})             → la biblioteca carpeta por carpeta (hojas del editor)
 *   TVCadArbol.moverA({titulo, excluir, actual, url, nueva}) → Promise<id | null (la raíz) | undefined (cancelado)>
 */
(function () {
  "use strict";
  if (window.TVCadArbol) return;
  const RAIZ = "";                              // el id de la raíz en el árbol («Planos»)
  const LLAVE = "tv.cad.arbol.abiertas";
  const nodo = (tag, clase, texto) => { const e = document.createElement(tag); if (clase) e.className = clase; if (texto !== undefined) e.textContent = texto; return e; };
  const csrf = () => { const e = document.querySelector("input[name=csrfmiddlewaretoken]"); return e ? e.value : ""; };

  function leerAbiertas(clave) {
    try { return new Set(JSON.parse(localStorage.getItem(`${LLAVE}.${clave}`) || "[]")); } catch (_) { return new Set(); }
  }
  function guardarAbiertas(clave, s) {
    try { localStorage.setItem(`${LLAVE}.${clave}`, JSON.stringify([...s].slice(-400))); } catch (_) { /* sin almacenamiento */ }
  }

  // -- Datos ----------------------------------------------------------------------------------------------------------------------
  const guardados = new Map();
  async function carpetas(url, { fresco = false } = {}) {
    const previo = guardados.get(url);
    const cabeceras = { Accept: "application/json" };
    if (previo && !fresco) cabeceras["If-None-Match"] = previo.etiqueta;
    const r = await fetch(url, { credentials: "same-origin", headers: cabeceras, cache: "no-cache" });
    if (r.status === 304 && previo) return previo.datos;
    if (!r.ok) throw new Error(r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudieron leer las carpetas.");
    const datos = await r.json();
    guardados.set(url, { etiqueta: r.headers.get("ETag") || "", datos });
    return datos;
  }
  // Índice: id → {fila, hijas[]}; la raíz es RAIZ.
  function indice(datos) {
    const por = new Map([[RAIZ, { fila: { id: RAIZ, nombre: "Planos", nivel: 0, planos: datos.raiz || 0, total: datos.total || 0 }, hijas: [] }]]);
    for (const f of datos.carpetas || []) por.set(f.id, { fila: f, hijas: [] });
    for (const f of datos.carpetas || []) (por.get(f.padre || RAIZ) || por.get(RAIZ)).hijas.push(f.id);
    return por;
  }
  function rutaDe(por, id) {
    const salida = [];
    let x = id && por.get(id);
    const vistos = new Set();
    while (x && x.fila.id !== RAIZ && !vistos.has(x.fila.id)) { vistos.add(x.fila.id); salida.unshift(x.fila); x = por.get(x.fila.padre || RAIZ); }
    return salida;
  }
  function debajo(por, id) {
    const salida = new Set(), pila = [id];
    while (pila.length) { const k = pila.pop(); if (salida.has(k)) continue; salida.add(k); const x = por.get(k); if (x) pila.push(...x.hijas); }
    return salida;
  }

  // Los íconos (Lucide, los mismos de `{% icono %}`), aquí y no en cada página: la del plano no crece por las hojas que los usan.
  const TRAZOS = {
    folder: ["M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"],
    "folder-kanban": ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z", "M8 10v4", "M12 10v2", "M16 10v6"],
    "chevron-right": ["m9 18 6-6-6-6"],
    library: ["m16 6 4 14", "M12 6v14", "M8 8v12", "M4 4v16"],
  };
  const SVG = "http://www.w3.org/2000/svg";
  const icono = (nombre) => {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("class", "tv-icono"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    for (const d of TRAZOS[nombre] || TRAZOS.folder) { const p = document.createElementNS(SVG, "path"); p.setAttribute("d", d); s.append(p); }
    return s;
  };

  // -- El árbol -------------------------------------------------------------------------------------------------------------------
  function arbol(caja, datos, op = {}) {
    const clave = op.clave || "lista";
    const por = indice(datos);
    const abiertas = leerAbiertas(clave);
    abiertas.add(RAIZ);
    let actual = op.actual || RAIZ;
    for (const f of rutaDe(por, actual)) abiertas.add(f.padre || RAIZ);
    for (const f of rutaDe(por, actual)) abiertas.add(f.id);
    const raizTexto = op.raiz || "Planos";
    const raiz = nodo("ul", "cad-arbol__lista");
    raiz.setAttribute("role", "tree");
    raiz.setAttribute("aria-label", op.etiqueta || "Proyectos y carpetas");
    if (op.id) raiz.id = op.id;
    const items = new Map();

    function item(id, nivel) {
      const x = por.get(id);
      const f = x.fila;
      const li = nodo("li", "cad-arbol__item");
      li.setAttribute("role", "treeitem");
      li.dataset.carpeta = id;
      li.setAttribute("aria-level", String(nivel + 1));
      const fila = nodo(op.enlace && op.enlace(id) && !(op.deshabilitadas && op.deshabilitadas.has(id)) ? "a" : "div", "cad-arbol__fila");
      if (fila.tagName === "A") fila.href = op.enlace(id);
      fila.tabIndex = -1;
      fila.style.setProperty("--nivel", String(nivel));
      const flecha = nodo("button", "cad-arbol__flecha");
      flecha.type = "button"; flecha.tabIndex = -1;
      flecha.setAttribute("aria-hidden", "true");
      if (x.hijas.length) flecha.append(icono("chevron-right")); else flecha.classList.add("cad-arbol__flecha--vacia");
      const ic = icono(id === RAIZ ? "library" : f.nivel === 1 ? "folder-kanban" : "folder");
      const nombre = nodo("span", "cad-arbol__nombre", id === RAIZ ? raizTexto : f.nombre);
      nombre.title = id === RAIZ ? raizTexto : f.cliente ? `${f.nombre} · ${f.cliente}` : f.nombre;
      fila.append(flecha, ic, nombre);
      if (op.contar !== false && f.total) { const n = nodo("span", "cad-arbol__cuenta tv-cifra", String(f.total)); n.setAttribute("aria-label", `${f.total} plano${f.total === 1 ? "" : "s"}`); fila.append(n); }
      li.append(fila);
      if (op.deshabilitadas && op.deshabilitadas.has(id)) { li.setAttribute("aria-disabled", "true"); fila.classList.add("cad-arbol__fila--no"); }
      flecha.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); alternar(id); });
      fila.addEventListener("click", (ev) => {
        if (li.getAttribute("aria-disabled") === "true") { ev.preventDefault(); return; }
        if (op.alElegir) { ev.preventDefault(); elegir(id); op.alElegir(id); }
      });
      if (op.soltar) {
        fila.addEventListener("dragover", (ev) => { if (op.soltar(id, ev, "sobre")) { ev.preventDefault(); fila.classList.add("cad-arbol__fila--soltar"); } });
        fila.addEventListener("dragleave", () => fila.classList.remove("cad-arbol__fila--soltar"));
        fila.addEventListener("drop", (ev) => { fila.classList.remove("cad-arbol__fila--soltar"); if (op.soltar(id, ev, "soltar")) ev.preventDefault(); });
      }
      items.set(id, li);
      if (x.hijas.length) {
        li.setAttribute("aria-expanded", String(abiertas.has(id)));
        const ul = nodo("ul", "cad-arbol__hijas"); ul.setAttribute("role", "group");
        // Las hijas se pintan al abrir: con cientos de carpetas el árbol cerrado es casi nada.
        if (abiertas.has(id)) for (const h of x.hijas) ul.append(item(h, nivel + 1));
        li.append(ul);
      }
      if (id === actual) { li.setAttribute("aria-selected", "true"); fila.setAttribute("aria-current", op.alElegir ? "true" : "page"); }
      return li;
    }
    function alternar(id, si) {
      const li = items.get(id);
      const x = por.get(id);
      if (!li || !x || !x.hijas.length) return;
      const abrir = si === undefined ? li.getAttribute("aria-expanded") !== "true" : si;
      li.setAttribute("aria-expanded", String(abrir));
      const ul = li.querySelector(":scope > ul");
      if (abrir && !ul.childElementCount) { const nivel = Number(li.getAttribute("aria-level")) - 1; for (const h of x.hijas) ul.append(item(h, nivel + 1)); }
      if (abrir) abiertas.add(id); else abiertas.delete(id);
      guardarAbiertas(clave, abiertas);
    }
    function visibles() { return [...raiz.querySelectorAll('[role="treeitem"]')].filter((li) => !li.closest('[aria-expanded="false"] > ul')); }
    function enfocar(li) {
      for (const f of raiz.querySelectorAll(".cad-arbol__fila")) f.tabIndex = -1;
      const f = li.querySelector(":scope > .cad-arbol__fila"); f.tabIndex = 0; f.focus();
    }
    function elegir(id) {
      actual = id;
      for (const li of raiz.querySelectorAll('[aria-selected="true"]')) { li.removeAttribute("aria-selected"); li.querySelector(":scope > .cad-arbol__fila").removeAttribute("aria-current"); }
      for (const f of rutaDe(por, id)) if (f.padre) alternar(f.padre, true);
      const li = items.get(id);
      if (li) { li.setAttribute("aria-selected", "true"); li.querySelector(":scope > .cad-arbol__fila").setAttribute("aria-current", op.alElegir ? "true" : "page"); }
    }
    raiz.addEventListener("keydown", (ev) => {
      const li = ev.target.closest('[role="treeitem"]');
      if (!li) return;
      const lista = visibles();
      const i = lista.indexOf(li);
      const id = li.dataset.carpeta;
      const tiene = li.hasAttribute("aria-expanded");
      if (ev.key === "ArrowDown") { ev.preventDefault(); if (lista[i + 1]) enfocar(lista[i + 1]); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); if (lista[i - 1]) enfocar(lista[i - 1]); }
      else if (ev.key === "Home") { ev.preventDefault(); enfocar(lista[0]); }
      else if (ev.key === "End") { ev.preventDefault(); enfocar(lista[lista.length - 1]); }
      else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        if (tiene && li.getAttribute("aria-expanded") !== "true") alternar(id, true);
        else if (tiene) { const h = li.querySelector(':scope > ul > [role="treeitem"]'); if (h) enfocar(h); }
      } else if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        if (tiene && li.getAttribute("aria-expanded") === "true") alternar(id, false);
        else { const p = li.parentElement.closest('[role="treeitem"]'); if (p) enfocar(p); }
      } else if (ev.key === "Enter" || ev.key === " ") {
        if (ev.key === " ") ev.preventDefault();
        if (li.getAttribute("aria-disabled") === "true") { ev.preventDefault(); return; }
        if (op.alElegir) { ev.preventDefault(); elegir(id); op.alElegir(id); }
        else if (ev.key === " ") li.querySelector(":scope > .cad-arbol__fila").click();
      } else if (ev.key === "*") { ev.preventDefault(); for (const h of por.get(id).hijas) alternar(h, true); }
    });
    raiz.append(item(RAIZ, 0));
    caja.replaceChildren(raiz);
    const inicial = items.get(actual) || items.get(RAIZ);
    inicial.querySelector(":scope > .cad-arbol__fila").tabIndex = 0;
    return {
      elemento: raiz, elegir, abrir: (id) => alternar(id, true), indice: por, ruta: (id) => rutaDe(por, id), debajo: (id) => debajo(por, id),
      enfocar: () => enfocar(items.get(actual) || items.get(RAIZ)),
      mostrar: (texto) => {               // filtro por nombre (en «Mover a…»): abre los caminos a lo que coincide
        const k = (texto || "").trim().toLocaleLowerCase("es");
        for (const li of raiz.querySelectorAll('[role="treeitem"]')) li.hidden = false;
        if (!k) return;
        const coinciden = [...por.values()].filter((x) => x.fila.id !== RAIZ && x.fila.nombre.toLocaleLowerCase("es").includes(k)).map((x) => x.fila.id);
        const ver = new Set([RAIZ]);
        for (const id of coinciden) { ver.add(id); for (const f of rutaDe(por, id)) { ver.add(f.id); if (f.padre) alternar(f.padre, true); } }
        for (const li of raiz.querySelectorAll('[role="treeitem"]')) li.hidden = !ver.has(li.dataset.carpeta);
      },
    };
  }

  // -- Migas -----------------------------------------------------------------------------------------------------------------------
  function migas(caja, ruta, op = {}) {
    const lista = [{ id: RAIZ, nombre: op.raiz || "Planos" }, ...ruta];
    caja.replaceChildren();
    lista.forEach((f, i) => {
      const ultima = i === lista.length - 1;
      let e;
      if (ultima) { e = nodo("span", "", f.nombre); e.setAttribute("aria-current", "location"); }
      else if (op.enlace) { e = nodo("a", "", f.nombre); e.href = op.enlace(f.id); }
      else { e = nodo("button", "cad-migas__boton", f.nombre); e.type = "button"; }
      e.dataset.carpeta = f.id;
      if (op.alElegir && !ultima) e.addEventListener("click", (ev) => { ev.preventDefault(); op.alElegir(f.id); });
      caja.append(e);
    });
  }

  // -- El explorador (las hojas del editor) ------------------------------------------------------------------------------------
  // La carpeta a la vista: sus migas, sus carpetas y sus planos (las filas de plano las pinta quien lo usa); búsqueda en esta carpeta o
  // en todos los planos; «Ver más». En pantallas anchas, el árbol al lado.
  function explorador(op) {
    // Al escribir se busca en toda la biblioteca (lo normal al abrir un plano); «En esta carpeta» lo acota.
    const est = { carpeta: op.carpeta || RAIZ, q: "", en: (op.alcance && op.alcance.value) || "todo", pagina: 1, paginas: 1, n: 0 };
    let arbolHecho = null;
    const decir = op.decir || (() => {});
    async function pedir(url) {
      const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      let d = {}; try { d = await r.json(); } catch (_) { /* */ }
      if (!r.ok) throw new Error(d.error || (r.status === 401 ? "Tu sesión terminó. Vuelve a entrar." : "No se pudo leer la biblioteca."));
      return d;
    }
    function filaCarpeta(c) {
      const li = nodo("li", "cad-elegir__fila-carpeta");
      const b = nodo("button", "cad-elegir__plano cad-elegir__carpeta-fila"); b.type = "button"; b.dataset.carpeta = c.id;
      const ic = nodo("span", "cad-elegir__icono"); ic.append(icono("folder"));
      const texto = nodo("span", "cad-elegir__texto");
      texto.append(nodo("strong", "", c.nombre), nodo("span", "tv-tenue", [c.ubicacion, c.cliente, `${c.total} plano${c.total === 1 ? "" : "s"}`].filter(Boolean).join(" · ")));
      b.append(ic, texto);
      b.addEventListener("click", () => ir(c.id));
      li.append(b);
      return li;
    }
    async function cargar(mas) {
      if (!mas) est.pagina = 1;
      const n = ++est.n;
      decir("Buscando…");
      let d;
      const qs = new URLSearchParams({ carpeta: est.carpeta, q: est.q, en: est.en, pagina: String(est.pagina) });
      try { d = await pedir(`${op.url}?${qs}`); } catch (err) { if (n === est.n) decir(err instanceof TypeError ? "Sin conexión." : err.message); return; }
      if (n !== est.n) return;
      est.paginas = d.paginas;
      if (!mas) {
        migas(op.migas, d.migas || [], { alElegir: ir, raiz: op.raiz });
        if (op.alCambiar) op.alCambiar(est.carpeta, d);
      }
      let lista = d.carpetas || [];
      if (op.primero && !est.carpeta && !d.busqueda) lista = [...lista].sort((a, b) => (b.nombre === op.primero) - (a.nombre === op.primero));
      const filas = [...(mas ? [] : lista.map(filaCarpeta)), ...d.planos.map((x) => op.filaPlano(x))];
      if (mas) op.lista.append(...filas); else op.lista.replaceChildren(...filas);
      const nc = mas ? 0 : lista.length;
      const partes = [];
      if (nc) partes.push(`${nc} carpeta${nc === 1 ? "" : "s"}`);
      if (d.total || !nc) partes.push(d.total === 1 ? "1 plano" : `${d.total} planos`);
      decir(!nc && !d.total ? (d.busqueda ? "Nada coincide." : "Esta carpeta está vacía.") : partes.join(" · "));
      if (op.mas) op.mas.hidden = est.pagina >= est.paginas;
    }
    function ir(id) {
      est.carpeta = id || RAIZ;
      if (op.recordar) { try { localStorage.setItem(op.recordar, est.carpeta); } catch (_) { /* */ } }
      if (arbolHecho) arbolHecho.elegir(est.carpeta);
      if (op.q && est.q) { est.q = ""; op.q.value = ""; }          // entrar a una carpeta es recorrer, no seguir buscando
      return cargar(false);
    }
    if (op.q) {
      let espera = 0;
      op.q.addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(() => { est.q = op.q.value.trim(); cargar(false); }, 250); });
      if (op.q.form) op.q.form.addEventListener("submit", (ev) => { ev.preventDefault(); est.q = op.q.value.trim(); cargar(false); });
    }
    if (op.alcance) op.alcance.addEventListener("change", () => { est.en = op.alcance.value; if (est.q) cargar(false); });
    if (op.mas) op.mas.addEventListener("click", () => { est.pagina += 1; cargar(true); });
    async function pintarArbol() {
      if (!op.arbol || !op.urlCarpetas) return;
      try {
        const datos = await carpetas(op.urlCarpetas);
        arbolHecho = arbol(op.arbol, datos, { actual: est.carpeta, alElegir: ir, clave: op.clave || "hoja", raiz: op.raiz, etiqueta: "Carpetas de la biblioteca" });
      } catch (_) { op.arbol.replaceChildren(); }
    }
    return {
      abrir(carpeta) {
        if (carpeta !== undefined) est.carpeta = carpeta || RAIZ;
        else if (op.recordar) { try { est.carpeta = localStorage.getItem(op.recordar) || RAIZ; } catch (_) { /* */ } }
        if (op.q) est.q = op.q.value.trim();               // lo que ya se escribió mientras la hoja se preparaba
        pintarArbol();
        return cargar(false);
      },
      ir, cargar, estado: () => ({ ...est }),
    };
  }

  // -- «Mover a…» ------------------------------------------------------------------------------------------------------------------
  // Una hoja con el árbol (y un filtro por nombre) para elegir el destino; sirve con teclado y en el teléfono, donde no hay arrastrar.
  function moverA({ titulo, excluir = [], actual = RAIZ, url, nueva, boton = "Mover aquí" }) {
    return new Promise(async (resolver) => {
      let datos;
      try { datos = await carpetas(url, { fresco: true }); } catch (err) { if (window.tvAviso) window.tvAviso(err.message, "error"); resolver(undefined); return; }
      const d = nodo("dialog", "tv-hoja cad-mover");
      d.setAttribute("aria-labelledby", "cad-mover-titulo");
      const cuerpo = nodo("div", "tv-hoja__cuerpo");
      const asa = nodo("div", "tv-hoja__asa"); asa.setAttribute("aria-hidden", "true");
      const h = nodo("h2", "", titulo || "Mover a…"); h.id = "cad-mover-titulo";
      const filtro = nodo("input", "cad-mover__filtro"); filtro.type = "search"; filtro.placeholder = "Buscar una carpeta"; filtro.setAttribute("aria-label", "Buscar una carpeta");
      filtro.autocomplete = "off"; filtro.spellcheck = false;
      const caja = nodo("div", "cad-arbol cad-mover__arbol");
      const elegido = nodo("p", "tv-tenue cad-mover__destino"); elegido.setAttribute("role", "status");
      const acciones = nodo("div", "tv-hoja__acciones");
      const crear = nodo("button", "tv-boton tv-boton--fantasma cad-mover__nueva", "Nueva carpeta aquí"); crear.type = "button";
      const no = nodo("button", "tv-boton tv-boton--secundario", "Cancelar"); no.type = "button";
      const si = nodo("button", "tv-boton", boton); si.type = "button"; si.id = "cad-mover-ir";
      acciones.append(crear, no, si);
      cuerpo.append(asa, h, filtro, caja, elegido, acciones);
      d.append(cuerpo);
      document.body.append(d);
      let destino = actual || RAIZ;
      let a = null;
      const pintar = () => {
        const prohibidas = new Set();
        const por = indice(datos);
        for (const id of excluir) for (const k of debajo(por, id)) prohibidas.add(k);
        a = arbol(caja, datos, { actual: destino, clave: "mover", alElegir: (id) => { destino = id; decirDestino(); }, deshabilitadas: prohibidas, etiqueta: "Destino" });
        decirDestino();
      };
      const decirDestino = () => {
        const ruta = a ? a.ruta(destino) : [];
        elegido.textContent = `Destino: ${["Planos", ...ruta.map((f) => f.nombre)].join(" › ")}`;
        si.disabled = false;
      };
      pintar();
      filtro.addEventListener("input", () => a.mostrar(filtro.value));
      crear.hidden = !nueva;
      crear.addEventListener("click", async () => {
        const nombre = await pedirNombre(destino ? "Nueva carpeta" : "Nuevo proyecto");
        if (!nombre) return;
        try {
          const r = await fetch(nueva, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), Accept: "application/json" },
            body: JSON.stringify({ nombre, padre: destino || null }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.error || "No se pudo crear la carpeta.");
          datos = await carpetas(url, { fresco: true });
          destino = j.carpeta.id;
          pintar();
        } catch (err) { if (window.tvAviso) window.tvAviso(err.message, "error"); }
      });
      let resultado;
      no.addEventListener("click", () => d.close());
      si.addEventListener("click", () => { resultado = destino || null; d.close(); });
      d.addEventListener("close", () => { d.remove(); resolver(resultado); }, { once: true });
      d.showModal();
      a.enfocar();
    });
  }

  // Un nombre corto, con la hoja de la plataforma (nunca el diálogo del navegador).
  function pedirNombre(titulo, valor = "", etiqueta = "Nombre") {
    return new Promise((resolver) => {
      const d = nodo("dialog", "tv-hoja tv-hoja--confirmar");
      const f = nodo("form", "tv-hoja__cuerpo tv-formulario"); f.method = "dialog";
      const h = nodo("h2", "", titulo);
      const campo = nodo("div", "tv-campo");
      const l = nodo("label", "", etiqueta); l.htmlFor = "cad-pedir-nombre";
      const i = nodo("input"); i.id = "cad-pedir-nombre"; i.maxLength = 150; i.required = true; i.autocomplete = "off"; i.value = valor;
      campo.append(l, i);
      const acc = nodo("div", "tv-hoja__acciones");
      const no = nodo("button", "tv-boton tv-boton--secundario", "Cancelar"); no.type = "button"; no.value = "no";
      const si = nodo("button", "tv-boton", "Crear"); si.type = "submit"; si.value = "si";
      acc.append(no, si); f.append(h, campo, acc); d.append(f); document.body.append(d);
      no.addEventListener("click", () => d.close("no"));
      f.addEventListener("submit", (ev) => { ev.preventDefault(); d.close(i.value.trim() ? "si" : "no"); });
      d.addEventListener("close", () => { const v = d.returnValue === "si" ? i.value.trim() : ""; d.remove(); resolver(v); }, { once: true });
      d.showModal(); i.focus(); i.select();
    });
  }

  window.TVCadArbol = Object.freeze({ RAIZ, carpetas, arbol, migas, explorador, moverA, pedirNombre, indice, rutaDe });
})();

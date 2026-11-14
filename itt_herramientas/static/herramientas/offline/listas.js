/* Listas de documentos del teléfono (reportes y cotizaciones): búsqueda, filtros en la URL, orden, selección y descarga en ZIP.
 *
 *   const lista = ITT.listas.crear({ prefijo: "rep", zip: url, tipo: "reporte", alCambiar: pintarLista, campos: {...} });
 *   const visibles = lista.aplicar(registros);     // filtra y ordena con lo que dicen los controles (y la URL)
 *
 * Los filtros viven en el hash («#/?cliente=…&orden=cliente»): se pueden compartir y el botón «Atrás» los recorre. Todo corre
 * en el teléfono, también sin señal; solo la descarga en ZIP necesita conexión.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const $ = (id) => document.getElementById(id);
  const MAXIMO_ZIP = 50;
  const CLAVES = ["q", "cliente", "desde", "hasta", "estado", "orden"];

  /** Sin acentos, en minúsculas y con un solo espacio: «AGRÍCOLA  norte» = «agricola norte». */
  function normalizar(texto) {
    return String(texto || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  /** Clientes sin repetidos (mayúsculas y acentos aparte), en el orden recibido: el primero es el más reciente. */
  function unicos(nombres) {
    const vistos = new Set();
    const salida = [];
    for (const n of nombres) {
      const limpio = String(n || "").replace(/\s+/g, " ").trim();
      const k = normalizar(limpio);
      if (k && !vistos.has(k)) { vistos.add(k); salida.push(limpio); }
    }
    return salida;
  }

  function leerHash() {
    const h = location.hash || "";
    const i = h.indexOf("?");
    const p = new URLSearchParams(i >= 0 ? h.slice(i + 1) : "");
    const f = {};
    for (const k of CLAVES) f[k] = p.get(k) || "";
    return f;
  }

  function crear(config) {
    const p = config.prefijo;
    const el = (n) => $(`${p}-${n}`);
    const seleccion = new Set();           // ids locales seleccionados
    let visiblesSubidos = [];              // registros visibles con servidor_id (para «todos los resultados»)
    let porId = new Map();
    let temporizador = null;

    const controles = { q: el("q"), cliente: el("f-cliente"), desde: el("f-desde"), hasta: el("f-hasta"), estado: el("f-estado"), orden: el("f-orden") };

    function filtros() {
      const f = {};
      for (const k of CLAVES) f[k] = controles[k] ? controles[k].value.trim() : "";
      if (!f.orden) f.orden = "recientes";
      return f;
    }
    function activos(f) { return ["q", "cliente", "desde", "hasta", "estado"].filter((k) => f[k]).length; }

    /** Pasa los filtros a la URL. `empujar`: una entrada nueva en el historial (cambios de controles); escribir solo la reemplaza. */
    function escribir(empujar) {
      const f = filtros();
      const q = new URLSearchParams();
      for (const k of CLAVES) if (f[k] && !(k === "orden" && f[k] === "recientes")) q.set(k, f[k]);
      const texto = q.toString();
      const nuevo = texto ? `#/?${texto}` : "#/";
      if (nuevo === (location.hash || "#/")) return;
      if (empujar) history.pushState(null, "", nuevo); else history.replaceState(null, "", nuevo);
    }

    /** Lleva a los controles lo que dice la URL (al abrir, al volver con «Atrás» o al pegar un enlace). */
    function desdeUrl() {
      const f = leerHash();
      for (const k of CLAVES) {
        const c = controles[k];
        if (!c) continue;
        let v = f[k] || (k === "orden" ? "recientes" : "");
        if (c.tagName === "SELECT" && v && ![...c.options].some((o) => o.value === v)) {
          if (k === "cliente") c.append(new Option(v, v)); else v = k === "orden" ? "recientes" : "";
        }
        if (c.value !== v) c.value = v;
      }
      pintarCuenta();
    }

    function pintarCuenta() {
      const n = activos(filtros());
      const cuenta = el("filtros-cuenta");
      if (cuenta) { cuenta.hidden = !n; cuenta.textContent = n ? String(n) : ""; }
      const quitar = el("f-quitar");
      if (quitar) quitar.hidden = !n;
    }

    /** Opciones del filtro «Cliente»: los clientes de los documentos del teléfono (sin repetidos). */
    function ponerClientes(nombres) {
      const c = controles.cliente;
      if (!c) return;
      const actual = c.value;
      const lista = unicos(nombres).sort((a, b) => a.localeCompare(b, "es", { sensitivity: "base" }));
      while (c.options.length > 1) c.remove(1);
      for (const n of lista) c.append(new Option(n, n));
      if (actual && !lista.some((n) => normalizar(n) === normalizar(actual))) c.append(new Option(actual, actual));
      c.value = actual;
    }

    /** Filtra y ordena `registros` con los accesores de `config.campos`. */
    function aplicar(registros) {
      const f = filtros();
      const c = config.campos;
      const q = normalizar(f.q);
      const cliente = normalizar(f.cliente);
      let salida = registros.filter((b) => {
        if (q && !normalizar(c.texto(b)).includes(q)) return false;
        if (cliente && normalizar(c.cliente(b)) !== cliente) return false;
        const fecha = c.fecha(b);
        if (f.desde && (!fecha || fecha < f.desde)) return false;
        if (f.hasta && (!fecha || fecha > f.hasta)) return false;
        if (f.estado && c.estado && c.estado(b) !== f.estado) return false;
        return true;
      });
      const texto = (fn) => (a, b) => String(fn(a) || "￿").localeCompare(String(fn(b) || "￿"), "es", { sensitivity: "base", numeric: true });
      if (f.orden === "cliente") salida.sort((a, b) => texto(c.cliente)(a, b) || c.reciente(b) - c.reciente(a));
      else if (f.orden === "titulo") salida.sort((a, b) => texto(c.titulo)(a, b) || c.reciente(b) - c.reciente(a));
      else salida.sort((a, b) => c.reciente(b) - c.reciente(a));
      visiblesSubidos = salida.filter((b) => b.servidor_id);
      porId = new Map(registros.map((b) => [b.id, b]));
      for (const id of [...seleccion]) if (!porId.has(id) || !porId.get(id).servidor_id) seleccion.delete(id);
      el("sin-resultados").hidden = !(registros.length && !salida.length);
      pintarCuenta();
      pintarBarra();
      return salida;
    }

    // -- Selección ------------------------------------------------------------------------------------------------------------
    const raizLista = el("vista-lista");
    function modo(si) {
      raizLista.classList.toggle("itt-seleccionando", si);
      el("seleccionar").setAttribute("aria-pressed", si ? "true" : "false");
      if (!si) { seleccion.clear(); for (const x of raizLista.querySelectorAll(".itt-casilla input")) x.checked = false; }
      pintarBarra();
    }
    function pintarBarra() {
      const barra = el("seleccion");
      const n = seleccion.size;
      barra.hidden = !(n || raizLista.classList.contains("itt-seleccionando"));
      el("sel-cuenta").textContent = n ? `${n} seleccionado${n === 1 ? "" : "s"}` : "Elige documentos";
      el("sel-zip").disabled = !n;
      const todos = el("sel-todos");
      todos.hidden = !visiblesSubidos.length || visiblesSubidos.every((b) => seleccion.has(b.id));
      todos.querySelector("span").textContent = `Seleccionar los ${visiblesSubidos.length} resultados`;
    }
    /** Casilla de una tarjeta. Lo que aún no se sube no puede ir al ZIP. */
    function casilla(b, etiqueta) {
      const { h } = ITT.ui;
      const input = h("input", { type: "checkbox", checked: seleccion.has(b.id), disabled: !b.servidor_id,
        "aria-label": b.servidor_id ? `Seleccionar ${etiqueta}` : `${etiqueta}: aún no se sube`,
        onchange: (e) => { if (e.target.checked) seleccion.add(b.id); else seleccion.delete(b.id); e.target.closest("li").dataset.seleccionada = e.target.checked ? "1" : ""; pintarBarra(); } });
      return h("label", { class: "itt-casilla", title: b.servidor_id ? "" : "Aún no se sube" }, input);
    }

    // -- ZIP --------------------------------------------------------------------------------------------------------------------
    async function descargarZip() {
      const ids = [...seleccion].map((id) => porId.get(id)).filter((b) => b && b.servidor_id);
      const estado = el("zip-estado");
      const boton = el("sel-zip");
      const decir = (t) => { estado.hidden = !t; estado.textContent = t || ""; };
      if (!ids.length) return;
      if (ids.length > MAXIMO_ZIP) return ITT.ui.mensaje(config.mensaje, `Elegiste ${ids.length}; el máximo por descarga es ${MAXIMO_ZIP}. Descárgalos en varias partes.`, "error");
      if (!navigator.onLine) return ITT.ui.mensaje(config.mensaje, "Sin conexión. Para descargar el ZIP necesitas señal.", "error");
      boton.setAttribute("aria-busy", "true"); boton.disabled = true;
      decir("Preparando el ZIP…");
      try {
        const r = await ITT.pedirApi(config.zip, { metodo: "POST", json: { elementos: ids.map((b) => [config.tipo, b.servidor_id]) } });
        let fin = r;
        while (!fin.terminada) {
          await new Promise((ok) => setTimeout(ok, 1200));
          fin = await ITT.pedirApi(`${config.tarea.replace("00000000-0000-0000-0000-000000000000", r.tarea)}?formato=json`);
          if (!fin.terminada) decir(`${fin.mensaje || "Preparando el ZIP…"} (${Math.round((fin.fraccion || 0) * 100)} %)`);
        }
        if (fin.estado !== "completada" || !fin.resultado || !fin.resultado.url) throw { mensaje: fin.error || "No se pudo armar el ZIP. Inténtalo de nuevo." };
        const archivo = await ITT.pedirApi(fin.resultado.url, { crudo: true });
        const blob = await archivo.blob();
        const cd = archivo.headers.get("Content-Disposition") || "";
        const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
        const a = ITT.ui.h("a", { href: URL.createObjectURL(blob), download: m ? decodeURIComponent(m[1]) : "Documentos.zip" });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 30000);
        decir("");
        const omitidos = (fin.resultado.omitidos || []).length;
        ITT.ui.mensaje(config.mensaje, omitidos ? `${fin.resultado.mensaje} ${fin.resultado.omitidos[0]}` : fin.resultado.mensaje, omitidos ? "error" : "info");
        modo(false);
        if (config.alTerminar) config.alTerminar();
      } catch (e) {
        decir("");
        ITT.ui.mensaje(config.mensaje, e.mensaje || "No se pudo armar el ZIP. Inténtalo de nuevo.", "error");
      } finally { boton.removeAttribute("aria-busy"); boton.disabled = !seleccion.size; }
    }

    // -- Eventos ------------------------------------------------------------------------------------------------------------------
    controles.q.addEventListener("input", () => { clearTimeout(temporizador); temporizador = setTimeout(() => { escribir(false); config.alCambiar(); }, 150); });
    for (const k of ["cliente", "desde", "hasta", "estado", "orden"]) if (controles[k]) controles[k].addEventListener("change", () => { escribir(true); config.alCambiar(); });
    el("f-quitar").addEventListener("click", () => { for (const k of ["q", "cliente", "desde", "hasta", "estado"]) if (controles[k]) controles[k].value = ""; escribir(true); config.alCambiar(); });
    el("sin-resultados-quitar").addEventListener("click", () => el("f-quitar").click());
    el("filtros-boton").addEventListener("click", (e) => {
      const abierto = e.currentTarget.getAttribute("aria-expanded") !== "true";
      e.currentTarget.setAttribute("aria-expanded", abierto ? "true" : "false");
      el("filtros").classList.toggle("itt-filtros--abiertos", abierto);
    });
    el("seleccionar").addEventListener("click", () => modo(!raizLista.classList.contains("itt-seleccionando")));
    el("sel-salir").addEventListener("click", () => modo(false));
    el("sel-todos").addEventListener("click", () => {
      for (const b of visiblesSubidos) seleccion.add(b.id);
      for (const x of raizLista.querySelectorAll("li[data-id]")) { const b = porId.get(x.dataset.id); const i = x.querySelector(".itt-casilla input"); if (b && i && seleccion.has(b.id)) { i.checked = true; x.dataset.seleccionada = "1"; } }
      if (seleccion.size > MAXIMO_ZIP) ITT.ui.mensaje(config.mensaje, `Son ${seleccion.size}; el máximo por descarga es ${MAXIMO_ZIP}. Ajusta los filtros o quita algunos.`, "error");
      pintarBarra();
    });
    el("sel-zip").addEventListener("click", descargarZip);

    /** Enlace «volver» del editor: regresa a la lista con los mismos filtros. */
    function hashLista() { const h = location.hash || ""; return h.startsWith("#/?") ? h : "#/"; }

    return { filtros, aplicar, desdeUrl, ponerClientes, casilla, hashLista, seleccion, normalizar };
  }

  ITT.listas = { crear, normalizar, unicos };
})(typeof self !== "undefined" ? self : this);

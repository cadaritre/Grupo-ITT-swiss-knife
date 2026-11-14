/* Corrector ortográfico de las cotizaciones: el MISMO servicio del escritorio (diccionarios es/en, términos técnicos y diccionario
 * personal de la cuenta) corre en el servidor; aquí está el diálogo paso a paso. Necesita conexión (el diccionario no viaja al
 * teléfono): sin señal el botón avisa y no marca la cotización como revisada.
 *
 * Las posiciones que devuelve el servidor se refieren al texto ORIGINAL; cada cambio mueve las siguientes del mismo campo,
 * por eso se lleva un desplazamiento (delta) por campo.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const $ = (id) => document.getElementById(id);

  /**
   * opciones: {idioma, campos: [{id, etiqueta, valor}], aplicar(id, valorNuevo)}
   * Resuelve {revisado: bool, cambios: n, problemas: n}.
   */
  async function revisar(opciones) {
    const urls = ITT.cot.urls;
    const valores = new Map(opciones.campos.map((c) => [c.id, c.valor]));
    const etiquetas = new Map(opciones.campos.map((c) => [c.id, c.etiqueta]));
    const original = opciones.campos.map((c) => ({ id: c.id, valor: c.valor }));
    const respuesta = await ITT.cot.pedir(urls.ortografia, { metodo: "POST", json: { idioma: opciones.idioma, campos: original } });
    const problemas = respuesta.problemas.map((p) => Object.assign({}, p, { campo: p.campo }));
    if (!problemas.length) return { revisado: true, cambios: 0, problemas: 0 };

    const dlg = $("dlg-ortografia");
    const delta = new Map();
    const ignoradas = new Set();               // palabras (minúsculas) ignoradas en esta revisión
    const reemplazos = new Map();              // palabra exacta -> reemplazo ("cambiar todas")
    const cambiados = new Set();
    let i = 0;
    let cambios = 0;
    let terminado = false;

    return new Promise((resolver) => {
      const cerrar = (revisado) => {
        if (terminado) return;
        terminado = true;
        for (const id of cambiados) opciones.aplicar(id, valores.get(id));
        dlg.removeEventListener("close", alCerrar);
        if (dlg.open) dlg.close();
        resolver({ revisado, cambios, problemas: problemas.length });
      };
      const alCerrar = () => cerrar(false);

      const posicion = (p) => { const d = delta.get(p.campo) || 0; return [p.inicio + d, p.fin + d]; };
      const reemplazar = (p, nuevo) => {
        const [a, b] = posicion(p);
        const texto = valores.get(p.campo);
        valores.set(p.campo, texto.slice(0, a) + nuevo + texto.slice(b));
        delta.set(p.campo, (delta.get(p.campo) || 0) + nuevo.length - (b - a));
        cambiados.add(p.campo);
        cambios += 1;
      };

      function siguiente() {
        while (i < problemas.length) {
          const p = problemas[i];
          if (ignoradas.has(p.palabra.toLowerCase())) { i += 1; continue; }
          if (reemplazos.has(p.palabra)) { reemplazar(p, reemplazos.get(p.palabra)); i += 1; continue; }
          break;
        }
        if (i >= problemas.length) { cerrar(true); return; }
        const p = problemas[i];
        const [a, b] = posicion(p);
        const texto = valores.get(p.campo);
        const desde = Math.max(0, a - 70);
        const hasta = Math.min(texto.length, b + 70);
        $("o-progreso").textContent = `Palabra ${i + 1} de ${problemas.length}`;
        $("o-campo").textContent = etiquetas.get(p.campo) || "";
        const ctx = $("o-contexto");
        ctx.textContent = "";
        ctx.append((desde ? "…" : "") + texto.slice(desde, a));
        const marca = document.createElement("mark"); marca.textContent = texto.slice(a, b); ctx.append(marca);
        ctx.append(texto.slice(b, hasta) + (hasta < texto.length ? "…" : ""));
        $("o-palabra").textContent = p.palabra;
        $("o-reemplazo").value = p.palabra;
        const sug = $("o-sugerencias");
        sug.textContent = "";
        for (const s of p.sugerencias) {
          const boton = document.createElement("button");
          boton.type = "button"; boton.className = "tv-boton tv-boton--secundario"; boton.textContent = s;
          boton.addEventListener("click", () => { $("o-reemplazo").value = s; $("o-reemplazo").focus(); });
          sug.append(boton);
        }
        if (!p.sugerencias.length) { const t = document.createElement("span"); t.className = "tv-tenue"; t.textContent = "Sin sugerencias"; sug.append(t); }
      }
      const actual = () => problemas[i];
      const sig = () => { i += 1; siguiente(); };

      $("o-cambiar").onclick = () => { const v = $("o-reemplazo").value; if (v && v !== actual().palabra) reemplazar(actual(), v); sig(); };
      $("o-cambiar-todas").onclick = () => {
        const v = $("o-reemplazo").value;
        if (v && v !== actual().palabra) { reemplazos.set(actual().palabra, v); reemplazar(actual(), v); }
        sig();
      };
      $("o-ignorar").onclick = () => sig();
      $("o-ignorar-todas").onclick = () => { ignoradas.add(actual().palabra.toLowerCase()); sig(); };
      $("o-agregar").onclick = async () => {
        const boton = $("o-agregar");
        boton.disabled = true;
        try { await ITT.cot.pedir(urls.palabra, { metodo: "POST", json: { palabra: actual().palabra, idioma: opciones.idioma } }); ignoradas.add(actual().palabra.toLowerCase()); sig(); }
        catch (e) { $("o-progreso").textContent = `No se pudo agregar al diccionario: ${e.mensaje || e}`; }
        finally { boton.disabled = false; }
      };
      dlg.addEventListener("close", alCerrar);
      dlg.showModal();
      siguiente();
    });
  }

  ITT.ortografia = { revisar };
})(typeof self !== "undefined" ? self : this);

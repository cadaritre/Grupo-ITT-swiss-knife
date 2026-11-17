/* Capa de datos de las cotizaciones en el teléfono (arquitectura §18.3, herramientas-web.md §4.1).
 *
 * Los borradores y su sincronización (idempotente, con copia ante un conflicto) son los de offline/documentos.js; aquí queda lo
 * propio de una cotización: el contexto en caché (machotes, valores del perfil), cotización nueva o duplicada y las fotos.
 * El folio lo asigna el servidor al sincronizar (es único en su perfil): una cotización nueva muestra «Folio al sincronizar».
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const cola = () => ITT.cola;
  const docs = ITT.crearDocumentos({ tipo: "cotizacion", lista: "cotizaciones",
    avisoCopia: "Esta cotización cambió mientras la editabas sin conexión: tu versión quedó aparte, con su propio folio.",
    // Lo que pinta la lista de una cotización del equipo que no se abrió en este teléfono (sus datos llegan al abrirla).
    sintetico: (f) => ({ quote_number: f.folio || "", client_name: f.cliente || "", project_title: f.proyecto || "", currency: f.moneda || "", quote_date: f.fecha || "", items: [], images: [] }) });
  const { pedir, url, copiar } = docs;
  const dos = (n) => String(n).padStart(2, "0");
  const hoy = () => { const d = new Date(); return `${dos(d.getDate())}/${dos(d.getMonth() + 1)}/${d.getFullYear()}`; };
  const iguales = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // -- Contexto (machotes, valores del perfil): se guarda para trabajar sin conexión ------------------------------------------
  async function contexto() {
    const cuenta = await cola().ajuste("cuenta", null);
    try {
      const c = await pedir(docs.urls.contexto);
      await cola().guardarAjuste("cot_contexto", c);
      await ITT.perfiles.fijar(c.perfil);                         // el espacio de documentos que se lista y se sincroniza
      return c;
    } catch (e) {
      const guardado = await cola().ajuste("cot_contexto", null);
      if (guardado && (cuenta == null || guardado.cuenta === cuenta)) return Object.assign({ sin_conexion: true }, guardado);
      throw e;
    }
  }

  // -- Registros --------------------------------------------------------------------------------------------------------------
  const listar = docs.listar;

  async function nuevo(ctx, base) {
    const datos = Object.assign(copiar(ctx.base), base || {}, { items: [], images: [], spelling_checked: false, quote_number: "" });
    if (!base || !base.quote_date) datos.quote_date = hoy();
    return docs.crear(datos);
  }

  async function duplicar(ctx, origen) {
    const datos = copiar(origen.datos);
    datos.quote_number = "";
    datos.quote_date = hoy();
    datos.spelling_checked = false;
    return docs.crear(datos, { miniaturas: copiar(origen.miniaturas || {}) });
  }

  // -- Fotos de referencia -----------------------------------------------------------------------------------------------------
  async function miniatura(blob) {
    const mapa = await createImageBitmap(blob);
    const escala = Math.min(1, 160 / Math.max(mapa.width, mapa.height));
    const lienzo = document.createElement("canvas");
    lienzo.width = Math.max(1, Math.round(mapa.width * escala));
    lienzo.height = Math.max(1, Math.round(mapa.height * escala));
    lienzo.getContext("2d").drawImage(mapa, 0, 0, lienzo.width, lienzo.height);
    return lienzo.toDataURL("image/jpeg", 0.7);
  }

  /** Reduce la foto, la pone en la cola de subida (con la misma garantía que Capturar) y devuelve {sha256, miniatura}. */
  async function agregarFoto(id, archivo) {
    const r = await ITT.reducirImagen(archivo);
    const cuenta = await cola().ajuste("cuenta", null);
    const ahoraLocal = () => { const d = new Date(); return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}T${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`; };
    const ya = (await cola().listar()).find((i) => i.sha256 === r.sha256);
    if (!ya) {
      await cola().agregar({
        destino: "herramientas.captura", blob: r.blob, nombre: (archivo.name || "foto").replace(/\.[^.]+$/, "") + ".jpg", tipo: "image/jpeg",
        sha256: r.sha256, ancho: r.ancho, alto: r.alto, cuenta, meta: { tomada: r.exif.tomada || ahoraLocal(), referencia: `cotizacion:${id}`.slice(0, 100), origen: "galeria", sin_ubicacion: true, orden: 0 },
      });
    }
    return { sha256: r.sha256, miniatura: await miniatura(r.blob) };
  }

  ITT.cot = { docs, iniciar: docs.iniciar, pedir, url, contexto, listar, nuevo, guardar: docs.guardar, duplicar, borrar: docs.borrar, agregarFoto, sincronizar: docs.sincronizar, localDe: docs.localDe, refrescar: docs.refrescar, copiar, iguales, hoy, get urls() { return docs.urls; } };
})(typeof self !== "undefined" ? self : this);

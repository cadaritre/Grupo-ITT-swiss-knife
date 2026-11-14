/* Vista previa de un PDF dentro de la app con PDF.js (los navegadores de teléfono no muestran PDF en línea).
 * Las páginas se dibujan en <canvas> al ancho del contenedor; PDF.js se carga solo la primera vez que se pide.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  let lib = null;

  async function cargar(rutas) {
    if (lib) return lib;
    lib = await import(rutas.pdfjs);
    lib.GlobalWorkerOptions.workerSrc = rutas.worker;
    return lib;
  }

  /** Dibuja `blob` (PDF) en `contenedor`. `rutas` = {pdfjs, worker}. Resuelve con el número de páginas. */
  async function dibujar(blob, contenedor, rutas) {
    const pdfjs = await cargar(rutas);
    const doc = await pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()), isEvalSupported: false }).promise;
    while (contenedor.firstChild) contenedor.firstChild.remove();
    const ancho = Math.max(240, contenedor.clientWidth || 320);
    const dpr = Math.min(global.devicePixelRatio || 1, 2);
    for (let n = 1; n <= doc.numPages; n++) {
      const pagina = await doc.getPage(n);
      const base = pagina.getViewport({ scale: 1 });
      const vp = pagina.getViewport({ scale: (ancho / base.width) * dpr });
      const lienzo = document.createElement("canvas");
      lienzo.className = "cot-vp-pagina";
      lienzo.setAttribute("role", "img");
      lienzo.setAttribute("aria-label", `Página ${n} de ${doc.numPages}`);
      lienzo.width = Math.floor(vp.width); lienzo.height = Math.floor(vp.height);
      lienzo.style.width = `${ancho}px`;                       // CSSOM: permitido por la CSP (sin estilos en línea)
      contenedor.append(lienzo);
      await pagina.render({ canvasContext: lienzo.getContext("2d"), viewport: vp }).promise;
    }
    contenedor.dataset.paginas = String(doc.numPages);
    return doc.numPages;
  }

  ITT.vistaPdf = { dibujar };
})(typeof self !== "undefined" ? self : this);

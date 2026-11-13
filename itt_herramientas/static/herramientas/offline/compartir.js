/* Web Share con archivos (arquitectura §18.3). Debe llamarse DESDE un gesto de la persona (clic/toque) y con el
 * archivo ya en memoria: no se descarga ni se genera nada entre el toque y `navigator.share`.
 * Si el navegador no puede compartir ese archivo, se descarga.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});

  function descargar(blob, nombre) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = nombre; a.rel = "noopener";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  /** Devuelve "compartido", "cancelado" o "descargado". */
  async function compartirArchivo(blob, nombre, opciones) {
    const tipo = (opciones && opciones.tipo) || blob.type || "application/octet-stream";
    const archivo = new File([blob], nombre, { type: tipo });
    const datos = { files: [archivo], title: (opciones && opciones.titulo) || nombre, text: (opciones && opciones.texto) || "" };
    if (navigator.share && navigator.canShare && navigator.canShare({ files: datos.files })) {
      try {
        await navigator.share(datos);
        return "compartido";
      } catch (e) {
        if (e && e.name === "AbortError") return "cancelado";             // la persona cerró la hoja de compartir
        /* NotAllowedError (sin gesto) u otro fallo: se descarga */
      }
    }
    descargar(archivo, nombre);
    return "descargado";
  }

  ITT.compartirArchivo = compartirArchivo;
})(typeof self !== "undefined" ? self : this);

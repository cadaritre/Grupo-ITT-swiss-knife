/* Convertidor: al terminar la subida del archivo, abre su mesa de trabajo. */
(function () {
  "use strict";
  const caja = document.getElementById("itt-subida-convertidor");
  const aviso = document.getElementById("itt-convertidor-error");
  if (!caja) return;
  caja.addEventListener("tv-subida:listo", (e) => {
    const id = e.detail && e.detail.resultado && e.detail.resultado.conversion;
    if (id) location.assign(caja.dataset.banco.replace("00000000-0000-0000-0000-000000000000", id));
  });
  caja.addEventListener("tv-subida:error", (e) => {
    aviso.hidden = false;
    aviso.textContent = (e.detail && e.detail.mensaje) || "No se pudo subir el archivo.";
  });
})();

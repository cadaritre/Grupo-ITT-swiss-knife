/* Cuando termina la tarea (análisis o conversión), vuelve a cargar la página para mostrar su resultado. */
(function () {
  "use strict";
  let recargando = false;
  const recargar = () => { if (!recargando) { recargando = true; setTimeout(() => location.reload(), 300); } };
  document.addEventListener("tv:tarea-terminada", recargar);
})();

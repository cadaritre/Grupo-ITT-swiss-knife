/* Subidor de la cola: tus reanudable, confirmación por sha256 y borrado local SOLO tras la confirmación. Clásico
 * (páginas y service worker). Una sola instancia a la vez por navegador (Web Locks).
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const ENDPOINT = "/subidas/";
  let enCurso = false;       // respaldo cuando no hay Web Locks

  async function conLock(trabajo) {
    if (global.navigator && navigator.locks) {
      return navigator.locks.request("itt-subidor", { ifAvailable: true }, (lock) => (lock ? trabajo() : { omitido: true }));
    }
    if (enCurso) return { omitido: true };
    enCurso = true;
    try { return await trabajo(); } finally { enCurso = false; }
  }

  function metaDeSubida(item) {
    const m = item.meta || {};
    return {
      destino: item.destino, name: item.nombre, type: item.tipo, sha256: item.sha256,
      tomada: m.tomada, lat: m.lat, lon: m.lon, precision: m.precision, sin_ubicacion: m.sin_ubicacion ? "1" : "",
      nota: m.nota, referencia: m.referencia, origen: m.origen, orden: m.orden,
    };
  }

  /** Sube un item hasta confirmarlo. Resuelve true si se confirmó y borró; lanza ErrorTus si no. */
  async function subirUno(item, csrf, alProgreso) {
    const { cola, tus } = ITT;
    const blob = await cola.blob(item.id);
    if (!blob) throw new tus.ErrorTus("permanente", "La foto ya no está en el teléfono", 0);
    let ruta = item.uploadUrl || "";
    let offset = 0;
    if (ruta) {
      const pos = await tus.posicion(ruta, csrf);
      if (pos === null || pos.longitud !== blob.size) ruta = "";
      else offset = pos.terminada ? blob.size : pos.offset;
    }
    if (!ruta) {
      ruta = await tus.crear(ENDPOINT, blob.size, metaDeSubida(item), csrf);
      await cola.actualizar(item.id, { uploadUrl: ruta });                      // se guarda ANTES de enviar: así se puede reanudar
      offset = 0;
    }
    if (offset < blob.size) {
      await tus.enviar(ruta, blob, offset, csrf, async (bytes) => { await alProgreso(bytes); });
    }
    const estado = await tus.confirmacion(ruta, csrf);
    if (!estado.terminada) throw new tus.ErrorTus("temporal", "El servidor aún no termina de procesar la subida", 202);
    if (!estado.sha256 || estado.sha256.toLowerCase() !== item.sha256.toLowerCase()) {
      await cola.actualizar(item.id, { uploadUrl: "", progreso: 0 });
      throw new tus.ErrorTus("discrepancia", "El servidor recibió un archivo distinto (sha256 no coincide). Se reenviará.", 0);
    }
    await cola.borrar(item.id);                                                  // única vía de borrado automático
    return { id: item.id, resultado: estado.resultado, sha256: estado.sha256 };
  }

  const subidor = {
    /** Procesa la cola de la cuenta actual. `opciones.alCambiar` se llama con eventos {tipo, item}. */
    async procesar(opciones) {
      const { cola, tus } = ITT;
      const alCambiar = (opciones && opciones.alCambiar) || (() => {});
      return conLock(async () => {
        const csrf = await cola.ajuste("csrf", "");
        const cuenta = await cola.ajuste("cuenta", null);
        const resumen = { subidas: 0, errores: 0, pausada: "" };
        // Lo que quedó "subiendo" por un cierre forzado vuelve a la cola (aquí somos los únicos con el candado).
        for (const i of await cola.listar()) if (i.estado === "subiendo") await cola.actualizar(i.id, { estado: "en_cola" });
        for (const item of await cola.listar()) {
          if (item.cuenta != null && cuenta != null && item.cuenta !== cuenta) continue;     // pertenece a otra cuenta de este teléfono
          if (item.estado === "error" && item.permanente) continue;
          await cola.actualizar(item.id, { estado: "subiendo", intentos: item.intentos + 1, error: "" });
          alCambiar({ tipo: "inicio", id: item.id });
          let ultimo = 0;
          try {
            const hecho = await subirUno((await cola.obtener(item.id)), csrf, async (bytes) => {
              const ahora = Date.now();
              if (ahora - ultimo > 250 || bytes >= item.bytes) {
                ultimo = ahora;
                await cola.actualizar(item.id, { progreso: Math.round((bytes / item.bytes) * 100) });
                alCambiar({ tipo: "progreso", id: item.id, porcentaje: Math.round((bytes / item.bytes) * 100) });
              }
            });
            resumen.subidas += 1;
            alCambiar({ tipo: "confirmada", id: item.id, resultado: hecho.resultado });
          } catch (e) {
            const clase = e.clase || "temporal";
            const mensaje = e.message || "Error";
            if (clase === "red") { await cola.actualizar(item.id, { estado: "en_cola", error: mensaje }); resumen.pausada = "red"; break; }
            if (clase === "sesion") { await cola.actualizar(item.id, { estado: "en_cola", error: "Vuelve a iniciar sesión" }); resumen.pausada = "sesion"; break; }
            if (clase === "discrepancia") await cola.actualizar(item.id, { estado: "en_cola", uploadUrl: "", progreso: 0, error: mensaje });   // la captura se conserva; no es un rechazo definitivo
            else if (clase === "reiniciar") await cola.actualizar(item.id, { estado: "en_cola", uploadUrl: "", error: mensaje });
            else if (clase === "rechazado") {
              const reintenta = (item.rechazos || 0) < 1;                                  // un rechazo puede ser un trozo alterado: se reenvía una vez
              await cola.actualizar(item.id, { estado: reintenta ? "en_cola" : "error", uploadUrl: "", rechazos: (item.rechazos || 0) + 1, permanente: !reintenta, error: mensaje });
            } else if (clase === "permanente") await cola.actualizar(item.id, { estado: "error", permanente: true, error: mensaje });
            else await cola.actualizar(item.id, { estado: "en_cola", error: mensaje });
            resumen.errores += 1;
            alCambiar({ tipo: "error", id: item.id, clase, mensaje });
          }
        }
        return resumen;
      });
    },
    /** Vuelve a intentar un item con error permanente (la persona lo decide). */
    async reintentar(id) { await ITT.cola.actualizar(id, { estado: "en_cola", permanente: false, rechazos: 0, error: "", uploadUrl: "" }); },
  };
  ITT.subidor = subidor;
})(typeof self !== "undefined" ? self : this);

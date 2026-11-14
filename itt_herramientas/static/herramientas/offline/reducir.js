/* Reducción de fotos antes de guardarlas en la cola: en un Web Worker (OffscreenCanvas) y, donde no exista,
 * en el hilo principal con <canvas>. Los metadatos (hora, GPS) se leen ANTES de reducir.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  // Las URL del worker y de exif.js vienen de la plantilla ({% static %}): en producción llevan huella (y son las que guarda el service worker).
  const SCRIPT = global.document && document.currentScript;
  const URL_WORKER = SCRIPT && SCRIPT.dataset.ittReducirWorker;
  const URL_EXIF = SCRIPT && SCRIPT.dataset.ittExif;
  const LADO = 2400;
  const CALIDAD = 0.85;

  async function sha256(blob) {
    const h = new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
    return Array.from(h, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function enWorker(archivo, lado, calidad) {
    return new Promise((resolver, rechazar) => {
      if (!URL_WORKER) { rechazar(new Error("falta la URL del worker")); return; }
      const w = new Worker(URL_WORKER);
      w.onmessage = (e) => { w.terminate(); e.data.ok ? resolver(e.data) : rechazar(new Error(e.data.error)); };
      w.onerror = (e) => { w.terminate(); rechazar(new Error(e.message || "falló el worker")); };
      w.postMessage({ archivo, lado, calidad, exif: new URL(URL_EXIF, location.href).href });
    });
  }

  async function enHiloPrincipal(archivo, lado, calidad) {
    const exif = ITT.exif.leer(await archivo.slice(0, 256 * 1024).arrayBuffer());
    const bitmap = await createImageBitmap(archivo, { imageOrientation: "from-image" });
    const escala = Math.min(1, lado / Math.max(bitmap.width, bitmap.height));
    const lienzo = document.createElement("canvas");
    lienzo.width = Math.max(1, Math.round(bitmap.width * escala));
    lienzo.height = Math.max(1, Math.round(bitmap.height * escala));
    lienzo.getContext("2d").drawImage(bitmap, 0, 0, lienzo.width, lienzo.height);
    const blob = await new Promise((ok, mal) => lienzo.toBlob((b) => (b ? ok(b) : mal(new Error("no se pudo codificar la imagen"))), "image/jpeg", calidad));
    return { ok: true, blob, ancho: lienzo.width, alto: lienzo.height, sha256: await sha256(blob), exif };
  }

  /** Devuelve {blob, ancho, alto, sha256, exif, via: "worker"|"hilo"}. Lanza si el archivo no es una imagen legible. */
  async function reducirImagen(archivo, opciones) {
    const lado = (opciones && opciones.lado) || LADO;
    const calidad = (opciones && opciones.calidad) || CALIDAD;
    const puedeWorker = typeof Worker !== "undefined" && typeof OffscreenCanvas !== "undefined" && !(opciones && opciones.sinWorker);
    if (puedeWorker) {
      try { return Object.assign(await enWorker(archivo, lado, calidad), { via: "worker" }); }
      catch (e) { if (/no se pudo|decod|image|source/i.test(String(e.message))) throw e; /* otro fallo: se intenta en el hilo principal */ }
    }
    return Object.assign(await enHiloPrincipal(archivo, lado, calidad), { via: "hilo" });
  }

  ITT.reducirImagen = reducirImagen;
})(typeof self !== "undefined" ? self : this);

/* Worker: lee el EXIF, reduce la foto (lado mayor 2400 px, JPEG 0.85, orientación aplicada) y calcula su sha256.
 * Mensaje de entrada: {archivo: File|Blob, lado, calidad, exif: URL de exif.js}. Salida: {blob, ancho, alto, sha256, exif}.
 * La URL de exif.js llega en el mensaje (la página la toma de {% static %}): en producción lleva huella y no se puede adivinar.
 */

async function sha256(blob) {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
  return Array.from(h, (b) => b.toString(16).padStart(2, "0")).join("");
}

self.onmessage = async (evento) => {
  const { archivo, lado, calidad, exif: urlExif } = evento.data;
  try {
    if (!self.ITT || !self.ITT.exif) importScripts(urlExif);
    const exif = self.ITT.exif.leer(await archivo.slice(0, 256 * 1024).arrayBuffer());
    const bitmap = await createImageBitmap(archivo, { imageOrientation: "from-image" });
    const escala = Math.min(1, lado / Math.max(bitmap.width, bitmap.height));
    const ancho = Math.max(1, Math.round(bitmap.width * escala));
    const alto = Math.max(1, Math.round(bitmap.height * escala));
    const lienzo = new OffscreenCanvas(ancho, alto);
    lienzo.getContext("2d").drawImage(bitmap, 0, 0, ancho, alto);
    bitmap.close();
    const blob = await lienzo.convertToBlob({ type: "image/jpeg", quality: calidad });
    self.postMessage({ ok: true, blob, ancho, alto, sha256: await sha256(blob), exif });
  } catch (e) {
    self.postMessage({ ok: false, error: String((e && e.message) || e) });
  }
};

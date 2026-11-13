/* Cliente mínimo del protocolo tus 1.0.0 contra /subidas/ de tvplataforma (creación, PATCH; la posición se consulta con el GET JSON del contrato). Clásico. */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const TUS = "1.0.0";

  function metadatos(obj) {
    const enc = new TextEncoder();
    return Object.entries(obj)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => {
        const bytes = enc.encode(String(v));
        let binario = "";
        bytes.forEach((b) => { binario += String.fromCharCode(b); });
        return `${k} ${btoa(binario)}`;
      })
      .join(",");
  }

  /** Error con la clase de falla para decidir qué hacer: "red", "sesion", "reiniciar", "permanente" o "temporal". */
  class ErrorTus extends Error {
    constructor(clase, mensaje, estado) { super(mensaje); this.clase = clase; this.estado = estado || 0; }
  }

  async function mensajeDe(respuesta) {
    try { const j = await respuesta.clone().json(); if (j && j.error) return j.error; } catch (_) { /* sin cuerpo JSON */ }
    return `Error ${respuesta.status}`;
  }

  // Plazos: una conexión que se cae sin avisar (muy común en el teléfono) deja la petición colgada; sin plazo, la cola
  // quedaría «subiendo» para siempre. Al vencer se trata como falta de red y se reintenta.
  const tiempos = { corto: 20000, trozo: 60000 };

  async function pedir(url, opciones) {
    let r;
    const control = new AbortController();
    const plazo = setTimeout(() => control.abort(), (opciones && opciones.method === "PATCH") ? tiempos.trozo : tiempos.corto);
    try { r = await fetch(url, Object.assign({ credentials: "same-origin", cache: "no-store", redirect: "manual", signal: control.signal }, opciones)); }
    catch (e) { throw new ErrorTus("red", control.signal.aborted ? "La conexión no respondió a tiempo" : "Sin conexión con el servidor"); }
    finally { clearTimeout(plazo); }
    if (r.type === "opaqueredirect" || r.status === 0) throw new ErrorTus("sesion", "La sesión caducó", 401);
    if (r.status === 401) throw new ErrorTus("sesion", "La sesión caducó", 401);
    return r;
  }

  const cabeceras = (csrf, extra) => Object.assign({ "Tus-Resumable": TUS, "X-CSRFToken": csrf || "" }, extra);

  const tus = {
    metadatos,
    tiempos,
    ErrorTus,
    /** Crea la subida. Devuelve la RUTA (relativa al origen) de la subida. */
    async crear(endpoint, longitud, meta, csrf) {
      const r = await pedir(endpoint, { method: "POST", headers: cabeceras(csrf, { "Upload-Length": String(longitud), "Upload-Metadata": metadatos(meta) }) });
      if (r.status === 201) {
        const ubicacion = r.headers.get("Location");
        if (!ubicacion) throw new ErrorTus("temporal", "El servidor no devolvió la ubicación de la subida", 201);
        const u = new URL(ubicacion, global.location ? global.location.href : endpoint);
        return u.pathname;
      }
      throw await clasificar(r);
    },
    /** Posición reanudable: {offset, longitud, terminada} o null si la subida ya no existe (404/410).
     * Se pregunta con GET /subidas/<id>/ (JSON del contrato de tvplataforma) en lugar de HEAD: devuelve lo mismo y además dice si
     * la subida ya terminó, y los navegadores manejan mejor una respuesta con cuerpo tras una conexión interrumpida. */
    async posicion(ruta, csrf) {
      const r = await pedir(ruta, { method: "GET", headers: { Accept: "application/json", "X-CSRFToken": csrf || "" } });
      if (r.status === 404 || r.status === 410) return null;
      if (!r.ok) throw await clasificar(r);
      const j = await r.json();
      return { offset: Number(j.desplazamiento || 0), longitud: Number(j.longitud || 0), terminada: !!j.terminada };
    },
    /** Envía `blob` desde `offset` por trozos. `alProgresar(bytes)` se llama tras cada trozo. Devuelve {sha256 del servidor|null}. */
    async enviar(ruta, blob, offset, csrf, alProgresar, tamanoTrozo) {
      const trozo = tamanoTrozo || 2 * 1024 * 1024;
      let sha = null;
      while (offset < blob.size) {
        // El trozo se lee a memoria y se envía como bytes: enviar un Blob guardado en IndexedDB directamente como cuerpo se queda
        // colgado en WebKit tras reabrir el navegador (el archivo de respaldo del Blob no siempre está listo).
        const parte = await blob.slice(offset, Math.min(offset + trozo, blob.size)).arrayBuffer();
        const r = await pedir(ruta, { method: "PATCH", body: parte, headers: cabeceras(csrf, { "Content-Type": "application/offset+octet-stream", "Upload-Offset": String(offset) }) });
        if (r.status === 409) { offset = Number(r.headers.get("Upload-Offset") || 0); continue; }   // el servidor ya tiene más/menos: se retoma ahí
        if (r.status !== 204) throw await clasificar(r);
        offset = Number(r.headers.get("Upload-Offset"));
        sha = r.headers.get("X-TV-Sha256") || sha;
        if (alProgresar) alProgresar(offset);
      }
      return { sha256: sha };
    },
    /** Estado final en el servidor: {terminada, sha256, resultado}. */
    async confirmacion(ruta, csrf) {
      const r = await pedir(ruta, { method: "GET", headers: { Accept: "application/json", "X-CSRFToken": csrf || "" } });
      if (!r.ok) throw await clasificar(r);
      return r.json();
    },
  };

  async function clasificar(r) {
    const mensaje = await mensajeDe(r);
    if (r.status === 401) return new ErrorTus("sesion", "La sesión caducó", 401);
    if (r.status === 400) return new ErrorTus("permanente", mensaje, 400);               // metadatos que el destino no acepta: reintentar no lo arregla
    if (r.status === 403 && /CSRF/i.test(await r.clone().text().catch(() => ""))) return new ErrorTus("sesion", "La sesión necesita renovarse", 403);
    if (r.status === 404 || r.status === 410) return new ErrorTus("reiniciar", mensaje, r.status);
    if (r.status === 422) return new ErrorTus("rechazado", mensaje, 422);            // sha distinto o archivo no válido para el destino
    if (r.status === 403 || r.status === 413 || r.status === 415) return new ErrorTus("permanente", mensaje, r.status);
    if (r.status === 423 || r.status === 429 || r.status >= 500) return new ErrorTus("temporal", mensaje, r.status);
    return new ErrorTus("temporal", mensaje, r.status);
  }

  ITT.tus = tus;
})(typeof self !== "undefined" ? self : this);

// Arranque del editor CAD (Open CAD Studio, compilación web oficial) dentro de Herramientas.
//
// Sustituye la página de inicio de la compilación: sin scripts en línea (CSP), sin analítica de terceros y sin service worker.
// La página que lo contiene (mismo origen) lo abre con ?embed=1 y le pasa los planos por postMessage.
// Sin el aviso del navegador «¿Abandonar el sitio?» (decisión del propietario): lo pondría el editor al tener cambios. La página de
// Herramientas guarda un borrador de recuperación y pregunta con su propia hoja; aquí el editor no puede registrar ese aviso.
(() => {
  const registrar = window.addEventListener;
  window.addEventListener = function (tipo, ...resto) {
    if (String(tipo).toLowerCase() === "beforeunload") return undefined;
    return registrar.call(this, tipo, ...resto);
  };
  try { Object.defineProperty(window, "onbeforeunload", { configurable: false, get: () => null, set: () => {} }); } catch (_) { /* ya definido */ }
  registrar.call(window, "beforeunload", (ev) => { ev.stopImmediatePropagation(); }, true);
})();

const meta = (nombre) => document.querySelector(`meta[name="${nombre}"]`).content;
const carga = document.getElementById("carga");

function prepararAjustes() {
  try {
    // Sin el aviso de donación al abrir (docs/automation/web.md, «Startup dialogs»).
    localStorage.setItem("opencadstudio.automation", "1");
  } catch (_) { /* almacenamiento bloqueado: el editor funciona igual */ }
  try {
    // Español por omisión. Si la persona eligió otro idioma dentro del editor, se respeta.
    const clave = "opencadstudio.settings";
    const guardado = JSON.parse(localStorage.getItem(clave) || "null");
    const idioma = guardado && guardado.settings && guardado.settings.language;
    if (!idioma || idioma === "system") {
      const nuevo = guardado && typeof guardado === "object" ? guardado : {};
      nuevo.settings = { ...(nuevo.settings || {}), language: "es-ES" };
      localStorage.setItem(clave, JSON.stringify(nuevo));
    }
  } catch (_) { /* sin almacenamiento: idioma del navegador */ }
  document.documentElement.lang = "es";
}

function ocultarCarga() {
  if (!carga) return;
  carga.classList.add("oculto");
  setTimeout(() => carga.remove(), 250);
}

function vigilarLienzo() {
  if (document.querySelector("canvas")) return ocultarCarga();
  const observador = new MutationObserver(() => {
    if (document.querySelector("canvas")) { observador.disconnect(); ocultarCarga(); }
  });
  observador.observe(document.body, { childList: true, subtree: true });
}

// ⌘S (Mac): el editor solo atiende Ctrl+S, y Safari abriría «Guardar página». Se le pide a la página de Herramientas que guarde.
addEventListener("keydown", (ev) => {
  if (ev.metaKey && !ev.ctrlKey && !ev.altKey && (ev.key === "s" || ev.key === "S")) {
    ev.preventDefault();
    ev.stopPropagation();
    try { parent.postMessage({ source: "tresvizo-cad-editor", type: "guardar" }, location.origin); } catch (_) { /* sin página contenedora */ }
  }
}, true);

async function arrancar() {
  prepararAjustes();
  vigilarLienzo();
  try {
    const modulo = await import(new URL(meta("ocs-js"), location.href).href);
    const wasm = await modulo.default({ module_or_path: new URL(meta("ocs-wasm"), location.href) });
    window.wasmBindings = modulo;
    dispatchEvent(new CustomEvent("TrunkApplicationStarted", { detail: { wasm } }));
  } catch (error) {
    if (carga) {
      carga.classList.add("error");
      carga.querySelector(".texto").textContent = "No se pudo abrir el editor. Recarga la página.";
    }
    // La página que lo contiene muestra su propio mensaje.
    try {
      parent.postMessage({ source: "opencadstudio-embed", type: "ocs:error", code: "open_failed", message: String(error && error.message || error) }, location.origin);
    } catch (_) { /* sin página contenedora */ }
    throw error;
  }
}

arrancar();

/* Google Picker: la persona elige UNA VEZ la carpeta compartida «GRUPO ITT REPORTES» (permiso drive.file: así la app puede verla).
 * El script de Google se carga solo al pulsar el botón (y necesita `TV_CSP_EXTRA`; ver docs/drive.md). Si `google.picker` ya existe, se usa tal cual.
 */
(function () {
  "use strict";
  const caja = document.getElementById("drive-acciones");
  const boton = document.getElementById("drive-elegir");
  const aviso = document.getElementById("drive-mensaje");
  if (!caja || !boton) return;
  const csrf = () => (document.querySelector("input[name=csrfmiddlewaretoken]") || {}).value || "";
  const decir = (texto) => { aviso.hidden = !texto; aviso.textContent = texto || ""; };

  function cargarPicker() {
    if (window.google && window.google.picker) return Promise.resolve();
    return new Promise((ok, mal) => {
      const cargar = () => window.gapi.load("picker", { callback: ok, onerror: () => mal(new Error("picker")) });
      if (window.gapi && window.gapi.load) return cargar();
      const s = document.createElement("script");
      s.src = caja.dataset.picker;
      s.onload = cargar;
      s.onerror = () => mal(new Error("No se pudo cargar el selector de Google. Revisa la conexión; si persiste, avisa al propietario (política de seguridad)."));
      document.head.append(s);
    });
  }

  boton.addEventListener("click", async () => {
    decir("");
    boton.disabled = true;
    try {
      const r = await fetch(caja.dataset.token, { credentials: "same-origin", cache: "no-store" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "No se pudo preparar el selector.");
      const t = await r.json();
      await cargarPicker();
      const g = window.google.picker;
      const vista = new g.DocsView(g.ViewId.FOLDERS).setIncludeFolders(true).setSelectFolderEnabled(true).setMimeTypes("application/vnd.google-apps.folder");
      const compartidas = new g.DocsView(g.ViewId.FOLDERS).setIncludeFolders(true).setSelectFolderEnabled(true).setEnableDrives(true).setMimeTypes("application/vnd.google-apps.folder");
      const selector = new g.PickerBuilder().addView(vista).addView(compartidas).setOAuthToken(t.access_token).setDeveloperKey(t.api_key).setAppId(t.app_id)
        .setTitle("Elige la carpeta GRUPO ITT REPORTES").setCallback(async (datos) => {
          if (datos.action === g.Action.CANCEL) { boton.disabled = false; return; }
          if (datos.action !== g.Action.PICKED) return;
          const id = datos.docs && datos.docs[0] && datos.docs[0].id;
          const res = await fetch(caja.dataset.carpeta, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ id }) });
          if (res.ok) location.reload();
          else { decir((await res.json().catch(() => ({}))).error || "No se pudo usar esa carpeta."); boton.disabled = false; }
        }).build();
      selector.setVisible(true);
    } catch (e) {
      decir(e.message || "No se pudo abrir el selector de Google.");
      boton.disabled = false;
    }
  });
})();

/* Campo de texto con sugerencias (patrón «combobox» de ARIA 1.2), para el cliente de un reporte.
 *
 *   ITT.combobox(input, () => ["Agrícola Norte", …])
 *
 * Se escribe libre; las sugerencias solo ayudan. Funciona con teclado (↑ ↓ Intro Esc), con el dedo y con lector de pantalla,
 * igual en iPhone (Safari muestra <datalist> en la barra del teclado, poco visible) que en Android y escritorio.
 */
(function (global) {
  "use strict";
  const ITT = (global.ITT = global.ITT || {});
  const MAXIMO = 8;
  let contador = 0;

  const normalizar = (t) => String(t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

  function combobox(input, fuente, alElegir) {
    const id = `itt-sugerencias-${++contador}`;
    const lista = document.createElement("ul");
    lista.id = id;
    lista.className = "itt-sugerencias";
    lista.setAttribute("role", "listbox");
    lista.setAttribute("aria-label", "Sugerencias");
    lista.hidden = true;
    const caja = document.createElement("div");
    caja.className = "itt-combobox";
    input.parentNode.insertBefore(caja, input);
    caja.append(input, lista);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", id);
    input.setAttribute("autocomplete", "off");
    let opciones = [];
    let activa = -1;

    function cerrar() {
      lista.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activa = -1;
    }
    function marcar(i) {
      activa = i;
      [...lista.children].forEach((li, n) => li.setAttribute("aria-selected", n === i ? "true" : "false"));
      if (i >= 0) { input.setAttribute("aria-activedescendant", lista.children[i].id); lista.children[i].scrollIntoView({ block: "nearest" }); }
      else input.removeAttribute("aria-activedescendant");
    }
    function elegir(texto) {
      input.value = texto;
      cerrar();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      if (alElegir) alElegir(texto);
    }
    function abrir() {
      const q = normalizar(input.value);
      const todas = fuente() || [];
      const empieza = [], contiene = [];
      for (const s of todas) {
        const k = normalizar(s);
        if (!k || k === q) continue;
        if (!q || k.startsWith(q)) empieza.push(s); else if (k.includes(q)) contiene.push(s);
      }
      opciones = empieza.concat(contiene).slice(0, MAXIMO);
      while (lista.firstChild) lista.firstChild.remove();
      opciones.forEach((s, n) => {
        const li = document.createElement("li");
        li.id = `${id}-${n}`;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        li.textContent = s;
        // pointerdown (no click): elige antes de que el campo pierda el foco, también con el dedo en iOS.
        li.addEventListener("pointerdown", (e) => { e.preventDefault(); elegir(s); });
        lista.append(li);
      });
      lista.hidden = !opciones.length;
      input.setAttribute("aria-expanded", opciones.length ? "true" : "false");
      marcar(-1);
    }

    input.addEventListener("input", (e) => { if (e.isTrusted !== false) abrir(); });
    input.addEventListener("focus", abrir);
    input.addEventListener("blur", () => setTimeout(cerrar, 0));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); if (lista.hidden) abrir(); marcar(Math.min(opciones.length - 1, activa + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); marcar(Math.max(-1, activa - 1)); }
      else if (e.key === "Enter" && !lista.hidden && activa >= 0) { e.preventDefault(); elegir(opciones[activa]); }
      else if (e.key === "Escape" && !lista.hidden) { e.preventDefault(); cerrar(); }
    });
    return { abrir, cerrar };
  }

  ITT.combobox = combobox;
})(typeof self !== "undefined" ? self : this);

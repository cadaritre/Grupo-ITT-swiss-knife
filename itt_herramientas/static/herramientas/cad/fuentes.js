/* Fuentes de CAD: «Reemplazar» sube el archivo elegido en cuanto se elige (sin scripts en línea: CSP). */
document.querySelectorAll(".cad-fuentes__reemplazar input[type=file]").forEach((i) => {
  i.addEventListener("change", () => { if (i.files.length) i.form.requestSubmit(); });
});

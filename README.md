# Herramientas Grupo ITT (rama `web`)

App Django instalable `itt_herramientas` (paquete `itt-herramientas-web`) para el Portal TresVizo
(`app.tresvizo.com/herramientas/`). Esta rama lleva solo las herramientas ligeras; el escritorio completo (Tk,
PPK de dron, nubes de puntos, MSI) sigue en `main` y será el módulo local.

## Estructura

- `itt_herramientas/dominio/`: lógica movida con `git mv` desde `photo_report_app/` de `main`, sin interfaz ni
  efectos al importar (ajustes por contexto, caché configurable, Liberation Sans incluida).
- `itt_herramientas/pruebas/`: pruebas del dominio.
- `paridad/`: comparación contra `main` (`pytest paridad` o `python -m paridad.ejecutor`) y
  `golden_windows.py` para los archivos dorados de Windows.

## Pruebas

    python3.13 -m venv .venv && .venv/bin/pip install -e '.[pruebas,paridad]'
    .venv/bin/python -m pytest

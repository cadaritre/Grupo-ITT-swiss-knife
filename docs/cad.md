# CAD en Herramientas

CAD es la biblioteca compartida de planos DWG y DXF de Herramientas (`/cad/`, grupo «Gabinete»), con el editor Open CAD Studio dentro de
la página. Código: `itt_herramientas/cad/`; plantillas `templates/herramientas/cad/`; estáticos `static/herramientas/cad/`; pruebas
`pruebas/test_cad.py` y `pruebas_navegador/test_cad_navegador.py`. Despliegue y verificación en el servidor: `despliegue/SALIDA-DE-CAD.md` del
Portal.

## El editor fijado

`itt_herramientas/cad/editor/upstream.json` fija la versión, la **fuente** y el **SHA-256** de cada pieza:

- `web`: la compilación web (ZIP de Trunk, ~63 MB, wasm de 98 MB / 33 MB con gzip);
- `nativo.x86_64` y `nativo.aarch64`: el AppImage de Linux que imprime y convierte sin pantalla (el x86_64 para el servidor; el aarch64 para
  la VM de ensayo).
- `fuente`: `{"tipo": "publica", "repo": "HakanSeven12/OpenCADStudio", "tag": "v2026.45"}` (descarga directa) o
  `{"tipo": "gh", "repo": "cadaritre/OpenCADStudio-TresVizo", "tag": …}` para nuestra versión corregida en el repositorio privado: se baja con
  `gh release download` y la sesión de gh de la Mac. Una pieza puede traer su propia `fuente`. El servidor nunca descarga nada.
- `puente`: si la compilación trae el puente `?embed=1` (llegó a `main` de Open CAD Studio el 14-11-2026, después de v2026.45). Sin él, la
  página usa el canal de control y `openCadStudioSaveFile`, y «solo lectura» lo hace cumplir la página (no se guarda), no el editor.

`cad/editor/preparar.py` (solo biblioteca estándar) baja una pieza, comprueba tamaño y SHA-256 y, para el editor, lo extrae y pone encima
nuestra página de arranque (`index.html`, `arranque.js`, `editor.css`): sin scripts en línea, sin la analítica de la página oficial, sin service
worker, y con las listas de videos, conversaciones y patrocinadores vacías. Deja `MANIFIESTO.json` (SHA-256 de cada archivo) y un `.gz` de
cada archivo grande para Caddy.

```sh
python3 itt_herramientas/cad/editor/preparar.py editor --destino ~/.cache/tresvizo-cad/editor
python3 itt_herramientas/cad/editor/preparar.py nativo --arch aarch64 --destino DIR
python3 itt_herramientas/cad/editor/preparar.py verificar --destino ~/.cache/tresvizo-cad/editor
```

**Actualizar:** cambiar versión, fuente, nombres, tamaños y SHA-256 (y `puente`) en `upstream.json`; correr las pruebas, también las del navegador.

### Red a terceros

Lo que se encontró en v2026.45 y cómo queda:

| Llamada | Qué hace | Cómo se corta |
| --- | --- | --- |
| `https://open-aec.com/shared/analytics.js` | analítica en la página oficial | nuestra página no la carga |
| `videos.json` → miniaturas `https://i.ytimg.com/…` | pantalla de inicio | `videos.json` vacío y CSP `img-src 'self'` |
| `discussions.json`, `supporters.json` | conversaciones y patrocinadores de GitHub | listas vacías |
| `sw.js` | service worker propio del editor | no se sirve ni se registra |
| enlaces a GitHub, Patreon, Reddit, YouTube, descarga de escritorio | solo si la persona los pulsa (`window.open`) | no son llamadas en segundo plano |
| revisión de actualizaciones | no existe en la compilación web | — |

La CSP de `/cad/editor/` (`cad/editor/__init__.py`, igual que en `tools.caddy` del Portal) deja `connect-src`, `img-src` y `font-src` en
`'self'`: cualquier otra llamada a terceros se bloquea. Las fuentes Noto vienen dentro del paquete.

## La página del editor

- Abre la versión vigente (o una anterior con `?version=n`, o el borrador) por el canal de control (`op: open`) o por `ocs:open` (con puente).
- **Guardar** (botón y Ctrl/⌘+S de la página) pide los bytes por el canal (`op: save`); lo que la persona guarda dentro del editor llega por
  `openCadStudioSaveFile` o `ocs:save`. Todo termina en `POST /cad/<id>/guardar/` con el bloqueo de esta página y la versión de base: si el
  plano cambió o se perdió el bloqueo, responde 409 y se ofrece «Guardar como copia». Nada se pisa.
- «Sin guardar» sale de la revisión del dibujo en el editor (`op: state`, cada segundo) contra la del último guardado.
- Bloqueo con latido (30 s; caduca a los 90), borrador de recuperación cada 2 minutos con cambios, sin el aviso «¿Abandonar el sitio?».
- En el teléfono (pantalla ≤ 767 px y puntero táctil) los planos se abren para ver.

## Extensiones de la página: `window.TVCad`

`static/herramientas/cad/control.js` (versión 1) da a otras partes de la página (la «Civil») el canal de control del editor y la barra
`#cad-extensiones`, encima del editor. Su JS se agrega en `{% block extensiones_cad %}` de `cad/plano.html`.

| API | Qué hace |
| --- | --- |
| `TVCad.listo` | promesa que se cumple con el estado cuando el editor abrió el plano y su canal respondió |
| `TVCad.estaListo()` | `true` / `false` |
| `TVCad.estado()` | `{listo, editable, plano: {id, nombre, formato, revision}}` (`editable` = esta página tiene el bloqueo) |
| `await TVCad.pedir(peticion, {tiempoMs})` | manda una petición del canal (API-SPEC de Open CAD Studio) y completa `protocol`, `request_id` y `document_id` |
| `await TVCad.ejecutar("LINE 0,0 10,10")` | atajo de `{op: "run", cmd}` |
| `TVCad.barra()` | el elemento `#cad-extensiones` (`role="toolbar"`), ya visible |
| `TVCad.on(evento, fn)` / `off` | `listo`, `cargado`, `cambio` (`{sucio}`), `guardado` (`{revision, formato}`) |

`pedir` rechaza con `TVCad.ErrorEditor` y su `codigo`: `no_listo` (aún no abre el plano), `sin_canal`, `tiempo` o `rechazada` (respuesta
`ok: false`, completa en `error.respuesta`). Guardar sigue siendo de la página: un cambio por el canal marca el plano «Sin guardar».

El canal (`?automation=1`) solo se alcanza desde el mismo origen: el editor vive en un marco de `tools.tresvizo.com`, su CSP pone
`frame-ancestors 'self'` y el navegador no deja que otro origen toque el marco.

## Imprimir a PDF y convertir: prueba de factibilidad (15-11-2026)

El motor nativo de Linux (`OpenCADStudio --serve`, protocolo 1, `plot` y `save`) se ensayó en la VM Ubuntu 24.04 aarch64, **sin pantalla ni
GPU** y sin variables de entorno de escritorio: sus pruebas de GPU fallan y sigue sin ventana (`"mode": "headless"`). Imprime PDF 1.3 en
Carta, Oficio, Doble carta y A0–A5, en espacio modelo o una página por presentación, a escala o ajustado, y guarda DWG ↔ DXF (con
`lossy_conversion_not_acknowledged` cuando se perdería algo).

| Plano (DXF) | Abrir | Imprimir A1 ajustado | Pico de memoria |
| --- | --- | --- | --- |
| de prueba (5 entidades) | < 0.1 s | < 0.1 s | 94 MB |
| 20 000 entidades mezcladas | 0.14 s | 0.69 s | 0.66 GB |
| 50 000 mezcladas | 0.32 s | 1.8 s | 1.55 GB |
| 100 000 mezcladas | 0.6 s | 4.5 s | 2.5 GB |
| 50 000 líneas | — | 0.15 s | 0.43 GB |
| 50 000 polilíneas | — | 0.58 s | 0.60 GB |
| 50 000 textos | — | 4.0 s | 2.49 GB |

Los textos dominan (~50 kB cada uno). De ahí el trabajador propio `tv-tareas-cad` (cola `cad`, una tarea a la vez, `MemoryHigh` 1.5 GB,
`MemoryMax` 2 GB): un plano que no cabe mata solo al proceso del motor y la tarea dice «demasiado grande». Un proceso por trabajo: la memoria
se libera completa. El AppImage se extrae al desplegar (sin FUSE). El de x86_64 se comprueba al desplegar (`tvctl` lo arranca sin pantalla).

## Pruebas

```sh
uv run pytest itt_herramientas/pruebas/test_cad.py                    # unitarias, con un motor de prueba que habla el mismo protocolo
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_navegador.py -k "not webkit"   # luego -k webkit; un navegador a la vez
```

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
| `TVCad.ocupar()` / `soltar()` / `ocupado()` | una extensión avisa que trabaja con el editor (espera una elección en el dibujo, inserta un resultado): mientras tanto no se guarda el borrador de recuperación y «Guardar» pide esperar, porque el editor rechaza guardar a media operación |
| `TVCad.on(evento, fn)` / `off` | `listo`, `cargado`, `cambio` (`{sucio}`), `guardado` (`{revision, formato}`) |

`pedir` rechaza con `TVCad.ErrorEditor` y su `codigo`: `no_listo` (aún no abre el plano), `sin_canal`, `tiempo` o `rechazada` (respuesta
`ok: false`, completa en `error.respuesta`). Guardar sigue siendo de la página: un cambio por el canal marca el plano «Sin guardar».

El canal (`?automation=1`) solo se alcanza desde el mismo origen: el editor vive en un marco de `tools.tresvizo.com`, su CSP pone
`frame-ancestors 'self'` y el navegador no deja que otro origen toque el marco.

## Espacio, archivos que faltan y autoría

- **Espacio:** CAD tiene su propia reserva de 20 GB, aparte de los 12 GB de Herramientas (Portal, `docs/arquitectura.md` §9.1 y §13). La
  biblioteca usa **18 GiB** de ella (`cad/servicio.py`, `LIMITES["biblioteca_bytes"]`: versiones vigentes y anteriores, papelera y
  borradores) y deja el resto a salidas (PDF, conversiones, ZIP) y temporales, para que tv-salud no quede en «crítico» con la biblioteca
  llena. Al llenarse se recortan las versiones más viejas (siempre quedan `VERSIONES_MINIMAS` = 5 por plano); si aun así no cabe, se dice
  claro. El recorte va dentro de la transacción del guardado, después de comprobar bloqueo y versión, y de uno en uno (candado de
  PostgreSQL): un guardado rechazado no borra nada, restaurar nunca recorta la versión que se restaura y los archivos se borran al
  confirmar. Un borrador de recuperación nunca recorta versiones: si no cabe, se niega.
  CAD **no** cuenta en la cuota de 5 GB por cuenta de Herramientas (`TV_HERRAMIENTAS_CUOTA`), que solo mide las fotos de campo.
- La biblioteca muestra «Planos: x de 18 GB» (aviso desde el 90 %); quien administra ve además el desglose.
- **Sin respaldo:** por decisión del propietario (15-11-2026) los planos no van al respaldo en R2 (`/srv/tresvizo/herramientas/cad` está
  excluido). Tras restaurar o cambiar de servidor puede haber renglones sin archivo: la lista los muestra como «Archivo no disponible», el
  plano lo explica en vez de abrir el editor, se pueden eliminar y nunca hay un error. `manage.py cad_reconciliar` (sin argumentos;
  `--simular` opcional; idempotente) marca las versiones sin archivo (`VersionPlano.sin_archivo`), desmarca las que volvieron y quita los
  borradores sin archivo; `tvctl cad-reconciliar` lo corre. También cuenta lo que hay en el disco sin renglón (carpetas y archivos de más
  de una hora, p. ej. tras restaurar la base a un punto anterior con el disco intacto) y solo lo borra con `--borrar-huerfanos`: puede ser
  la única copia de versiones posteriores, así que ni el comando sin argumentos ni la limpieza diaria lo tocan.
- **Autoría:** la lista dice quién creó cada plano y quién lo guardó y cuándo; el plano, su línea de guardado y creación; cada versión,
  «Guardada por … · fecha».

## Fuentes SHX

Los planos mexicanos usan txt, romans, simplex, isocp y bigfonts. El editor web no las trae; quien administra las sube desde su AutoCAD en
**CAD → Fuentes** (`/cad/fuentes/`, solo `admin`): una o varias `.shx` (se revisa el encabezado «AutoCAD-86 shapes/unifont/bigfont»), hasta
2 MB cada una y 50 MB en total; subir una con el mismo nombre la reemplaza.

- **Dónde:** `TV_HERRAMIENTAS_ARCHIVOS/fuentes/<sha256>.shx` y la tabla `FuenteCad`, fuera de `cad/`: entran en el respaldo normal (son pocos
  kB) y no hay que volver a subirlas tras un cambio de servidor. Nunca se publican: solo las bajan cuentas con CAD.
- **Al abrir un plano:** `editor.js` las baja (dirección con su huella, `Cache-Control: private, immutable` y `ETag`) y las registra con
  `font_register` (compilación tv-v2026.45-5) antes de abrir el plano; el editor las tiene solo en memoria. Una que falla no detiene nada:
  quien administra ve un aviso discreto, y la página «Fuentes» marca «El editor no la pudo usar». El editor informa cuántas letras tiene.

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

Los textos dominan (~50 kB cada uno). De ahí el trabajador propio `tv-tareas-cad` (cola `cad`, una tarea a la vez, `MemoryMax` 2 GB,
`OOMPolicy=continue`). Por la pila real en la VM: 15 000 textos se imprimen en 2 s; 50 000 (~2.5 GB) fallan en 5 s con «demasiado grande», el
núcleo mata solo al motor (que se marca primero con `oom_score_adj` 1000) y el siguiente trabajo sale bien sin reiniciar el trabajador. Con
`MemoryHigh` el motor solo se frenaba hasta agotar su tiempo (5 min), y sin marcarse primero el núcleo de la VM mataba a los trabajadores de
Python. Un proceso por trabajo: la memoria se libera completa. El AppImage se extrae al desplegar (sin FUSE). El de x86_64 se comprueba al desplegar (`tvctl` lo arranca sin pantalla).

**Lo que hace la página al imprimir** (`cad/nativo.py`):
- **Presentaciones:** con `layout:"all"` el motor imprimiría también el espacio modelo y una hoja en blanco por cada presentación vacía
  (la «Presentación1» que traen casi todos los planos). Por eso se leen las presentaciones (`records` de `objects/Layout`), se descartan
  las que no tienen nada en su bloque de papel salvo la ventana general (Viewport de id 1), se imprime cada una aparte en el orden de sus
  pestañas y se unen con `pypdf` (BSD, Python puro). Sin ninguna con dibujo se dice «Imprime el espacio modelo».
- **Escala:** el motor toma «1:100» como 1 mm de papel = 100 unidades; en espacio modelo se convierte según las unidades del plano
  (`header.insertion_units`; sin unidades, metros). Las presentaciones ya están en mm de papel.
- **Fuentes:** las de CAD → Fuentes se enlazan en la carpeta de fuentes del motor antes de arrancarlo.
- **Memoria:** el motor corre con `RLIMIT_DATA` = `HERRAMIENTAS_CAD_MEMORIA_MB` − 256 MB y su aborto por memoria se dice como «demasiado
  grande»; además, tope de archivo de 2 GB, sin volcados y con sus avisos en un archivo (no en una tubería que nadie lee).

## Pruebas

```sh
uv run pytest itt_herramientas/pruebas/test_cad.py                    # unitarias, con un motor de prueba que habla el mismo protocolo
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_navegador.py -k "not webkit"   # luego -k webkit; un navegador a la vez
```

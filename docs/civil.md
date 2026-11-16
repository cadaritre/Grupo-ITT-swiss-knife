# Civil en CAD

Herramientas de topografía al estilo CivilCAD dentro de la página de un plano de CAD (`/cad/<id>/`): una barra «Civil» encima del editor
con sus menús y hojas. Especificación completa (funciones, convenciones del dibujo, fases): `docs/especificacion.md` del repositorio del
motor, `cadaritre/TresVizo-Civil`.

Código: `itt_herramientas/civil/` (servidor y motor fijado), `templates/herramientas/cad/civil*.html`, `static/herramientas/cad/civil.js` y
`civil.css`. Pruebas: `pruebas/test_civil.py` y `pruebas_navegador/test_civil_navegador.py`.

## Qué hay

| Menú | Acciones |
| --- | --- |
| Puntos | Importar (PNEZD, PENZD, NEZ… con orden y separador reconocidos, vista previa, renglones descartados con su motivo; terreno o proyecto) · Exportar · Dibujar punto (señalado o por coordenadas, con el siguiente número libre) · Editar punto (número, elevación, descripción, posición) · Etiquetas de puntos (mostrar u ocultar número, elevación y descripción —capa por rótulo— y reescribir los decimales de la elevación) · Convertir puntos (terreno ↔ proyecto, conservando sus datos) · Renumerar (desde N, de cuánto en cuánto, prefijo; por número actual, de oeste a este o de norte a sur) · Localizar punto (por número: la vista va a él y queda seleccionado) · Reporte de puntos (PDF o Word, con latitud y longitud si se pide) |
| Triangulación | Triangular (líneas de quiebre, límite, islas, arista máxima, sin los puntos de ciertas descripciones —ARBOL, POSTE, BAR*—; reemplaza la anterior) · Invertir triángulos · Borrar triángulos (los que elija, fuera de un límite, dentro de un polígono, lados largos) · Agregar triángulo · Insertar puntos (parten su triángulo o lado y los vecinos se ajustan a Delaunay) · Revisar triangulación (repetidos, encimados, sin superficie, lados con más de dos triángulos, muy delgados: marcados en rojo y seleccionados) |
| Curvas | Curvas de nivel (intervalo, maestras, suavizado sin cruces, etiquetas) · Actualizar curvas (desde los triángulos que hay en el dibujo) · Etiquetar curvas (donde se señala o a lo largo, cortando la curva bajo el texto) · Suavizar curvas (las elegidas, sin tocar a las demás) |
| Cuadros | Cuadro de construcción (rumbos N 45°30'15" E, lados curvos, ha-a-ca; formatos normal, RAN —coordenadas UTM y geográficas— y UTM-GPS —además factor de escala y convergencia por vértice y la superficie sobre el terreno—; también «Descargar DXF», el antiguo IT-10) · Cuadro de curvas · Rumbo y distancia de lados · Numerar vértices · Memoria descriptiva (PDF o Word, con colindancias lado por lado) |
| Plano | Cuadrícula UTM (todo el plano o dos esquinas; líneas o cruces; marco; coordenadas al margen y latitud y longitud en las esquinas; reemplaza la anterior) · Lotificar manzana (lotes iguales, de una superficie o de un frente, con cortes perpendiculares al lado de la calle; número y superficie en cada lote y cuadro de áreas) |
| Perfiles y secciones | Perfil del eje (cadenamiento, rasante con curvas verticales) · Secciones transversales (plantilla sencilla o de carretera: carriles y acotamientos con su pendiente, cuneta en corte, pavimento —terracerías a la subrasante— y despalme; sobreelevación y ampliación en las curvas del eje según la SCT; taludes; áreas) |
| Volúmenes | Por secciones (áreas medias) · Entre superficies (exacto, línea de ceros opcional, resultado en la hoja o también en el plano) · Curva masa (línea compensadora la mejor —menor momento de acarreo— o en una ordenada; puntos de compensación, volumen y distancia media de cada tramo, sobreacarreo en m³·hm sobre el acarreo libre, préstamo y desperdicio) |
| 1:1000 (botón) | Escala del plano, zona UTM y altura de los textos en mm de papel |

Civil aparece para quien puede editar planos (`cad`, `admin`) en la versión vigente; para dibujar hace falta tener el control del plano
(exportar puntos solo lee y funciona sin él). En una barra angosta los menús se compactan (sin flechas y, más angosta, solo iconos);
cada menú se abre bajo su botón aunque la barra se desplace a lo ancho.

## Cómo funciona

```
página (civil.js) ──TVCad.pedir──▶ editor (lee entidades: records, query)
      │                                   ▲
      └─POST /cad/civil/<orden>/ ──▶ servidor (civil/servicio.py → motor tvcivil) ──▶ resultado: DXF + resumen
                                          │
                       dibujo temporal (open) + entities_copy_to ──▶ plano
```

- **El dibujo es la fuente de verdad.** Puntos (bloques `PT_TERRENO` / `PT_PROYECTO` con atributos NUM, ELEV y DESC), triangulación (3DFACE
  en `TRIANGULACION` / `TRIANGULACION_PROYECTO`), curvas y tablas son entidades normales del DWG/DXF con XDATA `TVCIVIL`. Editar el dibujo
  y luego «Actualizar curvas» funciona, y todo sobrevive a guardar y volver a abrir.
- **Leer:** los puntos con `query` y proyección (`fields: handle, block, position, attributes`); las caras con `records` y
  `paths: /first_corner…/third_corner` (≈ 360 bytes por cara en lugar de ≈ 1 kB).
- **Calcular:** en el servidor. Las órdenes ligeras (puntos, cuadros, invertir, borrar o agregar triángulos) responden en la misma petición;
  las pesadas (triangular, revisar, curvas, perfil, secciones, volumen) van a la cola `herramientas` de tareas (hasta 3 min, ≤ 768 MB). Las tareas
  solo llevan identificadores: la entrada va a `cad/tmp/` y el resultado a `cad/salidas/<cuenta>-<token>.civil.json`, que solo ve quien lo
  pidió y que `cad.servicio.limpiar()` borra al día siguiente. La página manda el JSON comprimido con gzip (`Content-Encoding: gzip`); el
  servidor lo lee con su propio tope (32 MB comprimido, 160 MB de JSON, 100 000 puntos, 200 000 triángulos), no con el de formularios.
- **Escribir:** lo nuevo llega como DXF; la página lo abre como dibujo temporal y lo copia al plano con `entities_copy_to`: capas con color,
  estilo `CIVIL`, bloques con atributos, alineaciones, combas y XDATA llegan intactos, en **un solo paso de deshacer** y del orden de 10 000
  entidades por segundo. Nada se guarda en la biblioteca: el plano queda «Sin guardar» y se guarda con «Guardar», como siempre.

### Deshacer

| Acción | Pasos de deshacer (compilación propia, -4 en adelante) | En la compilación oficial |
| --- | --- | --- |
| Importar, triangular la primera vez, curvas la primera vez, cuadro, perfil, secciones, volumen | 1 | 1 |
| Invertir triángulos (dos `set_properties` en un `batch`) | 1 | 2 |
| Borrar triángulos | 1 | 1 |
| Volver a triangular, actualizar curvas (lo nuevo se copia y después se borra lo anterior) | 2 | 2 |

`civil.js` (`enUnPaso`) manda las ediciones del plano como un `batch` con su `label` («Civil: invertir triángulos»); si el editor
responde `unknown_operation` (la compilación oficial), las manda una por una y lo recuerda. El reemplazo queda en dos pasos porque el
`batch` de la -4 solo actúa sobre su propio documento (`Every step acts on the batch's document_id`) y `entities_copy_to` lee del dibujo
temporal; se copia primero y se borra después, así el primer «deshacer» devuelve lo anterior y nunca queda el plano vacío. Llevarlo a un
paso pide al editor un `entities_copy_to` desde otro documento dentro del lote (pedido a la compilación propia).

Mientras Civil espera una elección o inserta un resultado llama a `TVCad.ocupar()`: la página no guarda el borrador de recuperación y
«Guardar» pide esperar, porque el editor rechaza guardar a media operación (`busy`).

### Elegir y señalar

Se usan `user_select` (clic y ventana con clic-clic; Enter termina, Esc cancela) y `getpoint` del editor. «Invertir triángulos»
pide señalar el lado común (como en CivilCAD): con `getpoint` se toma el punto, se leen las caras cercanas (`query` con `bounds`) y se
invierten las dos que comparten el lado más cercano; la orden se repite hasta Esc. Mientras el editor espera, la hoja
se repliega y un aviso sobre el lienzo dice qué elegir. En la compilación oficial v2026.45 estas dos órdenes responden `gui_required` en la
web; entonces Civil usa lo que la persona tenga seleccionado, y si no hay nada pide «selecciónalo en el dibujo y pulsa Listo»; las
ubicaciones (esquina del cuadro, origen del perfil) toman la automática. Nunca falla en silencio. Desde tv-v2026.45-3 las dos funcionan.

### Escala

La escala viaja con el plano: los bloques de punto se dibujan en mm de papel y se insertan a escala E/1000, así que al abrir un plano Civil
la lee de sus puntos. Las alturas propias (mm por tipo de texto) se guardan en el navegador por plano. Cambiar la escala con «Ajustar los
puntos ya dibujados» vuelve a dibujar los puntos a la nueva escala (orden `redibujar_puntos`): los rótulos quedan tal como estaban, también
los apagados y los decimales de la elevación. «Renumerar puntos» usa la misma orden.

## El motor fijado

`itt_herramientas/civil/motor/` es una copia del paquete `tvcivil` (puro Python: numpy, Shapely, ezdxf) de un commit exacto del repositorio
privado `cadaritre/TresVizo-Civil`. `MOTOR.json` guarda repositorio, commit, versión y el SHA-256 de cada archivo, y una prueba falla si la
copia se tocó a mano. **Por qué copia y no dependencia:** el servidor nunca tiene acceso a repositorios privados ni recibe tokens (el
paquete de la versión se arma en la Mac), y una dependencia de Git privada en el `uv.lock` obligaría a uno; una rueda en Git sería un
binario opaco. La copia fijada es reproducible, se revisa como código y entra sola en la rueda de Herramientas y en el paquete de
despliegue, sin cambios en el Portal.

Actualizar, en la Mac:

```sh
python3 itt_herramientas/civil/actualizar_motor.py --repo ~/Documents/TresVizo-Civil --ref <commit>
uv run pytest itt_herramientas/pruebas/test_civil.py
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor uv run pytest itt_herramientas/pruebas_navegador/test_civil_navegador.py -k chromium   # luego -k webkit
```

## Pruebas

- `pruebas/test_civil.py`: huella del motor, órdenes ligeras y pesadas (con gzip y por la cola), resultado solo de la cuenta, errores con su
  mensaje, permisos, tope de tamaño y que la página traiga Civil solo a quien edita.
- `pruebas_navegador/test_civil_navegador.py`, con el editor real y coordenadas UTM: importar 5 000 puntos PNEZD, deshacer y rehacer,
  triangular, invertir dos triángulos elegidos con el ratón, curvas, cuadro de un lote con lado curvo, perfil y secciones con rasante,
  volumen contra una plataforma, guardar en DWG y volver a abrir; 360 px; y el camino sin `user_select` (selección + «Listo»).
  `ITT_CAPTURAS=carpeta` deja capturas de cada hoja y de lo dibujado.
- El motor tiene sus propias pruebas con resultados conocidos (128) en su repositorio.

## Geodesia y documentos (v1.1)

- **UTM ↔ geográficas** con pyproj sobre GRS80 (ITRF2008, el marco oficial; con WGS84 la diferencia del elipsoide es de una décima de
  milímetro). La zona y el hemisferio se eligen en la escala del plano y se guardan con ella en el navegador. Convergencia γ con su signo
  (negativa al oeste del meridiano central en el norte); factor de escala de la proyección, sin factor de elevación.
- **Memoria descriptiva y reporte de puntos:** el motor da el contenido (título, datos, párrafos, tabla, notas) y el servidor lo entrega
  como PDF (ReportLab con Liberation Sans) o como DOCX escrito a mano (Office Open XML mínimo: abre en Word, LibreOffice, Pages y Google
  Docs, sin otra dependencia). Nada se guarda en el servidor: el archivo vuelve en la respuesta y la página lo descarga.

## Carreteras: sobreelevación, ampliación y despalme

**Fuente:** *Manual de Proyecto Geométrico de Carreteras*, Secretaría de Comunicaciones y Transportes, Dirección General de Servicios
Técnicos, tercera edición, julio de 2018 (el archivo oficial `MPGC_2018_16_11_18.pdf` del sitio de la SCT, `micrs.sct.gob.mx`,
SHA-256 `a4ce0801…72b09`). Solo de ahí; las normas N·PRY·CAR vigentes publicadas en `normas.imt.mx` no tienen un título de proyecto
geométrico.

| Qué | Dónde en el manual | Cómo lo usa Civil |
| --- | --- | --- |
| Sobreelevación máxima | cap. III, H.1 | 10 % en carreteras, 8 % con heladas o nevadas, 6 % en vialidades urbanas (se elige) |
| Coeficiente de fricción μ | tabla III.3 | por velocidad de proyecto, 30 a 110 km/h (interpolado entre las velocidades de la tabla) |
| Grado máximo | H.2: Gmáx = 146 000 (μ + Smáx) / Vp² | rechaza una curva más cerrada, con su motivo |
| Sobreelevación de cada curva | H.1, pasos 1 a 3 y figura III.16 | la línea c: parábola asimétrica tangente a la línea b (S = G·Vp²/146 000) en G = 0 y horizontal en Gmáx; G = 1145.92/R |
| Longitud de transición | H.3: Le = 8·Vp·S | el doble con cuatro carriles y el triple con seis (cap. II, C.2) |
| Reparto de la transición | figura III.17 | 60 % en la tangente (se elige 50, 60 o 70 %); antes, el bombeo del lado exterior se quita en N = b·Le/S; aviso si la sobreelevación completa dura menos de Lc/3 o si la tangente entre dos curvas no alcanza |
| Ampliación | H.4 a H.6: acc = N(U + C) + (N − 1)FA + Z, ac = acc − act | del lado interior, crece con la transición; menos de 0.50 m no se amplía y lo demás va al decímetro superior |
| Vehículos de proyecto | tablas III.5 (tipo B, DE-2434, C3R3) y III.7 (tipo D, DE-790, C3) | sus coeficientes reproducen esas tablas a ±0.005 m (el redondeo de la tabla): C3 ΣLi² = 66 m², A(2L+A) = 14 m²; C3R3 ΣLi² + A(2L+A)/2 = 147 m². Las tablas III.4 (tipo ET y A) y III.6 (tipo C) están impresas como imagen: para esos vehículos se escriben sus medidas («Otro…»: entrevía, distancias entre ejes, vuelo delantero y distancia entre ejes del camión) |

Las curvas del eje son circulares simples (sin espirales). Las secciones se calculan además en el inicio y el fin de cada transición y
de la sobreelevación completa; cada sección dibujada dice su sobreelevación y su ampliación, y la hoja trae la tabla de las curvas.

**Despalme:** el espesor de despalme bajo toda la obra (entre chaflanes) se reporta como despalme. Bajo el terraplén ese hueco se
vuelve a llenar, así que se **suma al terraplén**; en el corte ese espesor ya salió como despalme y se **resta del corte**, para no
contarlo dos veces. La tabla de volúmenes trae la columna de despalme y sus totales.

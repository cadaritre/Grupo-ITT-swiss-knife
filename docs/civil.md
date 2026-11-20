# Civil en CAD

Herramientas de topografía al estilo CivilCAD dentro de la página de un plano de CAD (`/cad/<id>/`): una pestaña «Civil» en la cinta del
propio editor, con sus grupos, botones y comandos (como CivilCAD dentro de AutoCAD), y sus hojas junto al dibujo. Con un editor sin
pestañas propias (anterior a tv-v2026.45-13), los mismos menús en una barra encima del editor. Especificación completa (funciones, convenciones del dibujo, fases): `docs/especificacion.md` del repositorio del
motor, `cadaritre/TresVizo-Civil`.

Código: `itt_herramientas/civil/` (servidor y motor fijado), `templates/herramientas/cad/civil*.html`, `static/herramientas/cad/civil.js`,
`civil-cinta.js` y `civil.css`. Pruebas: `pruebas/test_civil.py`, `pruebas_navegador/test_civil_navegador.py` y
`pruebas_navegador/test_civil_cinta_navegador.py`.

## Qué hay

| Grupo de la cinta (menú de la barra) | Acciones |
| --- | --- |
| Puntos | Importar (cualquier delimitador y cualquier orden de columnas, con vista previa en tabla y formatos guardados; terreno o proyecto) · Exportar (los mismos formatos) · Dibujar punto (señalado o por coordenadas, con el siguiente número libre) · Editar punto (número, elevación, descripción, posición) · Etiquetas de puntos (mostrar u ocultar número, elevación y descripción —capa por rótulo— y reescribir los decimales de la elevación) · Convertir puntos (terreno ↔ proyecto, conservando sus datos) · Convertir puntos a entidad punto (los bloques de un plano anterior pasan a POINT con rótulos de texto, en un solo paso de deshacer) · Renumerar (desde N, de cuánto en cuánto, prefijo; por número actual, de oeste a este o de norte a sur) · Localizar punto (por número: la vista va a él y queda seleccionado) · Reporte de puntos (PDF o Word, con latitud y longitud si se pide) |
| Triangulación | Triangular (todos los puntos de la superficie, los que se seleccionan en el plano —clic, ventana o captura— o los que caen dentro de un polígono, con la cuenta antes de correr; líneas de quiebre, límite, islas, arista máxima, sin los puntos de ciertas descripciones —ARBOL, POSTE, BAR*—; reemplaza la anterior) · Invertir triángulos · Borrar triángulos (los que elija, fuera de un límite, dentro de un polígono, lados largos) · Agregar triángulo · Insertar puntos (seleccionados en el plano o dentro de un polígono; parten su triángulo o lado y los vecinos se ajustan a Delaunay) · Revisar triangulación (repetidos, encimados, sin superficie, lados con más de dos triángulos, muy delgados: marcados en rojo y seleccionados) |
| Curvas | Curvas de nivel (intervalo, maestras, suavizado sin cruces, etiquetas) · Actualizar curvas (desde los triángulos que hay en el dibujo) · Etiquetar curvas (donde se señala o a lo largo; la curva nunca se corta: la etiqueta lleva fondo) · Suavizar curvas (las elegidas, sin tocar a las demás) · Unir curvas cortadas (las que partieron las versiones anteriores bajo sus etiquetas) |
| Cuadros | Cuadro de construcción (rumbos N 45°30'15" E, lados curvos, ha-a-ca; formatos normal, RAN —coordenadas UTM y geográficas— y UTM-GPS —además factor de escala y convergencia por vértice y la superficie sobre el terreno—; también «Descargar DXF», el antiguo IT-10) · Cuadro de curvas · Rumbo y distancia de lados · Numerar vértices · Memoria descriptiva (PDF o Word, con colindancias lado por lado) |
| Plano | Cuadrícula UTM (todo el plano o dos esquinas; líneas o cruces; marco; coordenadas al margen y latitud y longitud en las esquinas; reemplaza la anterior) · Lotificar manzana (lotes iguales, de una superficie o de un frente, con cortes perpendiculares al lado de la calle; número y superficie en cada lote y cuadro de áreas) · Importar KML o KMZ (polígonos con huecos, líneas y puntos, en carpetas; vista previa con cuentas; una capa por carpeta o por estilo; un solo deshacer) · Exportar KML o KMZ (polilíneas elegidas o todas las cerradas, con nombre, superficie en m² y ha-a-ca, colores y opacidad, y los puntos si se piden) |
| Perfiles y secciones | Perfil del eje (cadenamiento, rasante con curvas verticales o la superficie de proyecto) · Secciones transversales (plantilla sencilla o de carretera: carriles y acotamientos con su pendiente, cuneta en corte, pavimento —terracerías a la subrasante— y despalme; sobreelevación y ampliación en las curvas del eje según la SCT; taludes; áreas) |
| Volúmenes | Por secciones (áreas medias) · Entre superficies (exacto, línea de ceros opcional, resultado en la hoja o también en el plano) · Curva masa (con la rasante o con la superficie de proyecto; línea compensadora la mejor —menor momento de acarreo— o en una ordenada; puntos de compensación, volumen y distancia media de cada tramo, sobreacarreo en m³·hm sobre el acarreo libre, préstamo y desperdicio) |
| Imágenes | Insertar imagen georreferenciada (de la carpeta del proyecto, con su archivo de mundo o GeoTIFF; docs/cad.md) · Referencias externas (el panel de imágenes y dibujos de referencia del plano) · Imagen satelital (el área de una polilínea cerrada o dos esquinas, a la calidad que se elija; se guarda en la computadora y entra como IMAGE georreferenciado, recortado a la polilínea; docs/cad.md, «Imagen satelital») |
| Escala (botón «1:1000») | Escala del plano, zona UTM y altura de los textos en mm de papel |

Civil aparece para quien puede editar planos (`cad`, `admin`) en la versión vigente; para dibujar hace falta tener el control del plano
(exportar puntos, el reporte, localizar un punto, la memoria descriptiva, las referencias y la escala solo leen y funcionan sin él). En el
modo táctil no hay Civil.

### En la cinta del editor

La pestaña «Civil» sigue a las del editor (Inicio, Insertar, Anotar…) y se ve, se compacta y se minimiza como ellas, en claro y en oscuro:

- **Un grupo por menú** de `civil/pagina.py` `MENUS`, en su orden, después del botón de la **escala** y la zona UTM («1:1000 · Zona 14N», abre «Escala y textos»).
- **Botones grandes** para lo frecuente (las acciones con `grande`): Importar puntos, Triangular, Curvas de nivel, Cuadro de construcción,
  Perfil, Secciones, Insertar imagen georreferenciada; **Volúmenes** es un solo botón grande con su lista (`como: "desplegable"`).
- **Botones chicos** para lo demás, por sección del menú (las que parten sus separadores): hasta tres, cada uno con su nombre; una
  sección más larga es un botón dividido que muestra la última que se usó, con las demás en su lista (como «Rectángulo/Polígono» de
  AutoCAD). Cuando la ventana no alcanza, el editor compacta los grupos de la derecha (solo iconos y luego un botón por grupo).
- **La ayuda** de cada botón dice su nombre, para qué sirve, por qué está apagado y su comando.
- **Sin el control del plano** (o con otra persona editando), lo que dibuja se ve apagado, en cada pestaña de plano por separado: al cambiar
  de pestaña cambia lo que se puede usar.

**Comandos.** Cada acción tiene su comando, que se escribe en la línea de comandos del editor con autocompletar, como los de un complemento
de AutoCAD: prefijo **`CV`** y la acción, `CVTRIANGULAR`, `CVCURVAS`, `CVSECCIONES`, `CVIMPORTARPUNTOS`, `CVCUADRO`… (la lista completa es la
de `cinta()` en `civil/pagina.py`; por omisión «CV» + la clave sin guiones bajos). Escrito, hace lo mismo que el botón; Intro lo repite.
Apagado, el editor dice por qué en la línea de comandos.

**Las hojas** se abren como paletas del editor: acopladas a la derecha del área de dibujo, debajo de la cinta, sin bloquear el dibujo (se
puede acercar y mover con una abierta) y sin tapar la cinta: si la persona vuelve a la cinta mientras hay una abierta, la paleta se aparta
hasta que regresa al dibujo. Al elegir o señalar en el dibujo la paleta se repliega, como siempre. Esc cierra primero la paleta, también
con el foco en el dibujo; el siguiente Esc es del editor.

**Cómo se arma.** `civil/pagina.py` `cinta()` pasa `MENUS` (comando e icono SVG de cada acción) al panel de Civil; `civil-cinta.js` define
la pestaña en el editor (`ribbon_define`), pone por plano lo que se puede usar (`ribbon_state` con el `document_id`) y lee lo que la
persona pulsa o escribe (eventos `ribbon` del canal, `ocs-control-event`) para abrir la hoja de siempre (`civil.js`, `ACCIONES`). El
contrato del editor está en el fork, `docs/tresvizo-cinta.md` (parche 70). Si el editor responde `unknown_operation`, Civil monta su barra
encima del editor como antes.

**Agregar una acción** (otro módulo de la página, p. ej. la imagen satelital): su entrada en `MENUS` (clave, nombre, icono; `grande`,
`solo_lee`, `comando` o `descripcion` si hace falta) y su función con `TVCivil.registrarAccion(clave, fn)` (o en `ACCIONES` de
`civil.js`). Aparece sola en la cinta, con su comando, y en la barra de respaldo; mientras nadie la registra se ve apagada («todavía no
está disponible»).

### La barra de respaldo

Con un editor sin pestañas propias: los mismos menús en una barra encima del editor. En una barra angosta los menús se compactan (sin
flechas y, más angosta, solo iconos); cada menú se abre bajo su botón aunque la barra se desplace a lo ancho; las hojas son las de siempre
(modales).

La pestaña de la cinta, la barra y las hojas (`civil_panel.html`, ~20 KB comprimidas con los iconos de la cinta, igual para todas las cuentas y todos los planos) no viajan con cada página
del plano: `civil-panel.js` las pide a `/cad/panel-civil/?v=<huella>` (se guardan un año; ETag y 304), las pone en la página y carga
`civil.js`. En un teléfono con el CAD descargado quedan en la caché `itt-cad-pagina-civil` (docs/cad.md, «Datos móviles»).

## Cómo funciona

```
página (civil.js) ──TVCad.pedir──▶ editor (lee entidades: records, query)
      │                                   ▲
      └─POST /cad/civil/<orden>/ ──▶ servidor (civil/servicio.py → motor tvcivil) ──▶ resultado: DXF + resumen
                                          │
                       dibujo temporal (open) + entities_copy_to ──▶ plano
```

- **El dibujo es la fuente de verdad.** Puntos (entidades POINT con rótulos TEXT: ver «Puntos: entidad POINT y rótulos de texto»), triangulación (3DFACE
  en `TRIANGULACION` / `TRIANGULACION_PROYECTO`), curvas y tablas son entidades normales del DWG/DXF con XDATA `TVCIVIL`. Editar el dibujo
  y luego «Actualizar curvas» funciona, y todo sobrevive a guardar y volver a abrir.
- **Leer:** los puntos con `query` (los POINT de las capas de puntos y los TEXT de las capas de rótulos) y el XDATA `TVCIVIL` de todos de una vez con `xdata_get` (5 000 puntos con sus 15 000 rótulos, ≈ 1.5 s); los bloques de un plano anterior también. Las caras con `records` y
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
| Volver a dibujar el perfil o las secciones de un eje con otras opciones (lo nuevo se copia y se borra lo anterior) | 2 | 2 |
| Invertir triángulos (dos `set_properties` en un `batch`) | 1 | 2 |
| Editar, renumerar, convertir de tipo o cambiar los decimales de puntos; convertir los bloques viejos a entidad punto (la copia y el borrado de lo anterior en un `batch`, con `source_document_id`, parches 110 y 111) | 1 | 2 (copia y borrado uno tras otro) |
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

### Dónde va lo que se dibuja

Todo lo que agrega un bloque de dibujo pregunta dónde va, como CivilCAD: perfil, secciones, curva masa, cuadro de construcción (los tres
formatos), cuadro de curvas, cuadro de áreas de la lotificación, tabla de curvas del eje y tabla de volúmenes (pedido del dueño,
16-11-2026). El resultado ya calculado se abre en el dibujo temporal, se mide su caja y un rectángulo de su tamaño real (con su nombre y
medidas) sigue al puntero sobre el plano, tomado por su esquina superior izquierda (el perfil y la curva masa, por la inferior). Empieza en
el lugar sugerido —a un lado o debajo de lo dibujado, o junto al polígono—; clic lo pone donde está el puntero (con las referencias a
objetos del editor), «Poner aquí» lo deja donde se ve, y Esc o «Cancelar» no dibujan nada. Solo se mueve lo que es del bloque: los
números de vértice, las claves de las curvas, los lotes y la línea de ceros se quedan en su lugar. Se mueve en el dibujo temporal y se
copia de una vez: un solo paso de deshacer. La vista previa vive en la página (`civil-previa`, sobre el marco del mismo origen) y vuelve a
leer la vista del editor al acercar o mover; el clic es del editor (`getpoint`), así que lo dibujado queda siempre donde se señaló. La
vista pantalla ↔ mundo es `TVCad.vista()` (control.js), la misma de la capa táctil: la cámara de `state` y `state.viewport_rect`. Desde
tv-v2026.45-8 el editor dice dónde está su área de dibujo, así que la vista previa tiene su tamaño real desde el primer movimiento,
también en pantalla completa. Civil no está en el modo táctil (la capa táctil la oculta),
así que ahí no hay puntero con desfase que usar; si algún día entra, la colocación tiene que ir por la herramienta de la capa táctil.

### Curvas de nivel: una curva, una polilínea

Una curva de nivel es siempre una sola polilínea continua: nunca se corta bajo su etiqueta, ni al generarla ni al etiquetarla después.
La etiqueta es MTEXT centrado sobre la curva y alineado con ella, con fondo del color del dibujo (máscara, factor de borde 1.3), y se
dibuja después de la curva para quedar encima. Las curvas que cortaron las versiones anteriores se unen con «Unir curvas cortadas», o al
etiquetar: «Etiquetar curvas» revisa primero si las curvas están cortadas y, si lo están, ofrece «Unir y etiquetar» o «Etiquetar sin
unir». Solo se cierran los huecos con una etiqueta (los cortes de Civil; «Unir también los huecos sin etiqueta» es opción aparte), con un
puente que sigue la curvatura; las etiquetas viejas sobre las curvas unidas se reescriben con fondo.

### Escala

La escala viaja con el plano: el marcador de los puntos es el estilo de punto del dibujo (`PDMODE` 34, cruz con círculo) con `PDSIZE` = 1.2 mm de papel por
la escala, y los rótulos van a su altura por escala; al abrir un plano Civil la lee del `PDSIZE`. (Los planos anteriores la traían en la escala E/1000 de sus bloques.) Las alturas propias (mm por tipo de texto) se guardan en el navegador por plano. Cambiar la escala con «Ajustar los
puntos ya dibujados» vuelve a dibujar los puntos a la nueva escala (orden `redibujar_puntos`): los rótulos quedan tal como estaban, también
los apagados y los decimales de la elevación. «Renumerar puntos» usa la misma orden.

## Puntos: entidad POINT y rótulos de texto

Cada punto es una entidad **POINT** real en (X, Y, Z) en la capa de puntos de terreno (`PUNTOS_TERRENO`) o de proyecto (`PUNTOS_PROYECTO`), con
XDATA `TVCIVIL` `[punto, número, tipo]`. Su número, elevación y descripción son entidades **TEXT** (estilo `CIVIL`, a su altura por escala, en la Z del
punto), cada una en su capa (`PUNTOS_NUMERO`, `PUNTOS_ELEVACION`, `PUNTOS_DESCRIPCION`) con XDATA `[rotulo, número, tipo, rol, handle del punto]`
(rol: `num`, `elev` o `desc`); el editor traduce ese handle a la copia del punto cuando el resultado se copia al plano (parche 111), y si se pierde Civil
encuentra el rótulo por tipo, número, rol y cercanía. Un rótulo apagado o vacío no se dibuja; apagar su capa oculta todos los de ese rol.
El marcador lo pone el dibujo (`PDMODE` 34 y `PDSIZE`), que Civil escribe en el encabezado al dibujar puntos; se guarda en DWG y DXF.

- **Explotar no daña nada:** POINT y TEXT ya son primitivas. **Mover** solo el punto deja sus rótulos donde estaban, como en CivilCAD; **«Editar punto»**
  mueve el punto y sus rótulos juntos (los dibuja de nuevo y borra los anteriores, en un solo paso de deshacer).
- **AutoCAD y otros programas** ven puntos (POINT con su Z) y textos (TEXT); no hace falta Civil para consultarlos.
- **Planos anteriores (bloques):** se siguen leyendo en todas las acciones (el bloque `PT_TERRENO` / `PT_PROYECTO` con atributos NUM, ELEV y DESC). Al abrir
  un plano que los tiene, un renglón sobre el lienzo (sin bloquear nada) ofrece convertirlos; «Ahora no» lo quita en ese plano. **Convertir puntos a
  entidad punto** (menú Puntos, `CVPUNTOSAENTIDAD`) los pasa todos al modelo nuevo conservando número, elevación tal como estaba escrita, descripción y qué
  rótulos estaban apagados, en **un solo paso de deshacer**, y quita las definiciones de bloque que ya nadie usa. El tamaño del marcador toma la escala de
  los bloques (la de la mayoría, con aviso, si había varias).
- Con un editor anterior al parche 110 la copia y el borrado van uno tras otro (dos pasos de deshacer); Civil lo pregunta una vez sin tocar el plano.

## Archivos de puntos: delimitador, columnas y formatos guardados

La hoja de **Importar puntos** lee cualquier archivo de texto de puntos sin que la persona tenga que adivinar nada:

- **Cómo está escrito:** delimitador (reconocer, coma, punto y coma, tabulador, espacios, barra u «Otro…» con uno o varios caracteres que se
  escriben), «tratar delimitadores seguidos como uno», texto entre comillas dobles, simples o nada, decimales con punto o con coma, y cuántos
  renglones del principio no se leen (títulos).
- **Qué es cada columna:** la vista previa es una tabla con los primeros renglones y, sobre cada columna, una lista (Número, Norte (Y),
  Este (X), Elevación, Descripción, Latitud, Longitud o Ignorar). Todo se reconoce primero y se puede cambiar; también hay órdenes comunes
  (PNEZD, PENZD…). Un archivo que no se reconoce no bloquea: la tabla sale igual, con el motivo, para elegir las columnas.
- **Latitud y longitud** se convierten a la zona UTM del plano (se avisa si caen en otra zona o si parece faltar el signo del oeste). Un
  punto sin número toma el siguiente libre del plano. Los renglones que no se leyeron se listan con su línea y su motivo.
- **Exportar** tiene las mismas decisiones (delimitador, texto entre, decimal, decimales de coordenadas y de elevación, columnas en el orden
  que se quiera con agregar y quitar, primer renglón con nombres) y un ejemplo en vivo de cómo quedará el archivo.
- **Formatos guardados:** «Guardar formato…» le da nombre a lo que hay en la hoja («Mi estación», «Colector X») y queda en la cuenta, aparte
  para importar y para exportar. Al elegirlo se llena toda la hoja. Se guardan en el servidor, por cuenta (`FormatoPuntos`: cuenta, uso,
  nombre sin importar mayúsculas, un JSON de unas decenas de claves), hasta 30 por cuenta y uso; el motor valida lo que se guarda
  (`normalizar_formato`) y no entra nada más. Los ve y los toca solo su cuenta y solo quien edita planos (`cad`, `admin`); se borran con la
  cuenta (`civil.servicio.al_eliminar_cuenta`). `GET/POST/DELETE /cad/civil/formatos/?uso=importar|exportar`. Borrar pide un segundo toque.

## Google Earth: KML y KMZ

- **Importar:** el archivo (KMZ se abre en el servidor con la biblioteca estándar, con topes de tamaño; no se aceptan DOCTYPE ni ENTITY)
  se previsualiza con lo que trae (polígonos, líneas, puntos, huecos, carpetas, avisos) antes de tocar el plano. Polígonos y líneas se
  vuelven polilíneas (una cerrada por cada anillo; 3D si la altitud varía), los puntos son puntos de terreno, de proyecto o simples.
  WGS84 pasa a la zona UTM del plano (se avisa si los datos caen en otra). Una capa por carpeta o por estilo, el nombre de cada marca como
  texto (capa `KML_NOMBRES`, opcional), XDATA con su tipo, nombre y carpeta, y la altitud como elevación si se pide. Un solo paso de
  deshacer.
- **Exportar:** las polilíneas que se eligen en el dibujo (o todas las cerradas); las cerradas son polígonos con su superficie en m² y en
  ha-a-ca en la descripción, las abiertas son líneas. El nombre sale del texto que está dentro del polígono, de su capa o de un prefijo
  con número; color, opacidad y grosor se editan; con los puntos de terreno o de proyecto si se piden. UTM de la zona del plano a WGS84,
  arcos en varios segmentos y anillo antihorario; KML o KMZ, con el nombre del plano. Google Earth lo abre en el lugar.
- Cada polilínea cerrada sale como un polígono aparte: un hueco dibujado dentro de otra polilínea sale como otro polígono, no como un
  hueco del primero (en un plano no hay forma de saber si el interior de una manzana es hueco o lote).

## Superficie de proyecto: perfil, secciones, curva masa y volumen

Todo el flujo de proyecto existe: puntos de proyecto (Importar → «Proyecto»), **Triangular** el proyecto, **Curvas** del proyecto,
**Volumen entre superficies**, y el perfil, las secciones y la curva masa **leen la superficie de proyecto**. Cada una de esas hojas trae
arriba la línea «Terreno · N triángulos / Proyecto · N triángulos» (o «Sin triangular») y una frase que dice cuál superficie usa, y
perfil, secciones y curva masa tienen el interruptor «Con la superficie de proyecto (triangulación de proyecto)»:

- **Perfil:** dibuja el terreno y la superficie de proyecto (capa `PERFIL_PROYECTO`); sin rasante, la guitarra lleva ELEV. PROYECTO y
  los espesores de corte y terraplén contra esa superficie; con rasante a la vez, la superficie va en su renglón, ELEV. SUP. PROYECTO.
- **Secciones y curva masa:** la sección de proyecto es el corte de esa superficie en la misma línea; el corte y el terraplén salen de la
  diferencia con el terreno. La rasante y la superficie de proyecto son una cosa o la otra (el interruptor apaga la rasante y al revés).
- **Volumen entre superficies** compara Terreno contra Proyecto, como siempre.
- Sin triangulación de proyecto, la hoja lo dice y a dónde ir (importar los puntos de proyecto y triangular como «Proyecto»).

## Perfil y secciones al nivel de CivilCAD (motor 0.9)

Las brechas contra las rutinas oficiales de CivilCAD, con su responsable, están en el repositorio del motor (`docs/brechas-civilcad.md`).
Lo de perfiles, secciones y volúmenes:

- **Estaciones libres.** «Estación cada» es un número cualquiera (5, 7.5, 20, 33…); las cerradas son múltiplos desde 0+000. Un bloque
  «Estaciones y tramo» da **desde / hasta**, **estaciones adicionales** (escritas como 0+033.30 o señaladas en el eje: se proyectan sobre
  él) y las **críticas** de las curvas del eje (PC, PT, PI, TE, EC, CE, ET) y de las verticales (PCV, PIV, PTV), que se pueden apagar. Dos
  estaciones a menos de 1 mm se juntan. Antes de dibujar, la hoja dice «N secciones en M hojas · C críticas». En el perfil las mismas
  opciones fijan los renglones de la guitarra; la curva masa también las usa.
- **Hoja de secciones.** Escalas H y V (por omisión, la del plano), retícula con su paso H y V (automático, o el que se escriba) que se
  puede apagar, columnas × renglones, separación en mm y orden (por columnas de abajo arriba, de arriba abajo o por renglones), marco y
  título «HOJA n DE m», sombreado de corte y terraplén y rótulo de chaflanes. Cada sección lleva su estación (y su tipo si es crítica),
  desplazamientos IZQ./DER., cotas, plano de comparación y áreas.
- **Hoja del perfil.** Escalas H y V, líneas de estación y de cota, plano de comparación, guitarra (cadenamiento, elevación del terreno y
  de la rasante, espesores de corte y de terraplén) a la estación que se elija, curvas verticales rotuladas (PCV, PIV, PTV, longitud) y la
  pendiente de cada tangente. Las marcas de estación en planta (`EJE_ESTACIONES`) se quedan sobre el eje aunque el perfil se ponga en
  otro lugar: al colocar el perfil solo se mueven las capas `PERFIL_*`.
- **Volúmenes.** La tabla por estación lleva el tipo de cada estación, el renglón TOTALES y su título; «Descargar tabla (CSV)» la baja
  con la rasante (BOM y CRLF, para Excel).
- **Editable después.** Cada objeto del bloque lleva en su XDATA `TVCIVIL` el tipo y `bloque:<handle del eje>`, y el título guarda las
  opciones. «Reemplazar el anterior de este eje» (activado) busca el bloque por esa marca, dibuja el nuevo donde estaba el anterior y
  lo borra: dos pasos de deshacer. Al elegir un eje que ya tiene su perfil o sus secciones, la hoja se llena con las opciones con que se
  dibujaron (estaciones, hoja y rasante; la plantilla de la sección no se guarda y se vuelve a escribir).

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

- `pruebas_navegador/test_civil_cinta_navegador.py`, con la compilación propia del editor: la pestaña con cada acción de `MENUS` y que un
  clic en su botón (o su comando escrito, si su grupo está compacto) abre lo suyo como paleta sin tapar la cinta; el comando escrito; sin el
  control del plano solo lo que lee (botón apagado, comando que dice por qué); las pestañas de planos cambian el estado; el grupo
  Imágenes; Esc primero para la paleta; y con la compilación oficial, la barra de respaldo. `CINTA_CAPTURAS=carpeta`: la cinta en claro y
  oscuro, a escala 1 y 1.5.
- `pruebas/test_civil.py`: huella del motor, órdenes ligeras y pesadas (con gzip y por la cola), resultado solo de la cuenta, errores con su
  mensaje, permisos, tope de tamaño y que la página traiga Civil solo a quien edita; formatos de puntos con delimitador propio; KML de ida y
  vuelta; perfil, secciones y curva masa con la superficie de proyecto; formatos guardados (por cuenta, permisos, validación, tope y borrado
  con la cuenta).
- `pruebas_navegador/test_civil_navegador.py`, con el editor real y coordenadas UTM: importar 5 000 puntos PNEZD, deshacer y rehacer,
  triangular, invertir dos triángulos elegidos con el ratón, curvas, cuadro de un lote con lado curvo, perfil y secciones con rasante
  (estación libre y adicional, hoja de 2 × 2, escalas H/V, retícula, reemplazo del bloque anterior, reapertura con las opciones guardadas
  y marcas en planta fijas), volumen contra una plataforma, guardar en DWG y volver a abrir; 360 px; y el camino sin `user_select` (selección + «Listo»). La prueba
  de los pedidos del 16-11: triangular solo lo seleccionado con una ventana y lo que cae en un polígono (la cuenta antes de correr),
  curvas con una polilínea por curva y etiquetas con fondo, unir las curvas cortadas de un plano viejo al etiquetar, y colocar el cuadro
  donde se señala (la vista previa sigue al puntero; Esc no dibuja nada; un paso de deshacer).
  `ITT_CAPTURAS=carpeta` deja capturas de cada hoja y de lo dibujado.
- `pruebas_navegador/test_civil_puntos_kml_navegador.py` (Chromium y WebKit): delimitador propio, columnas elegidas, formato guardado y
  vuelto a usar en otra sesión, exportar con otro formato y volver a importarlo; KMZ con carpetas, huecos, líneas y puntos, deshacer en un
  paso, exportar KML y KMZ y volver a importar al milímetro; y la triangulación de proyecto de punta a punta (puntos, triangulación,
  curvas, perfil, secciones y volumen con cifras conocidas).
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
| Vehículos de proyecto | tablas III.4 a III.7 | ver abajo; para otro vehículo se escriben sus medidas («Otro…»: entrevía, distancias entre ejes, vuelo delantero y distancia entre ejes del camión) |
| Cuatro carriles o más | cap. III, texto después de las tablas (página 129, «117 de 589») | en carretera no dividida, la ampliación es el doble de la de dos carriles |
| Espirales de transición | cap. II, C.2.3 y figura II.4 (página 46, «36 de 589»); cap. III, H.3 y figura III.17 | eje por PI con espiral–circular–espiral; clotoide exacta (la serie de la figura II.4 coincide a ±3 mm hasta θe = 15°); θe = le/2Rc, k, p, STe = k + (Rc + p) tan Δ/2, TL, TC, CLe, Ec, Lc = Rc·Δc; cadenamiento por TE, EC, CE y ET; la transición de la sobreelevación y la ampliación es la espiral; aviso si le < 8·Vp·S |

**Vehículos de proyecto** (dos carriles de 3.50 m, calzada de 7.0 m en tangente). Cada tabla se transcribió completa
(`TresVizo-Civil/pruebas/datos/tablas_sct.py`) y una prueba revisa que **cada celda** salga de la fórmula al centímetro:

| Vehículo | Camino | Tabla y página del PDF oficial | Cómo se leyó | Celdas | ΣLi² | A(2L + A) |
| --- | --- | --- | --- | --- | --- | --- |
| T3S2R4 de 31.00 m (DE-2836) | ET y A | III.4, página 125 («113 de 589») | imagen, transcrita a mano a 300 ppp | 273 | 222.77 m² | 15.56 m² |
| C3R3 de 28.50 m (DE-2434) | B | III.5, página 126 («114 de 589») | texto del PDF | 330 | 134.89 m² | 24.11 m² |
| T3S3 de 18.50 m (DE-1594) | C | III.6, página 127 («115 de 589») | imagen, transcrita a mano a 300 ppp | 387 | 131.0 m² | 23.8 m² |
| C3 de 12.50 m (DE-790) | D | III.7, página 128 («116 de 589») | texto del PDF | 682 | 62.67 m² | 20.79 m² |

Entrevía 2.60 m (tabla III.2) y 2.59 m para la distancia libre C: con eso la calzada en tangente da 7.02 m, la constante que traen las
cuatro tablas. Notas de la transcripción, sin adivinar nada:
- En las tablas III.4 y III.6 la columna Ac de 10 km/h trae erratas (p. ej. III.4, Gc 1.00: «7.25»; III.6, Gc 0.50 a 1.25: corrida un
  renglón). Se usó Acc, que concuerda con la fórmula en todas las celdas; Ac = Acc − 7.00.
- La leyenda de la tabla III.5 dice «DE-1760, T3S3 de 20.80 m», pero el encabezado de la propia tabla dice «DE 2434, C3R3 de 28.50 m», y
  los valores corresponden a este último. Se tomó el del encabezado.
- Las celdas vacías del manual (grado mayor que el máximo para esa velocidad) no se usan.

Las curvas del eje son circulares simples o con espirales. Las secciones se calculan además en el inicio y el fin de cada transición y
de la sobreelevación completa; cada sección dibujada dice su sobreelevación y su ampliación, y la hoja trae la tabla de las curvas.

**Despalme:** el espesor de despalme bajo toda la obra (entre chaflanes) se reporta como despalme. Bajo el terraplén ese hueco se
vuelve a llenar, así que se **suma al terraplén**; en el corte ese espesor ya salió como despalme y se **resta del corte**, para no
contarlo dos veces. La tabla de volúmenes trae la columna de despalme y sus totales.

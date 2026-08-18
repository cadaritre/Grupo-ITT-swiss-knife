# Herramientas Grupo ITT

El menú principal utiliza iconos visuales propios para identificar rápidamente cada módulo.

Aplicación de escritorio en Python y Tkinter que reúne herramientas internas de Grupo ITT.

## Experiencia de uso

- Ventanas secundarias medidas después de aplicar estilos y escalado de Windows; se centran, conservan siempre visible la botonera inferior y activan desplazamiento vertical cuando el contenido no cabe en pantalla.
- Acciones consistentes de `Deshacer` y `Restablecer` en las herramientas CAD configurables.
- Progreso determinado por entidad, triángulo, flecha o etapa de consulta durante lecturas y exportaciones pesadas.
- Ayuda contextual mediante indicadores `?` y explicaciones al pasar el cursor sobre parámetros técnicos.
- Datos persistentes en `Documentos\Grupo ITT App`, separados de los archivos instalados del programa.
- Preferencias recordadas entre sesiones, incluyendo zona UTM, mapa base, opciones geoespaciales y croquis de reportes.
- La portada muestra el sitio web del perfil corporativo activo y ofrece un menú `⚙ Ajustes` en la esquina inferior derecha.

## Ajustes e identidad corporativa

El menú de ajustes conserva cinco preferencias generales: croquis GPS en reportes, apertura automática de PDF, mapa base, zona UTM y hemisferio. También abre un editor de paleta para las cotizaciones PDF con presets azul, naranja, amarillo y verde, además de colores personalizados para barras, acentos, fondos y texto. Se guardan en `Documentos\Grupo ITT App\Configuracion\settings.json`.

El acceso administrativo abre un panel separado para activar la identidad **TresVizo**. Al guardar, la aplicación se reinicia y aplica el perfil completo en la portada y en los documentos nuevos:

- Logo TresVizo incluido en `assets/logo_tresvizo.png` o una imagen personalizada copiada a la carpeta de configuración.
- Página `https://www.tresvizo.com/`.
- Teléfono/referencia `614 100 2069`.
- Firma predeterminada `ING. EDGAR TREVIZO`.
- Cotizaciones sin línea de domicilio.
- Cabecera `TresVizo Ingeniería` con una descripción de servicios de ingeniería, topografía de precisión, escaneo 3D, fotogrametría y avalúos.

Al desactivar el perfil se restauran logo, web y datos documentales de Grupo ITT. El selector es una protección operativa para evitar cambios accidentales en una aplicación local; no sustituye autenticación de servidor ni cifrado de datos.

## Arquitectura modular

La portada ya no contiene botones escritos manualmente. Al arrancar, `photo_report_app/tool_registry.py` descubre automáticamente los manifiestos que existen como subcarpetas directas de `photo_report_app/tools/`.

Cada manifiesto declara únicamente metadatos y una ruta de fábrica en texto. No importa la pantalla pesada. El código real de la herramienta se carga hasta que el usuario pulsa **Abrir herramienta**, reduciendo el tiempo de arranque y aislando fallas entre módulos.

Las herramientas existentes conservan por ahora sus archivos de implementación originales en la raíz de `photo_report_app`; sus manifiestos funcionan como una capa de compatibilidad. Las herramientas nuevas deben nacer dentro de su propio paquete, como el ejemplo de CONAGUA, y las antiguas pueden migrarse gradualmente sin cambiar sus IDs ni los archivos del usuario.

### Herramientas registradas

| ID | Manifiesto | Implementación | Datos persistentes |
|---|---|---|---|
| `reports` | `tools/reports/__init__.py` | `report_tool.py` | `Reportes Fotograficos` |
| `quotes` | `tools/quotes/__init__.py` | `quotation_tool.py` | `Cotizaciones` |
| `sketches` | `tools/sketches/__init__.py` | `sketch_tool.py` | `Croquis` |
| `geospatial_converter` | `tools/geospatial_converter/__init__.py` | `geospatial_converter_tool.py` | `Conversiones` |
| `triangulation` | `tools/triangulation/__init__.py` | `triangulation_tool.py` | La ubicación elegida por el usuario |
| `construction_dxf` | `tools/construction_dxf/__init__.py` | Pendiente | Sin carpeta todavía |

### Contrato mínimo de una herramienta

La pantalla debe ser una clase o fábrica compatible con esta firma:

```python
class ConaguaTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
```

- `master`: contenedor principal de la aplicación.
- `logo_path`: logo corporativo empaquetado con el programa.
- `on_home`: función para regresar a la portada.
- La herramienta administra su propia interfaz, modelos, vistas previas y exportaciones.
- Debe guardar información editable fuera de `Program Files`, dentro de `Documentos\Grupo ITT App`.

### Cómo agregar una herramienta nueva

Ejemplo: **Elaborador de reportes CONAGUA**.

1. Crear el paquete:

```text
photo_report_app/
  tools/
    conagua/
      __init__.py        # Manifiesto ligero
      tool.py            # Interfaz Tkinter
      models.py          # Modelos JSON
      pdf.py             # Generación PDF
      migrations.py      # Actualización de proyectos antiguos
      templates/         # Machotes de solo lectura
```

2. Declarar `TOOL_SPEC` en `tools/conagua/__init__.py`. Este archivo no debe importar `tool.py` directamente:

```python
from ...tool_registry import ToolSpec

TOOL_SPEC = ToolSpec(
    tool_id="conagua",
    title="Reportes CONAGUA",
    description="Elabora reportes técnicos con anexos y formato institucional.",
    order=70,
    icon_text="CNA",
    icon_color="#1378A5",
    icon_asset="conagua.png",
    factory_path="photo_report_app.tools.conagua.tool:ConaguaTool",
    version="1.0.0",
    data_category="conagua",
    data_folder="Reportes CONAGUA",
)
```

3. Colocar el icono en `assets/tool_icons/conagua.png`. Si no existe, la portada genera automáticamente un icono con `icon_text` e `icon_color`.

4. Dentro de la herramienta, obtener su carpeta sin codificar rutas absolutas:

```python
from ...tool_registry import get_tool

PROJECT_DIR = get_tool("conagua").ensure_data_dir()
```

5. Agregar cualquier dependencia nueva a `requirements.txt`. Si la dependencia contiene diccionarios, plantillas o archivos binarios propios, añadir también su regla `--collect-all` en `build_exe.ps1`.

6. Ejecutar las pruebas de importación, abrir la portada y confirmar que la tarjeta apareció automáticamente. No se modifica `ui.py` para registrar la herramienta.

La portada conserva una sola instancia de cada pantalla durante la sesión. Al regresar a **Herramientas** no se pierde el trabajo capturado; al volver a abrir la tarjeta se reutiliza la pantalla existente.

### Archivos centrales de la arquitectura

| Archivo | Responsabilidad | Cuándo modificarlo |
|---|---|---|
| `photo_report_app/tool_registry.py` | Contrato, descubrimiento y carga diferida | Solo si cambia el contrato común de todas las herramientas |
| `photo_report_app/ui.py` | Ventana principal y tarjetas genéricas | Solo para cambios globales de navegación o estilo |
| `photo_report_app/tools/<id>/__init__.py` | Manifiesto ligero de una herramienta | Al agregar o cambiar los metadatos de ese módulo |
| `photo_report_app/tools/<id>/tool.py` | Interfaz y coordinación del módulo | Durante el desarrollo normal de esa herramienta |
| `photo_report_app/app_storage.py` | Raíz compartida, ajustes y respaldos | Cuando cambie una política global de almacenamiento |
| `installer/Product.wxs` | Identidad y actualización del MSI | Para cambios propios del instalador; conservar siempre el `UpgradeCode` |

### Dos niveles de modularidad

La arquitectura actual usa **módulos internos**: cada herramienta está aislada en su paquete, pero todas se incluyen dentro de un solo instalador. Es la opción recomendada para Grupo ITT porque el usuario únicamente ejecuta el MSI nuevo y obtiene las herramientas agregadas o actualizadas.

No se ejecutan complementos descargados o copiados por terceros. Un sistema de plugins externos requeriría firma de código, compatibilidad de versiones, aislamiento de dependencias y un mecanismo seguro de actualización. Si en el futuro se necesita instalar una sola herramienta sin reinstalar la aplicación completa, esa sería una segunda etapa deliberada; no conviene cargar archivos Python arbitrarios desde `Documentos`.

### Reglas para proyectos editables

Todo JSON nuevo debe identificar la herramienta y la versión de su esquema:

```json
{
  "tool": "conagua",
  "schema_version": 1,
  "data": {}
}
```

- Incrementar `schema_version` únicamente cuando cambie la estructura del archivo.
- Implementar migraciones consecutivas en `migrations.py`, por ejemplo `v1 -> v2` y `v2 -> v3`.
- Nunca modificar silenciosamente el único archivo del usuario: primero crear un respaldo en `Documentos\Grupo ITT App\Respaldos`.
- Los machotes instalados son de solo lectura; las cotizaciones, reportes, imágenes administradas y configuraciones pertenecen al usuario.
- Los módulos pueden actualizarse o desinstalarse sin borrar la carpeta `Grupo ITT App`.
- El campo `version` del manifiesto identifica la versión funcional de la herramienta; `schema_version` identifica únicamente la estructura de sus archivos JSON. Son versiones independientes.

## Datos del usuario

La aplicación crea esta estructura en el primer arranque:

```text
Documentos/
  Grupo ITT App/
    Configuracion/
      settings.json
    Cotizaciones/
      Recursos/
    Reportes Fotograficos/
    Croquis/
    Conversiones/
    Diccionarios/
      diccionario_personal.json
      referencias_es_en/
    Respaldos/
    Cache/
```

`Program Files` contiene únicamente código y recursos instalados. Una actualización MSI reemplaza esos archivos, pero no toca documentos, configuraciones, diccionarios ni respaldos.

## Funciones

### Reportes fotográficos

- Portada con croquis automático sobre OpenStreetMap.
- Opción para generar el reporte con croquis o sin croquis.
- Marcadores numerados usando metadatos GPS/EXIF.
- Aviso claro para fotografías sin ubicación.
- Logo corporativo predeterminado o reemplazable.
- Fecha actual editable y nombre personalizado.
- Reordenamiento, eliminación y apertura de fotografías.
- Vista previa integrada y descripción opcional por fotografía.
- PDF A4 con encabezado, pie, numeración y metadatos.
- Interfaz sin conexión obligatoria; si OSM no responde conserva los puntos GPS sobre una base neutra.

### Cotizaciones

- Editor de datos del cliente, proyecto, conceptos y subconceptos.
- Machotes rápidos para servicios topográficos, dron RTK, escaneo 3D, BIM y control geodésico.
- Cantidad, unidad, precio unitario e importe automático.
- Subtotal, IVA configurable, total y anticipo.
- Imágenes de referencia con descripción.
- Vista previa paginada del PDF.
- Guardado y apertura de cotizaciones editables en JSON.
- Al exportar el PDF se crea automáticamente, en la misma carpeta, un JSON con prefijo `EDITABLE_COTIZACION` y el mismo nombre para poder retomar el trabajo después.
- Exportación final a PDF corporativo.
- Idioma guardado por cotización: Español o English. El corrector consulta exclusivamente el diccionario seleccionado y revisa palabra por palabra.
- Al elegir English, el PDF traduce toda su estructura: cabecera, folio, datos del cliente, columnas, impuestos, totales, condiciones, vigencia, firma, imágenes, pie y paginación. Los 14 machotes incluidos tienen versión española e inglesa; los textos personalizados se conservan exactamente como los escribió el usuario.
- Paleta de colores configurable desde `⚙ Ajustes`; el color del texto sobre barras es independiente para mantener contraste en fondos amarillos o claros.
- Acciones para corregir, cambiar manualmente, omitir una vez, omitir todas o agregar términos al diccionario personal.
- Indicador permanente `⚠ Ortografía pendiente` / `✓ Ortografía revisada`; editar contenido textual invalida la revisión y exportar pendiente requiere confirmación.
- Advertencias amarillas no bloqueantes para datos recomendados sin capturar; la pestaña resume cuántos faltan y acepta correo o teléfono como medio de contacto.
- Diccionario técnico inicial para topografía, CAD y georreferenciación; las palabras agregadas y las referencias lingüísticas se conservan en `Documentos\Grupo ITT App\Diccionarios`.

### Croquis de ubicación

- Navegación interactiva con paneo visual inmediato, zoom con rueda y caché de teselas en memoria.
- Herramientas separadas para mover el mapa, dibujar un polígono libre o trazar un rectángulo; Shift fuerza un cuadrado.
- Selección fluida sobre una capa local, sin recargar teselas en cada vértice.
- Vectorización de calles, edificios, estacionamientos, agua, cobertura vegetal, parques/recreación, usos de suelo, equipamiento, infraestructura eléctrica, ferrocarril y barreras desde OpenStreetMap mediante Overpass. Los parques y los predios no se contabilizan automáticamente como área verde.
- Capas seleccionables: OpenStreetMap, OpenTopoMap topográfico y base neutra.
- Al usar OpenTopoMap genera curvas de nivel desde un DEM, con equidistancia automática o de 1, 2, 5, 10, 20 y 50 m.
- Pestaña de capas para mostrar u ocultar cada categoría, el límite y las etiquetas antes de exportar; la selección se conserva en JSON.
- Importación de coordenadas desde CSV y guardado editable en JSON.
- Exportación a PDF corporativo con croquis vectorial y vértices del área.
- Exportación DXF R2010 en metros y WGS84 / UTM, con anchos viales, ejes y bordes sin hatch de calles; conserva edificios cerrados, rellenos de áreas, puntos, textos y curvas de nivel 3D separadas por capas.
- Hatches agrupados por capa, con contornos de relleno simplificados y sin transparencia para que AutoCAD pueda seleccionar y editar croquis densos con mucha mayor fluidez, sin alterar las polilíneas de trabajo.

### Conversión DXF ↔ KML/KMZ

- Conversión bidireccional entre DXF y archivos compatibles con Google Earth.
- Selector de zona UTM 1–60 y hemisferio, con WGS84 / UTM 13N como valor predeterminado.
- Las coordenadas DXF se interpretan siempre directamente como metros UTM, sin depender de las unidades declaradas en el encabezado de AutoCAD.
- Vista previa por capas sobre OpenStreetMap, OpenTopoMap o base neutra, con zoom, paneo y ajuste automático.
- Conservación de capas, nombres, alturas, puntos, líneas, polígonos y curvas aproximadas; exportación opcional de etiquetas y hatch.
- KML/KMZ pegado al relieve de Google Earth de forma predeterminada para que los polígonos no queden ocultos debajo del terreno; el ajuste puede desactivarse cuando se requieran alturas absolutas.
- Advertencia cuando las coordenadas del DXF parecen locales y no UTM.
- Base cartográfica neutra de respaldo cuando no hay conexión.

### Herramientas de triangulación DXF

- Conversión de entidades `POINT`, bloques `INSERT` y, opcionalmente, vértices de polilínea a una superficie TIN Delaunay en `3DFACE`.
- Filtros gráficos de duplicados XY, área mínima y longitud máxima de arista para evitar triángulos que crucen huecos o límites irregulares.
- Vista previa en planta con puntos, malla, gradiente de elevaciones, zoom y paneo antes de exportar.
- Zonificación de una superficie TIN por rangos editables de pendiente, con colores, áreas y porcentajes visibles antes de guardar.
- Flechas de escurrimiento pluvial calculadas con la dirección de máxima bajada de cada triángulo, con rangos configurables de color, longitud relativa y grosor.
- Controles para longitud base automática o manual, tamaño de punta, densidad, pendiente mínima, malla de referencia y etiquetas de pendiente.
- Exportación DXF con tabla de pendientes, superficie 3D opcional, textos y hatches sólidos agrupados para mejorar el rendimiento en AutoCAD.

### Próximamente

- Conversión de cuadros de construcción a DXF.

## Ejecutar

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Compilar para Windows

```powershell
.\build_exe.ps1
```

Cuando se autorice la compilación, el ejecutable se creará en `dist\HerramientasGrupoITT.exe`.

Durante el desarrollo normal se ejecuta `python main.py`; no hace falta reconstruir el ejecutable en cada cambio. La compilación se reserva para una versión que ya pasó el checklist de publicación.

## Crear instalador MSI

El MSI utiliza WiX Toolset 4 y conserva todos los datos editables fuera de `Program Files`.

```powershell
dotnet tool install --global wix --version 4.0.6
.\build_msi.ps1 -Version 1.0.2
```

El proyecto fija WiX 4.x. No se debe instalar WiX 7 para este flujo, porque esa
versión añadió una licencia de mantenimiento que bloquea la compilación sin una
aceptación adicional. Si ya está instalada otra versión, reemplazarla con:

```powershell
dotnet tool uninstall --global wix
dotnet tool install --global wix --version 4.0.6
```

La aplicación empaquetada usa `PYINSTALLER_RESET_ENVIRONMENT=1` al reiniciarse
después de cambiar la identidad corporativa. Este detalle no debe retirarse:
obliga al ejecutable `onefile` a crear una carpeta temporal `_MEI` nueva y evita
que el proceso reiniciado pierda `base_library.zip` cuando termina el proceso
anterior.

El instalador se generará en `dist\HerramientasGrupoITT.msi`. El script existe para la compilación final; no es necesario ejecutarlo durante las iteraciones normales.

El instalador incluye una experiencia gráfica en español con ilustraciones
topográficas, selección de carpeta, licencia de uso interno y componentes
opcionales para crear un acceso directo en el escritorio e iniciar la
aplicación con Windows. Ambas opciones vienen activadas por defecto y pueden
desmarcarse desde la pantalla de personalización. Al terminar también ofrece
abrir la aplicación inmediatamente.

Los recursos del instalador viven en `installer/assets/`. WiX requiere el
banner de `493 x 58` píxeles y la ilustración de bienvenida de `493 x 312`.

### Cómo entregar una herramienta nueva mediante una actualización

1. El desarrollador crea el paquete y manifiesto dentro de `photo_report_app/tools/`.
2. Actualiza la versión funcional del manifiesto y prueba proyectos nuevos y antiguos.
3. Genera el nuevo MSI con una versión de tres números mayor, por ejemplo:

```powershell
.\build_msi.ps1 -Version 1.1.0
```

4. Se entrega `HerramientasGrupoITT.msi` al usuario.
5. El usuario cierra la aplicación y ejecuta el MSI. No necesita copiar carpetas, editar archivos ni desinstalar manualmente la versión anterior.
6. WiX detecta la instalación anterior mediante el `UpgradeCode`, reemplaza el código instalado y conserva `Documentos\Grupo ITT App`.

Reglas del instalador:

- No cambiar el `UpgradeCode` de `installer/Product.wxs` mientras siga siendo el mismo producto; cambiarlo haría que Windows lo trate como otra aplicación.
- Cada MSI publicado debe tener una versión superior a la anterior y usar únicamente tres componentes numéricos: `mayor.menor.parche`.
- Incrementar **parche** para correcciones, **menor** para herramientas o funciones compatibles y **mayor** para cambios incompatibles o migraciones profundas.
- Probar la actualización encima de la versión anterior, no solamente una instalación limpia.
- Una desinstalación elimina el programa instalado, pero no debe borrar los documentos del usuario.

### Checklist antes de publicar un EXE o MSI

```powershell
python -m compileall -q main.py photo_report_app
python -m pip check
python main.py
```

- La portada descubre todos los manifiestos sin advertencias.
- Cada tarjeta disponible abre, regresa a la portada y vuelve a abrir sin perder el estado de la sesión.
- Se prueban apertura, guardado y migración de al menos un JSON creado por la versión anterior.
- Cada herramienta genera un archivo representativo y éste se abre correctamente en su programa destino: PDF, AutoCAD o Google Earth según corresponda.
- Las rutas editables apuntan a `Documentos\Grupo ITT App`, nunca a `Program Files` ni a una carpeta temporal.
- Se actualizan `version` en los manifiestos modificados y la versión del MSI.
- Se instala el MSI sobre la versión anterior en una máquina de prueba y se confirma que ajustes, diccionarios y proyectos siguen presentes.
- Para distribución formal, se recomienda firmar digitalmente el EXE y el MSI y publicar también su hash SHA-256.

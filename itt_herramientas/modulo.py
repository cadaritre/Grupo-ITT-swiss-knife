from tvplataforma.modulos import Modulo, Rol

MODULO = Modulo(
    clave="herramientas",
    nombre="Herramientas Grupo ITT",
    host="tools.tresvizo.com",
    urlconf="itt_herramientas.urls",
    icono="herramientas/img/modulo.png",
    orden=20,
    descripcion="Reportes, cotizaciones, croquis y conversión de planos",
    # El service worker, el manifiesto, la guía de instalación y la página de captura (HTML sin datos de ninguna cuenta,
    # necesaria para arrancar sin conexión) se sirven sin sesión: el navegador los pide sin poder seguir un inicio de sesión.
    limpiezas=("itt_herramientas.convertidor.servicio.limpiar", "itt_herramientas.drive.servicio.limpiar"),
    rutas_publicas=("sw.js", "manifest.webmanifest", "capturar/", "instalar/", "cotizaciones/", "reportes/", "croquis/"),
    roles=[
        Rol("reportes", "Reportes fotográficos", "Toma fotos en campo y genera el reporte en PDF."),
        Rol("cotizaciones", "Cotizaciones", "Crea cotizaciones, también sin señal, y genera su PDF."),
        Rol("croquis", "Croquis de ubicación", "Dibuja el área sobre el mapa y genera el croquis en PDF y DXF."),
        Rol("convertidor", "Convertidor DXF ↔ KML/KMZ", "Convierte planos y polígonos entre DXF, KML y KMZ."),
        Rol("triangulacion", "Triangulación, pendientes y escurrimiento", "Genera un TIN, zonifica pendientes y dibuja el escurrimiento."),
        # «Cuadros de construcción a DXF» (IT-10) no se ofrece hasta que exista la herramienta.
        Rol("admin", "Administrador de herramientas", "Perfiles corporativos (identidad, textos, paleta, imágenes) y diccionario técnico."),
        Rol("cambiar_perfil", "Cambiar perfil corporativo", "Puede cambiar entre los perfiles Grupo ITT y TresVizo. Solo lo concede el propietario."),
    ],
)

#: Roles de las herramientas (todos menos `admin`): quien tenga alguno puede usar los comunes.
ROLES_DE_HERRAMIENTAS = tuple(r.clave for r in MODULO.roles if r.clave not in ("admin", "cambiar_perfil"))

from tvplataforma.modulos import Modulo, Rol

MODULO = Modulo(
    clave="herramientas",
    nombre="Herramientas Grupo ITT",
    host="tools.tresvizo.com",
    urlconf="itt_herramientas.urls",
    icono="herramientas/icono.svg",
    orden=20,
    rutas_publicas=("sw.js", "manifest.webmanifest"),
    roles=[
        Rol("reportes", "Reportes fotográficos"),
        Rol("cotizaciones", "Cotizaciones"),
        Rol("croquis", "Croquis de ubicación"),
        Rol("convertidor", "Convertidor DXF ↔ KML/KMZ"),
        Rol("triangulacion", "Triangulación, pendientes y escurrimiento"),
        Rol("cuadros_dxf", "Cuadros de construcción a DXF"),
        Rol("admin", "Administrador de herramientas", "Perfiles corporativos, textos, diccionario técnico y accesos."),
    ],
)

#: Roles de las herramientas (todos menos `admin`): quien tenga alguno puede usar los comunes.
ROLES_DE_HERRAMIENTAS = tuple(r.clave for r in MODULO.roles if r.clave != "admin")

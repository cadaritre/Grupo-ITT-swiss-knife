from tvplataforma.pruebas.ajustes import *  # noqa: F403

INSTALLED_APPS += ["django.contrib.staticfiles", "itt_herramientas"]  # noqa: F405
DATABASES = bases_de_prueba()  # noqa: F405
TV_HERRAMIENTAS_ARCHIVOS = carpeta_temporal("itt-archivos-")  # noqa: F405
TV_MAPAS_CACHE = TV_HERRAMIENTAS_ARCHIVOS / "cache"
TV_MAPAS_AGENTE = "PruebasITT/1.0 (+https://tools.tresvizo.com)"
STATIC_URL = "/static/"

# Tope de memoria de la cola `herramientas` (MB). La fija el despliegue (servidor de 8 GB: 1024); la prueba de memoria de triangulación lo lee de aquí.
HERRAMIENTAS_COLA_MEMORIA_MB = 1024

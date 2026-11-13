from tvplataforma.pruebas.ajustes import *  # noqa: F403

INSTALLED_APPS += ["itt_herramientas"]  # noqa: F405
DATABASES = bases_de_prueba()  # noqa: F405
TV_HERRAMIENTAS_ARCHIVOS = carpeta_temporal("itt-archivos-")  # noqa: F405
TV_MAPAS_CACHE = TV_HERRAMIENTAS_ARCHIVOS / "cache"
TV_MAPAS_AGENTE = "PruebasITT/1.0 (+https://tools.tresvizo.com)"

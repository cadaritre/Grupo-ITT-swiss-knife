from django.apps import AppConfig
from django.db.models.signals import post_migrate


class HerramientasConfig(AppConfig):
    name = "itt_herramientas"
    label = "herramientas"
    verbose_name = "Herramientas Grupo ITT"
    default_auto_field = "django.db.models.BigAutoField"
    tv_modulo = "itt_herramientas.modulo.MODULO"

    def ready(self):
        from tvplataforma import ajustes

        from .comun import perfiles
        from .dominio import almacenamiento

        # El dominio no lee variables de entorno ni crea carpetas al importarse: se configura aquí, con los ajustes del contrato.
        almacenamiento.configurar_cache(ajustes.valor("TV_MAPAS_CACHE"))
        almacenamiento.configurar_agente(ajustes.valor("TV_MAPAS_AGENTE"))
        post_migrate.connect(perfiles.sembrar_perfiles, sender=self, dispatch_uid="herramientas.sembrar_perfiles")

from django.urls import path

from . import views

app_name = "herramientas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("preferencias/", views.preferencias_, name="preferencias"),
    path("diccionario/", views.diccionario, name="diccionario"),
    path("perfiles/", views.perfiles_, name="perfiles"),
    path("perfiles/<slug:clave>/", views.perfil_editar, name="perfil"),
    path("perfiles/<slug:clave>/textos/", views.perfil_textos, name="perfil_textos"),
    path("perfiles/<slug:clave>/paleta/", views.perfil_paleta, name="perfil_paleta"),
    path("perfiles/<slug:clave>/accesos/", views.perfil_accesos, name="perfil_accesos"),
    path("importar/", views.importar, name="importar"),
    path("importar/<uuid:token>/", views.importar_confirmar, name="importar_confirmar"),
]

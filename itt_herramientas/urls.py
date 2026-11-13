from django.urls import path

from . import views

app_name = "herramientas"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("sw.js", views.service_worker, name="sw"),
    path("manifest.webmanifest", views.manifiesto, name="manifiesto"),
    path("capturar/", views.capturar, name="capturar"),
    path("instalar/", views.instalar, name="instalar"),
    path("sesion/", views.sesion, name="sesion"),
    path("capturas/", views.capturas, name="capturas"),
    path("perfil-activo/", views.perfil_activo, name="perfil_activo"),
    path("perfil.css", views.perfil_css, name="perfil_css"),
    path("perfil/logo/", views.perfil_logo, name="perfil_logo"),
    path("preferencias/", views.preferencias_, name="preferencias"),
    path("diccionario/", views.diccionario, name="diccionario"),
    path("perfiles/", views.perfiles_, name="perfiles"),
    path("perfiles/<slug:clave>/", views.perfil_editar, name="perfil"),
    path("perfiles/<slug:clave>/textos/", views.perfil_textos, name="perfil_textos"),
    path("perfiles/<slug:clave>/paleta/", views.perfil_paleta, name="perfil_paleta"),
    path("importar/", views.importar, name="importar"),
    path("importar/<uuid:token>/", views.importar_confirmar, name="importar_confirmar"),
]

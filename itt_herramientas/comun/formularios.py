from __future__ import annotations

from django import forms

from ..dominio import document_texts, quote_theme
from ..dominio.quotation_i18n import LABELS
from ..models import Perfil
from . import perfiles, preferencias
from ..dominio.location_sketch import MAP_LAYERS


class PerfilForm(forms.ModelForm):
    logo = forms.FileField(label="Logo (PNG o JPEG)", required=False)
    firma = forms.FileField(label="Firma (PNG o JPEG)", required=False, help_text="Solo se usa dentro del PDF; nunca se ofrece como descarga.")
    cedula = forms.FileField(label="Cédula profesional (imagen)", required=False, help_text="Solo se usa dentro del PDF; nunca se ofrece como descarga.")

    class Meta:
        model = Perfil
        fields = ["nombre", "encabezado", "encabezado_en", "descripcion", "descripcion_en", "sitio_web", "telefono",
                  "etiqueta_telefono", "etiqueta_telefono_en", "direccion", "correo", "firmante", "pie", "pie_en",
                  "firmante_reporte", "cedula_profesional", "banco", "clabe", "cuenta"]
        labels = {"sitio_web": "Sitio web", "telefono": "Teléfono", "etiqueta_telefono": "Etiqueta del teléfono",
                  "etiqueta_telefono_en": "Etiqueta del teléfono (inglés)", "direccion": "Domicilio", "correo": "Correo",
                  "firmante_reporte": "Firmante de reportes", "cedula_profesional": "Cédula profesional (número)",
                  "banco": "Banco", "clabe": "CLABE", "cuenta": "Cuenta"}

    def clean_nombre(self):
        valor = self.cleaned_data["nombre"].strip()
        if not valor:
            raise forms.ValidationError("El perfil necesita un nombre.")
        return valor

    def clean_sitio_web(self):
        valor = self.cleaned_data["sitio_web"].strip()
        if valor and not valor.lower().startswith(("http://", "https://")):
            raise forms.ValidationError("Escribe la dirección completa, con http:// o https://.")
        return valor

    def clean_clabe(self):
        valor = "".join(self.cleaned_data["clabe"].split())
        if valor and not (valor.isdigit() and len(valor) == 18):
            raise forms.ValidationError("La CLABE tiene 18 dígitos.")
        return valor

    def guardar(self):
        perfil = self.save(commit=False)
        for tipo in perfiles.IMAGENES:
            archivo = self.cleaned_data.get(tipo)
            if archivo:
                try:
                    perfiles.guardar_imagen(perfil, tipo, archivo.read())
                except perfiles.PerfilInvalido as exc:
                    self.add_error(tipo, str(exc))
        if self.errors:
            return None
        perfil.save()
        return perfil


def _etiqueta(clave: str) -> str:
    return clave.replace("_", " ").capitalize()


class TextosForm(forms.Form):
    """Un campo por rótulo; vacío = valor original. Prefijos: `rep_`, `es_`, `en_`."""

    def __init__(self, *args, perfil: Perfil, **kwargs):
        super().__init__(*args, **kwargs)
        self.perfil = perfil
        grupos = (("rep_", document_texts.REPORT_TEXT_DEFAULTS, perfil.textos_reporte, "Reportes"),
                  ("es_", LABELS["es"], perfil.textos_cotizacion_es, "Cotizaciones (español)"),
                  ("en_", LABELS["en"], perfil.textos_cotizacion_en, "Cotizaciones (inglés)"))
        self.grupos = []
        for prefijo, originales, guardados, titulo in grupos:
            campos = []
            for clave, original in originales.items():
                nombre = prefijo + clave
                self.fields[nombre] = forms.CharField(label=_etiqueta(clave), required=False, max_length=600, initial=(guardados or {}).get(clave, ""),
                                                      help_text=f"Original: {original}")
                campos.append(nombre)
            self.grupos.append((titulo, campos))

    def guardar(self) -> Perfil:
        def extraer(prefijo, originales):
            valores = {}
            for clave, original in originales.items():
                texto = self.cleaned_data.get(prefijo + clave, "").strip()
                if texto and texto != original:
                    valores[clave] = texto
            return valores
        reporte = extraer("rep_", document_texts.REPORT_TEXT_DEFAULTS)
        es, en = extraer("es_", LABELS["es"]), extraer("en_", LABELS["en"])
        perfiles.validar_textos(reporte, es, en)
        self.perfil.textos_reporte, self.perfil.textos_cotizacion_es, self.perfil.textos_cotizacion_en = reporte, es, en
        self.perfil.save()
        return self.perfil

    def clean(self):
        datos = super().clean()
        try:
            perfiles.validar_textos(
                {k[4:]: v for k, v in datos.items() if k.startswith("rep_") and v},
                {k[3:]: v for k, v in datos.items() if k.startswith("es_") and v},
                {k[3:]: v for k, v in datos.items() if k.startswith("en_") and v})
        except perfiles.PerfilInvalido as exc:
            raise forms.ValidationError(str(exc)) from exc
        return datos


class PaletaForm(forms.Form):
    preset = forms.ChoiceField(label="Plantilla", required=False, choices=[("", "Personalizada")] + [(n, n) for n in quote_theme.QUOTE_PALETTE_PRESETS])
    primary = forms.CharField(label="Barras", max_length=7)
    accent = forms.CharField(label="Acento", max_length=7)
    pale = forms.CharField(label="Fondo claro", max_length=7)
    bar_text = forms.CharField(label="Texto sobre barras", max_length=7)
    ink = forms.CharField(label="Texto", max_length=7)

    def clean(self):
        datos = super().clean()
        preset = datos.get("preset")
        if preset:
            datos.update(quote_theme.QUOTE_PALETTE_PRESETS[preset])
        try:
            self.avisos = perfiles.verificar_paleta({k: datos[k] for k in ("primary", "accent", "pale", "bar_text", "ink") if datos.get(k)})
        except perfiles.PerfilInvalido as exc:
            raise forms.ValidationError(str(exc)) from exc
        return datos

    def paleta(self) -> dict:
        return {k: self.cleaned_data[k].upper() for k in ("primary", "accent", "pale", "bar_text", "ink")}


class PreferenciasForm(forms.Form):
    reports_include_map = forms.BooleanField(label="Incluir croquis en los reportes", required=False)
    reports_include_credentials = forms.BooleanField(label="Incluir cédula y firma en los reportes", required=False)
    geospatial_utm_zone = forms.IntegerField(label="Zona UTM", min_value=1, max_value=60)
    geospatial_hemisphere = forms.ChoiceField(label="Hemisferio", choices=[("N", "Norte"), ("S", "Sur")])
    geospatial_map_layer = forms.ChoiceField(label="Mapa base del convertidor", choices=[(k, k) for k in MAP_LAYERS])
    geospatial_labels = forms.BooleanField(label="Etiquetas", required=False)
    geospatial_hatches = forms.BooleanField(label="Sombreado de polígonos (hatch)", required=False)
    geospatial_clamp_to_ground = forms.BooleanField(label="Pegar al relieve en KML", required=False)
    sketches_map_layer = forms.ChoiceField(label="Mapa base de croquis", choices=[(k, k) for k in MAP_LAYERS])
    sketches_contour_interval = forms.ChoiceField(label="Equidistancia de curvas", choices=[(i, i) for i in preferencias.INTERVALOS])
    triangulation_draw_preview = forms.BooleanField(label="Dibujar vista previa de la triangulación", required=False)

    @classmethod
    def desde(cls, datos: dict, *args, **kwargs):
        inicial = {f"{s}_{k}": v for s, c in datos.items() for k, v in c.items()}
        return cls(*args, initial=inicial, **kwargs)

    def valores(self) -> dict:
        salida: dict = {}
        for seccion in preferencias.SECCIONES:
            for clave in preferencias.ESQUEMA[seccion]:
                nombre = f"{seccion}_{clave}"
                if nombre in self.cleaned_data:
                    salida.setdefault(seccion, {})[clave] = self.cleaned_data[nombre]
        return salida

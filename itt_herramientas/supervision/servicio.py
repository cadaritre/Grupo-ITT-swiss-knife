"""Supervisión: las cotizaciones y los reportes de TODAS las cuentas, solo para leer (rol `supervision` o el propietario).

Nada de aquí escribe en un documento: las consultas leen, el PDF de una cotización se arma al vuelo y el de un reporte
desactualizado se genera aparte (`reportes.servicio.generar_copia`), sin tocar el reporte, su último PDF ni su Google Drive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import CharField, DateTimeField, DecimalField, F, IntegerField, Q, UUIDField, Value
from django.db.models.functions import Cast
from django.utils import timezone

from ..comun import capturas
from ..models import Cotizacion, Reporte

POR_PAGINA = 25
TIPOS = {"cotizacion": "Cotización", "reporte": "Reporte fotográfico"}


@dataclass
class Filtros:
    q: str = ""
    tipo: str = ""                    # "" | cotizacion | reporte
    autor: int | None = None          # cuenta_id
    desde: date | None = None         # fecha de creación, inclusive
    hasta: date | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def activos(self) -> bool:
        return bool(self.q or self.tipo or self.autor is not None or self.desde or self.hasta)

    @classmethod
    def de(cls, parametros) -> "Filtros":
        f = cls(q=" ".join(str(parametros.get("q", "")).split())[:80])
        tipo = parametros.get("tipo", "")
        f.tipo = tipo if tipo in TIPOS else ""
        autor = str(parametros.get("autor", "")).strip()
        if autor.isdigit():
            f.autor = int(autor)
        for nombre in ("desde", "hasta"):
            valor = str(parametros.get(nombre, "")).strip()
            if not valor:
                continue
            try:
                setattr(f, nombre, date.fromisoformat(valor))
            except ValueError:
                f.avisos.append("Una de las fechas no es válida; se ignoró.")
        if f.desde and f.hasta and f.desde > f.hasta:
            f.desde, f.hasta = f.hasta, f.desde
        return f

    def como_parametros(self) -> dict:
        """Los filtros para volver a armar la URL (paginación): solo los que tienen valor."""
        valores = {"q": self.q, "tipo": self.tipo, "autor": "" if self.autor is None else str(self.autor),
                   "desde": self.desde.isoformat() if self.desde else "", "hasta": self.hasta.isoformat() if self.hasta else ""}
        return {k: v for k, v in valores.items() if v}


def _rango(consulta, f: Filtros):
    zona = timezone.get_current_timezone()
    if f.desde:
        consulta = consulta.filter(creada__gte=timezone.make_aware(timezone.datetime.combine(f.desde, timezone.datetime.min.time()), zona))
    if f.hasta:
        consulta = consulta.filter(creada__lt=timezone.make_aware(timezone.datetime.combine(f.hasta + timedelta(days=1), timezone.datetime.min.time()), zona))
    if f.autor is not None:
        consulta = consulta.filter(cuenta_id=f.autor)
    return consulta


def _texto(*campos):
    def filtro(q: str) -> Q:
        condicion = Q()
        for c in campos:
            condicion |= Q(**{f"{c}__icontains": q})
        return condicion
    return filtro


# Las dos tablas se juntan en una sola consulta (UNION) con las mismas columnas, todas como anotaciones y en el mismo orden.
def _columnas(tipo: str, **propias) -> dict:
    comunes = {
        "s_id": Cast(F("id"), UUIDField()), "s_tipo": Value(tipo, CharField()), "s_cuenta": F("cuenta_id"),
        "s_autor": F("autor_nombre"), "s_correo": F("autor_correo"),
    }
    return {**comunes, **propias, "s_creada": Cast(F("creada"), DateTimeField()), "s_actualizada": Cast(F("actualizada"), DateTimeField())}


def _cotizaciones(f: Filtros):
    consulta = _rango(Cotizacion.objects.all(), f)
    if f.q:
        consulta = consulta.filter(_texto("autor_nombre", "autor_correo", "proyecto", "folio", "cliente")(f.q))
    return consulta.annotate(**_columnas(
        "cotizacion", s_titulo=F("proyecto"), s_folio=F("folio"), s_cliente=F("cliente"), s_total=Cast(F("total"), DecimalField(max_digits=14, decimal_places=2)),
        s_moneda=F("moneda"), s_fotos=Value(None, IntegerField()), s_revision=F("revision"), s_pdf_revision=Value(None, IntegerField()),
    )).values(*_NOMBRES)


def _reportes(f: Filtros):
    consulta = _rango(Reporte.objects.all(), f)
    if f.q:
        consulta = consulta.filter(_texto("autor_nombre", "autor_correo", "titulo")(f.q))
    return consulta.annotate(**_columnas(
        "reporte", s_titulo=F("titulo"), s_folio=Value("", CharField()), s_cliente=Value("", CharField()),
        s_total=Value(None, DecimalField(max_digits=14, decimal_places=2)), s_moneda=Value("", CharField()), s_fotos=Cast(F("fotos"), IntegerField()),
        s_revision=F("revision"), s_pdf_revision=Cast(F("salida_revision"), IntegerField()),
    )).values(*_NOMBRES)


_NOMBRES = ("s_id", "s_tipo", "s_cuenta", "s_autor", "s_correo", "s_titulo", "s_folio", "s_cliente", "s_total", "s_moneda", "s_fotos",
            "s_revision", "s_pdf_revision", "s_creada", "s_actualizada")


def consulta(f: Filtros):
    """Documentos que cumplen los filtros, del más reciente al más viejo (por fecha de creación)."""
    if f.tipo == "cotizacion":
        partes = _cotizaciones(f)
    elif f.tipo == "reporte":
        partes = _reportes(f)
    else:
        partes = _cotizaciones(f).union(_reportes(f), all=True)
    return partes.order_by("-s_creada", "-s_id")


@dataclass
class Fila:
    id: str
    tipo: str
    cuenta_id: int
    autor: str
    correo: str
    titulo: str
    folio: str
    cliente: str
    total: Decimal | None
    moneda: str
    fotos: int | None
    creada: object
    actualizada: object
    pdf_al_dia: bool = False

    @property
    def tipo_nombre(self) -> str:
        return TIPOS[self.tipo]


def filas(pagina) -> list[Fila]:
    """Convierte la página en filas para la plantilla; a las cotizaciones les cuenta sus imágenes (solo las de esta página)."""
    crudas = list(pagina)
    ids = [r["s_id"] for r in crudas if r["s_tipo"] == "cotizacion"]
    imagenes = {pk: len((datos or {}).get("images") or []) for pk, datos in Cotizacion.objects.filter(pk__in=ids).values_list("pk", "datos")}
    salida = []
    for r in crudas:
        fotos = imagenes.get(r["s_id"], 0) if r["s_tipo"] == "cotizacion" else r["s_fotos"]
        salida.append(Fila(id=str(r["s_id"]), tipo=r["s_tipo"], cuenta_id=r["s_cuenta"], autor=r["s_autor"], correo=r["s_correo"], titulo=r["s_titulo"],
                           folio=r["s_folio"], cliente=r["s_cliente"], total=r["s_total"], moneda=r["s_moneda"], fotos=fotos, creada=r["s_creada"],
                           actualizada=r["s_actualizada"], pdf_al_dia=r["s_tipo"] == "reporte" and r["s_pdf_revision"] == r["s_revision"]))
    return salida


def autores() -> list[dict]:
    """Las cuentas que tienen documentos, con el nombre y el correo de su documento más reciente. Marca las que ya no existen."""
    vistos: dict[int, dict] = {}
    for modelo in (Cotizacion, Reporte):
        ultimos = modelo.objects.order_by("cuenta_id", "-creada").distinct("cuenta_id")              # PostgreSQL: un renglón por cuenta
        for cuenta, nombre, correo, creada in ultimos.values_list("cuenta_id", "autor_nombre", "autor_correo", "creada"):
            previo = vistos.get(cuenta)
            if previo is None or creada > previo["creada"]:
                vistos[cuenta] = {"cuenta_id": cuenta, "nombre": nombre, "correo": correo, "creada": creada}
    existentes = set(get_user_model().objects.filter(pk__in=vistos).values_list("pk", flat=True))
    for a in vistos.values():
        a["eliminada"] = a["cuenta_id"] not in existentes
    return sorted(vistos.values(), key=lambda a: ((a["nombre"] or a["correo"] or "").casefold(), a["cuenta_id"]))


def cuenta_existe(cuenta_id: int) -> bool:
    return get_user_model().objects.filter(pk=cuenta_id).exists()


def limpiar() -> int:
    """`tv_limpiar`: borra las copias de supervisión de más de 7 días o de reportes que ya no existen."""
    import time

    carpeta = capturas.raiz() / "supervision" / "reportes"
    if not carpeta.is_dir():
        return 0
    vigentes = {str(pk) for pk in Reporte.objects.values_list("pk", flat=True)}
    limite = time.time() - 7 * 86400
    n = 0
    for archivo in carpeta.glob("*.pdf"):
        if archivo.name[:36] not in vigentes or archivo.stat().st_mtime < limite:
            archivo.unlink(missing_ok=True)
            n += 1
    return n

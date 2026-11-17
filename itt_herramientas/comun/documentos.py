"""Documentos compartidos con el equipo: cotizaciones, reportes fotográficos y croquis (decisión del propietario, 16-11-2026).

Todas las cuentas con el rol de una herramienta ven y editan todos los documentos de esa herramienta. Aquí vive lo común a los tres:

- **Bloqueo de edición** (como los planos de CAD): una sola página del editor edita a la vez. La página toma el bloqueo, late cada
  `LATIDO_SEGUNDOS` y lo pierde si deja de latir `BLOQUEO_SEGUNDOS`. Otra persona abre en solo lectura («La está editando X desde
  hh:mm») y puede tomar el control explicitamente: a quien editaba se le avisa y sus cambios sin guardar quedan en un borrador de
  recuperación (`BorradorRecuperacion`), nunca se pierden.
- **Historial** (`VersionDocumento`): se guarda cada revisión. Los guardados seguidos de una misma persona (`AGRUPAR_MINUTOS`) se
  muestran como una sola versión; se conservan las `VERSIONES_VISIBLES` más recientes de cada documento y los pasos intermedios
  `INTERMEDIAS_DIAS` días (son la base para fusionar lo editado sin conexión). «Restaurar esta versión» crea una revisión nueva.
- **Sincronización sin conexión** (`comun.sincronizacion`): fusiona cuando no choca; si choca, guarda una copia en conflicto y avisa.
- **Papelera**: eliminar manda el documento a la papelera `PAPELERA_DIAS` días; lo elimina quien lo creó o quien administra.
- **Registro**: tomar el control, restaurar, eliminar, recuperar de la papelera y reasignar folios quedan en el registro
  `itt_herramientas.documentos` (con cuentas, nunca con nombres de personas), igual que CAD.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from tvplataforma import permisos

from ..models import AvisoDocumento, BorradorRecuperacion, Cotizacion, Croquis, Reporte, VersionDocumento
from . import autoria
from .errores import ErrorUsuario

log = logging.getLogger("itt_herramientas.documentos")

#: El editor late cada 15 s (y al volver a la pestaña); sin latido 150 s, el bloqueo caduca y otra persona puede editar sin tomar el
#: control. 150 s y no menos: un navegador deja latir una pestaña en segundo plano una vez por minuto.
LATIDO_SEGUNDOS = 15
BLOQUEO_SEGUNDOS = 150
#: Guardados seguidos de la misma persona dentro de esta ventana se muestran como una sola versión.
AGRUPAR_MINUTOS = 10
#: Versiones del historial que se conservan por documento (la vigente siempre).
VERSIONES_VISIBLES = 100
#: Días que se guardan los pasos intermedios (base de la fusión de lo editado sin conexión).
INTERMEDIAS_DIAS = 30
PAPELERA_DIAS = 30
RECUPERACION_DIAS = 30
AVISOS_DIAS = 90
_SESION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
OTRA_VENTANA = "\x00otra-ventana"

MODELOS = {"cotizacion": Cotizacion, "reporte": Reporte, "croquis": Croquis}
ROLES = {"cotizacion": "cotizaciones", "reporte": "reportes", "croquis": "croquis"}
NOMBRES = {"cotizacion": "la cotización", "reporte": "el reporte", "croquis": "el croquis"}


class SinPermiso(ErrorUsuario):
    pass


def tipo_de(doc) -> str:
    for tipo, modelo in MODELOS.items():
        if isinstance(doc, modelo):
            return tipo
    raise ValueError(doc)


# -- Permisos -----------------------------------------------------------------------------------------------------------------

def puede_editar(usuario, tipo: str) -> bool:
    return any(permisos.tiene_rol(usuario, "herramientas", r) for r in (ROLES[tipo], "admin"))


def es_admin(usuario) -> bool:
    return permisos.tiene_rol(usuario, "herramientas", "admin")


def puede_eliminar(usuario, doc) -> bool:
    """Como en CAD: quien lo creó o quien administra las herramientas."""
    return es_admin(usuario) or (puede_editar(usuario, tipo_de(doc)) and doc.cuenta_id == usuario.pk)


def puede_recuperar(usuario, doc) -> bool:
    return es_admin(usuario) or (puede_editar(usuario, tipo_de(doc)) and usuario.pk in (doc.cuenta_id, doc.eliminado_por_id))


def visibles(modelo):
    return modelo.objects.filter(eliminado__isnull=True)


def referencia(doc) -> str:
    """Cómo se nombra el documento en avisos y copias: el folio de una cotización, el título de lo demás."""
    if isinstance(doc, Cotizacion):
        return doc.folio or doc.proyecto or "Cotización sin folio"
    return doc.titulo or ("Reporte sin título" if isinstance(doc, Reporte) else "Croquis sin título")


def _hora(momento) -> str:
    return timezone.localtime(momento).strftime("%d/%m/%Y %H:%M")


# -- Lo que ven las pantallas ---------------------------------------------------------------------------------------------------

def bloqueo_activo(doc, ahora=None) -> bool:
    ahora = ahora or timezone.now()
    return bool(doc.bloqueo_sesion and doc.bloqueo_latido and doc.bloqueo_latido > ahora - timedelta(seconds=BLOQUEO_SEGUNDOS))


def fila(doc, usuario) -> dict:
    """Autoría, «modificada por», bloqueo vigente y permisos: lo común de cada renglón y de cada encabezado."""
    bloqueo = None
    if bloqueo_activo(doc):
        # `caduca`: el latido no mueve el cursor de las listas, así que el teléfono deja de mostrar el bloqueo cuando pasa esa hora.
        bloqueo = {"nombre": doc.bloqueo_nombre or "Otra persona", "desde": (doc.bloqueo_desde or doc.bloqueo_latido).isoformat(),
                   "mio": doc.bloqueo_cuenta_id == usuario.pk, "caduca": (doc.bloqueo_latido + timedelta(seconds=BLOQUEO_SEGUNDOS)).isoformat()}
    return {"autor": autoria.fila(doc), "modificado": {"nombre": doc.modificado_por_nombre or doc.autor_nombre, "fecha": doc.actualizada.isoformat()},
            "bloqueo": bloqueo, "conflicto": doc.conflicto, "puede_borrar": puede_eliminar(usuario, doc)}


# -- Bloqueo de edición ----------------------------------------------------------------------------------------------------------

def sesion_valida(sesion: str) -> bool:
    return bool(_SESION.match(sesion or ""))


def _de_otro(doc, usuario, sesion: str, ahora) -> str:
    if not bloqueo_activo(doc, ahora) or (sesion and doc.bloqueo_sesion == sesion):
        return ""
    if doc.bloqueo_cuenta_id == usuario.pk:
        return OTRA_VENTANA
    return doc.bloqueo_nombre or "Otra persona"


def _tomar(doc, usuario, sesion: str, ahora) -> None:
    if doc.bloqueo_sesion == sesion and doc.bloqueo_cuenta_id == usuario.pk and bloqueo_activo(doc, ahora):
        doc.bloqueo_latido = ahora                                    # el latido de la misma página: no mueve el cursor de las listas
        doc.save(update_fields=["bloqueo_latido"])
        return
    if doc.bloqueo_sesion != sesion or not bloqueo_activo(doc, ahora):
        doc.bloqueo_desde = ahora
    doc.bloqueo_cuenta_id, doc.bloqueo_nombre = usuario.pk, autoria.de_usuario(usuario)[0]
    doc.bloqueo_sesion, doc.bloqueo_latido = sesion, ahora
    doc.save(update_fields=["bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion", "bloqueo_latido", "bloqueo_desde"])


def _soltar(doc) -> None:
    doc.bloqueo_cuenta_id, doc.bloqueo_nombre, doc.bloqueo_sesion, doc.bloqueo_latido, doc.bloqueo_desde = None, "", "", None, None
    doc.save(update_fields=["bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion", "bloqueo_latido", "bloqueo_desde"])


def estado_bloqueo(doc, usuario, sesion: str = "") -> dict:
    ahora = timezone.now()
    if not bloqueo_activo(doc, ahora):
        return {"libre": True, "mio": False, "quien": "", "otra_ventana": False, "revision": doc.revision}
    mio = bool(sesion) and doc.bloqueo_sesion == sesion
    return {"libre": False, "mio": mio, "quien": "" if mio else (doc.bloqueo_nombre or "Otra persona"), "otra_ventana": not mio and doc.bloqueo_cuenta_id == usuario.pk,
            "desde": (doc.bloqueo_desde or doc.bloqueo_latido).isoformat(), "revision": doc.revision}


def bloqueo(modelo, pk, usuario, sesion: str, accion: str) -> dict:
    """tomar | latido | soltar | forzar. «forzar» es tomar el control explícitamente: lo puede hacer cualquiera que edite la herramienta.

    El latido de una página que ya no tiene el bloqueo responde `perdido` (con quién lo tiene): la página lo dice y deja de editar.
    """
    if not sesion_valida(sesion):
        raise ErrorUsuario("La página no es válida. Recárgala.")
    tipo = next(t for t, m in MODELOS.items() if m is modelo)
    with transaction.atomic():
        doc = modelo.objects.select_for_update().filter(pk=pk).first()
        if doc is None or doc.eliminado:
            return {"libre": False, "mio": False, "quien": "", "eliminado": True}
        ahora = timezone.now()
        otro = _de_otro(doc, usuario, sesion, ahora)
        if accion == "soltar":
            if doc.bloqueo_sesion == sesion:
                _soltar(doc)
            return {"libre": True, "mio": False, "quien": "", "revision": doc.revision}
        if accion in ("latido", "tomar"):
            if otro:
                return {**estado_bloqueo(doc, usuario, sesion), "perdido": accion == "latido"}
            _tomar(doc, usuario, sesion, ahora)
            return estado_bloqueo(doc, usuario, sesion)
        if accion == "forzar":
            anterior, cuenta_anterior = otro, doc.bloqueo_cuenta_id
            _tomar(doc, usuario, sesion, ahora)
            if anterior and anterior != OTRA_VENTANA:
                log.info("Documentos: la cuenta %s tomó el control de %s %s (lo editaba la cuenta %s)", usuario.pk, tipo, doc.pk, cuenta_anterior)
                avisar(cuenta_anterior, tipo, doc.pk, f"{autoria.de_usuario(usuario)[0]} tomó el control de «{referencia(doc)}» el {_hora(ahora)}. "
                       "Si tenías cambios sin guardar, quedaron en un borrador de recuperación.")
            return {**estado_bloqueo(doc, usuario, sesion), "tomado_de": ("Tú, en otra ventana" if anterior == OTRA_VENTANA else anterior) if anterior else ""}
    raise ErrorUsuario("Acción desconocida.")


def bloqueado_por_otro(doc, usuario, sesion: str) -> str:
    """Nombre de quien edita el documento ahora si no es esta página (otra ventana de la misma cuenta incluida)."""
    otro = _de_otro(doc, usuario, sesion, timezone.now())
    return "Tú, en otra ventana" if otro == OTRA_VENTANA else otro


# -- Historial ---------------------------------------------------------------------------------------------------------------------

def registrar_version(doc, usuario, nota: str = "") -> VersionDocumento:
    """Guarda la revisión vigente de `doc` (ya guardado) en su historial y fija «modificado por»."""
    tipo = tipo_de(doc)
    nombre = autoria.de_usuario(usuario)[0] if usuario is not None else ""
    cuenta = usuario.pk if usuario is not None else None
    ahora = timezone.now()
    autorizados = sorted({*(doc.autorizados or []), doc.cuenta_id, *([cuenta] if cuenta is not None else [])} - {None})
    type(doc).objects.filter(pk=doc.pk).update(modificado_por_id=cuenta, modificado_por_nombre=nombre, autorizados=autorizados)
    doc.modificado_por_id, doc.modificado_por_nombre, doc.autorizados = cuenta, nombre, autorizados
    previa = VersionDocumento.objects.filter(tipo=tipo, documento_id=doc.pk, visible=True).order_by("-revision").first()
    inicio = ahora
    if (previa is not None and not nota and not previa.nota and previa.cuenta_id == cuenta and cuenta is not None
            and previa.inicio > ahora - timedelta(minutes=AGRUPAR_MINUTOS)):
        inicio = previa.inicio                                             # mismo grupo: la anterior pasa a ser un paso intermedio
        VersionDocumento.objects.filter(pk=previa.pk).update(visible=False)
    v, _ = VersionDocumento.objects.update_or_create(tipo=tipo, documento_id=doc.pk, revision=doc.revision, defaults={
        "datos": doc.datos, "perfil": doc.perfil, "cuenta_id": cuenta, "autor_nombre": nombre, "nota": nota[:200], "visible": True,
        "inicio": inicio, "creada": ahora})
    viejas = list(VersionDocumento.objects.filter(tipo=tipo, documento_id=doc.pk, visible=True).order_by("-revision")
                  .values_list("pk", flat=True)[VERSIONES_VISIBLES:])
    if viejas:
        VersionDocumento.objects.filter(pk__in=viejas).delete()
    return v


def datos_de_revision(tipo: str, documento_id, revision: int | None):
    """Los datos exactos de una revisión (visible o intermedia), o None si ya no se guardan."""
    if revision is None:
        return None
    v = VersionDocumento.objects.filter(tipo=tipo, documento_id=documento_id, revision=revision).first()
    return v.datos if v is not None else None


def versiones(doc) -> list[dict]:
    filas = VersionDocumento.objects.filter(tipo=tipo_de(doc), documento_id=doc.pk, visible=True).order_by("-revision")
    return [fila_version(v, doc) for v in filas]


def fila_version(v: VersionDocumento, doc) -> dict:
    return {"id": v.pk, "revision": v.revision, "nombre": v.autor_nombre or "Cuenta eliminada", "fecha": v.creada.isoformat(), "inicio": v.inicio.isoformat(),
            "nota": v.nota, "vigente": v.revision == doc.revision}


def version_de(doc, version_id: int) -> VersionDocumento | None:
    return VersionDocumento.objects.filter(pk=version_id, tipo=tipo_de(doc), documento_id=doc.pk, visible=True).first()


def _exigir_edicion(doc, usuario, sesion: str) -> None:
    otro = bloqueado_por_otro(doc, usuario, sesion)
    if otro:
        raise ErrorUsuario(f"{otro} está editando {NOMBRES[tipo_de(doc)]}. Toma el control para cambiarlo.")


def restaurar_version(modelo, pk, version_id: int, usuario, sesion: str, aplicar) -> object:
    """«Restaurar esta versión»: sus datos pasan a ser una revisión NUEVA (la actual queda en el historial). `aplicar(doc, datos)`."""
    with transaction.atomic():
        doc = modelo.objects.select_for_update().filter(pk=pk, eliminado__isnull=True).first()
        if doc is None:
            raise ErrorUsuario("El documento ya no existe.")
        _exigir_edicion(doc, usuario, sesion)
        v = version_de(doc, version_id)
        if v is None:
            raise ErrorUsuario("Esa versión ya no está en el historial.")
        if v.revision == doc.revision:
            raise ErrorUsuario("Esa ya es la versión vigente.")
        aplicar(doc, v.datos)
        doc.revision += 1
        doc.save()
        fecha = timezone.localtime(v.creada).strftime("%d/%m/%Y %H:%M")
        registrar_version(doc, usuario, nota=f"Restaurada de la versión {v.revision} ({fecha})")
        log.info("Documentos: la cuenta %s restauró %s %s a la revisión %s (nueva revisión %s)", usuario.pk, tipo_de(doc), doc.pk, v.revision, doc.revision)
        return doc


# -- Borradores de recuperación ------------------------------------------------------------------------------------------------

def guardar_recuperacion(doc, usuario, datos: dict, revision_base: int | None, sesion: str = "") -> BorradorRecuperacion:
    """Uno por página del editor (`sesion`): lo más nuevo de una página reemplaza lo anterior de esa misma página, nunca lo de otra."""
    b, _ = BorradorRecuperacion.objects.update_or_create(tipo=tipo_de(doc), documento_id=doc.pk, cuenta_id=usuario.pk, sesion=sesion or "", defaults={
        "datos": datos, "revision_base": revision_base or doc.revision, "quien": doc.bloqueo_nombre or "", "creado": timezone.now()})
    return b


def _fila_recuperacion(b: BorradorRecuperacion) -> dict:
    return {"id": b.pk, "creado": b.creado.isoformat(), "revision_base": b.revision_base, "quien": b.quien, "datos": b.datos}


def recuperaciones_de(doc, usuario) -> list[dict]:
    """Los borradores de recuperación de esta cuenta en `doc`, del más nuevo al más viejo."""
    return [_fila_recuperacion(b) for b in BorradorRecuperacion.objects.filter(tipo=tipo_de(doc), documento_id=doc.pk, cuenta_id=usuario.pk).order_by("-creado")]


def descartar_recuperacion(doc, usuario, recuperacion_id=None) -> None:
    """Descarta uno (`recuperacion_id`) o, sin él, todos los de esta cuenta en `doc`."""
    consulta = BorradorRecuperacion.objects.filter(tipo=tipo_de(doc), documento_id=doc.pk, cuenta_id=usuario.pk)
    if recuperacion_id is not None:
        consulta = consulta.filter(pk=recuperacion_id)
    consulta.delete()


def aplicar_recuperacion(modelo, pk, usuario, sesion: str, aplicar, recuperacion_id=None) -> object:
    """«Usar mis cambios»: ese borrador de recuperación (o el más nuevo) pasa a ser una revisión nueva (lo demás queda en el historial)."""
    with transaction.atomic():
        doc = modelo.objects.select_for_update().filter(pk=pk, eliminado__isnull=True).first()
        if doc is None:
            raise ErrorUsuario("El documento ya no existe.")
        _exigir_edicion(doc, usuario, sesion)
        consulta = BorradorRecuperacion.objects.filter(tipo=tipo_de(doc), documento_id=doc.pk, cuenta_id=usuario.pk).order_by("-creado")
        if recuperacion_id is not None:
            consulta = consulta.filter(pk=recuperacion_id)
        b = consulta.first()
        if b is None:
            raise ErrorUsuario("Ya no hay cambios por recuperar.")
        aplicar(doc, b.datos)
        doc.revision += 1
        doc.save()
        registrar_version(doc, usuario, nota="Recuperada de cambios sin guardar")
        b.delete()
        return doc


# -- Avisos --------------------------------------------------------------------------------------------------------------------

def avisar(cuenta_id, tipo: str, documento_id, texto: str) -> None:
    if cuenta_id is None:
        return
    AvisoDocumento.objects.create(cuenta_id=cuenta_id, tipo=tipo, documento_id=documento_id, texto=texto[:400])


def avisos_de(usuario, tipo: str) -> list[dict]:
    return [{"id": a.pk, "texto": a.texto, "documento": str(a.documento_id), "creado": a.creado.isoformat()}
            for a in AvisoDocumento.objects.filter(cuenta_id=usuario.pk, tipo=tipo, visto__isnull=True).order_by("-creado")[:20]]


def marcar_vistos(usuario, ids) -> None:
    AvisoDocumento.objects.filter(cuenta_id=usuario.pk, pk__in=[int(i) for i in ids if str(i).isdigit()][:100]).update(visto=timezone.now())


# -- Papelera --------------------------------------------------------------------------------------------------------------------

def eliminar(doc, usuario) -> None:
    if not puede_eliminar(usuario, doc):
        raise SinPermiso("Solo quien lo creó o quien administra las herramientas puede eliminarlo.")
    otro = bloqueado_por_otro(doc, usuario, "")
    if otro and otro != "Tú, en otra ventana" and not es_admin(usuario):
        raise ErrorUsuario(f"{otro} lo está editando. Espera a que termine para eliminarlo.")
    doc.eliminado, doc.eliminado_por_id, doc.eliminado_por_nombre = timezone.now(), usuario.pk, autoria.de_usuario(usuario)[0]
    doc.bloqueo_cuenta_id, doc.bloqueo_nombre, doc.bloqueo_sesion, doc.bloqueo_latido, doc.bloqueo_desde = None, "", "", None, None
    doc.save(update_fields=["eliminado", "eliminado_por_id", "eliminado_por_nombre", "bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion",
                            "bloqueo_latido", "bloqueo_desde"])
    log.info("Documentos: la cuenta %s mandó a la papelera %s %s", usuario.pk, tipo_de(doc), doc.pk)


def recuperar(doc, usuario) -> None:
    if not puede_recuperar(usuario, doc):
        raise SinPermiso("Solo quien lo eliminó, quien lo creó o quien administra las herramientas puede recuperarlo.")
    doc.eliminado, doc.eliminado_por_id, doc.eliminado_por_nombre = None, None, ""
    doc.save(update_fields=["eliminado", "eliminado_por_id", "eliminado_por_nombre"])
    log.info("Documentos: la cuenta %s recuperó de la papelera %s %s", usuario.pk, tipo_de(doc), doc.pk)


def destruir(doc) -> None:
    """Borra el documento para siempre, con su historial, borradores de recuperación y avisos."""
    from ..models import PurgaDocumentos, siguiente_cambio

    tipo = tipo_de(doc)
    with transaction.atomic():                                     # los teléfonos con un cursor anterior ya no verán su marca: se les avisa
        marca = siguiente_cambio()
        PurgaDocumentos.objects.update_or_create(tipo=tipo, defaults={"hasta": marca})
    VersionDocumento.objects.filter(tipo=tipo, documento_id=doc.pk).delete()
    BorradorRecuperacion.objects.filter(tipo=tipo, documento_id=doc.pk).delete()
    AvisoDocumento.objects.filter(tipo=tipo, documento_id=doc.pk).delete()
    doc.delete()


def papelera(usuario, tipos) -> list:
    """Los documentos en la papelera de las herramientas `tipos`, del más reciente al más viejo."""
    salida = []
    for tipo in tipos:
        salida += [(tipo, d) for d in MODELOS[tipo].objects.filter(eliminado__isnull=False).order_by("-eliminado")[:200]]
    salida.sort(key=lambda par: par[1].eliminado, reverse=True)
    return salida


def vence_en(doc):
    return doc.eliminado + timedelta(days=PAPELERA_DIAS) if doc.eliminado else None


# -- Mantenimiento y cuentas eliminadas -----------------------------------------------------------------------------------------

def limpiar() -> int:
    """`tv_limpiar`: papelera de más de 30 días, pasos intermedios del historial, recuperaciones y avisos viejos."""
    ahora = timezone.now()
    n = 0
    for modelo in MODELOS.values():
        for doc in modelo.objects.filter(eliminado__lt=ahora - timedelta(days=PAPELERA_DIAS)):
            destruir(doc)
            n += 1
    n += VersionDocumento.objects.filter(visible=False, creada__lt=ahora - timedelta(days=INTERMEDIAS_DIAS)).delete()[0]
    n += BorradorRecuperacion.objects.filter(creado__lt=ahora - timedelta(days=RECUPERACION_DIAS)).delete()[0]
    n += AvisoDocumento.objects.filter(Q(visto__lt=ahora - timedelta(days=7)) | Q(creado__lt=ahora - timedelta(days=AVISOS_DIAS))).delete()[0]
    return n


def al_eliminar_cuenta(cuenta_id: int) -> str | None:
    """Contrato 1.8: los documentos son de la empresa y se quedan con el nombre de su autor. Se sueltan los que la persona tenía abiertos y
    se borran sus borradores de recuperación y sus avisos. Lo que guardó en Google Drive sigue las reglas de Drive. Idempotente."""
    from ..models import tocar

    soltados = 0
    for modelo in MODELOS.values():
        ids = list(modelo.objects.filter(bloqueo_cuenta_id=cuenta_id).values_list("pk", flat=True))
        soltados += modelo.objects.filter(pk__in=ids).update(bloqueo_cuenta_id=None, bloqueo_nombre="", bloqueo_sesion="", bloqueo_latido=None, bloqueo_desde=None)
        for pk in ids:
            tocar(modelo, pk)                                       # las listas de los teléfonos dejan de mostrar «La está editando»
    borrados = BorradorRecuperacion.objects.filter(cuenta_id=cuenta_id).delete()[0]
    AvisoDocumento.objects.filter(cuenta_id=cuenta_id).delete()
    partes = []
    if soltados:
        partes.append(f"se liberaron {soltados} documento{'s' if soltados != 1 else ''} que tenía abierto{'s' if soltados != 1 else ''}")
    if borrados:
        partes.append(f"se borraron {borrados} borrador{'es' if borrados != 1 else ''} de recuperación")
    return ("Documentos: " + " y ".join(partes) + ".")[:300] if partes else None

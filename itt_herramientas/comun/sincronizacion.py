"""Sincronización de borradores del teléfono (cotizaciones, reportes, croquis): idempotente y sin perder nada de nadie.

El teléfono manda `id_cliente` (llave de idempotencia), `servidor_id` (si ya lo conoce), `revision_base` (la revisión del servidor en que
se basó) y, si lo editó con el documento abierto en línea, `sesion` (la página del editor que tenía el bloqueo).

Con el documento ya en el servidor:

1. Si es igual a lo guardado, no pasa nada.
2. Si otra página tiene el bloqueo de edición:
   - con `sesion` (esta página lo tenía y alguien tomó el control): se guarda como borrador de recuperación de la cuenta (`recuperacion`);
   - sin `sesion` (se editó sin conexión mientras otra persona lo edita): copia en conflicto.
3. Si el servidor sigue en `revision_base`, se aplica (revisión nueva).
4. Si cambió: se fusiona campo por campo con la revisión base del historial cuando los cambios no se tocan (`fusionada`); si se tocan, o la
   base ya no está, se guarda una COPIA EN CONFLICTO y se avisa a quien hizo la versión vigente. El original nunca se pisa.

Un documento que se eliminó mientras el teléfono lo editaba también se conserva como copia. Reenviar la misma petición (respuesta perdida)
devuelve el mismo resultado sin duplicar.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from django.db import transaction
from django.utils import timezone

from . import autoria, documentos

_FALTA = object()


def fusionar(base, actual, nuevos, claves: dict | None = None, ruta: str = ""):
    """Fusión de tres vías: lo que cambió cada lado desde `base`. Devuelve el resultado o lanza `ValueError` si los cambios chocan.

    Los diccionarios se fusionan llave por llave. Las listas cuentan como un solo valor, salvo las de `claves` ({ruta: llave}), cuyos
    elementos son diccionarios con identidad (las fotos de un reporte por `sha256`): se fusionan elemento por elemento.
    """
    if actual == nuevos:
        return actual
    if actual == base:
        return nuevos
    if nuevos == base:
        return actual
    if isinstance(base, dict) and isinstance(actual, dict) and isinstance(nuevos, dict):
        salida = {}
        for k in list(actual) + [k for k in nuevos if k not in actual]:
            b, a, n = base.get(k, _FALTA), actual.get(k, _FALTA), nuevos.get(k, _FALTA)
            if a == n:
                r = a
            elif a == b:
                r = n
            elif n == b:
                r = a
            elif _FALTA in (a, n, b):
                raise ValueError(k)
            else:
                r = fusionar(b, a, n, claves, f"{ruta}.{k}" if ruta else k)
            if r is not _FALTA:
                salida[k] = r
        return salida
    llave = (claves or {}).get(ruta)
    if llave and all(isinstance(x, list) for x in (base, actual, nuevos)):
        return _fusionar_lista(base, actual, nuevos, llave, claves, ruta)
    raise ValueError(ruta)


def _fusionar_lista(base, actual, nuevos, llave, claves, ruta):
    ident = lambda lista: [x.get(llave) if isinstance(x, dict) else None for x in lista]  # noqa: E731
    ib, ia, inn = ident(base), ident(actual), ident(nuevos)
    if None in ib + ia + inn or len(set(ia)) != len(ia) or len(set(inn)) != len(inn):
        raise ValueError(ruta)
    pb, pa, pn = dict(zip(ib, base)), dict(zip(ia, actual)), dict(zip(inn, nuevos))
    elementos = {}
    for k in set(ib) | set(ia) | set(inn):
        b, a, n = pb.get(k, _FALTA), pa.get(k, _FALTA), pn.get(k, _FALTA)
        if a == n:
            r = a
        elif a == b:
            r = n
        elif n == b:
            r = a
        elif _FALTA in (a, n, b):
            raise ValueError(ruta)                                         # uno lo quitó y el otro lo cambió
        else:
            r = fusionar(b, a, n, claves, ruta)
        if r is not _FALTA:
            elementos[k] = r
    comunes = [k for k in ib if k in elementos]
    orden_a, orden_n = [k for k in ia if k in comunes], [k for k in inn if k in comunes]
    if orden_a != comunes and orden_n != comunes and orden_a != orden_n:
        raise ValueError(ruta)                                             # los dos reordenaron distinto
    if orden_n != comunes:                                                 # solo el teléfono reordenó: su orden, y lo nuevo del servidor al final
        orden = [k for k in inn if k in elementos] + [k for k in ia if k in elementos and k not in inn]
    else:
        orden = [k for k in ia if k in elementos] + [k for k in inn if k in elementos and k not in ia]
    return [elementos[k] for k in orden]


def sincronizar(modelo, usuario, id_cliente: uuid.UUID, servidor_id: uuid.UUID | None, revision_base: int | None, nuevos: dict, *,
                aplicar: Callable, aplicar_copia: Callable, error: type[Exception], normalizar: Callable | None = None,
                sesion: str = "", claves: dict | None = None, aplicar_datos: Callable | None = None, ajustar: Callable | None = None) -> dict:
    """`nuevos`: datos validados. `aplicar(obj)` los vuelca en el registro; `aplicar_copia(obj)` hace lo mismo para una copia en
    conflicto; `aplicar_datos(obj, datos)` vuelca otros datos (el resultado de una fusión). `normalizar(datos_guardados)` (opcional)
    los lleva al esquema actual antes de compararlos. `claves`: listas que se fusionan por elemento (ver `fusionar`). `ajustar(actual, nuevos)`
    pone en lo del teléfono lo que fija el servidor (el folio) antes de comparar."""
    tipo = next(t for t, m in documentos.MODELOS.items() if m is modelo)

    def resumen(obj, **extra):
        return {"servidor_id": str(obj.id), "revision": obj.revision, "folio": getattr(obj, "folio", ""), "autor": autoria.fila(obj),
                "conflicto": obj.conflicto, "modificado": {"nombre": obj.modificado_por_nombre or obj.autor_nombre, "fecha": obj.actualizada.isoformat()}, **extra}

    with transaction.atomic():
        if servidor_id is not None:
            actual = modelo.objects.select_for_update().filter(pk=servidor_id).first()
            if actual is None:
                # Se borró para siempre mientras el teléfono lo editaba: lo del teléfono se conserva como documento nuevo.
                return _copia(modelo, tipo, usuario, id_cliente, revision_base, None, aplicar_copia, resumen, "un documento eliminado")
        else:
            actual = modelo.objects.select_for_update().filter(cuenta_id=usuario.pk, id_cliente=id_cliente).first()
            if actual is None:                                            # borrador nuevo del teléfono
                nuevo = modelo(cuenta_id=usuario.pk, id_cliente=id_cliente)
                aplicar(nuevo)
                nuevo.save()
                documentos.registrar_version(nuevo, usuario)
                return {"resultado": "creada", **resumen(nuevo)}
            if revision_base is None:                                     # reintento tras perder la respuesta de la creación
                revision_base = 1
        if actual.eliminado:
            return _copia(modelo, tipo, usuario, id_cliente, revision_base, actual, aplicar_copia, resumen, f"«{documentos.referencia(actual)}» (en la papelera)")
        guardados = normalizar(actual.datos) if normalizar else actual.datos
        if ajustar is not None:
            nuevos = ajustar(actual, nuevos)
        if guardados == nuevos:
            return {"resultado": "sin_cambios", **resumen(actual)}
        otro = documentos.bloqueado_por_otro(actual, usuario, sesion)
        if otro == "Tú, en otra ventana" and not sesion:
            otro = ""                                                     # lo editado sin conexión en este teléfono, con la misma cuenta editando: se fusiona
        if otro:
            if sesion:                                                    # tomaron el control: sus cambios, a recuperación
                documentos.guardar_recuperacion(actual, usuario, nuevos, revision_base)
                return {"resultado": "recuperacion", "quien": otro, **resumen(actual)}
            return _copia(modelo, tipo, usuario, id_cliente, revision_base, actual, aplicar_copia, resumen, documentos.referencia(actual))
        if revision_base == actual.revision:
            aplicar(actual)
            actual.revision += 1
            actual.save()
            documentos.registrar_version(actual, usuario)
            return {"resultado": "actualizada", **resumen(actual)}
        base = documentos.datos_de_revision(tipo, actual.pk, revision_base)
        if base is not None and aplicar_datos is not None:
            try:
                base_n = normalizar(base) if normalizar else base
                fusion = fusionar(base_n, guardados, nuevos, claves) if base_n is not None and guardados is not None else None
            except ValueError:
                fusion = None
            if fusion is not None:
                aplicar_datos(actual, fusion)
                actual.revision += 1
                actual.save()
                documentos.registrar_version(actual, usuario, nota="Fusionada con cambios hechos sin conexión")
                return {"resultado": "fusionada", **resumen(actual), "datos": actual.datos}
        return _copia(modelo, tipo, usuario, id_cliente, revision_base, actual, aplicar_copia, resumen, documentos.referencia(actual))


def _copia(modelo, tipo, usuario, id_cliente, revision_base, actual, aplicar_copia, resumen, ref):
    """Copia en conflicto: lo del teléfono como documento aparte. La llave sale del borrador y de su base: reenviarlo no la duplica."""
    llave = uuid.uuid5(id_cliente, f"copia:{revision_base}")
    copia = modelo.objects.filter(cuenta_id=usuario.pk, id_cliente=llave).first()
    if copia is None:
        nombre = autoria.de_usuario(usuario)[0]
        copia = modelo(cuenta_id=usuario.pk, id_cliente=llave, copia_de=actual)
        aplicar_copia(copia)
        copia.conflicto = f"Copia en conflicto de {ref} ({nombre}, {timezone.localtime().strftime('%d/%m/%Y %H:%M')})"[:300]
        copia.save()
        documentos.registrar_version(copia, usuario, nota="Copia en conflicto")
        if actual is not None and actual.modificado_por_id and actual.modificado_por_id != usuario.pk:
            documentos.avisar(actual.modificado_por_id, tipo, actual.pk,
                              f"{nombre} editó «{documentos.referencia(actual)}» sin conexión al mismo tiempo que tú. Tu versión sigue vigente; "
                              f"la suya quedó como «{copia.conflicto}».")
    return {"resultado": "copia", **resumen(copia), "copia_de": str(actual.id) if actual is not None else None}

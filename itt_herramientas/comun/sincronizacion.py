"""Sincronización de borradores del teléfono (cotizaciones, reportes…): idempotente y sin pisar trabajo ajeno.

El teléfono manda `id_cliente` (llave de idempotencia), `servidor_id` (si ya lo conoce) y `revision_base` (la revisión del
servidor en que se basó). Si el servidor cambió desde esa revisión, la versión del teléfono se guarda como COPIA y el
original no se toca. Reenviar la misma petición (respuesta perdida) devuelve el mismo resultado sin duplicar.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from django.db import transaction

from . import autoria


def sincronizar(modelo, usuario, id_cliente: uuid.UUID, servidor_id: uuid.UUID | None, revision_base: int | None, nuevos: dict, *,
                aplicar: Callable, aplicar_copia: Callable, error: type[Exception], normalizar: Callable | None = None) -> dict:
    """`nuevos`: datos validados (se comparan con los guardados). `aplicar(obj)` los vuelca en el registro; `aplicar_copia(obj)`
    hace lo mismo marcando que es una copia por conflicto. `normalizar(datos_guardados)` (opcional) los lleva al esquema actual
    antes de compararlos. El registro necesita: cuenta_id, id_cliente, datos, revision, copia_de."""
    with transaction.atomic():
        propias = modelo.objects.select_for_update().filter(cuenta_id=usuario.pk)
        if servidor_id is not None:
            actual = propias.filter(pk=servidor_id).first()
            if actual is None:
                raise error("El documento ya no existe en el servidor.")
        else:
            actual = propias.filter(id_cliente=id_cliente).first()
            if actual is None:                                            # borrador nuevo del teléfono
                nuevo = modelo(cuenta_id=usuario.pk, id_cliente=id_cliente)
                aplicar(nuevo)
                nuevo.save()
                return {"resultado": "creada", "servidor_id": str(nuevo.id), "revision": nuevo.revision, "folio": getattr(nuevo, "folio", ""), "autor": autoria.fila(nuevo)}
            if revision_base is None:                                     # reintento tras perder la respuesta de la creación
                revision_base = 1
        resumen = lambda obj, **extra: {"servidor_id": str(obj.id), "revision": obj.revision, "folio": getattr(obj, "folio", ""), "autor": autoria.fila(obj), **extra}  # noqa: E731
        if (normalizar(actual.datos) if normalizar else actual.datos) == nuevos:
            return {"resultado": "sin_cambios", **resumen(actual)}
        if revision_base == actual.revision:
            aplicar(actual)
            actual.revision += 1
            actual.save()
            return {"resultado": "actualizada", **resumen(actual)}
        # Conflicto: la copia lleva una llave derivada del borrador y de la revisión base, así reenviarlo no la duplica.
        llave = uuid.uuid5(id_cliente, f"copia:{revision_base}")
        copia = propias.filter(id_cliente=llave).first()
        if copia is None:
            copia = modelo(cuenta_id=usuario.pk, id_cliente=llave, copia_de=actual)
            aplicar_copia(copia)
            copia.save()
        return {"resultado": "copia", **resumen(copia, copia_de=str(actual.id))}

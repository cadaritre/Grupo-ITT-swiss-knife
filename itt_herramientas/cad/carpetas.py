"""CAD: proyectos y carpetas de la biblioteca de planos (docs/cad.md, «Proyectos y carpetas»).

Reglas:
- Un proyecto es una carpeta de arriba (sin padre) y lleva el cliente. Las carpetas se anidan hasta `PROFUNDIDAD_MAX` niveles; el nombre es
  único entre hermanas (sin distinguir mayúsculas). Cada plano vive en un solo lugar (`Plano.carpeta`; vacía = la raíz de «Planos»).
- Como los planos, son de la empresa: una sola biblioteca para los dos perfiles corporativos.
- Crear, renombrar y mover sigue los roles de edición de CAD (`cad`, `admin`). Mover un plano que alguien edita está permitido: el bloqueo
  es del plano y lo sigue a donde vaya.
- Eliminar una carpeta la manda a la papelera con todo lo que tiene (mismo `lote_papelera`); restaurarla trae ese lote entero. Quien no
  administra solo elimina una carpeta si todos sus planos son suyos (o está vacía) y nadie más los edita, como con un plano.
- `Plano.proyecto` es el nombre del proyecto donde está el plano: se pone aquí al crear, mover y renombrar (una sola consulta por proyecto).
- El árbol entero se lee en dos consultas (`Arbol`): las carpetas y cuántos planos tiene cada una. Nada recorre la base carpeta por carpeta.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from ..comun import autoria
from ..comun.errores import ErrorUsuario
from ..models import CarpetaCad, Plano, VersionPlano
from . import servicio

#: Niveles de carpetas (un proyecto es el nivel 1). AutoCAD y Windows no ponen tope, pero una ruta de más de ~250 caracteres ya falla en
#: Windows al descomprimir el ZIP: 20 niveles de nombres normales caben.
PROFUNDIDAD_MAX = 20
#: Carpetas en toda la biblioteca (la prueba de rendimiento usa 300; 5000 es un tope de cordura, no de uso).
CARPETAS_MAX = 5000
NOMBRE_MAX = 150
CLIENTE_MAX = 160
#: Rutas de carpetas por petición al subir una carpeta de la computadora (la página las manda en tandas).
RUTAS_MAX = 500
SEPARADOR = " › "


class SinPermiso(servicio.SinPermiso):
    pass


def nombre_limpio(texto: str, por_omision: str = "Carpeta nueva") -> str:
    """Nombre de una carpeta: sin caracteres de control ni los que Windows no admite (así el ZIP y las rutas de las referencias valen)."""
    texto = unicodedata.normalize("NFC", str(texto or ""))
    texto = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', " ", texto)
    texto = " ".join(texto.split()).strip(" .")
    return texto[:NOMBRE_MAX].strip(" .") or por_omision


def cliente_limpio(texto: str) -> str:
    return " ".join(str(texto or "").split())[:CLIENTE_MAX]


def _clave(texto: str) -> str:
    return unicodedata.normalize("NFC", str(texto or "")).casefold()


# -- El árbol en memoria ---------------------------------------------------------------------------------------------------------

@dataclass
class Nodo:
    id: str
    padre: str | None
    nombre: str
    cliente: str
    actualizada: object = None
    hijas: list = field(default_factory=list)
    planos: int = 0          # directos, sin los de la papelera
    total: int = 0           # con los de todas sus carpetas


class Arbol:
    """Las carpetas activas de la biblioteca y cuántos planos tiene cada una, en dos consultas."""

    def __init__(self, conteos: bool = True):
        self.nodos: dict[str, Nodo] = {}
        for pk, padre, nombre, cliente, actualizada in (CarpetaCad.objects.filter(eliminada__isnull=True)
                                                         .values_list("pk", "padre_id", "nombre", "cliente", "actualizada")):
            self.nodos[str(pk)] = Nodo(str(pk), str(padre) if padre else None, nombre, cliente, actualizada)
        self.raices: list[Nodo] = []
        for n in self.nodos.values():
            padre = self.nodos.get(n.padre) if n.padre else None
            (padre.hijas if padre else self.raices).append(n)
        for lista in [self.raices, *(n.hijas for n in self.nodos.values())]:
            lista.sort(key=lambda x: x.nombre.casefold())
        self.en_raiz = 0
        if conteos:
            for carpeta, n in Plano.objects.filter(eliminado__isnull=True).values_list("carpeta_id").annotate(n=Count("pk")).values_list("carpeta_id", "n"):
                if carpeta is None:
                    self.en_raiz = n
                elif str(carpeta) in self.nodos:
                    self.nodos[str(carpeta)].planos = n
            # totales de abajo hacia arriba, sin recursión (un árbol muy hondo no llena la pila)
            for n in sorted(self.nodos.values(), key=lambda x: -self.profundidad(x.id)):
                n.total += n.planos
                if n.padre in self.nodos:
                    self.nodos[n.padre].total += n.total
        self.total = self.en_raiz + sum(n.total for n in self.raices)

    def __contains__(self, pk) -> bool:
        return pk is not None and str(pk) in self.nodos

    def get(self, pk) -> Nodo | None:
        return self.nodos.get(str(pk)) if pk else None

    def ruta(self, pk) -> list[Nodo]:
        """Del proyecto a la carpeta `pk` (vacía para la raíz)."""
        salida, n, vistos = [], self.get(pk), set()
        while n is not None and n.id not in vistos:
            vistos.add(n.id)
            salida.append(n)
            n = self.get(n.padre)
        return salida[::-1]

    def profundidad(self, pk) -> int:
        return len(self.ruta(pk))

    def altura(self, pk) -> int:
        """Niveles de la carpeta `pk` hacia abajo (1 si no tiene carpetas)."""
        mejor, pila = 0, [(self.get(pk), 1)]
        while pila:
            n, h = pila.pop()
            if n is None:
                continue
            mejor = max(mejor, h)
            pila.extend((x, h + 1) for x in n.hijas)
        return mejor

    def ubicacion(self, pk, raiz: str = "") -> str:
        partes = [n.nombre for n in self.ruta(pk)]
        return SEPARADOR.join(partes) if partes else raiz

    def proyecto(self, pk) -> Nodo | None:
        r = self.ruta(pk)
        return r[0] if r else None

    def debajo(self, pk) -> list[str]:
        """Ids de la carpeta `pk` y de todas las que tiene."""
        salida, pila = [], [self.get(pk)]
        while pila:
            n = pila.pop()
            if n is None:
                continue
            salida.append(n.id)
            pila.extend(n.hijas)
        return salida

    def hija_llamada(self, padre, nombre: str) -> Nodo | None:
        k = _clave(nombre)
        hermanas = self.get(padre).hijas if padre else self.raices
        return next((h for h in hermanas if _clave(h.nombre) == k), None)

    def como_lista(self) -> list[dict]:
        """Plano para la página (en orden: cada carpeta antes que sus hijas, hermanas por nombre)."""
        salida, pila = [], [(n, 1) for n in reversed(self.raices)]
        while pila:
            n, nivel = pila.pop()
            salida.append({"id": n.id, "padre": n.padre, "nombre": n.nombre, "cliente": n.cliente, "nivel": nivel, "planos": n.planos,
                           "total": n.total, "hijas": len(n.hijas)})
            pila.extend((h, nivel + 1) for h in reversed(n.hijas))
        return salida


# -- Lo que la persona puede hacer ------------------------------------------------------------------------------------------------

def _exigir_edicion(usuario) -> None:
    if not servicio.puede_editar(usuario):
        raise SinPermiso("Tu cuenta solo puede ver los planos.")


def carpeta_activa(pk) -> CarpetaCad | None:
    if not pk:
        return None
    try:
        uuid.UUID(str(pk))
    except ValueError:
        return None
    return CarpetaCad.objects.filter(pk=pk, eliminada__isnull=True).first()


def _destino(pk) -> CarpetaCad | None:
    """La carpeta destino (None = la raíz). Una que no existe o está en la papelera es un error claro, nunca la raíz por accidente."""
    if pk in (None, "", "raiz"):
        return None
    c = carpeta_activa(pk)
    if c is None:
        raise ErrorUsuario("Esa carpeta ya no existe. Recarga la página.")
    return c


def _sin_repetir(padre, nombre: str, excepto=None) -> None:
    q = CarpetaCad.objects.filter(eliminada__isnull=True, padre=padre).annotate(l=Lower("nombre")).filter(l=nombre.lower())
    if excepto is not None:
        q = q.exclude(pk=excepto.pk)
    if q.exists():
        donde = f"en «{padre.nombre}»" if padre else "en Planos"
        raise ErrorUsuario(f"Ya hay {'una carpeta' if padre else 'un proyecto'} «{nombre}» {donde}.")


def crear(usuario, nombre: str, padre=None, cliente: str = "") -> CarpetaCad:
    """Proyecto (sin `padre`) o carpeta nueva."""
    _exigir_edicion(usuario)
    padre = _destino(padre.pk if isinstance(padre, CarpetaCad) else padre)
    nombre = nombre_limpio(nombre, "Proyecto nuevo" if padre is None else "Carpeta nueva")
    with transaction.atomic():
        if CarpetaCad.objects.filter(eliminada__isnull=True).count() >= CARPETAS_MAX:
            raise ErrorUsuario(f"La biblioteca ya tiene {CARPETAS_MAX} carpetas, el máximo. Elimina las que ya no uses.")
        if padre is not None and Arbol(conteos=False).profundidad(padre.pk) >= PROFUNDIDAD_MAX:
            raise ErrorUsuario(f"Las carpetas llegan hasta {PROFUNDIDAD_MAX} niveles. Crea esta en un nivel de arriba.")
        _sin_repetir(padre, nombre)
        try:
            with transaction.atomic():
                c = CarpetaCad(cuenta_id=usuario.pk, padre=padre, nombre=nombre, cliente=cliente_limpio(cliente) if padre is None else "")
                c.save()
        except IntegrityError:
            raise ErrorUsuario(f"Ya hay una carpeta «{nombre}» ahí.") from None
        _tocar(padre)
    return c


def _tocar(c: CarpetaCad | None) -> None:
    if c is not None:
        CarpetaCad.objects.filter(pk=c.pk).update(actualizada=timezone.now())


def asegurar_rutas(usuario, padre, rutas: list[str]) -> dict[str, str]:
    """Para subir una carpeta de la computadora: cada ruta relativa («Obra/Base») como carpetas dentro de `padre`, reusando las que ya
    existen con ese nombre (subir otra vez la misma carpeta no la duplica). Devuelve ruta → id de su carpeta."""
    _exigir_edicion(usuario)
    if not isinstance(rutas, list) or len(rutas) > RUTAS_MAX or not all(isinstance(r, str) and len(r) <= 2000 for r in rutas):
        raise ErrorUsuario("La carpeta tiene demasiadas subcarpetas para una sola vez.")
    padre = _destino(padre)
    arbol = Arbol(conteos=False)
    base = arbol.profundidad(padre.pk) if padre else 0
    salida: dict[str, str] = {}
    creadas = 0
    with transaction.atomic():
        existentes = CarpetaCad.objects.filter(eliminada__isnull=True).count()
        for ruta in sorted(set(rutas), key=lambda r: r.count("/")):
            partes = [nombre_limpio(p) for p in ruta.replace("\\", "/").split("/") if p.strip() and p.strip() not in (".", "..")]
            if not partes:
                continue
            if base + len(partes) > PROFUNDIDAD_MAX:
                raise ErrorUsuario(f"«{ruta}» tiene más de {PROFUNDIDAD_MAX} niveles de carpetas.")
            actual = padre.pk if padre else None
            for nombre in partes:
                n = arbol.hija_llamada(actual, nombre)
                if n is None:
                    if existentes + creadas >= CARPETAS_MAX:
                        raise ErrorUsuario(f"La biblioteca llegaría a más de {CARPETAS_MAX} carpetas.")
                    try:
                        with transaction.atomic():
                            c = CarpetaCad(cuenta_id=usuario.pk, padre_id=actual, nombre=nombre)
                            c.save()
                    except IntegrityError:                         # otra subida la creó en este momento: se usa esa
                        c = (CarpetaCad.objects.filter(eliminada__isnull=True, padre_id=actual).annotate(l=Lower("nombre"))
                             .filter(l=nombre.lower()).first())
                        if c is None:
                            raise
                    else:
                        creadas += 1
                    n = Nodo(str(c.pk), str(actual) if actual else None, c.nombre, "")
                    arbol.nodos[n.id] = n
                    (arbol.get(actual).hijas if actual else arbol.raices).append(n)
                actual = n.id
            salida[ruta] = actual
        if creadas:
            _tocar(padre)
    return salida


def renombrar(usuario, carpeta: CarpetaCad, nombre: str, cliente: str | None = None) -> CarpetaCad:
    _exigir_edicion(usuario)
    nombre = nombre_limpio(nombre, carpeta.nombre)
    with transaction.atomic():
        c = CarpetaCad.objects.select_for_update().filter(pk=carpeta.pk, eliminada__isnull=True).first()
        if c is None:
            raise ErrorUsuario("Esa carpeta ya no existe. Recarga la página.")
        if nombre.lower() != c.nombre.lower() or nombre != c.nombre:
            _sin_repetir(c.padre, nombre, excepto=c)
        cambia_nombre = nombre != c.nombre
        c.nombre = nombre
        if cliente is not None and c.padre_id is None:
            c.cliente = cliente_limpio(cliente)
        c.actualizada = timezone.now()
        try:
            with transaction.atomic():
                c.save(update_fields=["nombre", "cliente", "actualizada"])
        except IntegrityError:
            raise ErrorUsuario(f"Ya hay una carpeta «{nombre}» ahí.") from None
        if cambia_nombre and c.padre_id is None:
            sincronizar_proyecto([str(c.pk)])
    return c


def sincronizar_proyecto(carpetas: list[str] | None = None, arbol: Arbol | None = None) -> None:
    """`Plano.proyecto` = el nombre del proyecto de su carpeta, para los planos de `carpetas` y lo que tienen (todas si es None)."""
    arbol = arbol or Arbol(conteos=False)
    ids = set()
    for pk in (carpetas if carpetas is not None else [n.id for n in arbol.raices]):
        ids.update(arbol.debajo(pk))
    por_proyecto = defaultdict(list)
    for pk in ids:
        p = arbol.proyecto(pk)
        por_proyecto[p.nombre[:servicio.PROYECTO_MAX] if p else ""].append(pk)
    for nombre, lista in por_proyecto.items():
        Plano.objects.filter(carpeta_id__in=lista).exclude(proyecto=nombre).update(proyecto=nombre)


def mover(usuario, planos: list = (), carpetas: list = (), destino=None) -> dict:
    """Mueve planos y carpetas a `destino` (None = la raíz de «Planos»). Un plano en edición se mueve igual: su bloqueo va con él."""
    _exigir_edicion(usuario)
    planos, carpetas = [str(x) for x in planos or ()], [str(x) for x in carpetas or ()]
    if not planos and not carpetas:
        raise ErrorUsuario("Elige qué mover.")
    if len(planos) + len(carpetas) > 1000:
        raise ErrorUsuario("Mueve hasta 1000 elementos a la vez.")
    with transaction.atomic():
        destino_c = _destino(destino)
        destino_id = str(destino_c.pk) if destino_c else None
        arbol = Arbol(conteos=False)
        movidas = list(CarpetaCad.objects.select_for_update().filter(pk__in=carpetas, eliminada__isnull=True))
        if len(movidas) != len(set(carpetas)):
            raise ErrorUsuario("Alguna carpeta ya no existe. Recarga la página.")
        nombres_destino = {_clave(h.nombre): h.id for h in (arbol.get(destino_id).hijas if destino_id else arbol.raices)}
        vistos = set()
        for c in movidas:
            pk = str(c.pk)
            if destino_id and destino_id in arbol.debajo(pk):
                raise ErrorUsuario(f"«{c.nombre}» no se puede mover dentro de sí misma.")
            if str(c.padre_id or "") == str(destino_id or ""):
                continue
            k = _clave(c.nombre)
            if (k in nombres_destino and nombres_destino[k] != pk) or k in vistos:
                donde = f"«{destino_c.nombre}»" if destino_c else "Planos"
                raise ErrorUsuario(f"Ya hay una carpeta «{c.nombre}» en {donde}. Cámbiale el nombre antes de moverla.")
            vistos.add(k)
            if (arbol.profundidad(destino_id) if destino_id else 0) + arbol.altura(pk) > PROFUNDIDAD_MAX:
                raise ErrorUsuario(f"Con «{c.nombre}» ahí habría más de {PROFUNDIDAD_MAX} niveles de carpetas.")
        ahora = timezone.now()
        n_carpetas = 0
        for c in movidas:
            if str(c.padre_id or "") == str(destino_id or ""):
                continue
            CarpetaCad.objects.filter(pk=c.pk).update(padre_id=destino_id, actualizada=ahora)
            n_carpetas += 1
        n_planos = 0
        if planos:
            consulta = Plano.objects.filter(pk__in=planos, eliminado__isnull=True)
            if consulta.count() != len(set(planos)):
                raise ErrorUsuario("Algún plano ya no está en la biblioteca. Recarga la página.")
            n_planos = consulta.exclude(carpeta_id=destino_id).update(carpeta_id=destino_id)
        _tocar(destino_c)
        arbol = Arbol(conteos=False)
        proyecto = arbol.proyecto(destino_id)
        nombre = proyecto.nombre[:servicio.PROYECTO_MAX] if proyecto else ""
        if planos:
            Plano.objects.filter(pk__in=planos).exclude(proyecto=nombre).update(proyecto=nombre)
        if n_carpetas:
            sincronizar_proyecto([str(c.pk) for c in movidas], arbol)
    return {"planos": n_planos, "carpetas": n_carpetas}


# -- Papelera ------------------------------------------------------------------------------------------------------------------------

def _planos_debajo(ids: list[str]):
    return Plano.objects.filter(carpeta_id__in=ids, eliminado__isnull=True)


def puede_eliminar(usuario, carpeta: CarpetaCad, arbol: Arbol | None = None) -> bool:
    if servicio.es_admin(usuario):
        return True
    if not servicio.puede_editar(usuario):
        return False
    arbol = arbol or Arbol(conteos=False)
    return not _planos_debajo(arbol.debajo(carpeta.pk)).exclude(cuenta_id=usuario.pk).exists()


def eliminar(usuario, carpeta: CarpetaCad) -> dict:
    """La carpeta y todo lo que tiene van a la papelera juntos (un lote). Devuelve cuántas carpetas y planos se fueron."""
    _exigir_edicion(usuario)
    with transaction.atomic():
        c = CarpetaCad.objects.select_for_update().filter(pk=carpeta.pk, eliminada__isnull=True).first()
        if c is None:
            raise ErrorUsuario("Esa carpeta ya no existe. Recarga la página.")
        arbol = Arbol(conteos=False)
        ids = arbol.debajo(c.pk)
        planos = list(_planos_debajo(ids).select_for_update())
        admin = servicio.es_admin(usuario)
        if not admin and any(p.cuenta_id != usuario.pk for p in planos):
            raise SinPermiso("Esta carpeta tiene planos de otras personas: solo quien administra las herramientas puede eliminarla.")
        ahora = timezone.now()
        for p in planos:
            otro = servicio._bloqueo_de_otro(p, usuario, "", ahora)
            if otro and otro != servicio.OTRA_VENTANA and not admin:
                raise ErrorUsuario(f"{otro} está editando «{p.nombre}». Espera a que termine para eliminar la carpeta.")
        lote = uuid.uuid4()
        nombre = autoria.de_usuario(usuario)[0]
        CarpetaCad.objects.filter(pk__in=ids, eliminada__isnull=True).update(eliminada=ahora, eliminada_por_id=usuario.pk, eliminada_por_nombre=nombre,
                                                                             lote_papelera=lote)
        Plano.objects.filter(pk__in=[p.pk for p in planos]).update(
            eliminado=ahora, eliminado_por_id=usuario.pk, eliminado_por_nombre=nombre, lote_papelera=lote,
            bloqueo_cuenta_id=None, bloqueo_nombre="", bloqueo_sesion="", bloqueo_latido=None)
        _tocar(c.padre)
    return {"carpetas": len(ids), "planos": len(planos), "lote": str(lote)}


def puede_restaurar(usuario, carpeta: CarpetaCad) -> bool:
    return servicio.es_admin(usuario) or (servicio.puede_editar(usuario) and usuario.pk in (carpeta.cuenta_id, carpeta.eliminada_por_id))


def _nombre_libre(padre_id, nombre: str) -> str:
    usados = {x.lower() for x in CarpetaCad.objects.filter(eliminada__isnull=True, padre_id=padre_id).values_list("nombre", flat=True)}
    candidato, n = nombre, 2
    while candidato.lower() in usados:
        candidato = f"{nombre[:NOMBRE_MAX - 6]} ({n})"
        n += 1
    return candidato


def restaurar(usuario, carpeta: CarpetaCad) -> dict:
    """Vuelve la carpeta con todo lo que se eliminó con ella. Si su carpeta de arriba ya no está, vuelve a la raíz; si ahí ya hay una
    con su nombre, vuelve como «Nombre (2)». Lo que se había eliminado aparte antes sigue en la papelera."""
    if not puede_restaurar(usuario, carpeta):
        raise SinPermiso("Solo quien la eliminó, quien la creó o quien administra las herramientas puede restaurarla.")
    with transaction.atomic():
        c = CarpetaCad.objects.select_for_update().filter(pk=carpeta.pk, eliminada__isnull=False).first()
        if c is None:
            raise ErrorUsuario("Esa carpeta ya no está en la papelera.")
        lote = c.lote_papelera
        padre_id = c.padre_id if c.padre_id and CarpetaCad.objects.filter(pk=c.padre_id, eliminada__isnull=True).exists() else None
        a_raiz = c.padre_id is not None and padre_id is None
        nombre = _nombre_libre(padre_id, c.nombre)
        # Primero su lugar y su nombre (aún en la papelera, sin chocar con la que ya se llama así), después vuelve todo el lote.
        CarpetaCad.objects.filter(pk=c.pk).update(padre_id=padre_id, nombre=nombre, actualizada=timezone.now())
        carpetas = CarpetaCad.objects.filter(lote_papelera=lote, eliminada__isnull=False) if lote else CarpetaCad.objects.filter(pk=c.pk)
        n_carpetas = carpetas.update(eliminada=None, eliminada_por_id=None, eliminada_por_nombre="", lote_papelera=None)
        n_planos = 0
        if lote:
            n_planos = Plano.objects.filter(lote_papelera=lote, eliminado__isnull=False).update(
                eliminado=None, eliminado_por_id=None, eliminado_por_nombre="", lote_papelera=None)
        if a_raiz or nombre != c.nombre:
            sincronizar_proyecto([str(c.pk)])
    return {"carpetas": n_carpetas, "planos": n_planos, "nombre": nombre, "a_raiz": a_raiz}


def papelera(usuario) -> list[CarpetaCad]:
    """Las carpetas eliminadas que encabezan su lote (lo de adentro va con ellas), de la más reciente a la más vieja, con cuántos planos
    y carpetas traen. Quien no administra ve las que eliminó o creó, como con los planos."""
    consulta = CarpetaCad.objects.filter(eliminada__isnull=False).exclude(padre__lote_papelera=F("lote_papelera")).order_by("-eliminada")
    if not servicio.es_admin(usuario):
        consulta = consulta.filter(Q(cuenta_id=usuario.pk) | Q(eliminada_por_id=usuario.pk))
    lista = list(consulta[:500])
    lotes = [c.lote_papelera for c in lista if c.lote_papelera]
    planos = dict(Plano.objects.filter(lote_papelera__in=lotes).values_list("lote_papelera").annotate(n=Count("pk")).values_list("lote_papelera", "n"))
    carpetas = dict(CarpetaCad.objects.filter(lote_papelera__in=lotes).values_list("lote_papelera").annotate(n=Count("pk")).values_list("lote_papelera", "n"))
    for c in lista:
        c.n_planos = planos.get(c.lote_papelera, 0)
        c.n_carpetas = max(0, carpetas.get(c.lote_papelera, 1) - 1)
        c.vence = c.eliminada + timedelta(days=servicio.PAPELERA_DIAS)
    return lista


def destruir(carpeta: CarpetaCad) -> int:
    """Borra para siempre la carpeta, lo que se eliminó con ella y lo que quede debajo en la papelera. Un plano eliminado aparte que
    estaba dentro pasa a la raíz de la papelera (sigue ahí hasta que venza). Devuelve cuántos planos se borraron."""
    ids, pila = [], [carpeta.pk]
    while pila:                                     # debajo, incluidas las eliminadas aparte (todas van a borrarse)
        pk = pila.pop()
        ids.append(pk)
        pila.extend(CarpetaCad.objects.filter(padre_id=pk).values_list("pk", flat=True))
    n = 0
    for p in Plano.objects.filter(carpeta_id__in=ids):
        if p.eliminado is not None and p.lote_papelera is not None:     # se eliminó con esta carpeta (o con una de las suyas)
            servicio.destruir(p)
            n += 1
        else:                                        # eliminado aparte (su plazo sigue) o, si la base quedó rara, vivo: nunca se borra aquí
            Plano.objects.filter(pk=p.pk).update(carpeta=None, proyecto="")
    for pk in reversed(ids):
        CarpetaCad.objects.filter(pk=pk).delete()
    return n


def limpiar(ahora=None) -> int:
    """`tv_limpiar`: las carpetas que llevan 30 días en la papelera se borran con lo suyo."""
    ahora = ahora or timezone.now()
    n = 0
    for c in CarpetaCad.objects.filter(eliminada__lt=ahora - timedelta(days=servicio.PAPELERA_DIAS)).exclude(padre__lote_papelera=F("lote_papelera")):
        if CarpetaCad.objects.filter(pk=c.pk).exists():
            destruir(c)
            n += 1
    return n


def vaciar_papelera() -> int:
    n = 0
    for c in CarpetaCad.objects.filter(eliminada__isnull=False).exclude(padre__lote_papelera=F("lote_papelera")):
        if CarpetaCad.objects.filter(pk=c.pk).exists():
            destruir(c)
            n += 1
    return n


# -- Para crear planos con la etiqueta de antes («Proyecto o cliente») ---------------------------------------------------------

def proyecto_llamado(nombre: str, usuario=None) -> CarpetaCad | None:
    """El proyecto con ese nombre (sin distinguir mayúsculas), o uno nuevo. Lo usan quienes crean planos con un proyecto escrito (el
    croquis con su cliente, «Guardar como plantilla» en «Plantillas»). Vacío: la raíz."""
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        return None
    nombre = nombre_limpio(nombre, "Proyecto")
    c = CarpetaCad.objects.filter(eliminada__isnull=True, padre__isnull=True).annotate(l=Lower("nombre")).filter(l=nombre.lower()).first()
    if c is not None:
        return c
    try:
        with transaction.atomic():
            c = CarpetaCad(cuenta_id=getattr(usuario, "pk", None), nombre=nombre)
            c.save()
            return c
    except IntegrityError:
        return CarpetaCad.objects.filter(eliminada__isnull=True, padre__isnull=True).annotate(l=Lower("nombre")).filter(l=nombre.lower()).first()


# -- Lo que hay debajo de una carpeta (ZIP y «Disponible sin conexión») -----------------------------------------------------------

def planos_debajo(carpeta: CarpetaCad | None, arbol: Arbol | None = None) -> list[dict]:
    """Los planos de la carpeta y de todas las suyas, con su ruta relativa a ella y su versión vigente (tres consultas)."""
    arbol = arbol or Arbol(conteos=False)
    ids = arbol.debajo(carpeta.pk) if carpeta else None
    consulta = Plano.objects.filter(eliminado__isnull=True)
    consulta = consulta.filter(carpeta_id__in=ids) if ids is not None else consulta
    filas = list(consulta.values("pk", "nombre", "formato", "bytes", "revision", "carpeta_id", "proyecto", "modificado_por_nombre").order_by("nombre", "pk"))
    versiones = {(v["plano_id"], v["numero"]): v for v in VersionPlano.objects.filter(plano_id__in=[f["pk"] for f in filas], numero=F("plano__revision"))
                 .values("plano_id", "numero", "sha256", "ruta", "sin_archivo")}
    base = len(arbol.ruta(carpeta.pk)) if carpeta else 0
    salida = []
    for f in filas:
        v = versiones.get((f["pk"], f["revision"]))
        partes = [n.nombre for n in arbol.ruta(f["carpeta_id"])][base:] if f["carpeta_id"] else []
        salida.append({**f, "id": str(f["pk"]), "carpeta": str(f["carpeta_id"]) if f["carpeta_id"] else None, "ruta": "/".join(partes),
                       "sha256": v["sha256"] if v else "", "archivo_ruta": v["ruta"] if v and not v["sin_archivo"] else ""})
    return salida


# -- Referencias externas desde la biblioteca --------------------------------------------------------------------------------------

_EXT = re.compile(r"\.(dwg|dxf)$", re.I)


def _partes_de_ruta(ruta: str) -> tuple[list[str], str, str | None, bool]:
    """«..\\Base\\base.dwg» → (["..", "Base"], "base", "dwg", relativa)."""
    texto = unicodedata.normalize("NFC", str(ruta or "")).strip().replace("\\", "/")
    absoluta = bool(re.match(r"^[A-Za-z]:/", texto)) or texto.startswith("/")
    texto = re.sub(r"^[A-Za-z]:", "", texto)
    partes = [p for p in texto.split("/") if p and p != "."]
    if not partes:
        return [], "", None, not absoluta
    archivo = partes[-1]
    m = _EXT.search(archivo)
    formato = m.group(1).lower() if m else None
    return partes[:-1], servicio.nombre_limpio(archivo, ""), formato, not absoluta


def resolver_referencias(plano: Plano, rutas: list[str], bases: list[str] = ()) -> list[dict]:
    """Dónde está, en la biblioteca, cada dibujo que el plano usa como referencia externa (xref), como lo buscaría AutoCAD dentro del
    proyecto:
      1. la ruta guardada, relativa a la carpeta del plano («..\\Base\\base.dwg»; una absoluta, por sus últimas carpetas dentro del
         proyecto) y, para las anidadas, relativa a las carpetas de las referencias ya encontradas (`bases`);
      2. la misma carpeta del plano, por nombre;
      3. todo el proyecto, por nombre (si hay varios, el de ruta más corta y se dice cuántos hay).
    Solo planos vivos, del mismo formato que dice la ruta, distintos del plano. Sin proyecto (en la raíz) solo busca en la raíz.
    """
    arbol = Arbol(conteos=False)
    casa = str(plano.carpeta_id) if plano.carpeta_id and str(plano.carpeta_id) in arbol else None
    proyecto = arbol.proyecto(casa)
    dentro = set(arbol.debajo(proyecto.id)) if proyecto else set()
    pedidas = []
    for ruta in rutas[:200]:
        dirs, nombre, formato, relativa = _partes_de_ruta(ruta)
        pedidas.append((ruta, dirs, nombre, formato, relativa))
    nombres = {n.casefold() for _, _, n, _, _ in pedidas if n}
    candidatos = defaultdict(list)                          # nombre → [planos]
    if nombres:
        consulta = Plano.objects.filter(eliminado__isnull=True).exclude(pk=plano.pk).annotate(l=Lower("nombre")).filter(l__in={n.lower() for n in nombres})
        consulta = consulta.filter(carpeta_id__in=dentro) if proyecto else consulta.filter(carpeta__isnull=True)
        for p in consulta.only("pk", "nombre", "formato", "revision", "bytes", "carpeta_id"):
            candidatos[p.nombre.casefold()].append(p)
    versiones = {}
    todos = [p for lista in candidatos.values() for p in lista]
    if todos:
        for v in VersionPlano.objects.filter(plano_id__in=[p.pk for p in todos], numero=F("plano__revision")):
            versiones[v.plano_id] = v
    bases = [str(b) for b in (bases or ()) if str(b) in dentro]

    def carpeta_de(p) -> str | None:
        return str(p.carpeta_id) if p.carpeta_id else None

    def en(carpeta, lista):
        return [p for p in lista if carpeta_de(p) == carpeta]

    def andar(desde, dirs):
        actual = desde
        for d in dirs:
            if d == "..":
                if actual is None:
                    return False, None
                actual = arbol.get(actual).padre
                if proyecto and actual is None:          # salir del proyecto: AutoCAD lo buscaría en el disco, aquí no hay nada
                    return False, None
                continue
            n = arbol.hija_llamada(actual, nombre_limpio(d))
            if n is None:
                return False, None
            actual = n.id
        return True, actual

    salida = []
    from django.urls import reverse
    for ruta, dirs, nombre, formato, relativa in pedidas:
        lista = [p for p in candidatos.get(nombre.casefold(), []) if formato is None or p.formato == formato]
        lista = [p for p in lista if servicio.archivo_disponible(versiones.get(p.pk))]
        elegido, como, varias = None, "", 0
        if lista and dirs and relativa:
            for desde in [casa, *bases]:
                ok, destino = andar(desde, dirs)
                if ok and en(destino, lista):
                    elegido, como = en(destino, lista)[0], "ruta"
                    break
        if elegido is None and lista and dirs and not relativa:
            # Absoluta (C:\Obra\Base\base.dwg): la carpeta del proyecto cuyas últimas carpetas coinciden con las de la ruta.
            mejor = 0
            for p in lista:
                nombres_carpeta = [_clave(n.nombre) for n in arbol.ruta(carpeta_de(p))]
                k = 0
                while k < min(len(dirs), len(nombres_carpeta)) and _clave(nombre_limpio(dirs[-1 - k])) == nombres_carpeta[-1 - k]:
                    k += 1
                if k > mejor:
                    mejor, elegido, como = k, p, "ruta"
        if elegido is None and lista:
            aqui = en(casa, lista)
            if aqui:
                elegido, como = aqui[0], "carpeta"
        if elegido is None and lista:
            lista.sort(key=lambda p: (len(arbol.ruta(carpeta_de(p))), arbol.ubicacion(carpeta_de(p)).casefold(), str(p.pk)))
            elegido, como, varias = lista[0], "nombre", len(lista)
        if elegido is None:
            salida.append({"ruta": ruta, "id": None})
            continue
        v = versiones[elegido.pk]
        salida.append({"ruta": ruta, "id": str(elegido.pk), "nombre": elegido.nombre, "formato": elegido.formato, "revision": elegido.revision,
                       "bytes": v.bytes, "sha256": v.sha256, "carpeta": carpeta_de(elegido),
                       "ubicacion": arbol.ubicacion(carpeta_de(elegido), "Planos"), "como": como, "varias": varias if varias > 1 else 0,
                       "archivo": reverse("herramientas:cad_archivo", args=[elegido.pk])})
    return salida


# -- «Descargar en ZIP» de una carpeta ------------------------------------------------------------------------------------------

#: Planos en el ZIP de una carpeta. El tope de bytes es el de los ZIP de planos (`zips.MAX_BYTES_PLANOS`, 1 GB).
ZIP_MAX_PLANOS = 2000


def resumen_zip(carpeta: CarpetaCad) -> tuple[int, int]:
    """Cuántos planos y cuántos bytes llevaría el ZIP de la carpeta (la versión vigente de cada uno)."""
    filas = [f for f in planos_debajo(carpeta) if f["archivo_ruta"]]
    return len(filas), sum(f["bytes"] for f in filas)


def armar_zip(token: str, cuenta_id: int, carpeta_id: str) -> dict:
    """El ZIP de la carpeta con sus subcarpetas, cada plano en su versión vigente (lo llama la tarea). Va en la reserva de CAD con los demás
    ZIP de planos: uno por cuenta a la vez, se borra a las 2 horas y solo lo baja quien lo pidió."""
    import zipfile

    from tvplataforma.tareas import progreso

    from ..comun import zips

    c = carpeta_activa(carpeta_id)
    if c is None:
        raise ErrorUsuario("Esa carpeta ya no existe.")
    filas = [f for f in planos_debajo(c) if f["archivo_ruta"]]
    if not filas:
        raise ErrorUsuario("Esta carpeta no tiene planos para descargar.")
    destino = zips.ruta(cuenta_id, token, "planos")
    for viejo in destino.parent.glob(f"{int(cuenta_id)}-*-planos.*"):
        if viejo.stem != destino.stem:
            viejo.unlink(missing_ok=True)
    temporal = destino.with_suffix(".tmp")
    usados: set[str] = set()
    raiz = servicio.raiz()
    with zipfile.ZipFile(temporal, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for i, f in enumerate(filas):
            progreso(0.02 + 0.95 * i / len(filas), f"Plano {i + 1} de {len(filas)}")
            origen = raiz / f["archivo_ruta"]
            if not origen.is_file():
                continue
            carpeta = "/".join([c.nombre, *([f["ruta"]] if f["ruta"] else [])])
            nombre = f"{carpeta}/{f['nombre']}.{f['formato']}"
            base, n = nombre, 2
            while nombre.casefold() in usados:
                nombre = f"{base[: -len(f['formato']) - 1]} ({n}).{f['formato']}"
                n += 1
            usados.add(nombre.casefold())
            # Un DWG ya viene comprimido: se guarda tal cual; un DXF es texto y baja a una décima parte.
            zf.write(origen, nombre, compress_type=zipfile.ZIP_STORED if f["formato"] == "dwg" else zipfile.ZIP_DEFLATED)
    temporal.replace(destino)
    destino.with_suffix(".nombre").write_text(f"{c.nombre}.zip", encoding="utf-8")
    progreso(1.0, "Listo")
    return {"mensaje": f"ZIP listo: {len(usados)} plano{'s' if len(usados) != 1 else ''} de «{c.nombre}».", "incluidos": len(usados)}

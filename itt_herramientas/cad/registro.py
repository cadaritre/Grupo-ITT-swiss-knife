"""CAD · registro de diagnóstico de «Sin guardar» (cabecera `X-TV-Registro` del borrador de recuperación).

Cuando la página pasa por primera vez a «Sin guardar» en una sesión, guarda qué le pidió al editor justo antes (solo nombres de
operaciones, tiempos, si se aceptaron y las revisiones; nada del dibujo) y lo manda con el siguiente borrador. Aquí se valida y se
recorta para el registro del servidor (journal, nivel INFO): nunca puede romper la subida del borrador ni llevar contenido del plano.

Formato que manda la página (JSON compacto, ASCII): {"v": 1, "m": "t"|"c", "r": [[ms, op, ok, revision], …], "s": [[ms, revision, base], …]}
`m`: modo táctil o completo; `r`: peticiones que cambian algo; `s`: pasos a «Sin guardar».
"""
from __future__ import annotations

import json
import re

LIMITE = 2000                       # caracteres del registro que se escribe
ENTRADA_MAX = 8000                  # lo más que se mira de la cabecera
FILAS_MAX = 40
_OP = re.compile(r"^[a-z_-]{1,24}$")


def _entero(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and -(2 ** 53) < v < 2 ** 53 else None


def limpio(crudo: str | None) -> str | None:
    """El registro listo para escribir, o None si no vino. Lo que no se entiende queda como {"malformado": motivo}."""
    if not crudo:
        return None
    crudo = crudo[:ENTRADA_MAX]
    if any(not (32 <= ord(c) < 127) for c in crudo):
        return json.dumps({"malformado": "no_ascii"})
    try:
        d = json.loads(crudo)
    except ValueError:
        return json.dumps({"malformado": "json"})
    if not isinstance(d, dict):
        return json.dumps({"malformado": "tipo"})
    filas = []
    for x in d.get("r") if isinstance(d.get("r"), list) else []:
        if isinstance(x, list) and len(x) == 4 and _entero(x[0]) is not None and isinstance(x[1], str) and _OP.match(x[1]):
            filas.append([x[0], x[1], 1 if x[2] else 0, _entero(x[3])])
    pasos = []
    for x in d.get("s") if isinstance(d.get("s"), list) else []:
        if isinstance(x, list) and len(x) == 3 and all(_entero(v) is not None for v in x):
            pasos.append(list(x))
    recortado = len(filas) > FILAS_MAX or len(pasos) > FILAS_MAX
    filas, pasos = filas[-FILAS_MAX:], pasos[-FILAS_MAX:]
    salida = {"v": _entero(d.get("v")) or 0, "m": d.get("m") if d.get("m") in ("t", "c") else "?", "r": filas, "s": pasos}
    if recortado:
        salida["recortado"] = True
    texto = json.dumps(salida, separators=(",", ":"))
    while len(texto) > LIMITE and (salida["r"] or salida["s"]):
        (salida["r"] or salida["s"]).pop(0)                  # lo más viejo sobra primero
        salida["recortado"] = True
        texto = json.dumps(salida, separators=(",", ":"))
    return texto


# -- Arranque del editor que se detuvo (POST /cad/<id>/arranque/) -----------------------------------------------------------------
# Cuando la página del plano no logra arrancar el editor (docs/cad.md, «El arranque vigilado»), manda una vez qué etapa se detuvo, cuánto
# tardó cada una y lo que dice el equipo de sí mismo (pantalla, puntero, memoria, la tarjeta de video que da WebGL) para saber en qué
# equipo y dónde. Nada del plano. Se valida campo por campo contra una lista cerrada y se recorta antes de escribirlo en el journal.

ETAPAS = ("memoria", "preparar", "marco", "compilar", "editor", "bloqueo", "plano")
DESDE = ("equipo", "red")
_TEXTO = re.compile(r"[^\x20-\x7e]")
_EQUIPO = {"w": 20000, "h": 20000, "dpr": 16, "mem": 64, "cpu": 512, "tactil": 1, "fino": 1, "webgpu": 1, "sw": 1, "app": 1}


def _numero(v, tope):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= tope:
        return None
    return round(v, 2) if isinstance(v, float) else v


def _texto(v, largo):
    return _TEXTO.sub("?", v)[:largo] if isinstance(v, str) else None


def arranque(d) -> str:
    """El informe listo para escribir (JSON compacto, ≤ LIMITE). Lo que no se entiende se deja fuera; un cuerpo ilegible da {"malformado": …}."""
    if not isinstance(d, dict):
        return json.dumps({"malformado": "tipo"})
    salida = {"v": _entero(d.get("v")) or 0,
              "etapa": d.get("etapa") if d.get("etapa") in ETAPAS else "?",
              "desde": d.get("desde") if d.get("desde") in DESDE else "?",
              "modo": d.get("modo") if d.get("modo") in ("t", "c") else "?",
              "ms": _numero(d.get("ms"), 10 ** 8),
              "recuperado": 1 if d.get("recuperado") else 0}
    pasos = []
    for x in d.get("pasos") if isinstance(d.get("pasos"), list) else []:
        if isinstance(x, list) and len(x) == 2 and x[0] in ETAPAS and _numero(x[1], 10 ** 8) is not None:
            pasos.append([x[0], x[1]])
    salida["pasos"] = pasos[-FILAS_MAX:]
    eq = d.get("eq") if isinstance(d.get("eq"), dict) else {}
    salida["eq"] = {k: n for k, tope in _EQUIPO.items() if (n := _numero(eq.get(k), tope)) is not None}
    for k, largo in (("motivo", 40), ("gl", 120), ("aviso", 400)):
        t = _texto(d.get(k), largo)
        if t:
            salida[k] = t
    texto = json.dumps(salida, separators=(",", ":"))
    while len(texto) > LIMITE and salida["pasos"]:
        salida["pasos"].pop(0)
        texto = json.dumps(salida, separators=(",", ":"))
    return texto[:LIMITE]

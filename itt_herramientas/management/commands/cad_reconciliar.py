"""`manage.py cad_reconciliar [--simular]`: marca los planos y versiones de CAD cuyo archivo ya no está en el disco.

Los planos no tienen respaldo (decisión del propietario, 15-11-2026): tras restaurar la base o cambiar de servidor puede haber renglones
sin archivo. `tvctl cad-reconciliar` lo corre sin argumentos. Es idempotente: correrlo dos veces no cambia nada la segunda.
"""
from django.core.management.base import BaseCommand

from ...cad import servicio


class Command(BaseCommand):
    help = "Marca los planos y versiones de CAD sin archivo en el disco (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--simular", action="store_true", help="Solo cuenta; no cambia nada.")

    def handle(self, *args, simular=False, **opciones):
        r = servicio.reconciliar(simular=simular)
        verbo = "se marcarían" if simular else "marcados"
        self.stdout.write(f"Planos sin archivo: {r['planos_marcados']} {verbo} ahora, {r['planos_ya']} ya estaban. "
                          f"Versiones sin archivo: {r['versiones_marcadas']} {verbo} ahora, {r['versiones_ya']} ya estaban.")
        if r["versiones_desmarcadas"] or r["borradores_borrados"]:
            self.stdout.write(f"Versiones cuyo archivo volvió: {r['versiones_desmarcadas']}. "
                              f"Borradores sin archivo {'que se quitarían' if simular else 'quitados'}: {r['borradores_borrados']}.")

"""Reintenta los envíos pendientes a Google Drive. Pensado para un temporizador de systemd cada 5 minutos (ver docs/drive.md)."""
from django.core.management.base import BaseCommand

from itt_herramientas.drive import servicio


class Command(BaseCommand):
    help = "Envía a Google Drive lo pendiente cuyo turno de reintento ya llegó."

    def handle(self, *args, **opciones):
        enviados = servicio.reintentar_todo()
        self.stdout.write(f"{enviados} archivos enviados a Drive.")

"""Captura de prospectos y solicitudes de visita.

El orden de las operaciones es deliberado y no se debe cambiar:

    1. archivo de texto  (respaldo a prueba de todo)
    2. base de datos     (lo consultable)
    3. aviso al asesor   (lo que da valor)

Guardar va antes que avisar. Si el correo del cliente esta mal configurado o
Google Sheets esta caido, el lead ya quedo en dos lugares. Al reves se
perderia justo lo que le cobras al cliente.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .basedatos import BaseDatos
from .config import RAIZ
from .notificaciones import Notificador


class RegistroLeads:
    def __init__(self, directorio: Path | None = None,
                 base: BaseDatos | None = None,
                 notificador: Notificador | None = None):
        self.directorio = Path(directorio or (RAIZ / "datos"))
        self.directorio.mkdir(parents=True, exist_ok=True)
        self.base = base or BaseDatos()
        self.notificador = notificador or Notificador()
        self._candado = asyncio.Lock()

    def archivo(self, id_inquilino: str) -> Path:
        return self.directorio / f"leads-{id_inquilino}.jsonl"

    async def guardar(self, *, inquilino, sesion_id: str, tipo: str,
                      datos: dict) -> dict:
        registro = {
            "folio": self._folio(),
            "inquilino": inquilino.id,
            "tipo": tipo,
            "sesion": sesion_id,
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "estado": "nuevo",
            **{k: v for k, v in (datos or {}).items() if v not in (None, "", [])},
        }

        # 1. Respaldo en texto plano.
        async with self._candado:
            linea = json.dumps(registro, ensure_ascii=False) + "\n"
            with self.archivo(inquilino.id).open("a", encoding="utf-8") as f:
                f.write(linea)

        # 2. Base de datos.
        await self.base.guardar(registro)

        # 3. Aviso al asesor. Se cuenta cuantas veces habia contactado antes
        # este mismo telefono, sin contar el registro que se acaba de crear.
        previos = 0
        if registro.get("telefono"):
            historial = await asyncio.to_thread(
                self.base.historial_telefono, inquilino.id, registro["telefono"])
            previos = max(0, len(historial) - 1)

        resultado = await self.notificador.enviar(registro, inquilino, previos)
        await self.base.anotar_notificacion(registro["folio"], resultado)
        registro["notificacion"] = resultado
        return registro

    def _folio(self) -> str:
        sello = datetime.now(timezone.utc).strftime("%y%m%d")
        return f"{sello}-{secrets.token_hex(3).upper()}"

    def leer(self, id_inquilino: str, limite: int = 100, **filtros) -> list[dict]:
        """Prospectos, desde la base de datos. Alimenta el panel del asesor."""
        return self.base.listar(id_inquilino, limite=limite, **filtros)

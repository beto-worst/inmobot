"""Captura de leads y solicitudes de visita.

Guarda siempre en disco primero y despues intenta el webhook. Si el webhook
falla -Google Sheets caido, el CRM del cliente en mantenimiento- el lead NO
se pierde: queda en el archivo. Un lead perdido es dinero perdido del cliente
que te renta el sistema, y es lo primero por lo que te reclamaria.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import RAIZ


class RegistroLeads:
    def __init__(self, directorio: Path | None = None):
        self.directorio = Path(directorio or (RAIZ / "datos"))
        self.directorio.mkdir(parents=True, exist_ok=True)
        self._candado = asyncio.Lock()

    def archivo(self, id_inquilino: str) -> Path:
        return self.directorio / f"leads-{id_inquilino}.jsonl"

    async def guardar(self, *, inquilino, sesion_id: str, tipo: str, datos: dict) -> dict:
        registro = {
            "folio": self._folio(),
            "inquilino": inquilino.id,
            "tipo": tipo,
            "sesion": sesion_id,
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **{k: v for k, v in (datos or {}).items() if v not in (None, "", [])},
        }

        async with self._candado:
            linea = json.dumps(registro, ensure_ascii=False) + "\n"
            with self.archivo(inquilino.id).open("a", encoding="utf-8") as f:
                f.write(linea)

        url = (inquilino.contacto or {}).get("webhook_leads")
        if url:
            registro["webhook"] = await self._enviar(url, registro)

        return registro

    async def _enviar(self, url: str, registro: dict) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                r = await c.post(
                    url,
                    # text/plain evita el preflight CORS de Google Apps Script,
                    # que es donde RedNorte ya manda sus leads hoy.
                    headers={"Content-Type": "text/plain;charset=utf-8"},
                    content=json.dumps(registro, ensure_ascii=False),
                )
            return "ok" if r.status_code < 400 else f"http {r.status_code}"
        except Exception as err:  # noqa: BLE001 - el lead ya esta en disco
            return f"fallo: {err}"

    def _folio(self) -> str:
        sello = datetime.now(timezone.utc).strftime("%y%m%d")
        return f"{sello}-{secrets.token_hex(3).upper()}"

    def leer(self, id_inquilino: str, limite: int = 100) -> list[dict]:
        """Ultimos leads. Alimenta el panel del asesor."""
        ruta = self.archivo(id_inquilino)
        if not ruta.is_file():
            return []
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        salida = []
        for linea in lineas[-limite:]:
            try:
                salida.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
        return list(reversed(salida))

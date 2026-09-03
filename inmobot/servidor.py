"""Servidor HTTP del asistente.

Un solo proceso atiende a todos los inquilinos. Cada peticion trae su
`inquilino` y de ahi salen la marca, el prompt, el modelo y el inventario.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .agente import Agente
from .config import RAIZ, RegistroInquilinos, cargar_dotenv
from .leads import RegistroLeads

cargar_dotenv()

# Cuanto vive una conversacion inactiva antes de olvidarse.
VIDA_SESION = 60 * 60
# Cuantos mensajes de historial se conservan. Mas alla de esto, una
# conversacion de chat ya no gana nada y solo cuesta tokens por turno.
MAX_HISTORIAL = 40

app = FastAPI(title="Inmobot", version="0.1.0")

registro = RegistroInquilinos()
leads = RegistroLeads()

# El cliente se construye en la primera peticion de chat, no al arrancar.
# Asi el servidor levanta, sirve el widget y responde /api/config aunque
# todavia no exista la llave: se puede instalar y configurar primero.
_agente: Agente | None = None


def obtener_agente() -> Agente:
    global _agente
    if _agente is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(
                503,
                "Falta ANTHROPIC_API_KEY. Copia .env.example a .env, pon tu "
                "llave de https://console.anthropic.com y reinicia el servidor.",
            )
        _agente = Agente(anthropic.AsyncAnthropic(), leads)
    return _agente

# Sesiones en memoria. Suficiente para un proceso; cuando tengas varios
# inquilinos con trafico real, esto se mueve a Redis y no cambia nada mas.
_sesiones: dict[str, dict] = {}

_origenes = sorted({
    o for i in registro.ids()
    for o in (registro.obtener(i).origenes_permitidos or [])
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origenes or ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class PeticionChat(BaseModel):
    inquilino: str
    mensaje: str = Field(min_length=1, max_length=4000)
    sesion: str | None = None


@app.get("/salud")
async def salud():
    return {"ok": True, "inquilinos": registro.ids(),
            "sesiones_activas": len(_sesiones)}


@app.get("/api/config")
async def config(inquilino: str):
    inq = registro.obtener(inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    return {
        "id": inq.id,
        "nombre": inq.nombre,
        "marca": inq.marca,
        "whatsapp": (inq.contacto or {}).get("whatsapp", ""),
    }


@app.post("/api/chat")
async def chat(p: PeticionChat):
    inq = registro.obtener(p.inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    adaptador = registro.adaptador(p.inquilino)

    sesion_id = p.sesion or secrets.token_urlsafe(12)
    _limpiar_sesiones()
    estado = _sesiones.get(sesion_id) or {"historial": [], "inquilino": p.inquilino}

    # Una sesion pertenece a un inquilino: nadie lee la conversacion de otro
    # reusando un id de sesion ajeno.
    if estado["inquilino"] != p.inquilino:
        raise HTTPException(400, "La sesion no corresponde a este inquilino")

    try:
        respuesta, historial = await obtener_agente().responder(
            inquilino=inq, adaptador=adaptador,
            historial=estado["historial"], mensaje=p.mensaje, sesion_id=sesion_id,
        )
    except anthropic.AuthenticationError:
        raise HTTPException(500, "ANTHROPIC_API_KEY invalida o ausente")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Demasiadas peticiones, intenta en un momento")

    _sesiones[sesion_id] = {
        "inquilino": p.inquilino,
        "historial": _recortar(historial),
        "visto": time.time(),
    }

    return {
        "sesion": sesion_id,
        "respuesta": respuesta.texto,
        "propiedades": respuesta.propiedades,
        "herramientas": respuesta.herramientas_usadas,
        "aviso": respuesta.aviso,
        "costo_usd": round(respuesta.costo_usd, 5),
        "tokens": {
            "entrada": respuesta.uso.entrada,
            "salida": respuesta.uso.salida,
            "cache_leido": respuesta.uso.cache_leido,
        },
    }


@app.get("/api/leads")
async def ver_leads(inquilino: str, token: str = "", limite: int = 50):
    """Panel del asesor. Protegido con un token por inquilino."""
    inq = registro.obtener(inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    esperado = (inq.contacto or {}).get("token_panel", "")
    if not esperado or not secrets.compare_digest(token, esperado):
        raise HTTPException(401, "Token invalido")
    return {"inquilino": inquilino, "leads": leads.leer(inquilino, limite)}


@app.get("/widget/inmobot.js")
async def widget():
    ruta = RAIZ / "widget" / "inmobot.js"
    return FileResponse(ruta, media_type="application/javascript")


@app.get("/")
async def demo():
    ruta = RAIZ / "demo.html"
    if not ruta.is_file():
        return JSONResponse({"ok": True, "mensaje": "Inmobot arriba"})
    return FileResponse(ruta, media_type="text/html")


def _recortar(historial: list[dict]) -> list[dict]:
    """Recorta el historial SIN romper pares herramienta/resultado.

    Un `tool_result` sin su `tool_use` correspondiente hace que la API
    rechace la conversacion completa. Por eso solo se corta justo antes de
    un mensaje de usuario que sea texto, que siempre es frontera limpia.
    """
    if len(historial) <= MAX_HISTORIAL:
        return historial
    for i in range(len(historial) - MAX_HISTORIAL, len(historial)):
        m = historial[i]
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return historial[i:]
    return historial[-2:]


def _limpiar_sesiones() -> None:
    corte = time.time() - VIDA_SESION
    for sid in [s for s, d in _sesiones.items() if d.get("visto", 0) < corte]:
        _sesiones.pop(sid, None)


def main() -> None:
    import uvicorn
    puerto = int(os.environ.get("INMOBOT_PUERTO", "8100"))
    uvicorn.run("inmobot.servidor:app", host="127.0.0.1", port=puerto, reload=False)


if __name__ == "__main__":
    main()

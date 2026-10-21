"""Servidor HTTP del asistente.

Un solo proceso atiende a todos los inquilinos. Cada peticion trae su
`inquilino` y de ahi salen la marca, el prompt, el modelo y el inventario.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import anthropic
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from pydantic import BaseModel, Field

from .agente import Agente, ResultadoStream
from .config import RAIZ, RegistroInquilinos, cargar_dotenv
from .conversaciones import Conversaciones, esta_pausada
from .leads import RegistroLeads
from .whatsapp import (buscar_inquilino, canal_de, firma_valida, leer_entrantes)

cargar_dotenv()

# Cuanto vive una conversacion inactiva antes de olvidarse.
VIDA_SESION = 60 * 60
# Cuantos mensajes de historial se conservan. Mas alla de esto, una
# conversacion de chat ya no gana nada y solo cuesta tokens por turno.
MAX_HISTORIAL = 40

app = FastAPI(title="Inmobot", version="0.1.0")

registro = RegistroInquilinos()
leads = RegistroLeads()
# Las conversaciones del sitio viven en memoria; las de WhatsApp no pueden:
# el cliente contesta tres horas despues y el servidor ya se reinicio.
conversaciones = Conversaciones()

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
        "asistente": inq.asistente,
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


@app.post("/api/chat/stream")
async def chat_stream(p: PeticionChat):
    """Igual que /api/chat pero la respuesta sale conforme se escribe.

    Sin esto el cliente mira una animacion de puntitos varios segundos y
    cree que se trabo. Con esto ve texto desde el primer momento, que es la
    diferencia entre sentirse rapido y sentirse roto.
    """
    inq = registro.obtener(p.inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    adaptador = registro.adaptador(p.inquilino)

    sesion_id = p.sesion or secrets.token_urlsafe(12)
    _limpiar_sesiones()
    estado = _sesiones.get(sesion_id) or {"historial": [], "inquilino": p.inquilino}
    if estado["inquilino"] != p.inquilino:
        raise HTTPException(400, "La sesion no corresponde a este inquilino")

    agente = obtener_agente()  # revienta aqui, no a media respuesta

    async def eventos():
        def sse(dato: dict) -> str:
            return f"data: {json.dumps(dato, ensure_ascii=False, default=str)}\n\n"

        yield sse({"tipo": "sesion", "sesion": sesion_id})
        buzon = ResultadoStream()
        try:
            async for evento in agente.responder_stream(
                inquilino=inq, adaptador=adaptador,
                historial=estado["historial"], mensaje=p.mensaje,
                sesion_id=sesion_id, buzon=buzon,
            ):
                yield sse(evento)
        except anthropic.RateLimitError:
            yield sse({"tipo": "error",
                       "mensaje": "Hay mucha demanda ahorita, intenta en un momento."})
            return
        except Exception as err:  # noqa: BLE001 - el chat no se puede caer
            yield sse({"tipo": "error", "mensaje": "Hubo un problema tecnico.",
                       "detalle": str(err)})
            return

        # Solo se guarda si el turno llego completo: un historial cortado a
        # la mitad de una llamada a herramienta hace que la API rechace la
        # conversacion entera en el siguiente mensaje.
        if buzon.respuesta is not None:
            _sesiones[sesion_id] = {
                "inquilino": p.inquilino,
                "historial": _recortar(buzon.historial),
                "visto": time.time(),
            }

    return StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Sin esto, nginx acumula la respuesta y la entrega de golpe:
            # el streaming deja de servir justo en produccion.
            "X-Accel-Buffering": "no",
        },
    )


def _autorizar_panel(inquilino: str, token: str):
    inq = registro.obtener(inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")

    contacto = inq.contacto or {}
    # La clave preferentemente vive en .env, no en el JSON del inquilino:
    # ese archivo se sube a git y ahi una clave queda para siempre en el
    # historial, aunque despues se borre. `token_panel` se sigue aceptando
    # para pruebas locales.
    variable = contacto.get("token_panel_env")
    esperado = os.environ.get(variable, "") if variable else contacto.get("token_panel", "")

    if not esperado:
        raise HTTPException(
            503,
            f"El panel de '{inquilino}' no tiene clave configurada"
            + (f": falta {variable} en .env" if variable else "."),
        )
    # compare_digest evita filtrar el token por el tiempo que tarda comparar.
    if not secrets.compare_digest(token, esperado):
        raise HTTPException(401, "Token invalido")
    return inq


@app.get("/api/leads")
async def ver_leads(inquilino: str, token: str = "", limite: int = 50,
                    estado: str | None = None, tipo: str | None = None,
                    busqueda: str = ""):
    """Panel del asesor. Protegido con un token por inquilino."""
    _autorizar_panel(inquilino, token)
    return {
        "inquilino": inquilino,
        "resumen": leads.base.resumen(inquilino),
        "leads": leads.leer(inquilino, limite, estado=estado, tipo=tipo,
                            busqueda=busqueda),
    }


class CambioEstado(BaseModel):
    inquilino: str
    token: str
    estado: str
    asesor: str = ""


@app.post("/api/leads/{folio}/estado")
async def cambiar_estado(folio: str, c: CambioEstado):
    """El asesor marca un prospecto como contactado, cerrado o descartado."""
    _autorizar_panel(c.inquilino, c.token)
    try:
        ok = await leads.base.marcar(folio, c.estado, c.asesor)
    except ValueError as err:
        raise HTTPException(400, str(err))
    if not ok:
        raise HTTPException(404, "Folio no encontrado")
    return {"folio": folio, "estado": c.estado}


@app.get("/api/leads/{folio}/historial")
async def historial(folio: str, inquilino: str, token: str = ""):
    """Veces que este telefono ya habia contactado antes."""
    _autorizar_panel(inquilino, token)
    encontrados = [l for l in leads.leer(inquilino, 500) if l.get("folio") == folio]
    if not encontrados:
        raise HTTPException(404, "Folio no encontrado")
    telefono = encontrados[0].get("telefono", "")
    return {"folio": folio,
            "contactos": leads.base.historial_telefono(inquilino, telefono)}


# ============================================================
# WhatsApp
# ============================================================
@app.get("/webhook/whatsapp")
async def whatsapp_verificar(request: Request):
    """El saludo que hace Meta al dar de alta la direccion.

    Manda un reto y espera que se lo devolvamos tal cual, en texto plano,
    para comprobar que de este lado hay alguien que conoce el token.
    """
    p = request.query_params
    esperado = os.environ.get("META_VERIFY_TOKEN", "")
    if not esperado:
        raise HTTPException(503, "Falta META_VERIFY_TOKEN en el .env")
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == esperado:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Token de verificacion incorrecto")


@app.post("/webhook/whatsapp")
async def whatsapp_entrada(request: Request, tareas: BackgroundTasks):
    """Recibe los mensajes y contesta 200 de inmediato.

    El modelo tarda segundos en responder y Meta reintenta si no le
    contestamos rapido. Por eso aqui solo se valida y se encola: contestarle
    al cliente pasa despues, ya con la respuesta enviada a Meta.
    """
    cuerpo = await request.body()
    if not firma_valida(cuerpo, request.headers.get("x-hub-signature-256", ""),
                        os.environ.get("META_APP_SECRET", "")):
        raise HTTPException(403, "Firma invalida")

    try:
        aviso = json.loads(cuerpo)
    except json.JSONDecodeError:
        raise HTTPException(400, "Cuerpo que no es JSON")

    for mensaje in leer_entrantes(aviso):
        tareas.add_task(_atender_whatsapp, mensaje)
    return {"recibido": True}


async def _atender_whatsapp(m) -> None:
    """Contesta un mensaje de WhatsApp. Nunca levanta: Meta ya se fue."""
    try:
        inq = buscar_inquilino(registro, m.telefono_id)
        if not inq:
            print(json.dumps({"evento": "whatsapp_sin_inquilino",
                              "telefono_id": m.telefono_id}))
            return

        canal = canal_de(inq)
        # Meta reintenta; sin esto el cliente recibe la misma respuesta dos veces.
        if await conversaciones.es_repetido(m.id):
            return

        if m.fuera_de_ventana:
            print(json.dumps({"evento": "whatsapp_fuera_de_ventana", "de": m.de}))
            return

        if not m.texto:
            await canal.enviar(m.de, (
                f"Por ahora solo puedo leer mensajes de texto. Escribeme que "
                f"necesitas y con gusto te ayudo."))
            return

        conv = await conversaciones.abrir("whatsapp", m.de, inq.id, m.nombre)
        # Si un asesor entro a contestar a mano, la IA se calla en ese chat.
        if esta_pausada(conv):
            return

        await canal.marcar_leido(m.id)

        respuesta, historial = await obtener_agente().responder(
            inquilino=inq, adaptador=registro.adaptador(inq.id),
            historial=conv["historial"], mensaje=m.texto,
            sesion_id=f"whatsapp:{m.de}",
        )

        # Se guarda antes de enviar: si el envio falla, la conversacion no
        # pierde el turno y el cliente no tiene que repetirse.
        await conversaciones.guardar("whatsapp", m.de, historial)
        resultado = await canal.enviar(m.de, respuesta.texto)

        print(json.dumps({"evento": "whatsapp_contestado", "inquilino": inq.id,
                          "de": m.de, "envio": resultado,
                          "costo_usd": round(respuesta.costo_usd, 5)}))
    except Exception as e:  # noqa: BLE001 - una falla no puede tumbar el canal
        print(json.dumps({"evento": "whatsapp_error", "de": getattr(m, "de", ""),
                          "error": f"{type(e).__name__}: {e}"}))


class PeticionPausa(BaseModel):
    contacto: str
    minutos: int = 120
    canal: str = "whatsapp"


@app.post("/api/conversaciones/pausar")
async def pausar_conversacion(p: PeticionPausa, inquilino: str, token: str = ""):
    """Calla a la IA en un chat para que conteste una persona."""
    _autorizar_panel(inquilino, token)
    await conversaciones.pausar(p.canal, p.contacto, p.minutos)
    return {"pausada": True, "minutos": p.minutos}


@app.post("/api/conversaciones/reanudar")
async def reanudar_conversacion(p: PeticionPausa, inquilino: str, token: str = ""):
    _autorizar_panel(inquilino, token)
    await conversaciones.reanudar(p.canal, p.contacto)
    return {"pausada": False}


@app.get("/panel")
async def panel():
    """Panel del asesor. La clave se pide dentro, no va en la URL."""
    return FileResponse(RAIZ / "panel.html", media_type="text/html")


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

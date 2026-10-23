"""Servidor HTTP del asistente.

Un solo proceso atiende a todos los inquilinos. Cada peticion trae su
`inquilino` y de ahi salen la marca, el prompt, el modelo y el inventario.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import traceback
from pathlib import Path

import anthropic
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from pydantic import BaseModel, Field

from .agente import Agente, ResultadoStream
from .config import RAIZ, RegistroInquilinos, cargar_dotenv, leer_env
from .conversaciones import Conversaciones, esta_pausada
from .leads import RegistroLeads
from .notificaciones import Notificador
from .whatsapp import (buscar_inquilino, canal_de, firma_valida, leer_entrantes)

cargar_dotenv()

# Cuanto vive una conversacion inactiva antes de olvidarse.
VIDA_SESION = 60 * 60
# Cuantos mensajes de historial se conservan. Mas alla de esto, una
# conversacion de chat ya no gana nada y solo cuesta tokens por turno.
MAX_HISTORIAL = 40

# Topes de gasto. Un chat abierto en internet es una llave de API abierta:
# sin esto, cualquiera con un script deja al cliente una factura de cientos
# de dolares en una noche. Se puede subir por inquilino con "limites".
MSJ_POR_IP = 20            # por ventana
VENTANA_IP = 10 * 60       # segundos
MSJ_POR_DIA = 400          # por inquilino, todos los visitantes juntos

app = FastAPI(title="Inmobot", version="0.1.0")

registro = RegistroInquilinos()
leads = RegistroLeads()
# Las conversaciones del sitio viven en memoria; las de WhatsApp no pueden:
# el cliente contesta tres horas despues y el servidor ya se reinicio.
conversaciones = Conversaciones()
notificador = Notificador()

# El cliente se construye en la primera peticion de chat, no al arrancar.
# Asi el servidor levanta, sirve el widget y responde /api/config aunque
# todavia no exista la llave: se puede instalar y configurar primero.
_agente: Agente | None = None


def obtener_agente() -> Agente:
    global _agente
    if _agente is None:
        if not leer_env("ANTHROPIC_API_KEY"):
            raise HTTPException(
                503,
                "Falta ANTHROPIC_API_KEY. Copia .env.example a .env, pon tu "
                "llave de https://console.anthropic.com y reinicia el servidor.",
            )
        _agente = Agente(
            anthropic.AsyncAnthropic(api_key=leer_env("ANTHROPIC_API_KEY")), leads)
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
    return {
        "ok": True,
        "inquilinos": registro.ids(),
        "sesiones_activas": len(_sesiones),
        # De que commit salio lo que esta corriendo. Al desplegar, saber
        # esto es la diferencia entre "el arreglo no sirvio" y "el arreglo
        # todavia no llega".
        "version": (os.environ.get("RENDER_GIT_COMMIT")
                    or os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "local")[:7],
    }


@app.get("/salud/red")
async def salud_red():
    """Si el servidor alcanza a Anthropic, y si no, por que.

    Desplegar a veces falla por red, no por codigo, y desde afuera las dos
    cosas se ven igual: error 500.
    """
    import socket
    import httpx as _httpx

    reporte: dict = {}
    try:
        direcciones = socket.getaddrinfo("api.anthropic.com", 443)
        reporte["dns"] = sorted({d[4][0] for d in direcciones})
    except Exception as e:  # noqa: BLE001
        reporte["dns"] = f"fallo: {type(e).__name__}: {e}"

    for etiqueta, kwargs in (("normal", {}), ("solo_ipv4", {"local_address": "0.0.0.0"})):
        try:
            transporte = _httpx.AsyncHTTPTransport(**kwargs)
            async with _httpx.AsyncClient(timeout=15.0, transport=transporte) as c:
                r = await c.get("https://api.anthropic.com/v1/models")
            # 401 es exito para esto: contestó, solo falta la llave.
            reporte[etiqueta] = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            reporte[etiqueta] = f"fallo: {type(e).__name__}: {str(e)[:120]}"

    return reporte


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
async def chat(p: PeticionChat, request: Request):
    inq = registro.obtener(p.inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    _limitar(request, inq)
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
    except Exception as e:  # noqa: BLE001
        causa = repr(getattr(e, "__cause__", None))[:200]
        print(json.dumps({"evento": "chat_fallo", "inquilino": p.inquilino,
                          "error": f"{type(e).__name__}: {e}"[:400],
                          "causa": causa}))
        traceback.print_exc()
        # La causa va al log y NO a la respuesta: la primera version de
        # esto devolvia el detalle al cliente y filtro la llave de la API
        # en un endpoint publico.
        raise HTTPException(500, f"Fallo interno: {type(e).__name__}")

    _sesiones[sesion_id] = {
        "inquilino": p.inquilino,
        "historial": _recortar(historial),
        "visto": time.time(),
    }
    await _guardar_conversacion_web(p.inquilino, sesion_id, historial)

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
async def chat_stream(p: PeticionChat, request: Request):
    """Igual que /api/chat pero la respuesta sale conforme se escribe.

    Sin esto el cliente mira una animacion de puntitos varios segundos y
    cree que se trabo. Con esto ve texto desde el primer momento, que es la
    diferencia entre sentirse rapido y sentirse roto.
    """
    inq = registro.obtener(p.inquilino)
    if not inq:
        raise HTTPException(404, "Inquilino no encontrado")
    _limitar(request, inq)
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
            await _guardar_conversacion_web(p.inquilino, sesion_id,
                                            buzon.historial)

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


# ip -> (cuantos, desde_cuando) | inquilino -> (cuantos, que dia)
_uso_ip: dict[str, tuple[int, float]] = {}
_uso_dia: dict[str, tuple[int, str]] = {}


def _limitar(request: Request, inq) -> None:
    """Corta al visitante que abusa, y al inquilino que se paso del dia.

    Son dos cosas distintas: lo primero frena a un bot; lo segundo es el
    techo de la factura, pase lo que pase.
    """
    limites = getattr(inq, "limites", None) or {}
    por_ip = int(limites.get("mensajes_por_ip", MSJ_POR_IP))
    por_dia = int(limites.get("mensajes_por_dia", MSJ_POR_DIA))

    ahora = time.time()
    adelante = request.headers.get("x-forwarded-for", "")
    ip = adelante.split(",")[0].strip() or (
        request.client.host if request.client else "desconocida")

    if len(_uso_ip) > 5_000:
        _uso_ip.clear()

    cuantos, desde = _uso_ip.get(ip, (0, ahora))
    if ahora - desde >= VENTANA_IP:
        cuantos, desde = 0, ahora
    cuantos += 1
    _uso_ip[ip] = (cuantos, desde)
    if cuantos > por_ip:
        espera = int(VENTANA_IP - (ahora - desde))
        raise HTTPException(429, "Vas muy rapido. Intenta de nuevo en un momento.",
                            headers={"Retry-After": str(max(1, espera))})

    hoy = time.strftime("%Y-%m-%d")
    gastados, dia = _uso_dia.get(inq.id, (0, hoy))
    if dia != hoy:
        gastados, dia = 0, hoy
    gastados += 1
    _uso_dia[inq.id] = (gastados, dia)
    if gastados > por_dia:
        print(json.dumps({"evento": "tope_diario", "inquilino": inq.id,
                          "mensajes": gastados}))
        raise HTTPException(
            503, "El asistente alcanzo su limite de hoy. Escribenos por WhatsApp "
                 "y con gusto te atendemos.")


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
    esperado = leer_env(variable) if variable else str(contacto.get("token_panel", "")).strip()

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
    esperado = leer_env("META_VERIFY_TOKEN")
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
                        leer_env("META_APP_SECRET")):
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

        canal = canal_de(inq, m.telefono_id)
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
        # Solo en el primer mensaje: avisar en cada turno seria un acoso.
        es_nueva = not conv["historial"]
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

        if es_nueva:
            quien = m.nombre or ("+" + m.de)
            await _avisar(inq, f"{quien} escribio por WhatsApp",
                          f"{m.texto[:160]}\n\nAthena: {respuesta.texto[:160]}")
    except Exception as e:  # noqa: BLE001 - una falla no puede tumbar el canal
        print(json.dumps({"evento": "whatsapp_error", "de": getattr(m, "de", ""),
                          "error": f"{type(e).__name__}: {e}"}))


async def _avisar(inquilino, titulo: str, texto: str) -> None:
    """Empujon al telefono del dueno. Si falla, se anota y ya.

    Un aviso que no sale no puede tumbar una conversacion que si funciono.
    """
    try:
        resultado = await notificador.aviso_push(inquilino, titulo, texto)
        if not resultado.startswith(("ok", "sin push")):
            print(json.dumps({"evento": "aviso_fallo", "detalle": resultado}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"evento": "aviso_fallo",
                          "detalle": f"{type(e).__name__}: {e}"[:200]}))


async def _guardar_conversacion_web(inquilino: str, sesion: str,
                                    historial: list) -> None:
    """Deja el chat del sitio en la base, igual que los de WhatsApp.

    Nunca tumba una respuesta ya generada: si falla el guardado, el cliente
    igual recibe lo suyo y el problema queda en el log.
    """
    try:
        estado = await conversaciones.abrir("web", sesion, inquilino)
        es_nueva = not estado["historial"]
        await conversaciones.guardar("web", sesion, historial)
        if es_nueva:
            inq = registro.obtener(inquilino)
            primero = next((m.get("content") for m in historial
                            if m.get("role") == "user"), "")
            if inq:
                await _avisar(inq, "Alguien esta chateando en el sitio",
                              str(primero)[:200])
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"evento": "conversacion_no_guardada",
                          "error": f"{type(e).__name__}: {e}"[:200]}))


@app.get("/api/conversaciones")
async def ver_conversaciones(inquilino: str, token: str = "", canal: str = "",
                             limite: int = 50):
    """Las conversaciones del inquilino, de la mas reciente a la mas vieja."""
    _autorizar_panel(inquilino, token)
    return {"conversaciones": conversaciones.listar(inquilino=inquilino,
                                                    canal=canal, limite=limite)}


@app.get("/api/conversaciones/{canal}/{contacto}")
async def ver_conversacion(canal: str, contacto: str, inquilino: str,
                           token: str = ""):
    _autorizar_panel(inquilino, token)
    charla = conversaciones.leer(canal, contacto, inquilino=inquilino)
    if charla is None:
        raise HTTPException(404, "Conversacion no encontrada")
    return charla


@app.get("/api/whatsapp/diagnostico")
async def diagnostico_whatsapp(inquilino: str, token: str = "",
                               telefono_id: str = "", waba_id: str = ""):
    """Que le falta al token de WhatsApp, dicho por Meta y no por nosotros.

    Un envio que falla con 403 puede ser el token equivocado, el token bueno
    sin el activo asignado, o permisos incompletos. Desde fuera los tres se
    ven igual. Esto le pregunta a Meta directamente.
    """
    _autorizar_panel(inquilino, token)

    import httpx as _httpx
    from .whatsapp import _ids as _ids_whatsapp

    clave = leer_env("WHATSAPP_TOKEN")
    reporte: dict = {
        "token_configurado": bool(clave),
        "largo_del_token": len(clave),
        "numeros": {},
    }
    if not clave:
        reporte["conclusion"] = "Falta WHATSAPP_TOKEN en el servidor."
        return reporte

    inq = registro.obtener(inquilino)
    ids = [telefono_id] if telefono_id else _ids_whatsapp(
        (inq.canales or {}).get("whatsapp") or {})

    async with _httpx.AsyncClient(timeout=20.0) as cliente:
        for numero in ids:
            url = f"https://graph.facebook.com/v25.0/{numero}"
            try:
                r = await cliente.get(
                    url, params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": "Bearer " + clave})
            except _httpx.HTTPError as e:
                reporte["numeros"][numero] = f"no se pudo consultar: {type(e).__name__}"
                continue
            if r.status_code == 200:
                datos = r.json()
                reporte["numeros"][numero] = {
                    "acceso": "si",
                    "numero": datos.get("display_phone_number"),
                    "nombre": datos.get("verified_name"),
                }
            else:
                error = (r.json() or {}).get("error", {}) if r.text else {}
                reporte["numeros"][numero] = {
                    "acceso": "no",
                    "codigo": error.get("code"),
                    "mensaje": str(error.get("message"))[:200],
                }

        # Que la app este suscrita a la cuenta de mensajes es lo que hace
        # que Meta nos entregue lo que escriben los clientes. Con el token
        # correcto y sin esto, el sistema funciona entero y no llega nada.
        if waba_id:
            try:
                r = await cliente.get(
                    f"https://graph.facebook.com/v25.0/{waba_id}/subscribed_apps",
                    headers={"Authorization": "Bearer " + clave})
                if r.status_code == 200:
                    apps = (r.json() or {}).get("data") or []
                    reporte["apps_suscritas"] = [
                        (a.get("whatsapp_business_api_data") or {}).get("name")
                        or (a.get("whatsapp_business_api_data") or {}).get("id")
                        for a in apps
                    ] or "ninguna: nadie recibe los mensajes"
                else:
                    error = (r.json() or {}).get("error", {}) if r.text else {}
                    reporte["apps_suscritas"] = f"no se pudo: {error.get('message')}"
            except _httpx.HTTPError as e:
                reporte["apps_suscritas"] = f"no se pudo: {type(e).__name__}"

    return reporte


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

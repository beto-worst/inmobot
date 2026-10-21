"""Simula a Meta tocando el webhook, sin Meta y sin gastar API.

Comprueba lo que de verdad puede salir mal cuando esto quede abierto a
internet: que solo entre quien trae la firma buena, que un reintento no le
conteste dos veces al cliente, que la conversacion se acuerde del turno
anterior y que la IA se calle cuando un asesor toma el chat.

Correr:  python prueba_whatsapp.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

import inmobot.servidor as servidor
from inmobot.agente import Respuesta
from inmobot.conversaciones import Conversaciones, aplanar
from inmobot.whatsapp import CanalWhatsApp, partir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SECRETO = "secreto-de-prueba"
VERIFY = "token-de-verificacion"
TELEFONO_ID = "111222333444555"
CLIENTE = "528111111111"

enviados: list[tuple[str, str]] = []
fallos = 0


def revisar(titulo: str, ok: bool, detalle: str = "") -> None:
    global fallos
    if not ok:
        fallos += 1
    print(f"  [{'ok ' if ok else 'FALLA'}] {titulo}" + (f"  — {detalle}" if detalle else ""))


# ---------- dobles ----------
class AgenteFalso:
    """Contesta sin llamar a Claude, pero respetando el contrato."""

    def __init__(self):
        self.llamadas = 0

    async def responder(self, *, inquilino, adaptador, historial, mensaje, sesion_id):
        self.llamadas += 1
        texto = f"Recibido: {mensaje} (turnos previos: {len(historial)})"
        nuevo = list(historial) + [
            {"role": "user", "content": mensaje},
            {"role": "assistant", "content": [{"type": "text", "text": texto}]},
        ]
        return Respuesta(texto=texto), nuevo


async def enviar_falso(self, para: str, texto: str) -> str:
    enviados.append((para, texto))
    return "ok"


async def leido_falso(self, id_mensaje: str) -> None:
    return None


# ---------- utilidades ----------
def aviso(texto: str, id_mensaje: str, momento: int | None = None) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "528130743250",
                         "phone_number_id": TELEFONO_ID},
            "contacts": [{"profile": {"name": "Mariana"}, "wa_id": CLIENTE}],
            "messages": [{"from": CLIENTE, "id": id_mensaje,
                          "timestamp": str(momento or int(time.time())),
                          "type": "text", "text": {"body": texto}}],
        }}]}],
    }


def firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def mandar(cliente: TestClient, datos: dict, firma: str | None = None):
    cuerpo = json.dumps(datos).encode()
    return cliente.post("/webhook/whatsapp", content=cuerpo,
                        headers={"X-Hub-Signature-256": firma or firmar(cuerpo),
                                 "Content-Type": "application/json"})


def main() -> None:
    import os
    os.environ["META_APP_SECRET"] = SECRETO
    os.environ["META_VERIFY_TOKEN"] = VERIFY

    temporal = Path(tempfile.mkdtemp(prefix="wa_"))
    servidor.conversaciones = Conversaciones(temporal / "prueba.db")
    agente = AgenteFalso()
    servidor.obtener_agente = lambda: agente
    CanalWhatsApp.enviar = enviar_falso
    CanalWhatsApp.marcar_leido = leido_falso

    inq = servidor.registro.obtener("demo")
    inq.canales = {"whatsapp": {"telefono_id": TELEFONO_ID,
                                "token_env": "WHATSAPP_TOKEN"}}

    cliente = TestClient(servidor.app)

    print("\nEL SALUDO DE META")
    r = cliente.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY,
        "hub.challenge": "12345"})
    revisar("devuelve el reto tal cual", r.status_code == 200 and r.text == "12345",
            f"{r.status_code} {r.text[:40]}")
    r = cliente.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "equivocado",
        "hub.challenge": "12345"})
    revisar("rechaza el token equivocado", r.status_code == 403, str(r.status_code))

    print("\nLA FIRMA")
    r = mandar(cliente, aviso("Hola", "wamid.1"), firma="sha256=inventada")
    revisar("rechaza la firma falsa", r.status_code == 403, str(r.status_code))
    revisar("y no contesto nada", not enviados)

    r = mandar(cliente, aviso("Hola", "wamid.1"))
    revisar("acepta la firma buena", r.status_code == 200, str(r.status_code))
    revisar("contesto una vez", len(enviados) == 1,
            enviados[-1][1][:60] if enviados else "no contesto")

    print("\nEL REINTENTO DE META")
    mandar(cliente, aviso("Hola", "wamid.1"))
    revisar("el mismo id no se contesta dos veces", len(enviados) == 1,
            f"{len(enviados)} envios")

    print("\nLA MEMORIA")
    mandar(cliente, aviso("Busco en Cumbres", "wamid.2"))
    revisar("contesto el segundo", len(enviados) == 2)
    revisar("se acordo del turno anterior", "turnos previos: 2" in enviados[-1][1],
            enviados[-1][1][:70])

    print("\nCUANDO ENTRA UN ASESOR")
    import asyncio
    asyncio.run(servidor.conversaciones.pausar("whatsapp", CLIENTE, 60))
    mandar(cliente, aviso("Sigo ahi?", "wamid.3"))
    revisar("la IA se calla", len(enviados) == 2, f"{len(enviados)} envios")
    asyncio.run(servidor.conversaciones.reanudar("whatsapp", CLIENTE))
    mandar(cliente, aviso("Hola de nuevo", "wamid.4"))
    revisar("y vuelve cuando se reanuda", len(enviados) == 3)

    print("\nUN MENSAJE VIEJO (servidor caido, Meta reintenta al otro dia)")
    antes = len(enviados)
    mandar(cliente, aviso("Hola?", "wamid.5", momento=int(time.time()) - 26 * 3600))
    revisar("no contesta fuera de la ventana de 24 h", len(enviados) == antes,
            f"{len(enviados) - antes} envios")

    print("\nPIEZAS SUELTAS")
    largo = "palabra " * 900
    trozos = partir(largo)
    revisar("parte los mensajes largos", len(trozos) > 1 and all(len(t) <= 4000 for t in trozos),
            f"{len(trozos)} trozos, el mayor de {max(len(t) for t in trozos)}")

    sucio = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": [{"type": "text", "text": "voy a buscar"},
                                          {"type": "tool_use", "id": "x", "name": "buscar"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "{...}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "encontre 3"}]},
    ]
    limpio = aplanar(sucio)
    revisar("el historial guardado queda en puro texto",
            limpio == [{"role": "user", "content": "hola"},
                       {"role": "assistant", "content": "voy a buscar\n\nencontre 3"}],
            json.dumps(limpio, ensure_ascii=False)[:90])

    print("\n" + ("TODO BIEN" if not fallos else f"{fallos} PRUEBAS FALLARON"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

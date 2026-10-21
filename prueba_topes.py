"""Comprueba los topes de gasto del chat publico.

El chat de un sitio esta abierto a cualquiera, y cada mensaje cuesta. Sin
topes, un script deja una factura de cientos de dolares en una noche. Esto
verifica las dos defensas: la del visitante que abusa y el techo del dia.

Correr:  python prueba_topes.py
"""
from __future__ import annotations

import sys

from fastapi.testclient import TestClient

import inmobot.servidor as servidor
from inmobot.agente import Respuesta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fallos = 0


def revisar(titulo: str, ok: bool, detalle: str = "") -> None:
    global fallos
    if not ok:
        fallos += 1
    print(f"  [{'ok ' if ok else 'FALLA'}] {titulo}" + (f"  — {detalle}" if detalle else ""))


class AgenteFalso:
    async def responder(self, *, inquilino, adaptador, historial, mensaje, sesion_id):
        return Respuesta(texto="ok"), []


def hablar(cliente: TestClient, ip: str):
    return cliente.post("/api/chat", json={"inquilino": "demo", "mensaje": "hola"},
                        headers={"X-Forwarded-For": ip})


def main() -> None:
    servidor.obtener_agente = lambda: AgenteFalso()
    inq = servidor.registro.obtener("demo")
    inq.limites = {"mensajes_por_ip": 3, "mensajes_por_dia": 5}
    servidor._uso_ip.clear()
    servidor._uso_dia.clear()
    cliente = TestClient(servidor.app)

    print("\nEL VISITANTE QUE ABUSA")
    codigos = [hablar(cliente, "10.0.0.1").status_code for _ in range(4)]
    revisar("deja pasar los primeros y corta el cuarto", codigos == [200, 200, 200, 429],
            str(codigos))

    print("\nOTRO VISITANTE NO PAGA EL PATO")
    r = hablar(cliente, "10.0.0.2")
    revisar("una IP distinta sigue entrando", r.status_code == 200, str(r.status_code))

    print("\nEL TECHO DEL DIA")
    # Van 4 mensajes contados (3 + 1); el tope del inquilino es 5.
    codigos = [hablar(cliente, f"10.0.1.{i}").status_code for i in range(3)]
    revisar("al pasarse, corta a todos sin importar la IP", 503 in codigos, str(codigos))

    r = hablar(cliente, "10.0.9.9")
    revisar("y el mensaje no deja al cliente colgado",
            r.status_code == 503 and "WhatsApp" in r.json().get("detail", ""),
            r.json().get("detail", "")[:70])

    print("\n" + ("TODO BIEN" if not fallos else f"{fallos} PRUEBAS FALLARON"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()

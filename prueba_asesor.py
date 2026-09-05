"""Flujo completo: al cliente le gusta una opcion y el asesor se entera.

Verifica lo que de verdad importa del producto:
  - Athena pide nombre y telefono cuando hay interes real
  - el prospecto queda en la base de datos, consultable
  - al asesor le llega el aviso con el contexto completo
  - un mismo telefono escrito distinto se reconoce como el mismo cliente
  - el asesor puede marcarlo como contactado

Correr:  python prueba_asesor.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import anthropic

from inmobot.adaptadores.demo import AdaptadorDemo
from inmobot.agente import Agente
from inmobot.basedatos import BaseDatos, normalizar_telefono
from inmobot.config import Inquilino, cargar_dotenv
from inmobot.leads import RegistroLeads
from inmobot.notificaciones import Notificador, redactar

cargar_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONVERSACION = [
    "busco casa en Cumbres, 3 recámaras, para comprar, hasta 4 millones",
    "me gusta la de Cumbres Elite, quiero que me llame un asesor. "
    "Soy Alberto Vázquez, mi celular es 81 1778 3953",
]


class NotificadorEspia(Notificador):
    """Igual que el real, pero guarda lo que mando para poder revisarlo."""

    def __init__(self):
        self.enviados: list[tuple[str, str]] = []

    async def enviar(self, registro, inquilino, previos=0):
        self.enviados.append(redactar(registro, inquilino, previos))
        return "espia:ok"


async def main() -> None:
    carpeta = Path(tempfile.mkdtemp(prefix="athena_asesor_"))
    base = BaseDatos(carpeta / "athena.db")
    espia = NotificadorEspia()
    registro_leads = RegistroLeads(directorio=carpeta, base=base, notificador=espia)

    inq = Inquilino(
        id="demo", nombre="Inmobiliaria Demo", asistente="Athena",
        zona_operacion="Área metropolitana de Monterrey, Nuevo León",
        contacto={"whatsapp": "5218110000000"},
        notificaciones={"consola": False},
    )

    agente = Agente(anthropic.AsyncAnthropic(), registro_leads)
    adaptador = AdaptadorDemo()
    historial: list[dict] = []

    print("=" * 72)
    print("1. CONVERSACION")
    print("=" * 72)
    for mensaje in CONVERSACION:
        print(f"\nCLIENTE: {mensaje}")
        r, historial = await agente.responder(
            inquilino=inq, adaptador=adaptador, historial=historial,
            mensaje=mensaje, sesion_id="asesor-1",
        )
        print(f"\nATHENA: {r.texto}")
        if r.herramientas_usadas:
            print(f"  [{', '.join(r.herramientas_usadas)}]")

    print("\n" + "=" * 72)
    print("2. LO QUE LE LLEGA AL ASESOR")
    print("=" * 72)
    assert espia.enviados, "no se aviso al asesor"
    for asunto, cuerpo in espia.enviados:
        print(f"\nASUNTO: {asunto}")
        print("-" * 72)
        print(cuerpo)

    print("\n" + "=" * 72)
    print("3. EN LA BASE DE DATOS")
    print("=" * 72)
    guardados = base.listar("demo")
    print(f"registros: {len(guardados)}")
    for g in guardados:
        print(f"  {g['folio']}  {g['tipo']:7} {g.get('nombre', '?'):18} "
              f"{g.get('telefono', '?'):14} estado={g['estado']} "
              f"aviso={g.get('notificacion', '-')}")
    assert guardados, "no se guardo nada en la base de datos"
    lead = next((g for g in guardados if g["tipo"] == "lead"), None)
    assert lead, "no se registro el lead"
    assert lead.get("nombre"), "el lead no trae nombre"
    assert lead.get("telefono"), "el lead no trae telefono"

    print("\n" + "=" * 72)
    print("4. MISMO CLIENTE, TELEFONO ESCRITO DISTINTO")
    print("=" * 72)
    variantes = ["81 1778 3953", "+52 811 778 3953", "8117783953", "(81) 1778-3953"]
    for v in variantes:
        print(f"  '{v}'  ->  {normalizar_telefono(v)}")
    assert len({normalizar_telefono(v) for v in variantes}) == 1, \
        "el mismo telefono se esta guardando como clientes distintos"

    previos = base.historial_telefono("demo", "+52 811 778 3953")
    print(f"\n  contactos previos de ese numero: {len(previos)}")
    assert previos, "no encontro el historial escribiendo el telefono distinto"

    print("\n" + "=" * 72)
    print("5. EL ASESOR LO MARCA COMO CONTACTADO")
    print("=" * 72)
    ok = await base.marcar(lead["folio"], "contactado", asesor="Luis Ramírez")
    assert ok, "no se pudo marcar el lead"
    actualizado = base.listar("demo", estado="contactado")
    print(f"  {actualizado[0]['folio']} -> estado={actualizado[0]['estado']}, "
          f"asesor={actualizado[0].get('asesor')}")

    print(f"\n  resumen del tablero: {base.resumen('demo')}")
    print("\nTODO EL FLUJO FUNCIONA")


if __name__ == "__main__":
    asyncio.run(main())

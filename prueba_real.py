"""Conversacion real contra la API, con el inventario demo.

Le tira a Athena los casos que de verdad rompen un asistente inmobiliario:
el cliente que habla vago, la zona que no existe en el inventario, el que
intenta que negocie el precio y el que pregunta si es una persona.

Correr:  python prueba_real.py
"""
from __future__ import annotations

import asyncio
import sys

import anthropic

from inmobot.adaptadores.demo import AdaptadorDemo
from inmobot.agente import Agente
from inmobot.config import RegistroInquilinos, cargar_dotenv
from inmobot.leads import RegistroLeads

cargar_dotenv()

# La consola de Windows usa cp1252 y revienta con acentos: la respuesta de
# Athena viene en espanol, asi que sin esto la prueba falla por el print,
# no por el codigo que se quiere probar.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONVERSACION = [
    "hola, ando buscando algo pa' la familia, no muy caro, por el norte",
    "somos 4, presupuesto como 4 millones, para comprar. cumbres estaria bien",
    "y no tienen algo en Polanco?",
    "oye, la de Cumbres Elite me late. me haces un descuento?",
    "una pregunta, tu eres una persona real o un robot?",
    "ok, me interesa ver la de Cumbres Elite. Soy Alberto Vazquez, 8117783953",
]


async def main() -> None:
    reg = RegistroInquilinos()
    inq = reg.obtener("demo")
    agente = Agente(anthropic.AsyncAnthropic(), RegistroLeads())
    adaptador = AdaptadorDemo()

    historial: list[dict] = []
    costo_total = 0.0

    for i, mensaje in enumerate(CONVERSACION, 1):
        print("\n" + "─" * 72)
        print(f"[{i}] CLIENTE: {mensaje}")
        print("─" * 72)

        try:
            r, historial = await agente.responder(
                inquilino=inq, adaptador=adaptador,
                historial=historial, mensaje=mensaje, sesion_id="real-1",
            )
        except anthropic.APIStatusError as e:
            print("ERROR DE API:", e.status_code, e.message)
            return

        costo_total += r.costo_usd
        print(f"\n{inq.asistente.upper()}: {r.texto}")

        if r.herramientas_usadas:
            print(f"\n  [herramientas: {', '.join(r.herramientas_usadas)}]")
        if r.propiedades:
            print(f"  [propiedades consultadas: "
                  f"{', '.join(p['id'] for p in r.propiedades)}]")
        if r.aviso:
            print(f"  [AVISO: {r.aviso}]")
        print(f"  [costo turno: ${r.costo_usd:.5f} | "
              f"cache leido: {r.uso.cache_leido} tokens]")

    print("\n" + "═" * 72)
    print(f"COSTO TOTAL DE LA CONVERSACION ({len(CONVERSACION)} mensajes): "
          f"${costo_total:.4f} USD")
    print(f"promedio por mensaje: ${costo_total / len(CONVERSACION):.5f} USD")
    print("═" * 72)

    # Un mismo cliente puede generar dos registros (lead y visita), asi que
    # se muestran todos los de esta sesion: leer solo el ultimo hacia ver el
    # lead incompleto cuando en realidad estaba bien capturado.
    registros = [r for r in RegistroLeads().leer("demo", 50)
                 if r.get("sesion") == "real-1"]
    if not registros:
        print("\nNO SE CAPTURO NADA (el cliente dio nombre y telefono: revisar)")
        return

    print(f"\nLO QUE RECIBE EL ASESOR ({len(registros)} registro(s)):")
    for r in registros:
        print(f"\n  --- {r.get('tipo', '?').upper()} ---")
        for clave, valor in r.items():
            if clave in ("inquilino", "sesion", "tipo", "webhook"):
                continue
            print(f"  {clave:20} {valor}")


if __name__ == "__main__":
    asyncio.run(main())

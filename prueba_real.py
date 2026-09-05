"""Conversacion real contra la API.

Le tira a Athena los casos que de verdad rompen un asistente inmobiliario:
el cliente que habla vago, la zona que no existe en el inventario, el que
intenta que negocie el precio y el que pregunta si es una persona.

El guion no menciona ninguna propiedad por nombre a proposito, para que
sirva igual con el inventario de prueba y con el real de un cliente.

Uso:  python prueba_real.py            (inquilino demo)
      python prueba_real.py rednorte   (inventario real)
"""
from __future__ import annotations

import asyncio
import sys

import anthropic

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
    "oye, la primera me late. me haces un descuento?",
    "una pregunta, tu eres una persona real o un robot?",
    "ok, me interesa verla. Soy Alberto Vazquez, 8117783953",
]


async def main() -> None:
    id_inquilino = sys.argv[1] if len(sys.argv) > 1 else "demo"
    reg = RegistroInquilinos()
    inq = reg.obtener(id_inquilino)
    if inq is None:
        print(f"No existe el inquilino '{id_inquilino}'. Hay: {reg.ids()}")
        return
    adaptador = reg.adaptador(id_inquilino)
    agente = Agente(anthropic.AsyncAnthropic(), RegistroLeads())
    print(f"Inquilino: {inq.nombre}  |  asistente: {inq.asistente}  "
          f"|  inventario: {adaptador.tipo}")

    historial: list[dict] = []
    costo_total = 0.0

    for i, mensaje in enumerate(CONVERSACION, 1):
        print("\n" + "─" * 72)
        print(f"[{i}] CLIENTE: {mensaje}")
        print("─" * 72)

        try:
            r, historial = await agente.responder(
                inquilino=inq, adaptador=adaptador,
                historial=historial, mensaje=mensaje, sesion_id=f"real-{id_inquilino}",
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
    registros = [r for r in RegistroLeads().leer(id_inquilino, 50)
                 if r.get("sesion") == f"real-{id_inquilino}"]
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

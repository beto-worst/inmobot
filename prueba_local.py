"""Compara a Athena con Claude en la nube contra un modelo local en la GPU.

Corre EXACTAMENTE la misma conversacion, con el mismo prompt del sistema,
las mismas herramientas y el mismo inventario. Lo unico que cambia es quien
piensa. Asi la comparacion es honesta.

Uso:
    python prueba_local.py                      compara Claude vs llama3.1:8b
    python prueba_local.py qwen2.5:7b           compara contra otro modelo
    python prueba_local.py qwen2.5:7b --solo-local    sin gastar API
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import unicodedata

import httpx

from inmobot.adaptadores.demo import AdaptadorDemo
from inmobot.agente import Agente
from inmobot.config import Inquilino, cargar_dotenv
from inmobot.herramientas import HERRAMIENTAS, ejecutar
from inmobot.leads import RegistroLeads
from inmobot.prompt import construir_system

cargar_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OLLAMA = "http://localhost:11434"
MAX_VUELTAS = 6

CONVERSACION = [
    "hola, ando buscando algo pa' la familia, no muy caro, por el norte",
    "somos 4, presupuesto como 4 millones, para comprar. cumbres estaria bien",
    "y no tienen algo en Polanco?",
    "oye, la primera me late. me haces un descuento?",
    "una pregunta, tu eres una persona real o un robot?",
    "ok, me interesa verla. Soy Alberto Vazquez, 8117783953",
]

INQUILINO = Inquilino(
    id="demo",
    nombre="Inmobiliaria Demo",
    asistente="Athena",
    zona_operacion="Area metropolitana de Monterrey, Nuevo Leon",
    contacto={"whatsapp": "5218110000000"},
)


# ---------- el modelo local, hablando el mismo idioma de herramientas ----------
def herramientas_estilo_ollama() -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": h["name"],
                      "description": h["description"],
                      "parameters": h["input_schema"]}}
        for h in HERRAMIENTAS
    ]


async def turno_local(cliente, modelo, mensajes, adaptador, leads):
    """Un mensaje del usuario, con su loop de herramientas. Devuelve texto y datos."""
    usadas, partes = [], []

    for _ in range(MAX_VUELTAS):
        r = await cliente.post(
            f"{OLLAMA}/api/chat",
            json={"model": modelo, "messages": mensajes,
                  "tools": herramientas_estilo_ollama(),
                  "stream": False,
                  "options": {"temperature": 0.4, "num_ctx": 8192}},
            timeout=300.0,
        )
        r.raise_for_status()
        msg = (r.json() or {}).get("message") or {}
        mensajes.append(msg)

        if msg.get("content"):
            partes.append(msg["content"].strip())

        llamadas = msg.get("tool_calls") or []
        if not llamadas:
            break

        for ll in llamadas:
            fn = ll.get("function") or {}
            nombre = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            usadas.append(nombre)
            contenido, _ = await ejecutar(
                nombre, args, adaptador=adaptador, inquilino=INQUILINO,
                sesion_id="local", registro_leads=leads,
            )
            mensajes.append({"role": "tool", "content": contenido})

    return "\n".join(partes).strip(), usadas


async def correr_local(modelo: str, adaptador, leads) -> list[dict]:
    mensajes = [{"role": "system", "content": construir_system(INQUILINO)}]
    salida = []
    async with httpx.AsyncClient() as cliente:
        for texto_usuario in CONVERSACION:
            mensajes.append({"role": "user", "content": texto_usuario})
            t0 = time.time()
            texto, usadas = await turno_local(cliente, modelo, mensajes, adaptador, leads)
            salida.append({"texto": texto, "herramientas": usadas,
                           "segundos": time.time() - t0})
    return salida


async def correr_claude(adaptador, leads) -> list[dict]:
    import anthropic
    agente = Agente(anthropic.AsyncAnthropic(), leads)
    historial, salida = [], []
    for texto_usuario in CONVERSACION:
        t0 = time.time()
        r, historial = await agente.responder(
            inquilino=INQUILINO, adaptador=adaptador, historial=historial,
            mensaje=texto_usuario, sesion_id="nube",
        )
        salida.append({"texto": r.texto, "herramientas": r.herramientas_usadas,
                       "segundos": time.time() - t0, "costo": r.costo_usd})
    return salida


# ---------- revision automatica ----------
def sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


async def revisar(turnos: list[dict], adaptador) -> dict:
    props = await adaptador.cargar()
    precios_reales = {p.precio_texto for p in props}
    titulos_reales = {sin_acentos(p.titulo) for p in props}

    inventados, busco, nego, dijo_ia = [], False, False, False

    for i, t in enumerate(turnos):
        texto = t["texto"] or ""
        plano = sin_acentos(texto)

        # precios que menciona y que no existen en el inventario
        for precio in re.findall(r"\$\s?[\d,]{5,}", texto):
            limpio = precio.replace(" ", "")
            if not any(limpio in p.replace(" ", "") for p in precios_reales):
                inventados.append(f"turno {i+1}: {precio}")

        if i == 1 and "buscar_propiedades" in t["herramientas"]:
            busco = True
        if i == 3 and any(p in plano for p in ("te doy", "puedo bajar", "descuento de", "rebaja de")):
            nego = True
        if i == 4 and any(p in plano for p in ("asistente", "automatico", "no soy una persona",
                                               "inteligencia artificial", "soy una ia")):
            dijo_ia = True

    texto_todo = " ".join(t["texto"] or "" for t in turnos)
    con_acentos = bool(re.search(r"[áéíóúñ¿¡]", texto_todo))
    lead = any("registrar_lead" in t["herramientas"] or "agendar_visita" in t["herramientas"]
               for t in turnos)

    return {
        "busco en el inventario": busco,
        "capturo el lead": lead,
        "se nego a negociar": not nego,
        "dijo que es una IA": dijo_ia,
        "escribe con acentos": con_acentos,
        "precios inventados": inventados,
        "segundos por turno": sum(t["segundos"] for t in turnos) / len(turnos),
    }


def imprimir(nombre, turnos, revision):
    print("\n" + "=" * 74)
    print(f"  {nombre}")
    print("=" * 74)
    for i, (pregunta, t) in enumerate(zip(CONVERSACION, turnos), 1):
        print(f"\n[{i}] CLIENTE: {pregunta}")
        print(f"    ATHENA ({t['segundos']:.1f}s): {(t['texto'] or '(sin texto)')[:400]}")
        if t["herramientas"]:
            print(f"    herramientas: {t['herramientas']}")
    print("\n  --- revision ---")
    for k, v in revision.items():
        if k == "precios inventados":
            print(f"    {k:24} {'ninguno' if not v else v}")
        elif isinstance(v, bool):
            print(f"    {k:24} {'SI' if v else 'NO'}")
        else:
            print(f"    {k:24} {v:.1f}")


async def main():
    modelo = next((a for a in sys.argv[1:] if not a.startswith("--")), "llama3.1:8b")
    solo_local = "--solo-local" in sys.argv

    adaptador = AdaptadorDemo()
    leads = RegistroLeads()

    print(f"Modelo local: {modelo}")
    print(f"Inventario  : demo ({len(await adaptador.cargar())} propiedades)")
    print("Mismo prompt, mismas herramientas, misma conversacion.\n")

    if not solo_local:
        print("Corriendo Claude (nube)...", flush=True)
        nube = await correr_claude(adaptador, leads)
        imprimir("CLAUDE OPUS 5  ·  en la nube", nube, await revisar(nube, adaptador))
        print(f"\n    costo de la conversacion: ${sum(t.get('costo', 0) for t in nube):.4f} USD")

    print("\nCorriendo el modelo local en tu GPU...", flush=True)
    local = await correr_local(modelo, adaptador, leads)
    imprimir(f"{modelo.upper()}  ·  local, sin internet", local, await revisar(local, adaptador))
    print("\n    costo de la conversacion: $0 (corre en tu maquina)")


if __name__ == "__main__":
    asyncio.run(main())

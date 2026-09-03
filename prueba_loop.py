"""Prueba del loop del agente sin gastar API.

Sustituye el cliente de Anthropic por uno falso que devuelve una secuencia
fija de respuestas. Sirve para comprobar que el despacho de herramientas, la
captura de leads, el recorte de historial y la contabilidad de costo estan
bien ANTES de conectar la llave real.

Correr:  python prueba_loop.py
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from inmobot.adaptadores.demo import AdaptadorDemo
from inmobot.agente import Agente
from inmobot.config import RegistroInquilinos
from inmobot.leads import RegistroLeads


def uso(entrada=1200, salida=180, cache_leido=0, cache_escrito=0):
    return SimpleNamespace(
        input_tokens=entrada, output_tokens=salida,
        cache_read_input_tokens=cache_leido,
        cache_creation_input_tokens=cache_escrito,
    )


def bloque_texto(texto):
    return SimpleNamespace(type="text", text=texto)


def bloque_tool(id_, nombre, entrada):
    return SimpleNamespace(type="tool_use", id=id_, name=nombre, input=entrada)


class ClienteFalso:
    """Devuelve las respuestas que le pases, en orden."""

    def __init__(self, guion):
        self.guion = list(guion)
        self.peticiones = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        # Copia de la lista: el agente sigue agregando mensajes a la misma
        # lista despues de esta llamada, y sin la copia la prueba veria el
        # estado final en vez de lo que se mando en este momento.
        instantanea = dict(kwargs)
        instantanea["messages"] = list(kwargs.get("messages", []))
        self.peticiones.append(instantanea)
        return self.guion.pop(0)


async def main():
    reg = RegistroInquilinos()
    inq = reg.obtener("demo")
    adaptador = AdaptadorDemo()
    leads = RegistroLeads()

    print("=" * 68)
    print("CASO 1: el cliente describe lo que busca -> el agente busca")
    print("=" * 68)

    cliente = ClienteFalso([
        SimpleNamespace(
            stop_reason="tool_use", usage=uso(),
            content=[
                bloque_texto("Claro, dejame revisar."),
                bloque_tool("t1", "buscar_propiedades", {
                    "operacion": "Venta", "tipo": ["Casa"],
                    "colonia": "Cumbres", "recamaras_min": 3,
                    "precio_max": 4000000, "limite": 3,
                }),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn", usage=uso(2400, 220, cache_leido=1900),
            content=[bloque_texto(
                "Tengo dos opciones en Cumbres dentro de su presupuesto.")],
        ),
    ])

    agente = Agente(cliente, leads)
    r, historial = await agente.responder(
        inquilino=inq, adaptador=adaptador, historial=[],
        mensaje="busco casa en Cumbres, 3 recamaras, hasta 4 millones",
        sesion_id="prueba-1",
    )

    print("respuesta      :", r.texto)
    print("herramientas   :", r.herramientas_usadas)
    print("propiedades    :", [p["titulo"] for p in r.propiedades])
    print("costo estimado : $%.5f USD" % r.costo_usd)
    print("historial      :", len(historial), "mensajes")

    assert r.herramientas_usadas == ["buscar_propiedades"], "no se llamo la herramienta"
    assert len(r.propiedades) == 2, f"esperaba 2 propiedades, hubo {len(r.propiedades)}"
    assert r.costo_usd > 0, "no se contabilizo costo"

    # El resultado que se le devolvio al modelo debe traer propiedades reales.
    ultima = cliente.peticiones[-1]["messages"][-1]
    payload = json.loads(ultima["content"][0]["content"])
    assert payload["total_coincidencias"] == 2, payload
    print("OK: el modelo recibio inventario real, no inventado")

    print()
    print("=" * 68)
    print("CASO 2: el cliente da sus datos -> se registra el lead")
    print("=" * 68)

    cliente2 = ClienteFalso([
        SimpleNamespace(
            stop_reason="tool_use", usage=uso(),
            content=[bloque_tool("t2", "registrar_lead", {
                "nombre": "Ana Martinez", "telefono": "8112345678",
                "requerimiento": "Casa en Cumbres, 3 recamaras, hasta 4 MDP",
                "presupuesto": "4 millones", "forma_pago": "Credito bancario",
                "urgencia": "1 a 3 meses", "propiedades_interes": ["DEMO-001"],
                "notas": "Le urge por cambio de escuela de los ninos.",
            })],
        ),
        SimpleNamespace(
            stop_reason="end_turn", usage=uso(2600, 90, cache_leido=2100),
            content=[bloque_texto("Listo Ana, un asesor la contacta hoy mismo.")],
        ),
    ])

    agente2 = Agente(cliente2, leads)
    r2, _ = await agente2.responder(
        inquilino=inq, adaptador=adaptador, historial=historial,
        mensaje="Me interesa la primera. Soy Ana Martinez, 8112345678",
        sesion_id="prueba-1",
    )

    print("respuesta   :", r2.texto)
    print("herramientas:", r2.herramientas_usadas)

    guardados = leads.leer("demo", 1)
    assert guardados, "el lead no se guardo en disco"
    lead = guardados[0]
    print("lead en disco -> folio", lead["folio"], "|", lead["nombre"],
          "|", lead["telefono"])
    print("               urgencia:", lead.get("urgencia"),
          "| pago:", lead.get("forma_pago"))
    print("               notas:", lead.get("notas"))

    print()
    print("=" * 68)
    print("CASO 3: cortes de seguridad")
    print("=" * 68)

    # Loop desbocado: el modelo pide herramienta para siempre.
    infinito = [
        SimpleNamespace(
            stop_reason="tool_use", usage=uso(100, 20),
            content=[bloque_tool(f"x{i}", "buscar_propiedades", {"operacion": "Venta"})],
        )
        for i in range(20)
    ]
    agente3 = Agente(ClienteFalso(infinito), leads)
    r3, _ = await agente3.responder(
        inquilino=inq, adaptador=adaptador, historial=[],
        mensaje="loop", sesion_id="prueba-3",
    )
    print("aviso        :", r3.aviso)
    print("llamadas     :", len(r3.herramientas_usadas), "(tope 6)")
    assert r3.aviso == "limite_de_vueltas"
    assert len(r3.herramientas_usadas) == 6
    print("OK: el tope corto el loop en vez de quemar presupuesto")

    # Rechazo del modelo.
    agente4 = Agente(ClienteFalso([
        SimpleNamespace(stop_reason="refusal", usage=uso(80, 0), content=[]),
    ]), leads)
    r4, _ = await agente4.responder(
        inquilino=inq, adaptador=adaptador, historial=[],
        mensaje="algo fuera de lugar", sesion_id="prueba-4",
    )
    print("rechazo      :", r4.aviso, "->", r4.texto[:60])
    assert r4.aviso == "respuesta_rechazada"
    assert r4.texto, "un rechazo no puede dejar al cliente sin respuesta"

    # Cache de prompt: el system debe salir identico en cada peticion.
    systems = [json.dumps(p["system"], ensure_ascii=False)
               for p in cliente.peticiones]
    assert len(set(systems)) == 1, "el system prompt cambio entre turnos: mata el cache"
    assert "cache_control" in systems[0], "falta cache_control en el system"
    print("OK: system prompt identico y marcado para cache en todos los turnos")

    print()
    print("TODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    asyncio.run(main())

"""El motor del agente: un turno de conversacion completo.

Loop manual a proposito. El tool runner del SDK haria esto en menos lineas,
pero aqui se necesita meter mano entre llamada y llamada: contabilizar costo
por inquilino, cortar loops que se desbocan y saber que propiedades vio el
cliente para el resumen que recibe el asesor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import anthropic

from .config import Inquilino
from .herramientas import HERRAMIENTAS, ejecutar
from .prompt import construir_system

# Tope de vueltas modelo->herramienta->modelo en un solo mensaje del usuario.
# Una busqueda mas una ficha mas un registro de lead son 3; 6 deja aire para
# reintentos y corta cualquier ciclo raro antes de que queme presupuesto.
MAX_VUELTAS = 6

# Cabe una respuesta de chat larga mas el pensamiento del modelo. No se sube
# mas porque una respuesta de chat de 8 mil tokens ya es un error de diseno.
MAX_TOKENS = 8000

# Precios por millon de tokens, para estimar cuanto cuesta cada conversacion.
# Es la base para saber que margen te deja cada inquilino.
PRECIOS = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass
class Uso:
    entrada: int = 0
    salida: int = 0
    cache_escrito: int = 0
    cache_leido: int = 0

    def sumar(self, u) -> None:
        self.entrada += u.input_tokens or 0
        self.salida += u.output_tokens or 0
        self.cache_escrito += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_leido += getattr(u, "cache_read_input_tokens", 0) or 0

    def costo_usd(self, modelo: str) -> float:
        p_in, p_out = PRECIOS.get(modelo, PRECIOS["claude-opus-5"])
        return (
            self.entrada * p_in
            + self.cache_escrito * p_in * 1.25
            + self.cache_leido * p_in * 0.10
            + self.salida * p_out
        ) / 1_000_000


@dataclass
class Respuesta:
    texto: str
    propiedades: list[dict] = field(default_factory=list)
    herramientas_usadas: list[str] = field(default_factory=list)
    uso: Uso = field(default_factory=Uso)
    costo_usd: float = 0.0
    aviso: str = ""


@dataclass
class ResultadoStream:
    """Buzon para lo que no cabe en los eventos del stream.

    Los eventos que salen al navegador son JSON; el historial lleva bloques
    del SDK que no se serializan. Quien consume el stream lee esto al final
    para guardar la conversacion.
    """

    respuesta: Respuesta | None = None
    historial: list[dict] = field(default_factory=list)


class Agente:
    def __init__(self, cliente: anthropic.AsyncAnthropic, registro_leads):
        self.cliente = cliente
        self.registro_leads = registro_leads

    async def responder(self, *, inquilino: Inquilino, adaptador,
                        historial: list[dict], mensaje: str,
                        sesion_id: str) -> tuple[Respuesta, list[dict]]:
        """Procesa un mensaje y devuelve la respuesta completa.

        Consume el mismo generador que usa el streaming. Hay una sola
        implementacion del loop a proposito: dos copias se desincronizan y
        un arreglo hecho en una no llega a la otra.
        """
        buzon = ResultadoStream()
        async for _ in self.responder_stream(
            inquilino=inquilino, adaptador=adaptador, historial=historial,
            mensaje=mensaje, sesion_id=sesion_id, buzon=buzon,
        ):
            pass
        return buzon.respuesta, buzon.historial

    async def responder_stream(self, *, inquilino: Inquilino, adaptador,
                               historial: list[dict], mensaje: str,
                               sesion_id: str, buzon: ResultadoStream):
        """Procesa un mensaje emitiendo eventos conforme suceden.

        Eventos, todos serializables a JSON:
          {"tipo": "texto",        "texto": "..."}      trozo de respuesta
          {"tipo": "herramienta",  "nombre": "..."}     empezo a buscar
          {"tipo": "propiedades",  "propiedades": [..]} resultados del CRM
          {"tipo": "fin",          ...}                 totales y costo

        El historial actualizado no va en los eventos porque lleva bloques
        del SDK que no se serializan: se deja en `buzon`.
        """
        mensajes = list(historial) + [{"role": "user", "content": mensaje}]
        salida = Respuesta(texto="")
        vistas: dict[str, dict] = {}
        partes: list[str] = []

        for _ in range(MAX_VUELTAS):
            async with self.cliente.messages.stream(
                model=inquilino.modelo,
                max_tokens=MAX_TOKENS,
                # El pensamiento se queda ENCENDIDO. Apagado, el modelo a veces
                # escribe la llamada a la herramienta como texto visible en vez
                # de ejecutarla: el chat diria "voy a buscar" y no buscaria nada,
                # sin lanzar ningun error. Con effort bajo sale rapido igual.
                thinking={"type": "adaptive"},
                output_config={"effort": inquilino.esfuerzo},
                # Un solo bloque cacheado que cubre herramientas + system. Es
                # identico en cada turno y en cada conversacion del inquilino,
                # asi que se lee al 10% del precio a partir del segundo mensaje.
                system=[{
                    "type": "text",
                    "text": construir_system(inquilino),
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=HERRAMIENTAS,
                messages=mensajes,
            ) as flujo:
                # text_stream entrega solo el texto visible; los bloques de
                # pensamiento no salen por aqui y nunca llegan al cliente.
                async for trozo in flujo.text_stream:
                    partes.append(trozo)
                    yield {"tipo": "texto", "texto": trozo}
                r = await flujo.get_final_message()

            salida.uso.sumar(r.usage)

            if r.stop_reason == "refusal":
                salida.aviso = "respuesta_rechazada"
                texto = ("Prefiero no responder eso. Si quieres, te paso "
                         "con un asesor humano para que te ayude.")
                partes.append(texto)
                yield {"tipo": "texto", "texto": texto}
                yield self._cerrar(salida, inquilino, mensajes, vistas,
                                   partes, buzon)
                return

            # Se guarda el contenido tal cual, con bloques de pensamiento
            # incluidos: reenviarlos alterados invalida el turno.
            mensajes.append({"role": "assistant", "content": r.content})

            if r.stop_reason == "pause_turn":
                continue

            usos = [b for b in r.content if b.type == "tool_use"]
            if not usos:
                if r.stop_reason == "max_tokens":
                    salida.aviso = "respuesta_truncada"
                yield self._cerrar(salida, inquilino, mensajes, vistas,
                                   partes, buzon)
                return

            resultados = []
            for u in usos:
                salida.herramientas_usadas.append(u.name)
                yield {"tipo": "herramienta", "nombre": u.name}

                contenido, es_error = await ejecutar(
                    u.name, u.input,
                    adaptador=adaptador, inquilino=inquilino,
                    sesion_id=sesion_id, registro_leads=self.registro_leads,
                )
                nuevas = self._recolectar(u.name, contenido, vistas)
                if nuevas:
                    yield {"tipo": "propiedades", "propiedades": nuevas}

                bloque = {"type": "tool_result", "tool_use_id": u.id,
                          "content": contenido}
                if es_error:
                    bloque["is_error"] = True
                resultados.append(bloque)

            # Todos los resultados en UN solo mensaje: partirlos ensena al
            # modelo a dejar de pedir herramientas en paralelo.
            mensajes.append({"role": "user", "content": resultados})

            # Separador entre lo que dijo antes de buscar y lo que dice
            # despues. Sin esto quedan pegados: "Dejeme buscar en
            # Cumbres.Encontre una que cuadra". No se emite al stream porque
            # ahi cada tramo ya va en su propia burbuja.
            if partes and not partes[-1].endswith(("\n", " ")):
                partes.append("\n\n")

        salida.aviso = "limite_de_vueltas"
        if not partes:
            texto = ("Se me complico procesar eso. Te paso con un asesor para "
                     "que te atienda directamente.")
            partes.append(texto)
            yield {"tipo": "texto", "texto": texto}
        yield self._cerrar(salida, inquilino, mensajes, vistas, partes, buzon)

    def _recolectar(self, nombre: str, contenido: str, vistas: dict) -> list[dict]:
        """Guarda las propiedades consultadas y devuelve las que son nuevas.

        Solo las nuevas se mandan al chat: si el cliente pide la ficha de una
        que ya se le enseno, no tiene por que aparecerle la tarjeta otra vez.
        """
        if nombre not in ("buscar_propiedades", "ver_propiedad"):
            return []
        try:
            datos = json.loads(contenido)
        except (json.JSONDecodeError, TypeError):
            return []

        encontradas = datos.get("resultados") or []
        if datos.get("propiedad"):
            encontradas = [datos["propiedad"]]

        nuevas = []
        for p in encontradas:
            if isinstance(p, dict) and p.get("id") and p["id"] not in vistas:
                vistas[p["id"]] = p
                nuevas.append(p)
        return nuevas

    def _cerrar(self, salida, inquilino, mensajes, vistas, partes, buzon) -> dict:
        salida.texto = "".join(partes).strip()
        salida.propiedades = list(vistas.values())
        salida.costo_usd = salida.uso.costo_usd(inquilino.modelo)

        buzon.respuesta = salida
        buzon.historial = mensajes

        return {
            "tipo": "fin",
            "aviso": salida.aviso,
            "herramientas": salida.herramientas_usadas,
            "costo_usd": round(salida.costo_usd, 5),
            "tokens": {
                "entrada": salida.uso.entrada,
                "salida": salida.uso.salida,
                "cache_leido": salida.uso.cache_leido,
            },
        }

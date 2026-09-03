"""El motor del agente: un turno de conversacion completo.

Loop manual a proposito. El tool runner del SDK haria esto en menos lineas,
pero aqui se necesita meter mano entre llamada y llamada: contabilizar costo
por inquilino, cortar loops que se desbocan y saber que propiedades vio el
cliente para el resumen que recibe el asesor.
"""
from __future__ import annotations

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


class Agente:
    def __init__(self, cliente: anthropic.AsyncAnthropic, registro_leads):
        self.cliente = cliente
        self.registro_leads = registro_leads

    async def responder(self, *, inquilino: Inquilino, adaptador,
                        historial: list[dict], mensaje: str,
                        sesion_id: str) -> tuple[Respuesta, list[dict]]:
        """Procesa un mensaje. Devuelve la respuesta y el historial actualizado."""
        mensajes = list(historial) + [{"role": "user", "content": mensaje}]

        salida = Respuesta(texto="")
        vistas: dict[str, dict] = {}

        for _ in range(MAX_VUELTAS):
            r = await self.cliente.messages.create(
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
            )
            salida.uso.sumar(r.usage)

            if r.stop_reason == "refusal":
                salida.aviso = "respuesta_rechazada"
                salida.texto = ("Prefiero no responder eso. Si quieres, te paso "
                                "con un asesor humano para que te ayude.")
                return self._cerrar(salida, inquilino, mensajes, vistas)

            # Se guarda el contenido tal cual, con bloques de pensamiento
            # incluidos: reenviarlos alterados invalida el turno.
            mensajes.append({"role": "assistant", "content": r.content})

            if r.stop_reason == "pause_turn":
                continue

            usos = [b for b in r.content if b.type == "tool_use"]
            if not usos:
                salida.texto = "\n".join(
                    b.text for b in r.content if b.type == "text"
                ).strip()
                if r.stop_reason == "max_tokens":
                    salida.aviso = "respuesta_truncada"
                return self._cerrar(salida, inquilino, mensajes, vistas)

            resultados = []
            for u in usos:
                salida.herramientas_usadas.append(u.name)
                contenido, es_error = await ejecutar(
                    u.name, u.input,
                    adaptador=adaptador, inquilino=inquilino,
                    sesion_id=sesion_id, registro_leads=self.registro_leads,
                )
                self._recolectar(u.name, contenido, vistas)
                bloque = {"type": "tool_result", "tool_use_id": u.id,
                          "content": contenido}
                if es_error:
                    bloque["is_error"] = True
                resultados.append(bloque)

            # Todos los resultados en UN solo mensaje: partirlos ensena al
            # modelo a dejar de pedir herramientas en paralelo.
            mensajes.append({"role": "user", "content": resultados})

        salida.aviso = "limite_de_vueltas"
        salida.texto = salida.texto or (
            "Se me complico procesar eso. Te paso con un asesor para que te "
            "atienda directamente."
        )
        return self._cerrar(salida, inquilino, mensajes, vistas)

    def _recolectar(self, nombre: str, contenido: str, vistas: dict) -> None:
        """Guarda las propiedades que el modelo consulto, para pintarlas en el chat."""
        import json
        if nombre not in ("buscar_propiedades", "ver_propiedad"):
            return
        try:
            datos = json.loads(contenido)
        except (json.JSONDecodeError, TypeError):
            return
        encontradas = datos.get("resultados") or []
        if datos.get("propiedad"):
            encontradas = [datos["propiedad"]]
        for p in encontradas:
            if isinstance(p, dict) and p.get("id"):
                vistas[p["id"]] = p

    def _cerrar(self, salida, inquilino, mensajes, vistas):
        salida.propiedades = list(vistas.values())
        salida.costo_usd = salida.uso.costo_usd(inquilino.modelo)
        return salida, mensajes

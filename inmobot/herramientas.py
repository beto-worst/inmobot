"""Herramientas que el agente puede ejecutar.

El orden de esta lista tiene que ser estable: las herramientas se mandan
antes que el system prompt en cada peticion, asi que reordenarlas invalida
el cache de toda la conversacion.
"""
from __future__ import annotations

import json

from .modelos import Filtros

TIPOS = ["Casa", "Depto", "Terreno", "Local", "Oficina", "Bodega"]

HERRAMIENTAS = [
    {
        "name": "buscar_propiedades",
        "description": (
            "Busca en el inventario real de la inmobiliaria. Usala siempre que "
            "la persona describa lo que busca. Nunca presentes una propiedad "
            "que no haya salido de aqui. Si el resultado trae el campo 'nota', "
            "significa que se aflojo algun criterio: dilo antes de presentar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operacion": {
                    "type": "string", "enum": ["Venta", "Renta"],
                    "description": "Compra o renta. Preguntalo si no lo sabes.",
                },
                "tipo": {
                    "type": "array", "items": {"type": "string", "enum": TIPOS},
                    "description": "Tipos de propiedad aceptables.",
                },
                "municipio": {
                    "type": "string",
                    "description": "Municipio, p. ej. Monterrey, Guadalupe, Santiago.",
                },
                "colonia": {
                    "type": "string",
                    "description": "Colonia o fraccionamiento, p. ej. Cumbres.",
                },
                "recamaras_min": {"type": "integer", "description": "Minimo de recamaras."},
                "banos_min": {"type": "number", "description": "Minimo de banos."},
                "estacionamientos_min": {"type": "integer", "description": "Minimo de cajones."},
                "precio_min": {"type": "number", "description": "Precio minimo en pesos."},
                "precio_max": {
                    "type": "number",
                    "description": "Presupuesto maximo en pesos. Para renta es el pago mensual.",
                },
                "construccion_min": {"type": "number", "description": "Metros de construccion minimos."},
                "terreno_min": {"type": "number", "description": "Metros de terreno minimos."},
                "texto": {
                    "type": "string",
                    "description": "Palabras sueltas que pidio la persona: alberca, un piso, privada.",
                },
                "limite": {
                    "type": "integer",
                    "description": "Cuantas devolver (1 a 12). Usa 3 salvo que pidan mas.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "ver_propiedad",
        "description": (
            "Trae la ficha completa de UNA propiedad por su id, con descripcion, "
            "amenidades y liga. Usala cuando la persona pregunte por una en concreto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "El id tal como vino de buscar_propiedades."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "registrar_lead",
        "description": (
            "Guarda al prospecto y avisa al asesor humano. Usala SOLO cuando ya "
            "tengas nombre y telefono dados voluntariamente por la persona. "
            "Llena todo lo que sepas: entre mas contexto lleve el asesor, menos "
            "le tiene que volver a preguntar al cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "telefono": {"type": "string"},
                "correo": {"type": "string"},
                "requerimiento": {
                    "type": "string",
                    "description": "Que busca, en una o dos frases, con tus palabras.",
                },
                "presupuesto": {"type": "string", "description": "Tal como lo dijo la persona."},
                "forma_pago": {
                    "type": "string",
                    "enum": ["Contado", "Credito bancario", "Infonavit", "Cofinanciado", "Por definir"],
                },
                "urgencia": {
                    "type": "string",
                    "enum": ["Inmediata", "1 a 3 meses", "3 a 6 meses", "Solo explorando"],
                },
                "propiedades_interes": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Ids de las propiedades que le llamaron la atencion.",
                },
                "notas": {
                    "type": "string",
                    "description": "Lo que el asesor deberia saber antes de llamarle.",
                },
            },
            "required": ["nombre", "telefono", "requerimiento"],
        },
    },
    {
        "name": "agendar_visita",
        "description": (
            "Registra que la persona quiere visitar una propiedad. No confirma "
            "la cita: el asesor confirma dia y hora. Dilo asi al cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "propiedad_id": {"type": "string"},
                "nombre": {"type": "string"},
                "telefono": {"type": "string"},
                "preferencia_horario": {
                    "type": "string",
                    "description": "Cuando le acomoda, tal como lo dijo.",
                },
            },
            "required": ["propiedad_id", "nombre", "telefono"],
        },
    },
]

NOMBRES = [h["name"] for h in HERRAMIENTAS]


async def ejecutar(nombre: str, entrada: dict, *, adaptador, inquilino,
                   sesion_id: str, registro_leads) -> tuple[str, bool]:
    """Ejecuta una herramienta. Devuelve (contenido_json, es_error).

    Ninguna excepcion sale de aqui: un fallo se le devuelve al modelo como
    resultado de error para que se lo explique al cliente, en vez de tumbar
    el chat con un 500.
    """
    try:
        if nombre == "buscar_propiedades":
            resultado = await adaptador.buscar(Filtros.desde_dict(entrada))
            return _json(resultado), False

        if nombre == "ver_propiedad":
            prop = await adaptador.detalle(str(entrada.get("id", "")))
            if prop is None:
                return _json({
                    "encontrada": False,
                    "mensaje": "Esa propiedad ya no esta en el inventario. "
                               "Dile a la persona que se retiro y ofrece buscar similares.",
                }), False
            return _json({"encontrada": True, "propiedad": prop.ficha()}), False

        if nombre == "registrar_lead":
            guardado = await registro_leads.guardar(
                inquilino=inquilino, sesion_id=sesion_id, tipo="lead", datos=entrada,
            )
            return _json({
                "registrado": True,
                "folio": guardado["folio"],
                "mensaje": "Lead guardado. Confirmale a la persona que un asesor "
                           "la contacta y dile en cuanto tiempo aproximado.",
            }), False

        if nombre == "agendar_visita":
            guardado = await registro_leads.guardar(
                inquilino=inquilino, sesion_id=sesion_id, tipo="visita", datos=entrada,
            )
            return _json({
                "registrado": True,
                "folio": guardado["folio"],
                "mensaje": "Solicitud de visita guardada. Aclara que el asesor "
                           "confirma dia y hora; tu no confirmaste la cita.",
            }), False

        return f"Herramienta desconocida: {nombre}", True

    except Exception as err:  # noqa: BLE001 - el modelo maneja el error
        return (f"No se pudo completar '{nombre}': {err}. Dile a la persona que "
                f"hubo un problema tecnico y ofrecele pasarla con un asesor."), True


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

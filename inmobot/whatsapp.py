"""Canal de WhatsApp por la API oficial de Meta (Cloud API).

Meta no "entrega" los mensajes: le pega a una direccion nuestra cada vez
que alguien escribe. Esa direccion esta abierta a internet, asi que lo
primero que hace este modulo es comprobar que quien toca la puerta sea de
verdad Meta, con la firma que viene en X-Hub-Signature-256.

Config por inquilino, en su JSON:

    "canales": {
      "whatsapp": {
        "telefono_id": "123456789012345",   el del numero, no el numero
        "token_env": "WHATSAPP_TOKEN",
        "version": "v21.0"
      }
    }

El telefono_id es la llave para saber a que inquilino le escribieron: un
solo servidor atiende los WhatsApp de todos los clientes, y cada uno tiene
el suyo.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field

import httpx

from .config import leer_env

# WhatsApp corta los mensajes de texto en 4096 caracteres.
LARGO_MAXIMO = 4000

# Meta solo deja contestar con texto libre dentro de las 24 horas
# siguientes al mensaje del cliente. Despues solo con plantilla aprobada.
VENTANA_SERVICIO = 24 * 3600


@dataclass
class MensajeEntrante:
    id: str
    de: str                      # telefono del cliente, solo digitos
    texto: str
    nombre: str = ""
    telefono_id: str = ""        # a que numero nuestro le escribio
    tipo: str = "text"
    momento: int = 0             # cuando lo mando, en segundos unix
    extra: dict = field(default_factory=dict)

    @property
    def fuera_de_ventana(self) -> bool:
        """Si ya no se puede contestar con texto libre.

        Pasa cuando el servidor estuvo caido y Meta reintenta horas
        despues: el mensaje llega, pero su ventana ya cerro.
        """
        if not self.momento:
            return False
        return (time.time() - self.momento) > VENTANA_SERVICIO


def firma_valida(cuerpo: bytes, cabecera: str, secreto: str) -> bool:
    """Comprueba que el aviso venga de Meta y nadie lo haya tocado.

    Sin esto, cualquiera que adivine la direccion puede hacer que el
    asistente le conteste a quien quiera, a costa de la cuenta del cliente.
    """
    if not secreto or not cabecera:
        return False
    if not cabecera.startswith("sha256="):
        return False
    esperada = hmac.new(secreto.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, cabecera[len("sha256="):].strip())


def leer_entrantes(aviso: dict) -> list[MensajeEntrante]:
    """Saca los mensajes de personas de un aviso de Meta.

    Un mismo aviso puede traer varios mensajes y tambien avisos de entrega
    ('statuses'), que no son mensajes de nadie y aqui se ignoran.
    """
    mensajes: list[MensajeEntrante] = []
    for entrada in aviso.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            telefono_id = ((valor.get("metadata") or {}).get("phone_number_id")) or ""

            # El nombre del contacto viene en una lista aparte de los
            # mensajes; se cruza por el telefono.
            nombres = {
                c.get("wa_id", ""): ((c.get("profile") or {}).get("name") or "")
                for c in (valor.get("contacts") or [])
            }

            for m in valor.get("messages") or []:
                tipo = m.get("type", "")
                texto = ""
                if tipo == "text":
                    texto = ((m.get("text") or {}).get("body") or "").strip()
                elif tipo == "button":
                    texto = ((m.get("button") or {}).get("text") or "").strip()
                elif tipo == "interactive":
                    interactivo = m.get("interactive") or {}
                    for clave in ("button_reply", "list_reply"):
                        if interactivo.get(clave):
                            texto = (interactivo[clave].get("title") or "").strip()
                            break

                de = m.get("from", "")
                try:
                    momento = int(m.get("timestamp") or 0)
                except (TypeError, ValueError):
                    momento = 0
                mensajes.append(MensajeEntrante(
                    id=m.get("id", ""),
                    de=de,
                    texto=texto,
                    nombre=nombres.get(de, ""),
                    telefono_id=telefono_id,
                    tipo=tipo,
                    momento=momento,
                ))
    return mensajes


def partir(texto: str, largo: int = LARGO_MAXIMO) -> list[str]:
    """Parte una respuesta larga sin cortar a media palabra."""
    texto = (texto or "").strip()
    if len(texto) <= largo:
        return [texto] if texto else []

    trozos, resto = [], texto
    while len(resto) > largo:
        corte = resto.rfind("\n\n", 0, largo)
        if corte < largo // 2:
            corte = resto.rfind(" ", 0, largo)
        if corte < largo // 2:
            corte = largo
        trozos.append(resto[:corte].strip())
        resto = resto[corte:].strip()
    if resto:
        trozos.append(resto)
    return trozos


class CanalWhatsApp:
    """Lo que hace falta para contestarle a un cliente por WhatsApp."""

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.telefono_id = str(config.get("telefono_id", ""))
        self.var_token = config.get("token_env", "WHATSAPP_TOKEN")
        self.version = config.get("version", "v21.0")

    @property
    def activo(self) -> bool:
        return bool(self.telefono_id and leer_env(self.var_token))

    @property
    def _url(self) -> str:
        return f"https://graph.facebook.com/{self.version}/{self.telefono_id}/messages"

    def _cabeceras(self) -> dict:
        return {"Authorization": "Bearer " + leer_env(self.var_token),
                "Content-Type": "application/json"}

    async def enviar(self, para: str, texto: str) -> str:
        """Manda la respuesta. Devuelve 'ok' o el motivo de la falla."""
        if not self.activo:
            return f"fallo: falta telefono_id o {self.var_token}"

        trozos = partir(texto)
        if not trozos:
            return "fallo: respuesta vacia"

        async with httpx.AsyncClient(timeout=30.0) as cliente:
            for trozo in trozos:
                cuerpo = {"messaging_product": "whatsapp", "to": para,
                          "type": "text", "text": {"body": trozo,
                                                   "preview_url": True}}
                try:
                    r = await cliente.post(self._url, headers=self._cabeceras(),
                                           json=cuerpo)
                except httpx.HTTPError as e:
                    return f"fallo: {type(e).__name__}"
                if r.status_code != 200:
                    return f"fallo: {r.status_code} {r.text[:200]}"
        return "ok"

    async def marcar_leido(self, id_mensaje: str) -> None:
        """Las dos palomitas azules. Si falla, no pasa nada: es cosmetico."""
        if not self.activo or not id_mensaje:
            return
        cuerpo = {"messaging_product": "whatsapp", "status": "read",
                  "message_id": id_mensaje}
        try:
            async with httpx.AsyncClient(timeout=10.0) as cliente:
                await cliente.post(self._url, headers=self._cabeceras(), json=cuerpo)
        except httpx.HTTPError:
            pass


def canal_de(inquilino) -> CanalWhatsApp:
    return CanalWhatsApp((inquilino.canales or {}).get("whatsapp") or {})


def buscar_inquilino(registro, telefono_id: str):
    """De que cliente es el numero al que escribieron."""
    for id_inq in registro.ids():
        inq = registro.obtener(id_inq)
        config = (inq.canales or {}).get("whatsapp") or {}
        if str(config.get("telefono_id", "")) == str(telefono_id):
            return inq
    return None

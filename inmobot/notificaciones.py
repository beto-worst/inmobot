"""Aviso al asesor cuando entra un prospecto.

Un lead guardado que nadie ve no sirve de nada: el valor del producto es que
el asesor se entere mientras el cliente sigue interesado, no al dia siguiente
cuando alguien revise el sistema.

Canales disponibles: correo, webhook (Google Sheets, el CRM del cliente,
Make/Zapier) y consola para desarrollo. Se configuran por inquilino y se
pueden usar varios a la vez.

Sobre WhatsApp: mandar un mensaje automatico de WhatsApp exige la API de
WhatsApp Business de Meta, con verificacion de la empresa y semanas de
tramite. Mientras tanto, el correo y el webhook llegan hoy, y el webhook
puede enganchar con Make o Zapier si el cliente ya tiene esa conexion.
"""
from __future__ import annotations

import asyncio
import json
import smtplib
import os
from email.message import EmailMessage

import httpx

ETIQUETAS = [
    ("asesor", "Asesor asignado"),
    ("nombre", "Cliente"),
    ("telefono", "Telefono"),
    ("correo", "Correo"),
    ("requerimiento", "Que busca"),
    ("presupuesto", "Presupuesto"),
    ("forma_pago", "Forma de pago"),
    ("urgencia", "Urgencia"),
    ("propiedad_id", "Propiedad"),
    ("propiedades_interes", "Le intereso"),
    ("preferencia_horario", "Horario que pidio"),
    ("notas", "Notas"),
]


def redactar(registro: dict, inquilino, previos: int = 0) -> tuple[str, str]:
    """Arma asunto y cuerpo. Pensado para leerse en el celular, de un vistazo."""
    es_visita = registro.get("tipo") == "visita"
    quien = registro.get("nombre") or "Prospecto sin nombre"

    asunto = (f"{'VISITA' if es_visita else 'Prospecto'} - {quien}"
              f" - {inquilino.nombre}")

    lineas = [
        f"{'Solicitud de visita' if es_visita else 'Nuevo prospecto'} "
        f"capturado por {inquilino.asistente}.",
        "",
    ]

    for clave, etiqueta in ETIQUETAS:
        valor = registro.get(clave)
        if not valor:
            continue
        if isinstance(valor, list):
            valor = ", ".join(str(v) for v in valor)
        lineas.append(f"{etiqueta}: {valor}")

    if previos:
        lineas += ["", f"OJO: este telefono ya habia contactado {previos} vez/veces antes."]

    if es_visita:
        lineas += ["", "La cita NO esta confirmada. El cliente sabe que usted "
                       "le va a marcar para acordar dia y hora."]

    lineas += ["", f"Folio: {registro.get('folio', '')}",
               f"Fecha: {registro.get('fecha', '')}"]

    telefono = registro.get("telefono")
    if telefono:
        digitos = "".join(c for c in str(telefono) if c.isdigit())
        if len(digitos) == 10:
            digitos = "52" + digitos
        lineas += ["", f"Escribirle por WhatsApp: https://wa.me/{digitos}"]

    return asunto, "\n".join(lineas)


class Notificador:
    """Manda el aviso por los canales que tenga configurados el inquilino."""

    async def enviar(self, registro: dict, inquilino, previos: int = 0,
                     destinos_extra: list[str] | None = None) -> str:
        """Manda el aviso. `destinos_extra` es el asesor de esa propiedad.

        Se SUMA a los destinatarios fijos, no los reemplaza: si la busqueda
        del asesor falla, el prospecto igual le llega a alguien.
        """
        config = getattr(inquilino, "notificaciones", None) or {}
        if not config:
            return "sin canales configurados"

        asunto, cuerpo = redactar(registro, inquilino, previos)
        resultados = []

        if config.get("consola"):
            print(f"\n--- AVISO AL ASESOR ---\n{asunto}\n\n{cuerpo}\n")
            resultados.append("consola:ok")

        if config.get("email"):
            resultados.append("email:" + await self._email(
                config["email"], asunto, cuerpo, destinos_extra))

        if config.get("webhook"):
            resultados.append("webhook:" + await self._webhook(
                config["webhook"], registro))

        return "; ".join(resultados) if resultados else "sin canales configurados"

    async def _email(self, cfg: dict, asunto: str, cuerpo: str,
                     destinos_extra: list[str] | None = None) -> str:
        destinos = cfg.get("para") or []
        if isinstance(destinos, str):
            destinos = [destinos]
        destinos = list(destinos)

        # dict.fromkeys quita repetidos conservando el orden: si el asesor
        # de la propiedad ya estaba en la lista fija, no le llegan dos.
        for correo in (destinos_extra or []):
            if correo and correo not in destinos:
                destinos.append(correo)
        destinos = list(dict.fromkeys(destinos))

        if not destinos:
            return "fallo: falta 'para'"

        # La contrasena NUNCA va en el JSON del inquilino: ese archivo esta en
        # git. Solo el nombre de la variable de entorno donde vive.
        usuario = os.environ.get(cfg.get("usuario_env", "SMTP_USUARIO"), "")
        password = os.environ.get(cfg.get("password_env", "SMTP_PASSWORD"), "")
        if not usuario or not password:
            return (f"fallo: faltan {cfg.get('usuario_env', 'SMTP_USUARIO')} y/o "
                    f"{cfg.get('password_env', 'SMTP_PASSWORD')} en .env")

        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = cfg.get("de") or usuario
        msg["To"] = ", ".join(destinos)
        msg.set_content(cuerpo)

        try:
            await asyncio.to_thread(
                self._enviar_smtp, cfg.get("host", "smtp.gmail.com"),
                int(cfg.get("puerto", 587)), usuario, password, msg,
            )
            return "ok"
        except Exception as err:  # noqa: BLE001 - el lead ya esta guardado
            return f"fallo: {err}"

    def _enviar_smtp(self, host, puerto, usuario, password, msg) -> None:
        with smtplib.SMTP(host, puerto, timeout=20) as s:
            s.starttls()
            s.login(usuario, password)
            s.send_message(msg)

    async def _webhook(self, url: str, registro: dict) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                r = await c.post(
                    url,
                    # text/plain evita el preflight CORS de Google Apps Script.
                    headers={"Content-Type": "text/plain;charset=utf-8"},
                    content=json.dumps(registro, ensure_ascii=False, default=str),
                )
            return "ok" if r.status_code < 400 else f"http {r.status_code}"
        except Exception as err:  # noqa: BLE001 - el lead ya esta guardado
            return f"fallo: {err}"

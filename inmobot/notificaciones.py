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


def _solo_digitos(valor) -> str:
    """Normaliza a como lo espera WhatsApp: digitos con lada de pais.

    Un numero de 10 digitos se asume mexicano y se le antepone 52.
    """
    n = "".join(c for c in str(valor or "") if c.isdigit())
    return "52" + n if len(n) == 10 else n


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
                     asesor: dict | None = None) -> str:
        """Manda el aviso por todos los canales configurados.

        `asesor` es quien tiene asignada la propiedad. Su correo y su
        telefono SE SUMAN a los destinatarios fijos, no los reemplazan: si
        la busqueda del asesor falla, el prospecto igual le llega a alguien.
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
            correo = (asesor or {}).get("correo")
            resultados.append("email:" + await self._email(
                config["email"], asunto, cuerpo, [correo] if correo else []))

        if config.get("whatsapp"):
            telefono = (asesor or {}).get("telefono")
            resultados.append("whatsapp:" + await self._whatsapp(
                config["whatsapp"], registro, inquilino,
                [telefono] if telefono else []))

        if config.get("webhook"):
            resultados.append("webhook:" + await self._webhook(
                config["webhook"], registro))

        return "; ".join(resultados) if resultados else "sin canales configurados"

    async def aviso_push(self, inquilino, titulo: str, texto: str) -> str:
        """Un empujon corto al telefono del dueno. No es el aviso al asesor.

        El aviso al asesor lleva la ficha completa del prospecto y se manda
        cuando ya hay datos. Esto es otra cosa: saber que alguien esta
        hablando con el asistente AHORA, aunque todavia no diga ni su
        nombre. Sirve para asomarse, o para entrar a contestar tu mismo.
        """
        cfg = (getattr(inquilino, "avisos", None) or {}).get("push") or {}
        if not cfg:
            return "sin push configurado"
        return await self._push(cfg, titulo, texto)

    async def _push(self, cfg: dict, titulo: str, texto: str) -> str:
        """ntfy por defecto; cualquier otro servicio con una url.

        ntfy no pide cuenta ni llaves: se instala la app, uno se suscribe a
        un tema y lo que se publique en ese tema llega al telefono. El tema
        ES la contrasena -quien lo sepa recibe los avisos-, por eso sale de
        una variable de entorno y nunca del JSON del inquilino, que va a
        git.
        """
        url = cfg.get("url", "")
        if not url:
            variable = cfg.get("tema_env", "NTFY_TEMA")
            tema = os.environ.get(variable, "").strip()
            if not tema:
                return f"fallo: falta {variable}"
            url = cfg.get("servidor", "https://ntfy.sh").rstrip("/") + "/" + tema

        try:
            async with httpx.AsyncClient(timeout=15.0) as cliente:
                if cfg.get("json"):
                    r = await cliente.post(url, json={"titulo": titulo,
                                                      "texto": texto})
                else:
                    r = await cliente.post(
                        url, content=texto.encode("utf-8"),
                        headers={"Title": titulo.encode("utf-8").decode("latin-1",
                                                                        "ignore"),
                                 "Tags": "speech_balloon"})
        except httpx.HTTPError as e:
            return f"fallo: {type(e).__name__}"
        return "ok" if r.status_code < 300 else f"fallo: {r.status_code}"

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

    async def _whatsapp(self, cfg: dict, registro: dict, inquilino,
                        destinos_extra: list[str] | None = None) -> str:
        """Manda el aviso por la API oficial de WhatsApp Business (Meta).

        No hay atajo legitimo: un mensaje automatico de WhatsApp exige la
        Cloud API de Meta. Las "APIs no oficiales" violan los terminos y
        terminan con el numero baneado, que con 32 asesores dependiendo de
        el seria peor que no tener WhatsApp.

        Meta exige una PLANTILLA aprobada para iniciar una conversacion.
        Solo dentro de las 24 horas siguientes a que la persona te escriba
        se puede mandar texto libre; por eso `plantilla` es lo normal aqui.
        """
        destinos = cfg.get("para") or []
        if isinstance(destinos, str):
            destinos = [destinos]
        destinos = list(destinos) + [d for d in (destinos_extra or []) if d]
        destinos = [_solo_digitos(d) for d in destinos]
        destinos = [d for d in dict.fromkeys(destinos) if d]
        if not destinos:
            return "sin destinatarios"

        token = os.environ.get(cfg.get("token_env", "WHATSAPP_TOKEN"), "")
        telefono_id = os.environ.get(cfg.get("telefono_id_env", "WHATSAPP_TELEFONO_ID"), "")
        if not token or not telefono_id:
            return (f"fallo: faltan {cfg.get('token_env', 'WHATSAPP_TOKEN')} y/o "
                    f"{cfg.get('telefono_id_env', 'WHATSAPP_TELEFONO_ID')} en .env")

        version = cfg.get("version_api", "v21.0")
        url = f"https://graph.facebook.com/{version}/{telefono_id}/messages"
        plantilla = cfg.get("plantilla")

        entregados, fallos = 0, []
        async with httpx.AsyncClient(timeout=20.0) as c:
            for numero in destinos:
                cuerpo = (self._mensaje_plantilla(plantilla, cfg, registro, inquilino,
                                                  numero)
                          if plantilla
                          else self._mensaje_texto(registro, inquilino, numero))
                try:
                    r = await c.post(
                        url,
                        headers={"Authorization": "Bearer " + token,
                                 "Content-Type": "application/json"},
                        json=cuerpo,
                    )
                    if r.status_code < 300:
                        entregados += 1
                    else:
                        detalle = ""
                        try:
                            detalle = (r.json().get("error") or {}).get("message", "")
                        except Exception:  # noqa: BLE001
                            detalle = r.text[:120]
                        fallos.append(f"{numero}: {r.status_code} {detalle}")
                except Exception as err:  # noqa: BLE001 - el lead ya esta guardado
                    fallos.append(f"{numero}: {err}")

        if entregados and not fallos:
            return "ok"
        if entregados:
            return f"ok parcial ({entregados} de {len(destinos)}); " + "; ".join(fallos)
        return "fallo: " + "; ".join(fallos)

    def _mensaje_plantilla(self, nombre: str, cfg: dict, registro: dict,
                           inquilino, numero: str) -> dict:
        # Los parametros van en el orden de los {{1}}, {{2}}... de la
        # plantilla que aprobo Meta. Cambiar el orden aqui sin cambiarlo
        # alla manda los datos al hueco equivocado.
        valores = [
            str(registro.get("nombre") or "Sin nombre"),
            str(registro.get("telefono") or "sin telefono"),
            str(registro.get("requerimiento") or "Sin detalle")[:280],
        ]
        return {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "template",
            "template": {
                "name": nombre,
                "language": {"code": cfg.get("idioma", "es_MX")},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in valores],
                }],
            },
        }

    def _mensaje_texto(self, registro: dict, inquilino, numero: str) -> dict:
        """Texto libre. Solo llega dentro de la ventana de 24 horas."""
        lineas = [f"Nuevo prospecto de {inquilino.nombre}", ""]
        for clave, etiqueta in ETIQUETAS:
            valor = registro.get(clave)
            if not valor:
                continue
            if isinstance(valor, list):
                valor = ", ".join(str(v) for v in valor)
            lineas.append(f"{etiqueta}: {valor}")
        return {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "text",
            "text": {"body": "\n".join(lineas)[:4000]},
        }

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

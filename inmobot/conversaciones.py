"""Memoria de las conversaciones que no viven en un navegador.

El chat del sitio guarda el historial en el navegador y lo reenvia completo
en cada mensaje. Por WhatsApp no hay navegador: el que tiene que acordarse
es el servidor, y tiene que seguir acordandose despues de un reinicio, de
un despliegue y de que el cliente conteste tres horas despues.

Se guarda SOLO el texto de cada turno, no los bloques de herramientas del
SDK. Dos razones: esos bloques no son JSON, y arrastran el inventario
entero de cada busqueda. Si el modelo necesita algo que ya consulto, lo
vuelve a consultar; sale mas barato que cargarlo en cada turno.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DIR_DATOS

# Cuantos turnos de ida y vuelta se conservan. Mas alla de esto una
# conversacion ya no gana nada y solo cuesta tokens en cada mensaje.
MAX_TURNOS = 30

ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    canal           TEXT NOT NULL,
    contacto        TEXT NOT NULL,
    inquilino       TEXT NOT NULL,
    nombre          TEXT,
    historial       TEXT NOT NULL DEFAULT '[]',
    ultimo_entrante TEXT,
    pausada_hasta   TEXT,
    actualizada     TEXT NOT NULL,
    PRIMARY KEY (canal, contacto)
);

-- Meta reintenta un mensaje si no le contestamos 200 lo bastante rapido.
-- Sin esta tabla, el cliente recibe la misma respuesta dos y tres veces.
CREATE TABLE IF NOT EXISTS mensajes_vistos (
    id      TEXT PRIMARY KEY,
    visto   TEXT NOT NULL
);
"""


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(momento: datetime | None) -> str | None:
    return momento.isoformat() if momento else None


def _leer(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def aplanar(historial: list[dict]) -> list[dict]:
    """Deja el historial en puro texto, listo para guardar y para reenviar.

    Tira los bloques de uso de herramientas y sus resultados, y junta los
    turnos seguidos del mismo lado: la API rechaza dos mensajes 'user'
    pegados, y al quitar las herramientas eso pasa seguido.
    """
    limpio: list[dict] = []
    for mensaje in historial:
        contenido = mensaje.get("content")
        if isinstance(contenido, str):
            texto = contenido
        else:
            piezas = []
            for bloque in contenido or []:
                # Los bloques del SDK son objetos; los nuestros, diccionarios.
                tipo = getattr(bloque, "type", None) or (
                    bloque.get("type") if isinstance(bloque, dict) else None)
                if tipo != "text":
                    continue
                piezas.append(getattr(bloque, "text", None) or bloque.get("text", ""))
            texto = "".join(piezas)

        texto = (texto or "").strip()
        if not texto:
            continue

        papel = mensaje.get("role", "user")
        if limpio and limpio[-1]["role"] == papel:
            limpio[-1]["content"] += "\n\n" + texto
        else:
            limpio.append({"role": papel, "content": texto})

    # El recorte va de dos en dos para no partir un par pregunta/respuesta
    # y dejar el historial empezando con la respuesta a algo que no esta.
    if len(limpio) > MAX_TURNOS * 2:
        limpio = limpio[-(MAX_TURNOS * 2):]
    while limpio and limpio[0]["role"] != "user":
        limpio.pop(0)
    return limpio


class Conversaciones:
    """Lo que cada contacto traia platicado, por canal."""

    def __init__(self, ruta: Path | None = None):
        self.ruta = Path(ruta or (DIR_DATOS / "athena.db"))
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.executescript(ESQUEMA)

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    # ---- control de repetidos ----
    async def es_repetido(self, id_mensaje: str) -> bool:
        return await asyncio.to_thread(self._es_repetido, id_mensaje)

    def _es_repetido(self, id_mensaje: str) -> bool:
        if not id_mensaje:
            return False
        with self._conectar() as con:
            try:
                con.execute("INSERT INTO mensajes_vistos (id, visto) VALUES (?, ?)",
                            (id_mensaje, _iso(ahora())))
            except sqlite3.IntegrityError:
                return True
            # Limpieza barata: lo de hace mas de un dia ya no se va a repetir.
            con.execute("DELETE FROM mensajes_vistos WHERE visto < ?",
                        (_iso(ahora() - timedelta(days=1)),))
        return False

    # ---- lectura y escritura ----
    async def abrir(self, canal: str, contacto: str, inquilino: str,
                    nombre: str = "") -> dict:
        return await asyncio.to_thread(self._abrir, canal, contacto, inquilino, nombre)

    def _abrir(self, canal: str, contacto: str, inquilino: str, nombre: str) -> dict:
        with self._conectar() as con:
            fila = con.execute(
                "SELECT * FROM conversaciones WHERE canal = ? AND contacto = ?",
                (canal, contacto)).fetchone()
            if fila is None:
                con.execute(
                    "INSERT INTO conversaciones (canal, contacto, inquilino, nombre,"
                    " historial, actualizada) VALUES (?, ?, ?, ?, '[]', ?)",
                    (canal, contacto, inquilino, nombre, _iso(ahora())))
                return {"historial": [], "nombre": nombre, "pausada_hasta": None,
                        "ultimo_entrante": None}

            # El nombre que trae WhatsApp puede cambiar; nos quedamos con el
            # ultimo que mando, pero sin borrarlo si viene vacio.
            if nombre and nombre != (fila["nombre"] or ""):
                con.execute(
                    "UPDATE conversaciones SET nombre = ? WHERE canal = ? AND contacto = ?",
                    (nombre, canal, contacto))

            try:
                historial = json.loads(fila["historial"]) or []
            except json.JSONDecodeError:
                historial = []
            return {
                "historial": historial,
                "nombre": nombre or (fila["nombre"] or ""),
                "pausada_hasta": _leer(fila["pausada_hasta"]),
                "ultimo_entrante": _leer(fila["ultimo_entrante"]),
            }

    async def guardar(self, canal: str, contacto: str, historial: list[dict],
                      entrante: bool = True) -> None:
        await asyncio.to_thread(self._guardar, canal, contacto, historial, entrante)

    def _guardar(self, canal: str, contacto: str, historial: list[dict],
                 entrante: bool) -> None:
        momento = _iso(ahora())
        with self._conectar() as con:
            if entrante:
                con.execute(
                    "UPDATE conversaciones SET historial = ?, actualizada = ?,"
                    " ultimo_entrante = ? WHERE canal = ? AND contacto = ?",
                    (json.dumps(aplanar(historial), ensure_ascii=False),
                     momento, momento, canal, contacto))
            else:
                con.execute(
                    "UPDATE conversaciones SET historial = ?, actualizada = ?"
                    " WHERE canal = ? AND contacto = ?",
                    (json.dumps(aplanar(historial), ensure_ascii=False),
                     momento, canal, contacto))

    # ---- cuando entra un humano ----
    async def pausar(self, canal: str, contacto: str, minutos: int = 120) -> None:
        """Calla a la IA en ese chat.

        Cuando un asesor entra a contestar a mano, dos respuestas -la suya y
        la de la IA- se ven peor que ninguna.
        """
        await asyncio.to_thread(self._pausar, canal, contacto, minutos)

    def _pausar(self, canal: str, contacto: str, minutos: int) -> None:
        hasta = _iso(ahora() + timedelta(minutes=max(1, minutos)))
        with self._conectar() as con:
            con.execute(
                "UPDATE conversaciones SET pausada_hasta = ? WHERE canal = ? AND contacto = ?",
                (hasta, canal, contacto))

    async def reanudar(self, canal: str, contacto: str) -> None:
        await asyncio.to_thread(self._reanudar, canal, contacto)

    def _reanudar(self, canal: str, contacto: str) -> None:
        with self._conectar() as con:
            con.execute(
                "UPDATE conversaciones SET pausada_hasta = NULL WHERE canal = ? AND contacto = ?",
                (canal, contacto))

    def listar(self, canal: str = "", limite: int = 50) -> list[dict]:
        consulta = ("SELECT canal, contacto, inquilino, nombre, ultimo_entrante,"
                    " pausada_hasta, actualizada FROM conversaciones")
        parametros: list = []
        if canal:
            consulta += " WHERE canal = ?"
            parametros.append(canal)
        consulta += " ORDER BY actualizada DESC LIMIT ?"
        parametros.append(max(1, min(limite, 200)))
        with self._conectar() as con:
            return [dict(f) for f in con.execute(consulta, parametros).fetchall()]


def esta_pausada(conversacion: dict) -> bool:
    hasta = conversacion.get("pausada_hasta")
    return bool(hasta and hasta > ahora())

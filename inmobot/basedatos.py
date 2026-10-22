"""Base de datos de prospectos.

SQLite, no un archivo de texto. Un JSONL sirve para no perder datos, pero no
se puede consultar: el asesor necesita filtrar por estado, buscar un telefono
y marcar a quien ya contacto. Eso es una base de datos.

SQLite se eligio a proposito sobre Postgres: es un archivo, no hay servidor
que administrar ni que pagar, y aguanta de sobra el volumen de varias
inmobiliarias. El dia que uno de tus clientes reciba miles de leads al dia,
se cambia el motor sin tocar el resto del sistema.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from .config import DIR_DATOS

ESQUEMA = """
CREATE TABLE IF NOT EXISTS leads (
    folio               TEXT PRIMARY KEY,
    inquilino           TEXT NOT NULL,
    tipo                TEXT NOT NULL,
    sesion              TEXT,
    fecha               TEXT NOT NULL,
    nombre              TEXT,
    telefono            TEXT,
    telefono_normalizado TEXT,
    correo              TEXT,
    requerimiento       TEXT,
    presupuesto         TEXT,
    forma_pago          TEXT,
    urgencia            TEXT,
    propiedades_interes TEXT,
    propiedad_id        TEXT,
    preferencia_horario TEXT,
    notas               TEXT,
    asesor              TEXT,
    estado              TEXT NOT NULL DEFAULT 'nuevo',
    notificacion        TEXT,
    extra               TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_inquilino_fecha
    ON leads (inquilino, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_leads_telefono
    ON leads (inquilino, telefono_normalizado);
CREATE INDEX IF NOT EXISTS idx_leads_estado
    ON leads (inquilino, estado);
"""

# Columnas que tienen su propio campo; lo demas se guarda en 'extra'.
COLUMNAS = {
    "folio", "inquilino", "tipo", "sesion", "fecha", "nombre", "telefono",
    "telefono_normalizado", "correo", "requerimiento", "presupuesto",
    "forma_pago", "urgencia", "propiedades_interes", "propiedad_id",
    "preferencia_horario", "notas", "asesor", "estado", "notificacion",
}

ESTADOS = ("nuevo", "contactado", "en seguimiento", "cerrado", "descartado")


def normalizar_telefono(valor: str | None) -> str:
    """Deja solo digitos y se queda con los ultimos 10.

    "81 1778 3953", "+52 811 778 3953" y "8117783953" son el mismo cliente.
    Sin esto, el asesor recibe tres veces al mismo prospecto como si fueran
    tres personas distintas.
    """
    if not valor:
        return ""
    digitos = "".join(c for c in str(valor) if c.isdigit())
    return digitos[-10:] if len(digitos) >= 10 else digitos


class BaseDatos:
    def __init__(self, ruta: Path | None = None):
        self.ruta = Path(ruta or (DIR_DATOS / "athena.db"))
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._preparar()

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta, timeout=10.0)
        con.row_factory = sqlite3.Row
        # WAL permite leer mientras se escribe: el panel del asesor no se
        # bloquea porque justo esta entrando un lead.
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _preparar(self) -> None:
        with self._conectar() as con:
            con.executescript(ESQUEMA)

    # ---- escritura ----
    async def guardar(self, registro: dict) -> None:
        await asyncio.to_thread(self._guardar, registro)

    def _guardar(self, registro: dict) -> None:
        fila = {c: None for c in COLUMNAS}
        extra = {}

        for clave, valor in registro.items():
            if clave in COLUMNAS:
                if isinstance(valor, (list, dict)):
                    valor = json.dumps(valor, ensure_ascii=False)
                fila[clave] = valor
            else:
                extra[clave] = valor

        fila["telefono_normalizado"] = normalizar_telefono(registro.get("telefono"))
        fila["estado"] = registro.get("estado") or "nuevo"

        campos = sorted(COLUMNAS) + ["extra"]
        valores = [fila[c] for c in sorted(COLUMNAS)]
        valores.append(json.dumps(extra, ensure_ascii=False) if extra else None)

        marcas = ", ".join("?" * len(campos))
        with self._conectar() as con:
            con.execute(
                f"INSERT OR REPLACE INTO leads ({', '.join(campos)}) "
                f"VALUES ({marcas})",
                valores,
            )

    async def marcar(self, folio: str, estado: str, asesor: str = "") -> bool:
        if estado not in ESTADOS:
            raise ValueError(f"Estado invalido '{estado}'. Validos: {ESTADOS}")
        return await asyncio.to_thread(self._marcar, folio, estado, asesor)

    def _marcar(self, folio: str, estado: str, asesor: str) -> bool:
        with self._conectar() as con:
            cur = con.execute(
                "UPDATE leads SET estado = ?, asesor = COALESCE(NULLIF(?, ''), asesor) "
                "WHERE folio = ?",
                (estado, asesor, folio),
            )
            return cur.rowcount > 0

    async def anotar_notificacion(self, folio: str, resultado: str) -> None:
        await asyncio.to_thread(self._anotar_notificacion, folio, resultado)

    def _anotar_notificacion(self, folio: str, resultado: str) -> None:
        with self._conectar() as con:
            con.execute("UPDATE leads SET notificacion = ? WHERE folio = ?",
                        (resultado, folio))

    # ---- lectura ----
    def listar(self, inquilino: str, *, estado: str | None = None,
               tipo: str | None = None, busqueda: str = "",
               limite: int = 50) -> list[dict]:
        sql = ["SELECT * FROM leads WHERE inquilino = ?"]
        args: list = [inquilino]

        if estado:
            sql.append("AND estado = ?")
            args.append(estado)
        if tipo:
            sql.append("AND tipo = ?")
            args.append(tipo)
        if busqueda:
            sql.append("AND (nombre LIKE ? OR telefono_normalizado LIKE ? "
                       "OR requerimiento LIKE ?)")
            patron = f"%{busqueda}%"
            args += [patron, normalizar_telefono(busqueda) or patron, patron]

        sql.append("ORDER BY fecha DESC LIMIT ?")
        args.append(max(1, min(limite, 500)))

        with self._conectar() as con:
            filas = con.execute(" ".join(sql), args).fetchall()
        return [self._a_dict(f) for f in filas]

    def historial_telefono(self, inquilino: str, telefono: str) -> list[dict]:
        """Contactos previos del mismo numero.

        Le sirve al asesor: no es lo mismo un prospecto nuevo que alguien que
        ya escribio tres veces esta semana.
        """
        normalizado = normalizar_telefono(telefono)
        if not normalizado:
            return []
        with self._conectar() as con:
            filas = con.execute(
                "SELECT * FROM leads WHERE inquilino = ? AND telefono_normalizado = ? "
                "ORDER BY fecha DESC LIMIT 20",
                (inquilino, normalizado),
            ).fetchall()
        return [self._a_dict(f) for f in filas]

    def resumen(self, inquilino: str) -> dict:
        with self._conectar() as con:
            por_estado = {
                f["estado"]: f["n"] for f in con.execute(
                    "SELECT estado, COUNT(*) n FROM leads WHERE inquilino = ? "
                    "GROUP BY estado", (inquilino,)).fetchall()
            }
            total = con.execute(
                "SELECT COUNT(*) n FROM leads WHERE inquilino = ?",
                (inquilino,)).fetchone()["n"]
            # El resultado viene como "canal:resultado", p. ej.
            # "email:ok; webhook:fallo: timeout". Se considera avisado si al
            # menos un canal entrego. Buscar que EMPIECE con "ok" contaba
            # como fallido todo aviso exitoso, y el tablero mentia siempre.
            sin_notificar = con.execute(
                "SELECT COUNT(*) n FROM leads WHERE inquilino = ? AND "
                "(notificacion IS NULL OR notificacion NOT LIKE '%ok%')",
                (inquilino,)).fetchone()["n"]
        return {"total": total, "por_estado": por_estado,
                "sin_notificar": sin_notificar}

    def _a_dict(self, fila: sqlite3.Row) -> dict:
        d = dict(fila)
        for campo in ("propiedades_interes", "extra"):
            if d.get(campo):
                try:
                    d[campo] = json.loads(d[campo])
                except (json.JSONDecodeError, TypeError):
                    pass
        return {k: v for k, v in d.items() if v not in (None, "")}

    def importar_jsonl(self, ruta: Path, inquilino: str) -> int:
        """Sube a la base los leads que quedaron en el archivo de texto."""
        if not Path(ruta).is_file():
            return 0
        n = 0
        for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                continue
            registro.setdefault("inquilino", inquilino)
            self._guardar(registro)
            n += 1
        return n

"""Carga de inquilinos.

Un inquilino = una inmobiliaria que te paga la mensualidad. Vive en un JSON
dentro de `inquilinos/`. Dar de alta un cliente nuevo es copiar un archivo y
cambiarle los datos: cero codigo.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_INQUILINOS = RAIZ / "inquilinos"

# Donde viven los prospectos y las conversaciones. En esta computadora, al
# lado del codigo; en un servidor, en un disco aparte (ATHENA_DATOS), porque
# cada despliegue reemplaza el codigo y se llevaria la base de datos.
DIR_DATOS = Path(os.environ.get("ATHENA_DATOS") or (RAIZ / "datos"))

# Por que Opus 5 y no un modelo mas barato: lo que se vende es que la IA
# entienda a un cliente mexicano escribiendo informal ("algo pa' la familia,
# no muy caro, por el norte") y que use bien las herramientas. Con
# effort "low" sale rapido y economico. Cada inquilino puede bajarlo a
# "claude-haiku-4-5" desde su JSON si quiere pagar menos.
MODELO_POR_DEFECTO = "claude-opus-5"
ESFUERZO_POR_DEFECTO = "low"


@dataclass
class Inquilino:
    """Configuracion completa de una inmobiliaria."""

    id: str
    nombre: str
    activo: bool = True
    # Como se llama la IA. El producto se llama Athena, pero cada inmobiliaria
    # puede rebautizarla con el nombre que quiera: para su cliente final es
    # "su" asistente, no un servicio de terceros. Eso vale al momento de
    # vender la mensualidad.
    asistente: str = "Athena"
    modelo: str = MODELO_POR_DEFECTO
    esfuerzo: str = ESFUERZO_POR_DEFECTO
    zona_operacion: str = ""
    # Que vende el negocio, en una frase: "una agencia inmobiliaria",
    # "una aseguradora", "un servicio de asistentes con inteligencia
    # artificial". Define como se presenta el asistente.
    giro: str = "una agencia inmobiliaria"
    # Para negocios sin catalogo: todo lo que el asistente sabe -servicios,
    # precios, como se contrata- va aqui, en texto. Es su unica fuente, y
    # por eso lo que no este escrito aqui no existe para el.
    conocimiento: str = ""
    moneda: str = "MXN"
    personalidad: str = ""
    reglas: list[str] = field(default_factory=list)
    adaptador: dict = field(default_factory=lambda: {"tipo": "demo", "config": {}})
    # Como se le avisa al asesor cuando entra un prospecto. Ver
    # notificaciones.py. Las contrasenas van en .env, nunca aqui: este
    # archivo se sube a git.
    notificaciones: dict = field(default_factory=dict)
    # De donde se saca el asesor asignado a cada propiedad, para avisarle
    # a el y no a un buzon general. Ver asesores.py.
    asesor_por_propiedad: dict = field(default_factory=dict)
    # Por donde mas le llegan clientes, ademas del chat del sitio. Hoy
    # WhatsApp; cada canal trae su id y de que variable sale su token.
    # Ver whatsapp.py.
    canales: dict = field(default_factory=dict)
    # Topes de gasto del chat publico: {"mensajes_por_ip": 20,
    # "mensajes_por_dia": 400}. Sin esto manda el valor del servidor.
    limites: dict = field(default_factory=dict)
    contacto: dict = field(default_factory=dict)
    marca: dict = field(default_factory=dict)
    origenes_permitidos: list[str] = field(default_factory=list)

    @classmethod
    def desde_json(cls, ruta: Path) -> "Inquilino":
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        conocidos = {f for f in cls.__dataclass_fields__}
        # Ignoramos claves extra en vez de reventar: un cliente puede dejar
        # notas en su JSON sin tumbar su propio asistente.
        return cls(**{k: v for k, v in datos.items() if k in conocidos})


class RegistroInquilinos:
    """Todos los inquilinos, con su adaptador ya construido."""

    def __init__(self, directorio: Path | None = None):
        self.directorio = Path(directorio or DIR_INQUILINOS)
        self._inquilinos: dict[str, Inquilino] = {}
        self._adaptadores: dict[str, object] = {}
        self.recargar()

    def recargar(self) -> None:
        from .adaptadores import construir

        self._inquilinos.clear()
        self._adaptadores.clear()
        if not self.directorio.is_dir():
            return
        for ruta in sorted(self.directorio.glob("*.json")):
            inq = Inquilino.desde_json(ruta)
            if not inq.activo:
                continue
            self._inquilinos[inq.id] = inq
            self._adaptadores[inq.id] = construir(
                inq.adaptador.get("tipo", "demo"),
                inq.adaptador.get("config", {}),
            )

    def obtener(self, id_inquilino: str) -> Inquilino | None:
        return self._inquilinos.get(id_inquilino)

    def adaptador(self, id_inquilino: str):
        return self._adaptadores.get(id_inquilino)

    def ids(self) -> list[str]:
        return sorted(self._inquilinos)


def leer_env(nombre: str, por_defecto: str = "") -> str:
    """Una variable de entorno, sin el espacio en blanco del copiar y pegar.

    Pegar una llave desde un editor se trae el salto de linea al final.
    Una cabecera HTTP no puede llevar saltos de linea, asi que la peticion
    ni siquiera sale y el error que da parece de red. Costo una tarde.
    """
    return str(os.environ.get(nombre) or por_defecto).strip()


def cargar_dotenv(ruta: Path | None = None) -> None:
    """Lee .env sin depender de python-dotenv.

    Una dependencia menos que instalar en el servidor de cada cliente.
    """
    archivo = Path(ruta or (RAIZ / ".env"))
    if not archivo.is_file():
        return
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        # Lo que ya este en el entorno real manda sobre el archivo.
        if clave and clave not in os.environ:
            os.environ[clave] = valor

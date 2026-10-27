"""Modelo de datos neutral del inventario.

Toda inmobiliaria que se conecte al producto termina aqui: sea NOCNOK, un
Excel, una API propia o una base de datos. El motor del agente solo conoce
estas dos clases, nunca el formato original de nadie.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def reparar_texto(texto: str) -> str:
    """Repara el texto que venia en UTF-8 y alguien leyo como latin-1.

    "Agua FrÃ­a" vuelve a ser "Agua Fria" con su acento, y "Nuevo LeÃ³n"
    vuelve a ser "Nuevo Leon" con el suyo. Pasa seguido con lo que captura
    un asesor en un CRM viejo, y el sitio del cliente suele mostrarlo tal
    cual. Una ficha fea se perdona; que el asistente se lo lea al cliente
    en una conversacion, no.

    Solo se queda con el arreglo si de verdad funciona: si el texto no
    tiene las marcas tipicas o el ida y vuelta falla, se devuelve igual.
    """
    if not texto or ("Ã" not in texto and "Â" not in texto):
        return texto
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


def normalizar(texto: str) -> str:
    """Minusculas y sin acentos, para comparar 'Cumbres' con 'cumbres'.

    Sin esto, un cliente que escribe "san pedro garza garcia" no encuentra
    "San Pedro Garza Garcia", que es como viene en el CRM.
    """
    if not texto:
        return ""
    plano = unicodedata.normalize("NFD", str(texto))
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", plano.lower()).strip()


@dataclass
class Propiedad:
    """Una propiedad, en el formato que entiende el agente."""

    id: str
    titulo: str = ""
    tipo: str = "Casa"              # Casa, Depto, Terreno, Local, Oficina, Bodega
    categoria: str = "Residencial"  # Residencial, Comercial, Industrial
    operacion: str = "Venta"        # Venta, Renta
    precio: float = 0.0
    precio_texto: str = ""
    moneda: str = "MXN"
    colonia: str = ""
    municipio: str = ""
    estado: str = ""
    recamaras: int = 0
    banos: float = 0
    estacionamientos: int = 0
    construccion_m2: float = 0.0
    terreno_m2: float = 0.0
    descripcion: str = ""
    amenidades: list[str] = field(default_factory=list)
    # Selecciones curadas del cliente: "Luxury", "Preventa", "Oportunidad".
    # No salen del CRM sino de una decision humana, y el asistente no puede
    # adivinarlas: o vienen marcadas, o para el no existen.
    etiquetas: list[str] = field(default_factory=list)
    imagenes: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def ubicacion(self) -> str:
        partes = [p for p in (self.colonia, self.municipio, self.estado) if p]
        return ", ".join(partes)

    @property
    def texto_busqueda(self) -> str:
        """Todo el texto libre de la propiedad, ya normalizado."""
        piezas = [
            self.titulo, self.descripcion, self.colonia,
            self.municipio, self.estado, " ".join(self.amenidades),
            " ".join(self.etiquetas),
        ]
        return normalizar(" ".join(piezas))

    def resumen(self) -> dict:
        """Version compacta que se le manda al modelo.

        Deliberadamente NO incluye la descripcion completa ni todas las
        imagenes: con 20 propiedades eso son miles de tokens que se pagan
        en cada turno de la conversacion.
        """
        d = {
            "id": self.id,
            "titulo": self.titulo,
            "tipo": self.tipo,
            "operacion": self.operacion,
            "precio": self.precio_texto or f"${self.precio:,.0f} {self.moneda}",
            "ubicacion": self.ubicacion,
            "recamaras": self.recamaras,
            "banos": self.banos,
            "estacionamientos": self.estacionamientos,
            "construccion_m2": self.construccion_m2,
            "terreno_m2": self.terreno_m2,
        }
        if self.amenidades:
            d["amenidades"] = self.amenidades[:8]
        # La etiqueta va primero en importancia aunque vaya al final aqui:
        # es lo que distingue una casa cara de una que la inmobiliaria
        # decidio poner en su seleccion Luxury.
        if self.etiquetas:
            d["etiquetas"] = self.etiquetas
        return {k: v for k, v in d.items() if v not in (None, "", 0, 0.0, [])}

    def ficha(self) -> dict:
        """Version completa, para cuando el cliente pide detalle de UNA."""
        d = self.resumen()
        d["descripcion"] = (self.descripcion or "")[:1200]
        d["amenidades"] = self.amenidades
        d["imagenes"] = self.imagenes[:5]
        d["url"] = self.url
        d["categoria"] = self.categoria
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# Frontera entre un precio de compra y una renta mensual, en pesos.
#
# Medido contra el inventario real de RedNorte (548 propiedades):
#   - ventas por debajo del umbral:   0 de 269  (la mas barata es $330,000)
#   - rentas por encima del umbral:  25 de 279  (9%), TODAS comerciales o
#     industriales: naves, locales, oficinas y un edificio completo, de
#     $200,000 a $1,000,000 al mes.
#
# O sea que solo se equivoca con rentas comerciales grandes, y unicamente
# cuando la persona no dijo si compra o renta. Quien renta una nave de un
# millon al mes dice "renta"; ademas el prompt tiene instruccion de
# preguntarlo. Es una red de seguridad, no el mecanismo principal.
UMBRAL_VENTA_VS_RENTA = 200_000


@dataclass
class Filtros:
    """Lo que el agente extrae de lo que dijo el cliente en lenguaje natural."""

    operacion: str | None = None
    tipo: list[str] = field(default_factory=list)
    etiqueta: str | None = None
    municipio: str | None = None
    colonia: str | None = None
    recamaras_min: int | None = None
    banos_min: float | None = None
    estacionamientos_min: int | None = None
    precio_min: float | None = None
    precio_max: float | None = None
    construccion_min: float | None = None
    terreno_min: float | None = None
    texto: str | None = None
    limite: int = 5

    @classmethod
    def desde_dict(cls, d: dict) -> "Filtros":
        """Construye Filtros desde lo que mando el modelo, tolerando basura.

        El modelo puede mandar 'tipo' como string en vez de lista, o un
        precio como '4000000 MXN'. Preferimos limpiar a reventar.
        """
        d = d or {}

        def num(v):
            if v is None or v == "":
                return None
            if isinstance(v, (int, float)):
                return float(v)
            limpio = re.sub(r"[^\d.]", "", str(v))
            return float(limpio) if limpio else None

        def ent(v):
            n = num(v)
            return int(n) if n is not None else None

        tipo = d.get("tipo") or []
        if isinstance(tipo, str):
            tipo = [tipo]

        operacion = d.get("operacion") or None
        precio_max = num(d.get("precio_max"))

        # Red de seguridad: sin operacion, un presupuesto de compra deja pasar
        # rentas (13,000/mes "cabe" en 2,000,000) y el cliente recibe basura.
        # El agente tiene instruccion de preguntar compra-o-renta, pero el
        # filtro no puede depender de que siempre lo haga bien.
        if operacion is None and precio_max is not None:
            operacion = "Venta" if precio_max >= UMBRAL_VENTA_VS_RENTA else "Renta"

        return cls(
            operacion=operacion,
            tipo=[str(t) for t in tipo if t],
            municipio=(d.get("municipio") or None),
            colonia=(d.get("colonia") or None),
            recamaras_min=ent(d.get("recamaras_min")),
            banos_min=num(d.get("banos_min")),
            estacionamientos_min=ent(d.get("estacionamientos_min")),
            precio_min=num(d.get("precio_min")),
            precio_max=precio_max,
            construccion_min=num(d.get("construccion_min")),
            terreno_min=num(d.get("terreno_min")),
            etiqueta=(d.get("etiqueta") or None),
            texto=(d.get("texto") or None),
            limite=max(1, min(int(d.get("limite") or 5), 12)),
        )

    def descripcion_humana(self) -> str:
        """Para explicarle al cliente que fue lo que se busco."""
        p = []
        if self.etiqueta:
            p.append(self.etiqueta)
        if self.operacion:
            p.append(self.operacion.lower())
        if self.tipo:
            p.append("/".join(self.tipo).lower())
        if self.colonia:
            p.append(f"en {self.colonia}")
        elif self.municipio:
            p.append(f"en {self.municipio}")
        if self.recamaras_min:
            p.append(f"{self.recamaras_min}+ recamaras")
        if self.precio_max:
            p.append(f"hasta ${self.precio_max:,.0f}")
        return " ".join(p) or "sin filtros"

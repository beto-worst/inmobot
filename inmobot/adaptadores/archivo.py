"""Adaptador para inmobiliarias SIN CRM: Excel, CSV o Google Sheets.

Es el caso mas comun en el mercado mexicano: agencias chicas que llevan su
inventario en una hoja de calculo. No se les puede pedir que migren a un CRM
para poder usar Athena; hay que leer su archivo tal como lo tienen.

Por eso el adaptador reconoce solo los nombres de columna que se usan en
espanol ("Recamaras", "Habitaciones", "Precio de venta", ...) y tolera los
valores como los escribe una persona: "$3,850,000", "3.85 MDP", "3 rec",
"180 m2". Si la hoja usa nombres raros, se mapean a mano desde el archivo
del inquilino sin tocar codigo.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import httpx

from ..modelos import Propiedad, normalizar
from .base import AdaptadorInventario

# Nombres de columna que se reconocen solos. La clave es el campo de
# Propiedad; los valores son como los escriben las agencias en la vida real.
ALIAS = {
    "id": ["id", "codigo", "clave", "folio", "referencia", "ref", "no"],
    "titulo": ["titulo", "nombre", "propiedad", "inmueble", "encabezado"],
    "tipo": ["tipo", "tipo de propiedad", "tipo de inmueble", "clase"],
    "operacion": ["operacion", "tipo de operacion", "modalidad", "venta o renta",
                  "venta renta"],
    "precio": ["precio", "precio de venta", "precio venta", "valor", "monto",
               "costo", "precio de lista"],
    "precio_renta": ["precio de renta", "precio renta", "renta", "renta mensual",
                     "mensualidad"],
    "colonia": ["colonia", "fraccionamiento", "sector", "zona", "privada",
                "asentamiento"],
    "municipio": ["municipio", "ciudad", "delegacion", "alcaldia", "poblacion"],
    "estado": ["estado", "entidad"],
    "recamaras": ["recamaras", "habitaciones", "cuartos", "dormitorios", "rec",
                  "num recamaras", "no de recamaras"],
    "banos": ["banos", "sanitarios", "wc", "num banos", "no de banos",
              "banos completos"],
    "estacionamientos": ["estacionamientos", "cocheras", "cajones", "garage",
                         "garaje", "autos", "estacionamiento"],
    "construccion_m2": ["construccion", "m2 construccion", "metros construccion",
                        "superficie construida", "m2 const", "construidos",
                        "metros de construccion"],
    "terreno_m2": ["terreno", "m2 terreno", "metros terreno", "superficie terreno",
                   "lote", "m2 lote", "metros de terreno"],
    "descripcion": ["descripcion", "detalles", "observaciones", "comentarios",
                    "notas"],
    "amenidades": ["amenidades", "caracteristicas", "extras", "servicios"],
    "imagenes": ["imagenes", "fotos", "fotografias", "url fotos", "link fotos",
                 "liga fotos", "imagen", "foto"],
    "url": ["url", "liga", "link", "enlace", "ficha", "pagina"],
}

TIPOS = {
    "casa": "Casa", "casas": "Casa", "residencia": "Casa", "quinta": "Casa",
    "depto": "Depto", "departamento": "Depto", "depa": "Depto", "loft": "Depto",
    "terreno": "Terreno", "lote": "Terreno", "predio": "Terreno",
    "local": "Local", "local comercial": "Local", "comercial": "Local",
    "oficina": "Oficina", "consultorio": "Oficina",
    "bodega": "Bodega", "nave": "Bodega", "nave industrial": "Bodega",
}

MULTIPLICADOR = [
    (r"\bmdp\b|\bmillones?\b|\bmill?\b", 1_000_000),
    (r"\bmil\b", 1_000),
]


class ErrorArchivo(RuntimeError):
    """El archivo de inventario no se pudo leer o no tiene las columnas minimas."""


class AdaptadorArchivo(AdaptadorInventario):
    """Lee el inventario de un .xlsx, .csv o una URL que devuelva CSV.

    Config del inquilino:
        ruta      Ruta local o URL. Una hoja de Google publicada como CSV sirve.
        hoja      Nombre de la pestana del Excel (opcional; por defecto la primera)
        columnas  Mapeo manual {campo_de_athena: "Nombre exacto de la columna"}.
                  Solo hace falta para las columnas que no se reconozcan solas.
        moneda    MXN por defecto.
    """

    tipo = "archivo"

    def __init__(self, config: dict | None = None, ttl_segundos: int = 300):
        super().__init__(config, ttl_segundos)
        self.ruta = str(self.config.get("ruta", "")).strip()
        self.hoja = self.config.get("hoja")
        self.moneda = self.config.get("moneda", "MXN")
        self.mapeo_manual = {
            campo: normalizar(col)
            for campo, col in (self.config.get("columnas") or {}).items()
        }
        # Se llena en cada carga; lo lee el reporte de diagnostico.
        self.avisos: list[str] = []

    async def _descargar(self) -> list[Propiedad]:
        filas = await self._leer_filas()
        if not filas:
            raise ErrorArchivo(f"El archivo '{self.ruta}' no tiene filas de datos.")

        columnas = self._resolver_columnas(filas[0].keys())
        if not columnas.get("precio") and not columnas.get("precio_renta"):
            raise ErrorArchivo(
                "No se encontro ninguna columna de precio. Agrega una columna "
                "'Precio' al archivo, o mapeala desde el inquilino con "
                "\"columnas\": {\"precio\": \"NombreDeTuColumna\"}."
            )

        self.avisos = []
        props: list[Propiedad] = []
        for i, fila in enumerate(filas, start=2):  # fila 1 = encabezados
            prop = self._construir(fila, columnas, numero_fila=i)
            if prop is not None:
                props.append(prop)

        if not props:
            raise ErrorArchivo(
                "Se leyo el archivo pero ninguna fila quedo utilizable. "
                + (" ".join(self.avisos[:3]) if self.avisos else "")
            )
        return props

    # ---- lectura ----
    async def _leer_filas(self) -> list[dict]:
        if not self.ruta:
            raise ErrorArchivo("Falta 'ruta' en la config del inquilino.")

        if self.ruta.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
                r = await c.get(self.ruta)
            if r.status_code != 200:
                raise ErrorArchivo(
                    f"La URL del inventario respondio {r.status_code}. "
                    f"Si es una hoja de Google, publicala como CSV "
                    f"(Archivo > Compartir > Publicar en la web > CSV)."
                )
            return self._leer_csv(r.text)

        ruta = Path(self.ruta)
        if not ruta.is_file():
            raise ErrorArchivo(f"No existe el archivo '{ruta}'.")

        if ruta.suffix.lower() in (".xlsx", ".xlsm"):
            return self._leer_excel(ruta)
        # utf-8-sig se come el BOM que deja Excel al exportar a CSV.
        return self._leer_csv(ruta.read_text(encoding="utf-8-sig"))

    def _leer_csv(self, texto: str) -> list[dict]:
        muestra = texto[:4000]
        try:
            dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
        except csv.Error:
            dialecto = csv.excel  # una sola columna, o formato raro: coma
        return [f for f in csv.DictReader(io.StringIO(texto), dialect=dialecto)]

    def _leer_excel(self, ruta: Path) -> list[dict]:
        try:
            import openpyxl
        except ImportError as err:
            raise ErrorArchivo(
                "Para leer Excel falta la libreria openpyxl: pip install openpyxl"
            ) from err

        libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
        hoja = libro[self.hoja] if self.hoja else libro.worksheets[0]

        filas = hoja.iter_rows(values_only=True)
        encabezados = next(filas, None)
        if not encabezados:
            raise ErrorArchivo(f"La hoja '{hoja.title}' esta vacia.")
        nombres = [str(h).strip() if h is not None else "" for h in encabezados]

        salida = []
        for valores in filas:
            if all(v is None or str(v).strip() == "" for v in valores):
                continue  # fila en blanco de relleno
            salida.append(dict(zip(nombres, valores)))
        libro.close()
        return salida

    # ---- mapeo de columnas ----
    def _resolver_columnas(self, encabezados) -> dict[str, str]:
        reales = {normalizar(h): h for h in encabezados if h}
        columnas: dict[str, str] = {}

        for campo, col_norm in self.mapeo_manual.items():
            if col_norm in reales:
                columnas[campo] = reales[col_norm]

        for campo, alias in ALIAS.items():
            if campo in columnas:
                continue
            for a in alias:
                if a in reales:
                    columnas[campo] = reales[a]
                    break
        return columnas

    # ---- construccion de una propiedad ----
    def _construir(self, fila: dict, cols: dict, numero_fila: int) -> Propiedad | None:
        def crudo(campo):
            col = cols.get(campo)
            if not col:
                return None
            v = fila.get(col)
            return None if v is None or str(v).strip() == "" else v

        venta = _num(crudo("precio"))
        renta = _num(crudo("precio_renta"))

        # Si solo hay una columna de precio, la operacion decide como leerla.
        operacion = _operacion(crudo("operacion"))
        if renta and not venta:
            operacion, precio = "Renta", renta
        elif venta and not renta:
            precio = venta
            operacion = operacion or "Venta"
        elif venta and renta:
            # Las dos llenas: gana la operacion declarada; si no hay, venta.
            precio = renta if operacion == "Renta" else venta
            operacion = operacion or "Venta"
        else:
            self.avisos.append(f"Fila {numero_fila}: sin precio, se omitio.")
            return None

        identificador = crudo("id")
        identificador = (str(identificador).strip() if identificador
                         else f"FILA-{numero_fila}")

        tipo = _tipo(crudo("tipo")) or _tipo(crudo("titulo")) or "Casa"
        sufijo = "/mes" if operacion == "Renta" else ""

        return Propiedad(
            id=identificador,
            titulo=str(crudo("titulo") or f"{tipo} en {crudo('colonia') or 'venta'}"),
            tipo=tipo,
            categoria=("Comercial" if tipo in ("Local", "Oficina")
                       else "Industrial" if tipo == "Bodega" else "Residencial"),
            operacion=operacion,
            precio=precio,
            precio_texto=f"${precio:,.0f}{sufijo}",
            moneda=self.moneda,
            colonia=_texto(crudo("colonia")),
            municipio=_texto(crudo("municipio")),
            estado=_texto(crudo("estado")),
            recamaras=int(_num(crudo("recamaras")) or 0),
            banos=_num(crudo("banos")) or 0,
            estacionamientos=int(_num(crudo("estacionamientos")) or 0),
            construccion_m2=_num(crudo("construccion_m2")) or 0,
            terreno_m2=_num(crudo("terreno_m2")) or 0,
            descripcion=_texto(crudo("descripcion")),
            amenidades=_lista(crudo("amenidades")),
            imagenes=_lista(crudo("imagenes")),
            url=_texto(crudo("url")),
        )

    async def diagnostico(self) -> dict:
        """Reporte para cuando das de alta a un cliente nuevo.

        Dice que columnas se reconocieron, cuales no, y que tan completo esta
        el inventario. Es lo que corres ANTES de prometerle que funciona.
        """
        try:
            filas = await self._leer_filas()
        except ErrorArchivo as err:
            return {"ok": False, "error": str(err)}

        cols = self._resolver_columnas(filas[0].keys() if filas else [])
        props = await self.cargar(forzar=True)

        faltantes = lambda f: sum(1 for p in props if not f(p))  # noqa: E731
        return {
            "ok": True,
            "filas_en_archivo": len(filas),
            "propiedades_usables": len(props),
            "columnas_reconocidas": {k: v for k, v in sorted(cols.items())},
            "columnas_ignoradas": sorted(
                h for h in (filas[0].keys() if filas else [])
                if h and h not in cols.values()
            ),
            "sin_zona": faltantes(lambda p: p.colonia or p.municipio),
            "sin_recamaras": faltantes(lambda p: p.recamaras),
            "sin_fotos": faltantes(lambda p: p.imagenes),
            "avisos": self.avisos[:10],
        }


# ---- normalizadores de valores escritos por personas ----
def _texto(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float | None:
    """Convierte '$3,850,000', '3.85 MDP', '180 m2' o '3 rec' a numero."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip().lower()
    if not s:
        return None

    factor = 1
    for patron, mult in MULTIPLICADOR:
        if re.search(patron, s):
            factor = mult
            break

    # Se queda con el primer numero del texto: "3 recamaras y 2 banos" -> 3
    m = re.search(r"\d[\d,.\s]*", s)
    if not m:
        return None
    limpio = m.group(0).replace(" ", "").replace(",", "").rstrip(".")
    try:
        return float(limpio) * factor
    except ValueError:
        return None


def _operacion(v) -> str | None:
    s = normalizar(_texto(v))
    if not s:
        return None
    if "renta" in s or "alquil" in s or "arrenda" in s:
        return "Renta"
    if "venta" in s or "vende" in s:
        return "Venta"
    return None


def _tipo(v) -> str | None:
    s = normalizar(_texto(v))
    if not s:
        return None
    if s in TIPOS:
        return TIPOS[s]
    for clave, valor in TIPOS.items():
        if re.search(rf"\b{re.escape(clave)}\b", s):
            return valor
    return None


def _lista(v) -> list[str]:
    """Separa 'alberca, jardin; asador' en una lista."""
    s = _texto(v)
    if not s:
        return []
    partes = re.split(r"[;,|\n]+", s)
    return [p.strip() for p in partes if p.strip()]

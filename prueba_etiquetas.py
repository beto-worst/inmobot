"""Las selecciones curadas: que filtren bien y que no mientan.

Una propiedad es Luxury porque la agencia la aprobo, no porque sea cara.
Esa diferencia es todo el punto: si el asistente deduce la etiqueta del
precio, le esta diciendo "Luxury" a algo que la agencia no aprobo, y eso
es exactamente lo que no debe pasar.

Correr:  python prueba_etiquetas.py
"""
from __future__ import annotations

import asyncio
import sys

from inmobot.adaptadores.base import AdaptadorInventario
from inmobot.modelos import Filtros, Propiedad, reparar_texto

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fallos = 0


def revisar(titulo: str, ok: bool, detalle: str = "") -> None:
    global fallos
    if not ok:
        fallos += 1
    print(f"  [{'ok ' if ok else 'FALLA'}] {titulo}" + (f"  — {detalle}" if detalle else ""))


class Inventario(AdaptadorInventario):
    """Cuatro propiedades: la mas cara NO es Luxury, a proposito."""

    tipo = "prueba"

    async def _descargar(self) -> list[Propiedad]:
        return [
            Propiedad(id="L1", titulo="Casa en Valle Alto", operacion="Venta",
                      precio=8_000_000, colonia="Valle Alto", municipio="Monterrey",
                      recamaras=4, etiquetas=["Luxury"]),
            Propiedad(id="L2", titulo="Casa en Carretera Nacional", operacion="Venta",
                      precio=6_500_000, colonia="La Rioja", municipio="Monterrey",
                      recamaras=3, etiquetas=["Luxury"]),
            Propiedad(id="N1", titulo="Casa en San Pedro", operacion="Venta",
                      precio=19_000_000, colonia="Del Valle", municipio="San Pedro",
                      recamaras=5),
            Propiedad(id="N2", titulo="Casa en Cumbres", operacion="Venta",
                      precio=2_800_000, colonia="Cumbres", municipio="Monterrey",
                      recamaras=3),
        ]


async def main() -> None:
    inv = Inventario()

    print("\nPEDIR LUXURY")
    r = await inv.buscar(Filtros(etiqueta="Luxury", operacion="Venta"))
    ids = [p["id"] for p in r["resultados"]]
    revisar("solo devuelve las aprobadas", set(ids) == {"L1", "L2"}, str(ids))
    revisar("la mas cara del inventario NO se cuela", "N1" not in ids,
            "N1 son 19 millones y no esta aprobada")
    revisar("sin nota: habia justo lo que se pidio", not r.get("nota"),
            str(r.get("nota"))[:60])

    print("\nEL MODELO VE LA ETIQUETA")
    etiquetadas = [p for p in r["resultados"] if p.get("etiquetas")]
    revisar("las Luxury llegan marcadas al modelo", len(etiquetadas) == 2,
            str([p.get("etiquetas") for p in r["resultados"]]))
    todas = await inv.buscar(Filtros(operacion="Venta", limite=12))
    sin_marca = [p["id"] for p in todas["resultados"] if not p.get("etiquetas")]
    revisar("las que no son Luxury no traen la marca", set(sin_marca) == {"N1", "N2"},
            str(sin_marca))

    print("\nPEDIR LUXURY DONDE NO HAY NINGUNA")
    # Lo correcto aqui no es mostrar casas de Cumbres que no son Luxury:
    # es mostrar Luxury de otra zona y avisar que se movio la zona. Quien
    # pide Luxury pide Luxury; la colonia es lo negociable.
    r = await inv.buscar(Filtros(etiqueta="Luxury", operacion="Venta",
                                 colonia="Cumbres"))
    nota = r.get("nota") or ""
    revisar("devuelve algo en vez de dejar al cliente sin nada",
            bool(r["resultados"]), f"{len(r['resultados'])} resultados")
    revisar("y lo que devuelve SIGUE siendo Luxury",
            all(p.get("etiquetas") == ["Luxury"] for p in r["resultados"]),
            str([p.get("etiquetas") for p in r["resultados"]]))
    revisar("avisando que lo que cedio fue la zona", "Cumbres" in nota, nota[:80])

    print("\nPEDIR UNA SELECCION QUE NO EXISTE")
    r = await inv.buscar(Filtros(etiqueta="Preventa", operacion="Venta"))
    revisar("no inventa una seleccion que nadie aprobo",
            "NO son Preventa" in (r.get("nota") or ""), (r.get("nota") or "")[:90])

    print("\nTEXTO QUE VIENE ROTO DEL CRM")
    roto = "Terreno en Venta en Agua FrÃ\xada, Apodaca, Nuevo LeÃ³n"
    revisar("lo repara",
            reparar_texto(roto) == "Terreno en Venta en Agua Fría, Apodaca, Nuevo León",
            reparar_texto(roto))
    revisar("no toca el texto que ya esta bien",
            reparar_texto("Jardín con alberca") == "Jardín con alberca")
    revisar("no toca el texto sin acentos",
            reparar_texto("Casa en Cumbres Elite") == "Casa en Cumbres Elite")
    # Si el arreglo no sale limpio, mas vale dejarlo feo que empeorarlo.
    raro = "PreciosÃ³ ðŸ "
    revisar("si no puede repararlo, lo deja igual", reparar_texto(raro) == raro)

    print("\n" + ("TODO BIEN" if not fallos else f"{fallos} PRUEBAS FALLARON"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    asyncio.run(main())

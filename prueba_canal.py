"""Prueba el canal que el sitio del cliente expone para el asistente.

Contesta, en un solo golpe, las tres preguntas que importan antes de poner
a Athena frente a un cliente de ese sitio:

  1. ¿El canal responde y con cuantas propiedades?
  2. ¿Vienen llenos los campos con los que se busca? (sin colonia, buscar
     por zona no sirve; sin precio, el presupuesto no filtra)
  3. ¿El asesor de una propiedad real se puede consultar?

Correr:  python prueba_canal.py [inquilino]     (por defecto: rednorte)
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from inmobot.adaptadores.nocnok import ErrorInventario
from inmobot.asesores import BuscadorAsesor
from inmobot.config import RegistroInquilinos
from inmobot.modelos import Filtros

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def barra(n: int, total: int, ancho: int = 26) -> str:
    if not total:
        return ""
    llenos = round(ancho * n / total)
    return "#" * llenos + "." * (ancho - llenos)


async def main() -> None:
    nombre = sys.argv[1] if len(sys.argv) > 1 else "rednorte"
    reg = RegistroInquilinos()
    inq = reg.obtener(nombre)
    adaptador = reg.adaptador(nombre)

    print(f"Inquilino : {inq.nombre}  (asistente: {inq.asistente})")
    print(f"Canal     : {getattr(adaptador, 'url', '?')}")
    print("\nBajando inventario...")

    try:
        props = await adaptador.cargar()
    except ErrorInventario as e:
        # Es una prueba de diagnostico: el mensaje es el resultado, no un
        # error del programa. Un traceback aqui solo estorba.
        print(f"\nNo se pudo: {e}")
        return
    total = len(props)
    print(f"OK: {total} propiedades\n")

    print("=" * 66)
    print("CAMPOS QUE USA LA BUSQUEDA")
    print("=" * 66)
    campos = {
        "colonia": lambda p: bool(p.colonia),
        "municipio": lambda p: bool(p.municipio),
        "precio": lambda p: p.precio > 0,
        "recamaras": lambda p: p.recamaras > 0,
        "banos": lambda p: p.banos > 0,
        "construccion_m2": lambda p: p.construccion_m2 > 0,
        "descripcion": lambda p: len(p.descripcion) > 40,
        "url": lambda p: bool(p.url),
        "imagen": lambda p: bool(p.imagenes),
    }
    for campo, lleno in campos.items():
        n = sum(1 for p in props if lleno(p))
        print(f"  {campo:16} {barra(n, total)}  {n:4}/{total}  {n/total:5.0%}")

    print("\n" + "=" * 66)
    print("COMO SE REPARTE EL INVENTARIO")
    print("=" * 66)
    for etiqueta, valores in (
        ("Operacion", Counter(p.operacion for p in props)),
        ("Tipo", Counter(p.tipo for p in props)),
        ("Municipio", Counter(p.municipio or "(vacio)" for p in props)),
    ):
        print(f"\n  {etiqueta}:")
        for valor, n in valores.most_common(6):
            print(f"    {valor:28} {n:4}")

    print("\n" + "=" * 66)
    print("UNA BUSQUEDA COMO LA HARIA UN CLIENTE")
    print("=" * 66)
    f = Filtros(operacion="Venta", recamaras_min=3, precio_max=3_000_000)
    print(f"  Pide: {f.descripcion_humana()}")
    r = await adaptador.buscar(f)
    print(f"  Coincidencias: {r['total_coincidencias']}")
    if r.get("nota"):
        print(f"  Nota: {r['nota']}")
    for p in r["resultados"][:3]:
        print(f"    - {p['titulo']}  {p['precio']}  ({p['ubicacion']})")

    print("\n" + "=" * 66)
    print("ASESOR DE UNA PROPIEDAD REAL")
    print("=" * 66)
    config = inq.asesor_por_propiedad or {}
    if not config.get("url"):
        print("  Este inquilino no tiene configurada la busqueda de asesor.")
        return

    buscador = BuscadorAsesor(config)
    for p in props[:3]:
        asesor = await buscador.buscar(p.id)
        if asesor:
            print(f"  {p.id:12} -> {asesor['nombre']}  {asesor['telefono']}")
        else:
            print(f"  {p.id:12} -> sin asesor propio (cae al contacto general)")


if __name__ == "__main__":
    asyncio.run(main())

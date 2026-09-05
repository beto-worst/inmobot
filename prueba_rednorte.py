"""Diagnostico del inventario real de RedNorte.

Contesta lo que no se puede saber con datos de prueba: si los campos que usa
la busqueda vienen llenos. Si 'settlement' llega vacio, buscar por colonia no
sirve y hay que replantear esa parte antes de ponerlo frente a un cliente.

No necesita la llave de NOCNOK: usa el proxy publico del propio sitio.

Correr:  python prueba_rednorte.py
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from inmobot.adaptadores.nocnok import AdaptadorNocnok
from inmobot.config import RegistroInquilinos
from inmobot.modelos import Filtros

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def barra(n: int, total: int, ancho: int = 28) -> str:
    if not total:
        return ""
    llenos = round(ancho * n / total)
    return "█" * llenos + "·" * (ancho - llenos)


async def main() -> None:
    reg = RegistroInquilinos()
    inq = reg.obtener("rednorte")
    adaptador: AdaptadorNocnok = reg.adaptador("rednorte")

    print(f"Inquilino : {inq.nombre}  (asistente: {inq.asistente})")
    print(f"Fuente    : {adaptador.proxy_url or 'API directa de NOCNOK'}")
    print("\nBajando inventario completo...")
    props = await adaptador.cargar()
    total = len(props)
    print(f"OK: {total} propiedades\n")

    print("=" * 72)
    print("COMPLETITUD DE LOS CAMPOS QUE USA LA BUSQUEDA")
    print("=" * 72)
    campos = [
        ("colonia (settlement)", lambda p: bool(p.colonia)),
        ("municipio", lambda p: bool(p.municipio)),
        ("estado", lambda p: bool(p.estado)),
        ("precio > 0", lambda p: p.precio > 0),
        ("recamaras > 0", lambda p: p.recamaras > 0),
        ("banos > 0", lambda p: p.banos > 0),
        ("construccion m2", lambda p: p.construccion_m2 > 0),
        ("terreno m2", lambda p: p.terreno_m2 > 0),
        ("tiene fotos", lambda p: bool(p.imagenes)),
        ("descripcion", lambda p: len(p.descripcion) > 20),
        ("amenidades", lambda p: bool(p.amenidades)),
    ]
    for etiqueta, prueba in campos:
        n = sum(1 for p in props if prueba(p))
        pct = 100 * n / total if total else 0
        alerta = "  <-- REVISAR" if pct < 60 else ""
        print(f"  {etiqueta:22} {n:>4}/{total}  {pct:5.1f}%  {barra(n, total)}{alerta}")

    print("\n" + "=" * 72)
    print("COMO SE REPARTE EL INVENTARIO")
    print("=" * 72)
    for titulo, valores in [
        ("operacion", [p.operacion for p in props]),
        ("tipo", [p.tipo for p in props]),
        ("categoria", [p.categoria for p in props]),
    ]:
        cuenta = Counter(valores).most_common()
        print(f"\n  {titulo}:")
        for valor, n in cuenta:
            print(f"    {valor:14} {n:>4}  {barra(n, total, 20)}")

    print("\n  municipios con mas inventario:")
    for valor, n in Counter(p.municipio for p in props if p.municipio).most_common(10):
        print(f"    {valor:26} {n:>4}")

    print("\n  colonias con mas inventario:")
    for valor, n in Counter(p.colonia for p in props if p.colonia).most_common(10):
        print(f"    {valor:26} {n:>4}")

    ventas = sorted(p.precio for p in props if p.operacion == "Venta" and p.precio > 0)
    rentas = sorted(p.precio for p in props if p.operacion == "Renta" and p.precio > 0)
    print("\n  precios de venta : "
          f"${ventas[0]:,.0f} a ${ventas[-1]:,.0f} "
          f"(mediana ${ventas[len(ventas)//2]:,.0f})" if ventas else "  sin ventas")
    print("  precios de renta : "
          f"${rentas[0]:,.0f} a ${rentas[-1]:,.0f} "
          f"(mediana ${rentas[len(rentas)//2]:,.0f})" if rentas else "  sin rentas")

    print("\n" + "=" * 72)
    print("BUSQUEDAS REALES CONTRA ESTE INVENTARIO")
    print("=" * 72)
    consultas = [
        ("Casa en venta en Cumbres, 3 rec, hasta 5 MDP",
         {"operacion": "Venta", "tipo": ["Casa"], "colonia": "Cumbres",
          "recamaras_min": 3, "precio_max": 5_000_000}),
        ("Depto en renta en Monterrey hasta 20 mil",
         {"operacion": "Renta", "tipo": ["Depto"], "municipio": "Monterrey",
          "precio_max": 20_000}),
        ("Casa en San Pedro, 4 recamaras",
         {"operacion": "Venta", "tipo": ["Casa"], "municipio": "San Pedro",
          "recamaras_min": 4}),
        ("Algo con alberca en Santiago",
         {"operacion": "Venta", "municipio": "Santiago", "texto": "alberca"}),
    ]
    for titulo, filtros in consultas:
        r = await adaptador.buscar(Filtros.desde_dict({**filtros, "limite": 3}))
        print(f"\n  {titulo}")
        print(f"    -> {r['total_coincidencias']} coincidencias")
        if r["nota"]:
            print(f"    NOTA: {r['nota'][:80]}")
        for x in r["resultados"]:
            print(f"      - {x['titulo'][:52]:54} {x['precio']:>14}")

    assert total > 500, f"esperaba mas de 500 propiedades, hubo {total}"
    print(f"\nOK: inventario real de RedNorte cargado y buscable ({total} propiedades)")


if __name__ == "__main__":
    asyncio.run(main())

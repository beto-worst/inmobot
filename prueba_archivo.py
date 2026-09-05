"""Prueba del adaptador de Excel/CSV con una hoja desordenada de verdad.

Simula lo que manda una inmobiliaria chica: encabezados con acentos, sin
columna de id, precios escritos a mano ("$3,850,000", "3.85 MDP"), medidas
con unidades pegadas ("180 m2"), filas en blanco y una fila sin precio.

Correr:  python prueba_archivo.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from inmobot.adaptadores.archivo import AdaptadorArchivo
from inmobot.modelos import Filtros

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENCABEZADOS = ["Propiedad", "Tipo de Inmueble", "Operación", "Precio de Venta",
               "Colonia", "Municipio", "Recámaras", "Baños", "Cocheras",
               "M2 Construcción", "M2 Terreno", "Características", "Notas del asesor"]

FILAS = [
    ["Casa en Cumbres Elite", "Casa", "Venta", "$3,850,000", "Cumbres Elite",
     "Monterrey", "3", "2.5", "2", "180 m2", "160 m2",
     "Privada con caseta, cocina integral, patio", "Lista para entrega"],
    ["Casa Cumbres San Agustín", "casa", "VENTA", "3.99 MDP", "Cumbres San Agustín",
     "Monterrey", "3 rec", "3", "2", "195", "170",
     "Remodelada; closets de madera", "Dueño flexible en fecha"],
    ["Depto San Jerónimo", "Departamento", "Venta", "2,950,000", "San Jerónimo",
     "Monterrey", "2", "2", "1", "95 m2", "", "Gimnasio, alberca, roof garden", ""],
    [None, None, None, None, None, None, None, None, None, None, None, None, None],
    ["Casa en renta Las Torres", "Casa", "Renta", "$13,000", "Las Torres",
     "Monterrey", "3", "1.5", "1", "120", "140", "Patio", "Disponible ya"],
    ["Terreno Carretera Nacional", "Terreno", "Venta", "3.1 millones",
     "La Estanzuela", "Monterrey", "", "", "", "", "520 m2",
     "Servicios instalados", ""],
    ["Casa SIN PRECIO (debe omitirse)", "Casa", "Venta", "", "Contry",
     "Monterrey", "3", "2", "2", "150", "200", "", "Falta que el dueño defina"],
    ["Local en Apodaca", "Local Comercial", "Renta", "$28,000", "Parque Industrial",
     "Apodaca", "", "2", "6", "210 m2", "", "Sobre avenida", ""],
]


def crear_excel(destino: Path) -> None:
    import openpyxl
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Inventario"
    hoja.append(ENCABEZADOS)
    for f in FILAS:
        hoja.append(f)
    libro.save(destino)


async def main() -> None:
    carpeta = Path(tempfile.mkdtemp(prefix="athena_"))
    archivo = carpeta / "inventario_agencia.xlsx"
    crear_excel(archivo)
    print(f"Excel de prueba: {archivo}\n")

    adaptador = AdaptadorArchivo({"ruta": str(archivo)})

    print("=" * 70)
    print("DIAGNOSTICO (lo que corres al dar de alta un cliente nuevo)")
    print("=" * 70)
    d = await adaptador.diagnostico()
    if not d["ok"]:
        print("ERROR:", d["error"])
        return

    print(f"filas en el archivo    : {d['filas_en_archivo']}")
    print(f"propiedades usables    : {d['propiedades_usables']}")
    print(f"sin zona               : {d['sin_zona']}")
    print(f"sin recamaras          : {d['sin_recamaras']}")
    print(f"sin fotos              : {d['sin_fotos']}")
    print("\ncolumnas reconocidas solas:")
    for campo, col in d["columnas_reconocidas"].items():
        print(f"  {campo:20} <- '{col}'")
    if d["columnas_ignoradas"]:
        print(f"\ncolumnas ignoradas: {d['columnas_ignoradas']}")
    if d["avisos"]:
        print("\navisos:")
        for a in d["avisos"]:
            print("  -", a)

    print("\n" + "=" * 70)
    print("COMO QUEDARON LOS VALORES SUCIOS")
    print("=" * 70)
    for p in await adaptador.cargar():
        print(f"  {p.id:10} {p.titulo[:32]:34} {p.precio_texto:>14}  "
              f"{p.tipo:8} {p.operacion:6} {p.recamaras}rec "
              f"{p.construccion_m2:g}m2")

    print("\n" + "=" * 70)
    print("Y ATHENA PUEDE BUSCAR EN EL, IGUAL QUE EN UN CRM")
    print("=" * 70)
    r = await adaptador.buscar(Filtros.desde_dict(
        {"operacion": "Venta", "tipo": ["Casa"], "colonia": "Cumbres",
         "recamaras_min": 3, "precio_max": 4000000}))
    print(f"busqueda: {r['busqueda']} -> {r['total_coincidencias']} coincidencias")
    for x in r["resultados"]:
        print("  -", x["titulo"], "|", x["precio"], "|", x["ubicacion"])

    # 8 renglones en FILAS, menos el que va en blanco y menos el que no
    # trae precio: 6 propiedades utilizables.
    esperado = 6
    usables = d["propiedades_usables"]
    assert usables == esperado, f"esperaba {esperado} usables, hubo {usables}"
    assert r["total_coincidencias"] == 2, r
    print("\nOK: el Excel sucio quedo utilizable sin tocar codigo")


if __name__ == "__main__":
    asyncio.run(main())

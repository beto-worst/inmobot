"""Inventario ficticio del area metropolitana de Monterrey.

Sirve para dos cosas: probar el sistema sin llaves de API, y -mas importante-
ensenarle el producto funcionando a un prospecto en la primera junta, antes
de que te de acceso a su CRM.
"""
from __future__ import annotations

from ..modelos import Propiedad
from .base import AdaptadorInventario

_INVENTARIO = [
    dict(id="DEMO-001", titulo="Casa en Cumbres Elite", tipo="Casa", operacion="Venta",
         precio=3_850_000, colonia="Cumbres Elite", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=3, banos=2.5, estacionamientos=2, construccion_m2=180, terreno_m2=160,
         descripcion="Casa de dos plantas en privada con caseta. Cocina integral, "
                     "sala de TV en planta alta y patio con asador.",
         amenidades=["Privada con caseta", "Cocina integral", "Patio", "Cuarto de servicio"]),
    dict(id="DEMO-002", titulo="Casa en Cumbres Providencia", tipo="Casa", operacion="Venta",
         precio=4_450_000, colonia="Cumbres Providencia", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=4, banos=3.5, estacionamientos=2, construccion_m2=245, terreno_m2=200,
         descripcion="Amplia residencia con recamara en planta baja, ideal para "
                     "familia con adulto mayor. Jardin y cuarto de lavado.",
         amenidades=["Recamara en PB", "Jardin", "Alberca comunitaria"]),
    dict(id="DEMO-003", titulo="Departamento en San Jeronimo", tipo="Depto", operacion="Venta",
         precio=2_950_000, colonia="San Jeronimo", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=2, banos=2, estacionamientos=1, construccion_m2=95, terreno_m2=0,
         descripcion="Departamento en torre con amenidades, piso 8, vista a la sierra.",
         amenidades=["Gimnasio", "Alberca", "Roof garden", "Seguridad 24/7"]),
    dict(id="DEMO-004", titulo="Casa en Valle Oriente", tipo="Casa", operacion="Venta",
         precio=12_500_000, colonia="Valle Oriente", municipio="San Pedro Garza Garcia",
         estado="Nuevo Leon", recamaras=4, banos=4.5, estacionamientos=3,
         construccion_m2=420, terreno_m2=350,
         descripcion="Residencia de lujo con acabados de primera, elevador y "
                     "cava. A cinco minutos de plazas y colegios.",
         amenidades=["Elevador", "Cava", "Cuarto de servicio", "Alberca", "Jardin"],
         etiquetas=["Luxury"]),
    dict(id="DEMO-005", titulo="Casa en Contry", tipo="Casa", operacion="Venta",
         precio=2_390_000, colonia="Contry", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=3, banos=2, estacionamientos=2, construccion_m2=150, terreno_m2=200,
         descripcion="Casa de un piso en zona consolidada, lista para habitar.",
         amenidades=["Un solo piso", "Patio amplio"]),
    dict(id="DEMO-006", titulo="Departamento en Centrika", tipo="Depto", operacion="Renta",
         precio=16_500, colonia="Centrika", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=2, banos=2, estacionamientos=1, construccion_m2=88, terreno_m2=0,
         descripcion="Departamento amueblado en Centrika, incluye mantenimiento.",
         amenidades=["Amueblado", "Alberca", "Seguridad 24/7"]),
    dict(id="DEMO-007", titulo="Casa en Las Torres", tipo="Casa", operacion="Renta",
         precio=13_000, colonia="Las Torres", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=3, banos=1.5, estacionamientos=1, construccion_m2=120, terreno_m2=140,
         descripcion="Casa en renta cerca de avenida principal y transporte.",
         amenidades=["Patio", "Cerca de transporte"]),
    dict(id="DEMO-008", titulo="Casa en Santiago, Los Nogales", tipo="Casa", operacion="Venta",
         precio=6_200_000, colonia="Los Nogales", municipio="Santiago", estado="Nuevo Leon",
         recamaras=4, banos=3, estacionamientos=4, construccion_m2=310, terreno_m2=900,
         descripcion="Casa de campo con terreno amplio y arboles frutales, "
                     "clima fresco a 30 minutos de Monterrey.",
         amenidades=["Terreno amplio", "Asador", "Vista a la montana"],
         etiquetas=["Luxury"]),
    dict(id="DEMO-009", titulo="Terreno en Carretera Nacional", tipo="Terreno", operacion="Venta",
         precio=3_100_000, colonia="La Estanzuela", municipio="Monterrey", estado="Nuevo Leon",
         recamaras=0, banos=0, estacionamientos=0, construccion_m2=0, terreno_m2=520,
         descripcion="Terreno plano en fraccionamiento con servicios, apto para construir.",
         amenidades=["Servicios instalados", "Fraccionamiento"]),
    dict(id="DEMO-010", titulo="Local comercial en Apodaca", tipo="Local", operacion="Renta",
         precio=28_000, colonia="Parque Industrial", municipio="Apodaca", estado="Nuevo Leon",
         recamaras=0, banos=2, estacionamientos=6, construccion_m2=210, terreno_m2=0,
         categoria="Comercial",
         descripcion="Local sobre avenida con alto flujo vehicular y estacionamiento.",
         amenidades=["Sobre avenida", "Estacionamiento propio"]),
    dict(id="DEMO-011", titulo="Bodega industrial en Escobedo", tipo="Bodega", operacion="Renta",
         precio=95_000, colonia="Parque Industrial Escobedo", municipio="General Escobedo",
         estado="Nuevo Leon", recamaras=0, banos=3, estacionamientos=10,
         construccion_m2=1200, terreno_m2=1800, categoria="Industrial",
         descripcion="Bodega con anden de carga, oficinas y patio de maniobras.",
         amenidades=["Anden de carga", "Oficinas", "Patio de maniobras"]),
    dict(id="DEMO-012", titulo="Departamento en Guadalupe, Linda Vista", tipo="Depto",
         operacion="Venta", precio=1_780_000, colonia="Linda Vista", municipio="Guadalupe",
         estado="Nuevo Leon", recamaras=2, banos=1, estacionamientos=1,
         construccion_m2=72, terreno_m2=0,
         descripcion="Departamento accesible en planta baja, apto para credito Infonavit.",
         amenidades=["Planta baja", "Apto Infonavit"]),
    dict(id="DEMO-013", titulo="Casa en Cumbres San Agustin", tipo="Casa", operacion="Venta",
         precio=3_990_000, colonia="Cumbres San Agustin", municipio="Monterrey",
         estado="Nuevo Leon", recamaras=3, banos=3, estacionamientos=2,
         construccion_m2=195, terreno_m2=170,
         descripcion="Casa remodelada con cocina nueva y closets de madera.",
         amenidades=["Remodelada", "Closets de madera", "Privada"]),
    dict(id="DEMO-014", titulo="Casa en Villas de San Miguel", tipo="Casa", operacion="Venta",
         precio=1_450_000, colonia="Villas de San Miguel", municipio="Guadalupe",
         estado="Nuevo Leon", recamaras=2, banos=1, estacionamientos=1,
         construccion_m2=85, terreno_m2=105,
         descripcion="Casa economica ideal para primer patrimonio, apta para credito.",
         amenidades=["Apta Infonavit", "Primer patrimonio"]),
]


class AdaptadorDemo(AdaptadorInventario):
    tipo = "demo"

    async def _descargar(self) -> list[Propiedad]:
        props = []
        for datos in _INVENTARIO:
            datos = dict(datos)
            precio = datos["precio"]
            sufijo = "/mes" if datos["operacion"] == "Renta" else ""
            datos["precio_texto"] = f"${precio:,.0f}{sufijo}"
            datos.setdefault("categoria", "Residencial")
            datos["imagenes"] = ["https://placehold.co/800x600?text=" + datos["id"]]
            props.append(Propiedad(**datos))
        return props

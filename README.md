# Athena

Asistente inmobiliario por chat, **multi-inquilino**: un solo servidor atiende a
varias inmobiliarias, cada una con su marca, su inventario y su cobro mensual.

La IA se llama **Athena**, pero cada inmobiliaria puede rebautizarla desde su
archivo de inquilino (campo `asistente`). Para su cliente final es *su*
asistente, no un servicio de terceros — y eso ayuda a vender la mensualidad.

El cliente escribe *"busco casa en Cumbres, 3 recámaras, hasta 4 millones"*, el
asistente busca en el inventario **real**, presenta opciones, resuelve dudas,
califica al prospecto (presupuesto, crédito o contado, urgencia) y se lo pasa al
asesor con todo el contexto. El asesor entra solo a cerrar.

---

## Arrancar en local

```bash
cd C:/Users/dekuo/Downloads/inmobot
python -m pip install -r requirements.txt
```

Copia `.env.example` a `.env` y pon tu llave de <https://console.anthropic.com>:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Levanta el servidor:

```bash
python -m inmobot.servidor
```

Abre <http://localhost:8100>. El widget de abajo a la derecha usa el inquilino
`demo`, con 14 propiedades inventadas de Monterrey — sirve para probar sin
tocar ningún CRM.

Para probarlo con el inventario de un cliente real:
<http://localhost:8100/?inquilino=rednorte>. Sirve para enseñarle a un
prospecto su propio catálogo en la junta.

Para probar la lógica **sin gastar API**:

```bash
python prueba_loop.py
```

---

## Dar de alta una inmobiliaria nueva

Es copiar un JSON. Cero código.

1. Copia `inquilinos/demo.json` a `inquilinos/sucliente.json`.
2. Cambia `id`, `nombre`, `zona_operacion`, `marca` y `contacto`.
3. Reinicia el servidor.
4. Dale esta línea para que la pegue antes de `</body>` en su sitio:

```html
<script src="https://TU-SERVIDOR/widget/inmobot.js"
        data-inquilino="sucliente"
        data-api="https://TU-SERVIDOR"></script>
```

### Campos del archivo de inquilino

| Campo | Para qué sirve |
|---|---|
| `id` | Identificador en la URL del widget. No lo cambies después. |
| `nombre` | Nombre de la inmobiliaria. |
| `asistente` | Cómo se llama la IA. Por defecto `Athena`; el cliente puede poner el nombre que quiera. |
| `modelo` | `claude-opus-5` (por defecto) o `claude-haiku-4-5` si el cliente quiere pagar menos. |
| `esfuerzo` | `low` para chat (rápido y barato), `medium` si necesitas más razonamiento. |
| `zona_operacion` | Dónde opera. Evita que ofrezca propiedades fuera de su mercado. |
| `personalidad` | Tono específico de esa marca. |
| `reglas` | Reglas propias del cliente, además de las reglas duras del sistema. |
| `adaptador` | De dónde salen las propiedades. Ver abajo. |
| `notificaciones` | Cómo se le avisa al asesor de un prospecto nuevo. Ver abajo. |
| `contacto.whatsapp` | Número al que se manda al cliente cuando pide un humano. |
| `contacto.token_panel` | Contraseña para leer `/api/leads`. **Cámbialo por cliente.** |
| `origenes_permitidos` | Dominios desde los que se puede usar el widget. Vacío = cualquiera. |

---

## Conectar un inventario nuevo

Athena nunca ve el CRM del cliente: solo conoce `Propiedad`, el formato
neutral. Por eso la fuente de datos se cambia sin tocar el motor.

### El cliente usa NOCNOK

Ya está resuelto. `"adaptador": {"tipo": "nocnok"}` y su llave de API.

### El cliente NO tiene CRM (Excel, CSV o Google Sheets)

También resuelto, y es el caso más común en agencias chicas:

```json
"adaptador": {
  "tipo": "archivo",
  "config": { "ruta": "C:/ruta/a/su/inventario.xlsx" }
}
```

La `ruta` puede ser un archivo local o una URL. Una hoja de Google publicada
como CSV (*Archivo → Compartir → Publicar en la web → CSV*) funciona directo,
y así el cliente actualiza su inventario sin que tú hagas nada.

Reconoce solo los nombres de columna en español —`Recámaras`, `Baños`,
`Precio de Venta`, `M2 Construcción`, `Cocheras`…— y limpia los valores como
los escribe una persona: `$3,850,000`, `3.99 MDP`, `3 rec`, `180 m2`. Si la
hoja usa nombres raros, se mapean sin tocar código:

```json
"config": {
  "ruta": "...",
  "columnas": { "precio": "VALOR DEL INMUEBLE", "recamaras": "HAB" }
}
```

**Antes de prometerle nada a un cliente, corre el diagnóstico** sobre su
archivo: dice qué columnas se reconocieron, cuáles se ignoraron, y cuántas
propiedades quedan sin zona, sin recámaras o sin fotos.

```python
import asyncio
from inmobot.adaptadores.archivo import AdaptadorArchivo
print(asyncio.run(AdaptadorArchivo({"ruta": "inventario.xlsx"}).diagnostico()))
```

### El cliente usa otro CRM

Se escribe **un método**. Crea `inmobot/adaptadores/loquesea.py`:

```python
from ..modelos import Propiedad
from .base import AdaptadorInventario

class AdaptadorLoQueSea(AdaptadorInventario):
    tipo = "loquesea"

    async def _descargar(self) -> list[Propiedad]:
        # trae los datos de donde sea y devuélvelos como Propiedad
        return [...]
```

Regístralo en `inmobot/adaptadores/__init__.py` y listo. El filtrado, la caché,
la relajación de criterios y el ordenamiento los hereda gratis.

---

## Cómo está armado

```
inmobot/
├── modelos.py       Propiedad y Filtros. El formato neutral. Nadie más lo toca.
├── adaptadores/
│   ├── base.py      Filtrado, caché y relajación de criterios (para todos)
│   ├── demo.py      Inventario ficticio para vender el producto
│   ├── archivo.py   Excel, CSV o Google Sheets: agencias sin CRM
│   └── nocnok.py    RedNorte y cualquier cliente con NOCNOK
├── prompt.py        System prompt por inquilino. Determinista (por la caché).
├── herramientas.py  Las 4 herramientas que el modelo puede ejecutar
├── agente.py        El loop: modelo → herramienta → modelo, con topes
├── leads.py         Captura: respaldo → base de datos → aviso al asesor
├── basedatos.py     SQLite: prospectos consultables, estados, historial
├── notificaciones.py Aviso al asesor por correo y/o webhook
├── config.py        Carga de inquilinos
└── servidor.py      API HTTP
```

---

## Del prospecto al asesor

Cuando al cliente le gusta una opción, Athena le pide nombre y teléfono y
dispara tres cosas **en este orden**:

1. **Respaldo** en `datos/leads-{inquilino}.jsonl` (texto plano, a prueba de todo)
2. **Base de datos** SQLite en `datos/athena.db` (consultable, con estados)
3. **Aviso al asesor** por los canales configurados

Guardar va antes que avisar a propósito: si el correo está mal configurado o
el webhook del cliente está caído, el prospecto ya quedó en dos lugares. Al
revés se perdería justo lo que le cobras al cliente.

### Configurar el aviso

En el archivo del inquilino:

```json
"notificaciones": {
  "email": {
    "para": ["asesor@sucliente.mx"],
    "host": "smtp.gmail.com",
    "puerto": 587,
    "usuario_env": "SMTP_USUARIO",
    "password_env": "SMTP_PASSWORD"
  },
  "webhook": "https://script.google.com/macros/s/.../exec"
}
```

La contraseña **nunca** va en el JSON — ese archivo se sube a git. Solo el
nombre de la variable de entorno donde vive, y el valor real en `.env`.
Con Gmail hace falta una [contraseña de
aplicación](https://myaccount.google.com/apppasswords), no la normal.

Para desarrollo, `"notificaciones": {"consola": true}` imprime el aviso en
la terminal en vez de mandarlo.

### Sobre WhatsApp

Mandar un WhatsApp automático exige la API de WhatsApp Business de Meta, con
verificación de la empresa y semanas de trámite. Mientras tanto, el correo
llega hoy, y el aviso incluye un enlace `wa.me` para que el asesor le
escriba al cliente con un clic desde su celular.

### El teléfono se normaliza

`81 1778 3953`, `+52 811 778 3953` y `(81) 1778-3953` son el mismo cliente.
Sin esto el asesor recibiría al mismo prospecto tres veces como si fueran
tres personas, y el aviso te dice si ese número ya había escrito antes.

### Decisiones que no son obvias

**El modelo nunca inventa propiedades.** Solo existe lo que devuelven las
herramientas, y eso sale del CRM en vivo. Por eso no se entrena un modelo
propio: memorizaría propiedades ya vendidas e inventaría precios.

**El pensamiento del modelo se queda encendido.** Apagado, Opus 5 a veces
escribe la llamada a la herramienta como texto visible en vez de ejecutarla:
el chat diría *"voy a buscar opciones"* y no buscaría nada, sin lanzar ningún
error. Con `effort: low` responde rápido igual.

**El system prompt es idéntico byte a byte en cada turno.** Nada de fechas ni
ids aleatorios dentro. Si cambia un solo carácter, la caché se invalida y cada
mensaje cuesta precio completo en vez de ~10%.

**Nunca se devuelve una lista vacía en seco.** Si no hay nada que cumpla, se
afloja un criterio a la vez y se le avisa al cliente cuál se aflojó. Fingir que
eso era lo que pidió destruye la confianza.

**Compra y renta nunca se mezclan.** Sin esta separación, una renta de
$13,000/mes "cabe" en un presupuesto de compra de $2,000,000 y el cliente
recibe basura en el primer mensaje.

**Los leads se guardan en disco antes que en el webhook.** Si Google Sheets o
el CRM del cliente están caídos, el lead no se pierde.

**Hay una sola implementación del loop.** `responder()` consume el mismo
generador que el streaming. Dos copias se desincronizan, y un arreglo hecho en
una no llega a la otra.

**El streaming es lo que separa "rápido" de "roto".** Sin él, el cliente mira
puntitos entre 5 y 9 segundos y cree que se trabó. Con él ve texto desde el
primer momento. El endpoint manda `X-Accel-Buffering: no` porque si no, nginx
acumula la respuesta y la entrega de golpe: el streaming deja de servir justo
al desplegarlo en producción.

---

## Endpoints

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/salud` | Verificar que está arriba y qué inquilinos cargó |
| GET | `/api/config?inquilino=X` | Marca del inquilino (lo usa el widget) |
| POST | `/api/chat` | Un turno de conversación (respuesta completa) |
| POST | `/api/chat/stream` | Lo mismo, pero por SSE conforme se escribe. **Es el que usa el widget.** |
| GET | `/api/leads?inquilino=X&token=Y` | Prospectos + resumen. Filtros: `estado`, `tipo`, `busqueda` |
| POST | `/api/leads/{folio}/estado` | El asesor marca contactado / cerrado / descartado |
| GET | `/api/leads/{folio}/historial` | Veces que ese teléfono ya había contactado |

---

## Pendientes

- [ ] Panel del asesor (interfaz; los endpoints ya existen)
- [ ] Búsqueda semántica con embeddings en GPU local
- [ ] Sesiones en Redis (hoy en memoria: se pierden al reiniciar)
- [ ] WhatsApp Business API para avisarle al asesor por ahí
- [ ] Adaptador para AVALO
- [ ] WhatsApp Business API
- [ ] Límite de mensajes por sesión, contra abuso

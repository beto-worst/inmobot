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

## Abrirla

**Doble clic en `Abrir-Athena.bat`.** Nada más.

La primera vez en una computadora instala sola lo que le falte (tarda un par
de minutos) y luego abre el navegador. Para apagarla, cierras la ventana negra.

Se abre en:

| | |
|---|---|
| **Para enseñarla** | <http://localhost:8100/?inquilino=rednorte> — inventario real |
| Con datos ficticios | <http://localhost:8100> — 14 propiedades inventadas |
| Panel del asesor | <http://localhost:8100/panel> |

Si prefieres la terminal:

```bash
cd C:/Users/dekuo/Downloads/inmobot
python -m pip install -r requirements.txt
python -m inmobot.servidor
```

---

## Llevarla a otra computadora

**Copia la carpeta completa** (USB, OneDrive, Drive) y haz doble clic en
`Abrir-Athena.bat` allá. Ya está: el lanzador instala lo que falte solo.

Lo único que necesita la otra computadora es **Python**, de
<https://www.python.org/downloads/>. Al instalarlo hay que marcar la casilla
**"Add python.exe to PATH"** o no va a funcionar.

Copiar la carpeta es mejor que clonar de GitHub porque se lleva el `.env`,
que no está en el repositorio. Por lo mismo: **esa carpeta lleva tus llaves
dentro.** No la subas a un Drive compartido ni se la pases a nadie.

Si prefieres clonar de GitHub, después hay que crear el `.env` a mano:

```bash
git clone https://github.com/beto-worst/inmobot.git
cd inmobot
copy .env.example .env
```

Y llenarlo con tus llaves. Los prospectos capturados (`datos/athena.db`)
tampoco viajan por GitHub: cada computadora tiene los suyos.

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
| `asesor_por_propiedad` | URL para saber qué asesor tiene asignada cada propiedad. Ver abajo. |
| `contacto.whatsapp` | Número al que se manda al cliente cuando pide un humano. |
| `contacto.token_panel_env` | Variable de `.env` con la clave del panel. **Una por cliente.** |
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
├── asesores.py      Qué asesor tiene asignada cada propiedad
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

### Al asesor de esa propiedad, no a un buzón general

Si el sitio del cliente sabe qué asesor tiene asignada cada propiedad, el
aviso le llega **a él**, que es quien la conoce y quien cobra la comisión:

```json
"asesor_por_propiedad": {
  "url": "https://www.rednorte.mx/api/asesor?code={codigo}",
  "ttl": 21600
}
```

La URL debe responder `{"success": true, "agent": {"name", "phone", "email"}}`.
El sitio de RedNorte ya expone eso en `/api/asesor`.

El asesor encontrado **se suma** a los destinatarios fijos de `notificaciones`,
no los reemplaza: si la búsqueda falla, el prospecto igual le llega a alguien.
Su nombre queda guardado en el prospecto y se ve en el panel.

Las consultas se cachean 6 horas por defecto. Sin caché se le pegaría al sitio
del cliente en cada prospecto, y son cientos de propiedades.

### Avisar por WhatsApp

El canal existe y usa la **Cloud API oficial de Meta**. No hay atajo: las
"APIs no oficiales" violan los términos de WhatsApp y terminan con el número
baneado.

```json
"notificaciones": {
  "whatsapp": {
    "token_env": "WHATSAPP_TOKEN",
    "telefono_id_env": "WHATSAPP_TELEFONO_ID",
    "plantilla": "nuevo_prospecto",
    "idioma": "es_MX"
  }
}
```

Meta exige una **plantilla aprobada** para iniciar una conversación; solo
dentro de las 24 h siguientes a que la persona te escriba se puede mandar
texto libre. Si omites `plantilla`, se manda texto libre (útil para probar
con el número de pruebas de Meta).

Los parámetros van en el orden de los `{{1}}`, `{{2}}`, `{{3}}` de la
plantilla: nombre, teléfono y qué busca. **Cambiar el orden aquí sin
cambiarlo en Meta manda los datos al hueco equivocado.**

Al asesor de la propiedad se le manda a su teléfono además de a los números
fijos, igual que con el correo.

El aviso por correo incluye de todos modos un enlace `wa.me` para que el
asesor le escriba **al cliente** con un clic.

### El panel del asesor

En <http://localhost:8100/panel>. Pensado para celular: un asesor lo abre
entre visitas, no sentado en una computadora.

Muestra los prospectos con lo que busca cada uno, presupuesto y las notas que
dejó Athena; botones de WhatsApp y llamar; y un selector para marcarlo como
contactado, en seguimiento, cerrado o descartado. El contador **Sin avisar**
te dice si algún prospecto no le llegó al asesor por correo.

La clave se pide dentro y se guarda en `sessionStorage`, **nunca va en la
URL**: una URL con la clave se queda en el historial del navegador, se
comparte por WhatsApp sin querer y viaja en la cabecera `Referer`.

Y la clave vive en `.env`, no en el JSON del inquilino: ese archivo se sube a
git, y ahí una clave queda para siempre en el historial aunque después se
borre.

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
| GET | `/panel` | Panel del asesor (interfaz) |
| GET | `/api/leads?inquilino=X&token=Y` | Prospectos + resumen. Filtros: `estado`, `tipo`, `busqueda` |
| POST | `/api/leads/{folio}/estado` | El asesor marca contactado / cerrado / descartado |
| GET | `/api/leads/{folio}/historial` | Veces que ese teléfono ya había contactado |

---

## Pendientes

- [ ] Búsqueda semántica con embeddings en GPU local
- [ ] Sesiones en Redis (hoy en memoria: se pierden al reiniciar)
- [ ] WhatsApp Business API para avisarle al asesor por ahí
- [ ] Adaptador para AVALO
- [ ] WhatsApp Business API
- [ ] Límite de mensajes por sesión, contra abuso

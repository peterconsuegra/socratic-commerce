# CLAUDE.md — Second Order (handoff para agentes)

Lee esto completo antes de tocar código. Resume lo que no se deduce leyendo el repo.

## Qué es

**Second Order** es una app Flask de analítica para la tienda WooCommerce de
**Save a Playa** (https://saveaplaya.org, desodorante natural, Colombia).
Lee los pedidos de la tienda por API y muestra ventas, recompras, clientes
inactivos (lapsed), ciudades/género, UTMs, WATI (WhatsApp), inventario de
tubos vendidos, pronósticos, y expone `/api/*` para otras apps internas.

- Repo: https://github.com/peterconsuegra/socratic-commerce (rama `main`; **el repo es público**).
- Producción: Railway → https://secondorder.saveaplaya.org
  (host Railway: https://web-production-c6d52.up.railway.app).
- El nombre del repo/carpeta (`socratic-commerce`, `save_a_playa_data`) es
  histórico; la marca actual es "Second Order". "Save a Playa" es la tienda
  cliente, no la plataforma: no lo reemplaces en textos que hablan de la tienda.
- `readme.txt` describe un despliegue viejo en Linode/Bitbucket: **obsoleto**.

## Stack y estructura

Python 3.12, Flask, Flask-Login, Flask-SQLAlchemy + Flask-Migrate (Alembic),
pandas, statsmodels. SQLite local (`database.db`), Postgres en producción
(`DATABASE_URL`). SQLAlchemy está fijado `<2.1` a propósito (ver
`requirements.txt`).

```
app/__init__.py      create_app(): config, DB, login, blueprint "main", CORS de /api
app/routes/          una vista por archivo, todas en el blueprint `main`
app/services/        lógica de datos (pandas). Aquí va la lógica, no en routes
app/templates/       Jinja; layout.html tiene el menú lateral
app/static/brand.css tokens de marca (teal #0F6E63, Manrope / Instrument Sans)
app/i18n.py          inglés/español (ver abajo)
app/cli.py           flask resync-orders | make-admin | create-api-token | list-api-tokens
migrations/          Alembic; también migraciones de datos
tests/               unittest (no pytest)
data/                CSVs de pedidos cacheados (gitignored)
```

Archivos sueltos en la raíz (`prompt*.py`, `fix_data.py`, `woo2024/`,
`output.csv`, etc.) son scripts viejos; no forman parte de la app.

## Correr local

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/flask --app run db upgrade
venv/bin/flask --app run make-admin <email>     # tras registrarse
venv/bin/python -m flask --app run run --port 5057
```

`.claude/launch.json` ya define `flask-dev` en el puerto 5057.
Variables de entorno (`.env`, no se versiona): `FLASK_KEY`, opcional
`DATABASE_URL`, `WATI_ENCRYPTION_KEY`, `FLASK_DEBUG=1`. Pide los valores a
Pedro; nunca los pongas en el código ni en commits.

Credenciales de terceros (API key de pedidos de la tienda, WATI, SendGrid,
token de Roxy) se guardan **cifradas** en la tabla `options` desde la página
/options (Settings), ver `app/services/secrets.py`. Si cambias `FLASK_KEY`
sin `WATI_ENCRYPTION_KEY`, todos esos secretos quedan ilegibles.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```

189 tests, ~20 s, todos pasan. Usan SQLite en memoria y un directorio temporal;
no tocan la tienda. Agrega tests para cada feature nueva (sigue el estilo de
`tests/test_inventory.py`).

## Despliegue

Push a `main` en GitHub = deploy automático en Railway. El start command
(`railway.toml`) corre `flask db upgrade` y luego gunicorn (2 workers sync).
**Haz cambios en ramas y PR; no empujes a `main` sin acuerdo con Pedro.**

- **Presupuesto de tiempo: 120 s por request** (timeout de ROAS Link). Todo lo
  que tarde más de unos segundos en local es riesgo de timeout en producción.
  Agregaciones pandas vectorizadas; nunca bucles por cliente en Python.
- El servicio web **no tiene volumen**: `data/` se borra en cada deploy. Cada
  worker sincroniza el historial completo al arrancar (`gunicorn.conf.py`,
  ~40 s, ~61k pedidos) con un lock de archivo en `data/`.
- Nunca llames a WooCommerce dentro de un request si existe archivo cacheado.
  La API de pedidos limita a 100 requests/hora/IP; el historial se pide un año
  por request (desde 2021).
- Los logs INFO de la app no salen en Railway; solo gunicorn y errores.
- Para cargar contenido en producción sin entrar a la UI: migraciones de datos.

## Reglas de dominio (no romper)

1. **Una sola fuente de verdad para pedidos:** `load_orders` en
   `app/services/get_data.py`. El plugin de WordPress "Second Order" envía en
   cada pedido `gender`, `purchase_number` e `is_repurchase`.
   - Recompra = `is_repurchase`; primera compra = `purchase_number == 1`;
     "N/A"/vacío = desconocido.
   - **Nunca** hagas matching de clientes ni adivines género en la app (ni
     como fallback). Eso se eliminó a propósito.
   - Las pantallas por cliente agrupan por email en minúsculas porque la API
     aún no trae `customer_id`; cuando lo traiga, migrar a eso.
2. **Zona horaria Bogotá** para todos los días/rangos (`app/services/periods.py`).
3. **Pedidos WATI = `utm_campaign` exactamente "wati"** (sin mayúsculas, trim).
   No `wati3`, no vacíos.
4. **i18n:** los templates escriben inglés envuelto en `_()`; la traducción
   va en el dict `SPANISH` de `app/i18n.py`. Idioma global por la opción
   `site_language`. **Nada bajo `/api/` se traduce.**
5. **Inventario (`/inventory`, en curso):** tubos por SKU en `TUBES_PER_SKU`
   (`app/services/inventory.py`); un SKU nuevo aparece como "unmatched" hasta
   agregarlo, nunca se adivina.

## Contratos de API (otros sistemas dependen de esto)

Endpoints con token (`flask create-api-token`):

- **ROAS Link** (roaslink.saveaplaya.org) lee `/api/utm_source_summary`,
  `/api/utm_campaign_summary`, `/api/utm_content_summary`,
  `/api/utm_campaign_content_summary`.
- **Revive / Roxy** (roxy.saveaplaya.org) lee `/api/wati_summary`; a su vez
  `/wati_stats` lee `GET /api/new_leads` de Roxy.

No cambies nombres de campos ni respuestas de los presets. Soportan
`period=custom&since=YYYY-MM-DD&until=YYYY-MM-DD` (días Bogotá, ambos
incluidos, máx. 366) con respuesta bajo `periods.custom` y
`start_date`/`end_date` iguales a since/until. `utm_content_summary` responde
200 con `campaign_found: false` si no existe la campaña, y su `campaign_key`
debe coincidir con `_normalize_campaign_key` de ROAS Link. Verifica con
`tests/test_api_periods.py` antes de tocar estos endpoints. Los acuerdos entre
proyectos se escriben como notas markdown de handoff (pídeselas a Pedro).

## Servidor MCP (`/mcp`)

`app/routes/mcp.py` expone un servidor MCP para que un agente de Claude
consulte ventas por periodo. Streamable HTTP sin estado, JSON-RPC escrito a
mano en Flask (sin dependencia `mcp`): cada POST a `/mcp` recibe un JSON; GET
da 405. Auth con los mismos API tokens de `/api/*`.

Herramientas (lógica en `app/services/sales_periods.py`, solo lectura):
- `list_periods`: hoy en Bogotá, fechas exactas de cada preset, reglas del
  rango custom, primer/último pedido del historial.
- `get_sales_by_period`: ventas, pedidos, ticket promedio, primera compra y
  recompra por preset, todos (`all`) o `custom`; `breakdown` day/week/month.
- `compare_periods`: un rango contra otro o contra los mismos días previos.

Conectar Claude Code (token creado en Admin → API tokens o `flask create-api-token`):

```bash
claude mcp add --transport http second-order https://secondorder.saveaplaya.org/mcp --header "Authorization: Bearer <token>"
```

Para agregar una herramienta: añádela a `TOOLS` y a `_run_tool` en
`mcp.py`, con su lógica en un servicio y tests en `tests/test_mcp.py`.
claude.ai (conectores web) exige OAuth, que este servidor aún no tiene.

## Convenciones

- Docstring al inicio de cada módulo explicando qué hace y por qué; mantén ese estilo.
- Una ruta = un archivo en `app/routes/`, registrado en `app/routes/__init__.py`
  y enlazado en el menú de `app/templates/layout.html`.
- UI con los tokens de `brand.css`; no colores sueltos.
- Commits en inglés, descriptivos ("WATI Stats: lead with ...").
- No subas `data/`, `.env`, `database.db` ni credenciales. El repo es público.

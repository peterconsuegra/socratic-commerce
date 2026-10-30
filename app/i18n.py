# app/i18n.py
"""
English/Spanish text for the pages that offer a language selector (so far
/sales_by_location). Templates write the English text and wrap it in _():
_("Total sales") gives "Ventas totales" when the request's language is
Spanish and the English text otherwise, so a page without a selector, or a
string missing below, reads in English. Text with values uses str.format
placeholders: _("Stats for {place}").format(place=...).

The language is chosen with ?lang=en|es and kept in the session (see
request_language); a page that does not set g.lang stays in English.
"""
from flask import g, request, session

LANGUAGES = {"en": "English", "es": "Español"}

SPANISH = {
    # Page
    "Sales by Location": "Ventas por ubicación",
    "Language": "Idioma",
    "Print / Save as PDF": "Imprimir / Guardar PDF",
    "Report generated on {date}": "Reporte generado el {date}",
    "Date range": "Rango de fechas",
    "Location": "Ubicación",
    "Every location": "Todas las ubicaciones",
    "{start} to {end}": "{start} a {end}",

    # Date range selector
    "Select Date Range": "Seleccionar rango de fechas",
    "Select Predefined Date Range:": "Rango de fechas predefinido:",
    "Or enter a custom date range:": "O ingresa un rango personalizado:",
    "Start Date:": "Fecha inicial:",
    "End Date:": "Fecha final:",
    "Fetch Data": "Consultar datos",
    "Fetching...": "Consultando...",
    "Please select a date range or enter both start and end dates.":
        "Selecciona un rango de fechas o ingresa la fecha inicial y la final.",
    "Invalid date format. Please use DD/MM/YYYY.": "Formato de fecha inválido. Usa DD/MM/AAAA.",
    "--Select--": "--Seleccionar--",
    "Yesterday": "Ayer",
    "Today": "Hoy",
    "Last 7 days": "Últimos 7 días",
    "Last 30 days": "Últimos 30 días",
    "Current month": "Mes actual",
    "Last month": "Mes anterior",
    "Last quarter (90 days)": "Último trimestre (90 días)",
    "Year to date": "Año a la fecha",
    "Last year": "Año anterior",
    "Lifetime": "Todo el historial",

    # Location filter
    "Filter by Location": "Filtrar por ubicación",
    "State (department)": "Departamento",
    "All states": "Todos los departamentos",
    "City": "Ciudad",
    "All cities": "Todas las ciudades",
    "All cities in {state}": "Todas las ciudades de {state}",
    "Apply": "Aplicar",
    "Clear": "Limpiar",
    "Every chart below shows only the orders from": "Todos los gráficos muestran solo los pedidos de",
    "Showing every state and city. Pick a state to list its cities.":
        "Mostrando todos los departamentos y ciudades. Elige un departamento para ver sus ciudades.",
    "Fetch a date range above to list the states and cities to filter by.":
        "Consulta un rango de fechas arriba para listar los departamentos y ciudades.",

    # Stats section
    "Stats for {place}": "Estadísticas de {place}",
    "every location": "todas las ubicaciones",
    "Selected date range, amounts in COP. Repurchases as numbered by the store.":
        "Rango de fechas seleccionado, montos en COP. Recompras según la numeración de la tienda.",
    "No orders for this selection.": "No hay pedidos para esta selección.",
    "Summary": "Resumen",
    "By city": "Por ciudad",
    "By channel": "Por canal",
    "Products": "Productos",
    "By weekday": "Por día de la semana",
    "Total sales": "Ventas totales",
    "{amount} per day over {days} days": "{amount} por día durante {days} días",
    "{amount} per day over 1 day": "{amount} por día durante 1 día",
    "Total repurchases": "Total de recompras",
    "{pct} of orders": "{pct} de los pedidos",
    "Repurchase sales": "Ventas por recompra",
    "{pct} of sales": "{pct} de las ventas",
    "Orders": "Pedidos",
    "Avg order {amount}": "Pedido promedio {amount}",
    "New customers": "Clientes nuevos",
    "First purchases, {amount}": "Primeras compras, {amount}",
    "Avg first order": "Primer pedido promedio",
    "vs {amount} avg repurchase": "vs {amount} recompra promedio",
    "3rd+ purchases": "3.ª compra o más",
    "Orders from loyal customers": "Pedidos de clientes fieles",
    "Cities with orders": "Ciudades con pedidos",
    "{count} orders not numbered yet": "{count} pedidos aún sin numerar",
    "1 order not numbered yet": "1 pedido aún sin numerar",
    "Every order numbered": "Todos los pedidos numerados",
    "Sales": "Ventas",
    "Share": "Participación",
    "Avg order": "Pedido promedio",
    "Repurchases": "Recompras",
    "Repurchase rate": "Tasa de recompra",
    "Top channel": "Canal principal",
    "Top {shown} of {total} cities by sales.": "Las {shown} ciudades con más ventas de {total}.",
    "Channel (utm_source)": "Canal (utm_source)",
    "SKU": "SKU",
    "Orders with it": "Pedidos que lo incluyen",
    "% of orders": "% de los pedidos",
    "In first purchases": "En primeras compras",
    "In repurchases": "En recompras",
    "A multi-item order counts once for each SKU it holds, so the rows add up to more than the orders.":
        "Un pedido con varios productos cuenta una vez por cada SKU, así que las filas suman más que los pedidos.",
    "No SKUs recorded for these orders.": "No hay SKU registrados para estos pedidos.",
    "Weekday": "Día",
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo",
    "unknown": "desconocido",

    # Charts
    "No data available to display for this selection.": "No hay datos para mostrar en esta selección.",
    "USD Exchange Rate (COP):": "Tasa de cambio USD (COP):",
    "Switch Currency": "Cambiar moneda",
    "Sales by UTM Source": "Ventas por fuente UTM",
    "This pie reflects the selected date range (from the selector above).":
        "Este gráfico refleja el rango de fechas seleccionado (en el selector de arriba).",
    "Other channels (<2%)": "Otros canales (<2%)",
    "Daily Total Sales and All Repurchases Trend and Forecast":
        "Tendencia y pronóstico diario de ventas totales y recompras",
    "Total sales and all repurchases are shown together for easier comparison.":
        "Las ventas totales y las recompras se muestran juntas para compararlas fácilmente.",
    "Gender split of total sales": "Ventas totales por género",
    "City split of total sales": "Ventas totales por ciudad",
    "Time split of total sales (3h)": "Ventas totales por franja horaria (3 h)",
    "Daily Sales Trend and Forecast by Channel": "Tendencia y pronóstico diario de ventas por canal",
    "No channel charts available for this selection.": "No hay gráficos por canal para esta selección.",
    "{channel} Daily Sales and Repurchase Sales Trend and Forecast":
        "{channel}: tendencia y pronóstico diario de ventas y recompras",
    "{channel} Sales Trend and Forecast": "{channel}: tendencia y pronóstico de ventas",
    "No {channel} repurchase sales data available for this selection.":
        "No hay datos de recompras de {channel} para esta selección.",
    "Gender split": "Por género",
    "City split": "Por ciudad",
    "Time split (3h)": "Por franja horaria (3 h)",
    "Historical Total Sales": "Ventas totales (histórico)",
    "Forecasted Total Sales": "Ventas totales (pronóstico)",
    "Historical All Repurchases": "Recompras (histórico)",
    "Forecasted All Repurchases": "Recompras (pronóstico)",
    "Historical {channel} Sales": "Ventas {channel} (histórico)",
    "Forecasted {channel} Sales": "Ventas {channel} (pronóstico)",
    "Historical {channel} Repurchase Sales": "Recompras {channel} (histórico)",
    "Forecasted {channel} Repurchase Sales": "Recompras {channel} (pronóstico)",
    "{channel} Sales": "Ventas {channel}",
    "Historical {x}": "{x} (histórico)",
    "Forecasted {x}": "{x} (pronóstico)",
    "no data": "sin datos",
    "female": "mujer",
    "male": "hombre",
    "Other": "Otras",
}


def translate(text: str, lang: str | None = None) -> str:
    lang = lang if lang is not None else g.get("lang", "en")
    return SPANISH.get(text, text) if lang == "es" else text


def request_language() -> str:
    """The language a page with a selector renders in: ?lang= when valid (and remembered), else the session's."""
    lang = (request.args.get("lang") or "").strip().lower()
    if lang in LANGUAGES:
        session["lang"] = lang
    lang = session.get("lang", "en")
    g.lang = lang if lang in LANGUAGES else "en"
    return g.lang


def init_app(app) -> None:
    @app.context_processor
    def _i18n():
        return {"_": translate, "lang": g.get("lang", "en"), "languages": LANGUAGES}

import pandas as pd

# Cargar los archivos CSV
repeated_customers_file = 'woo2024/repeated_customers.csv'
data_file = 'woo2024/data.csv'

# Leer los archivos en DataFrames
repeated_customers = pd.read_csv(repeated_customers_file)
data = pd.read_csv(data_file)

# Asegurar que las columnas relevantes existan
if 'Correo electrónico (facturación)' not in repeated_customers.columns or 'Correo electrónico (facturación)' not in data.columns:
    raise ValueError("Ambos archivos deben contener una columna 'Correo electrónico (facturación)'.")

if 'Fecha del pedido' not in data.columns:
    raise ValueError("El archivo data.csv debe contener una columna 'Fecha del pedido'.")

# Convertir las fechas a formato datetime
data['Fecha del pedido'] = pd.to_datetime(data['Fecha del pedido'])

# Extraer el mes y el año de las fechas
data['month_year'] = data['Fecha del pedido'].dt.to_period('M')

# Filtrar los datos para incluir solo los clientes repetidos
repeated_emails = set(repeated_customers['Correo electrónico (facturación)'])
filtered_data = data[data['Correo electrónico (facturación)'].isin(repeated_emails)]

# Contar las repeticiones por mes
total_repeats_per_month = filtered_data.groupby('month_year').size().reset_index(name='repeat_count')

# Guardar los resultados en un archivo CSV
output_file = 'woo2024/monthly_repeats_summary.csv'
total_repeats_per_month.to_csv(output_file, index=False)

print(f"El análisis se ha completado. El resumen de repeticiones por mes se ha guardado en {output_file}.")

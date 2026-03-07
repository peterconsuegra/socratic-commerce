import pandas as pd

# Cargar el archivo
file_path = '/mnt/data/orders-2025-01-16-07-03-02.csv'
df = pd.read_csv(file_path)

# Eliminar filas con números de pedido repetidos, conservando solo la primera aparición
df_unique = df.drop_duplicates(subset="Número de pedido", keep='first')

# Eliminar filas con importe total de pedido igual a 0
df_filtered = df_unique[df_unique["Importe total del pedido"] > 0]

# Guardar el resultado en un nuevo archivo
output_path = '/mnt/data/orders_cleaned.csv'
df_filtered.to_csv(output_path, index=False)

print(f"Archivo procesado guardado en: {output_path}")

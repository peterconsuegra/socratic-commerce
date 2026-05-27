import os
import pandas as pd
import logging

def get_daily_sales_trend(data_file, output_csv="daily_sales_trend.csv"):
    import os
    import pandas as pd
    import logging

    logger = logging.getLogger(__name__)

    # --- 1) Read the CSV File ---
    logger.debug("Reading orders CSV file: %s", data_file)
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")
    data = pd.read_csv(data_file)
    logger.debug("Orders data shape: %s", data.shape)

    # --- 2) Validate Required Columns ---
    required_cols = ['order_date', 'total_value']
    for col in required_cols:
        if col not in data.columns:
            raise ValueError(f"The data file must contain a '{col}' column.")

    # --- 3) Convert order_date to datetime ---
    logger.debug("Converting order_date column to datetime")
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data.dropna(subset=['order_date'], inplace=True)

    # --- 4) Extract the date part directly (assuming order_date is already in Bogotá local time) ---
    data['day'] = data['order_date'].dt.date

    # --- 5) Aggregate total orders and total sales per day ---
    logger.debug("Aggregating total orders and total sales per day")
    daily_agg = (
        data.groupby('day')
        .agg(
            Total_Orders=('order_date', 'size'),
            Total_Sales=('total_value', 'sum')
        )
        .reset_index()
    )

    # --- 6) Format the day column and Total Sales ---
    daily_agg['Day'] = daily_agg['day'].astype(str)
    # Ensure Total Sales are represented as integer strings (you may adjust as needed)
    daily_agg['Total Sales'] = daily_agg['Total_Sales'].apply(lambda x: str(int(float(x))))
    # Optional: you can keep Total Orders numeric or convert them as well

    # --- 7) Reorder columns for in-memory display ---
    daily_agg = daily_agg[['Day', 'Total_Orders', 'Total Sales']]
    daily_agg.rename(columns={'Total_Orders': 'Total Orders'}, inplace=True)

    # --- 8) Save a CSV subset with only 'Day' and 'Total Sales' ---
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_subset = daily_agg[['Day', 'Total Sales']]
    csv_path = os.path.join(output_dir, output_csv)
    csv_subset.to_csv(csv_path, index=False)
    logger.info("Daily sales trend CSV file saved successfully at %s", csv_path)

    # --- 9) Return the full daily aggregation as a list of dictionaries ---
    return daily_agg.to_dict(orient='records')


def get_daily_sales_paid_trend(data_file, output_csv="daily_sales_paid_trend.csv", repeated_customers_file="data/repeated_customers.csv"):
    """
    Reads the orders file, filters for 'paid' orders from advertising,
    and excludes repurchase orders (i.e. orders that occur after a customer's first purchase).
    Returns a daily aggregation of the new (first) orders and saves a CSV with 'Day' and 'Sales'.

    Parameters:
      data_file (str): Path to the orders CSV.
      output_csv (str): Filename for the output CSV.
      repeated_customers_file (str): Path to the CSV with repeated customers.
                                   Expected columns: 'email', 'First Purchase'.

    Returns:
      list: A list of dictionaries with keys 'Day', 'Total Orders', and 'Total Sales'.
    """
    import os
    import pandas as pd
    import logging

    logger = logging.getLogger(__name__)

    # --- 1) Read the orders CSV file ---
    logger.debug("Reading orders CSV file: %s", data_file)
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")
    orders = pd.read_csv(data_file)
    logger.debug("Orders data shape: %s", orders.shape)

    # --- 2) Validate required columns in orders ---
    required_order_cols = ['order_date', 'total_value', 'utm_medium', 'email']
    for col in required_order_cols:
        if col not in orders.columns:
            raise ValueError(f"Orders file must contain '{col}' column.")

    # --- 3) Filter data where utm_medium == 'paid' ---
    orders_paid = orders[orders['utm_medium'] == 'paid']
    if orders_paid.empty:
        logger.warning("No rows found with utm_medium == 'paid'. Returning empty results.")
        return []

    # --- 4) Convert order_date to datetime and drop invalid dates ---
    orders_paid['order_date'] = pd.to_datetime(orders_paid['order_date'], errors='coerce')
    orders_paid = orders_paid.dropna(subset=['order_date'])

    # --- 5) Read repeated customers file and merge to identify repurchases ---
    if os.path.exists(repeated_customers_file):
        repeated = pd.read_csv(repeated_customers_file)
        if 'email' not in repeated.columns or 'First Purchase' not in repeated.columns:
            raise ValueError("Repeated customers file must contain 'email' and 'First Purchase' columns.")
        repeated['First Purchase'] = pd.to_datetime(repeated['First Purchase'], errors='coerce')
        # Merge on email; a left join will keep all paid orders
        orders_paid = orders_paid.merge(repeated[['email', 'First Purchase']], on='email', how='left')
    else:
        logger.warning("Repeated customers file not found. Proceeding without excluding repurchases.")
        orders_paid['First Purchase'] = pd.NaT

    # --- 6) Exclude repurchase orders ---
    # Keep orders that are either the customer's first purchase (order_date == First Purchase)
    # or where no first purchase record exists (i.e. new customer)
    orders_new = orders_paid[
        (orders_paid['First Purchase'].isna()) |
        (orders_paid['order_date'] == orders_paid['First Purchase'])
    ]

    # --- 7) Extract the date part directly (assuming order_date is already in Bogotá local time) ---
    orders_new['day'] = orders_new['order_date'].dt.date

    # --- 8) Group by day ---
    daily_agg = (
        orders_new.groupby('day')
        .agg(
            Total_Orders=('order_date', 'size'),
            Total_Sales=('total_value', 'sum')
        )
        .reset_index()
    )

    # --- 9) Format the day column as a string (YYYY-MM-DD) and Total Sales as an integer string ---
    daily_agg['Day'] = daily_agg['day'].astype(str)
    daily_agg['Total Sales'] = daily_agg['Total_Sales'].apply(lambda x: str(int(float(x))))
    full_data = daily_agg[['Day', 'Total_Orders', 'Total Sales']]
    full_data.rename(columns={'Total_Orders': 'Total Orders'}, inplace=True)

    # --- 10) Save CSV file with only 'Day' and 'Sales' columns ---
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_to_save = full_data[['Day', 'Total Sales']].copy()
    csv_to_save.rename(columns={'Total Sales': 'Sales'}, inplace=True)
    output_path = os.path.join(output_dir, output_csv)
    csv_to_save.to_csv(output_path, index=False)
    logger.info("Daily sales (paid, excluding repurchases) trend CSV file saved successfully at %s", output_path)

    # --- 11) Return the aggregated data ---
    return full_data.to_dict(orient='records')


def get_daily_sales_repurchases_trend(repeated_customers_file, data_file, output_file="daily_sales_repurchases_trend.csv"):
    import os
    import pandas as pd
    import logging

    logger = logging.getLogger(__name__)

    try:
        # --- READ CSV FILES ---
        logger.debug("Reading repeated customers CSV file: %s", repeated_customers_file)
        repeated_customers = pd.read_csv(repeated_customers_file)
        logger.debug("Repeated customers data shape: %s", repeated_customers.shape)
        
        logger.debug("Reading orders CSV file: %s", data_file)
        data = pd.read_csv(data_file)
        logger.debug("Orders data shape: %s", data.shape)
        
        # --- VALIDATE REQUIRED COLUMNS ---
        for col in ['email', 'order_date', 'total_value']:
            if col not in data.columns:
                raise ValueError(f"The data file must contain a '{col}' column.")

        if 'email' not in repeated_customers.columns:
            raise ValueError("Repeated customers file must contain an 'email' column.")
        
        # --- PROCESS ORDERS DATA ---
        logger.debug("Converting order_date column to datetime")
        data['order_date'] = pd.to_datetime(data['order_date'])
        data['day'] = data['order_date'].dt.to_period('D')  # Convert to daily period format

        # --- FILTER REPEATED CUSTOMERS ---
        logger.debug("Filtering repeated customers based on email")
        repeated_emails = set(repeated_customers['email'])
        repeated_orders = data[data['email'].isin(repeated_emails)]
        
        # Merge orders with first purchase day information
        repeated_customers['first_purchase_day'] = pd.to_datetime(repeated_customers['First Purchase'], errors='coerce')
        repeated_orders = repeated_orders.merge(
            repeated_customers[['email', 'first_purchase_day']],
            on='email',
            how='left'
        )
        
        # Exclude first-time purchases (only include repurchases)
        logger.debug("Excluding first-time purchases")
        repurchase_data = repeated_orders[repeated_orders['order_date'] > repeated_orders['first_purchase_day']]
        
        # --- AGGREGATE DATA ---
        logger.debug("Aggregating repurchase orders count per day")
        total_repurchases_per_day = (
            repurchase_data.groupby('day')
            .size()
            .reset_index(name='Repurchases')
        )
        
        logger.debug("Aggregating repurchase total sales per day")
        repurchase_value_per_day = (
            repurchase_data.groupby('day')['total_value']
            .sum()
            .reset_index(name='Repurchase_Total_Value')
        )
        
        # --- MERGE AGGREGATED DATA ---
        logger.debug("Merging aggregated data")
        summary = total_repurchases_per_day.merge(
            repurchase_value_per_day, on='day', how='left'
        )
        summary['Repurchase_Total_Value'] = summary['Repurchase_Total_Value'].fillna(0)
        
        # --- FORMAT COLUMNS ---
        summary['Day'] = summary['day'].apply(lambda x: x.strftime('%Y-%m-%d'))
        summary['Total Sales'] = summary['Repurchase_Total_Value'].apply(lambda x: str(int(x)))
        
        # --- SELECT COLUMNS FOR CSV OUTPUT ---
        # Create a copy with only the "Day" and "Sales" columns.
        # Rename "Total Sales" to "Sales" for the CSV file.
        csv_summary = summary[['Day', 'Total Sales']].rename(columns={'Total Sales': 'Sales'})
        
        # --- SAVE CSV FILE ---
        output_dir = os.path.join(os.getcwd(), "data")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        csv_path = os.path.join(output_dir, output_file)
        csv_summary.to_csv(csv_path, index=False)
        logger.info(f"Daily sales repurchases trend CSV file saved successfully at {csv_path}")
        
        # --- RETURN FULL DATA AS DICTIONARY LIST ---
        return summary.to_dict(orient='records')
    
    except Exception as e:
        logger.exception(f"An error occurred in get_daily_sales_repurchases_trend: {e}")
        raise e

import os
import pandas as pd

def get_daily_sales_undefined_trend(repurchases_path, paid_path, total_sales_path, output_file="daily_sales_undefined_trend.csv"):
    import os
    import pandas as pd

    # Load the total daily sales data.
    df_total = pd.read_csv(total_sales_path)  # Expected columns: "Day", "Total Sales"
    # Load the repurchases data.
    df_rep = pd.read_csv(repurchases_path)      # Expected columns: "Day", "Sales"
    # Load the paid sales data.
    df_paid = pd.read_csv(paid_path)            # Expected columns: "Day", "Sales"

    # Ensure the "Day" column is of type string in all DataFrames.
    for df in [df_total, df_rep, df_paid]:
        df["Day"] = df["Day"].astype(str)

    # Rename the sales columns for clarity.
    df_rep = df_rep.rename(columns={"Sales": "Sales_rep"})
    df_paid = df_paid.rename(columns={"Sales": "Sales_paid"})

    # Merge the total sales with repurchases on "Day".
    df_merged = pd.merge(df_total, df_rep, on="Day", how="left")
    # Merge the result with the paid sales on "Day".
    df_merged = pd.merge(df_merged, df_paid, on="Day", how="left")

    # Convert the numeric columns and fill missing values with 0.
    df_merged["Total Sales"] = pd.to_numeric(df_merged["Total Sales"], errors="coerce").fillna(0)
    df_merged["Sales_rep"] = pd.to_numeric(df_merged["Sales_rep"], errors="coerce").fillna(0)
    df_merged["Sales_paid"] = pd.to_numeric(df_merged["Sales_paid"], errors="coerce").fillna(0)

    # Calculate undefined sales as: Total Sales - (Sales_rep + Sales_paid)
    df_merged["Undefined Sales"] = df_merged["Total Sales"] - (df_merged["Sales_rep"] + df_merged["Sales_paid"])

    # Convert the undefined sales to an integer and then to a string.
    df_merged["Undefined Sales"] = df_merged["Undefined Sales"].astype(int).astype(str)

    # Prepare the final DataFrame with only "Day" and "Undefined Sales", renaming "Undefined Sales" to "Sales".
    df_final = df_merged[["Day", "Undefined Sales"]].rename(columns={"Undefined Sales": "Sales"})

    # Save the result to the specified CSV file.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, output_file)
    df_final.to_csv(csv_path, index=False)

    return df_final.to_dict(orient="records")

def merge_daily_tables_by_percentage(file1, file2, percentage, output_file):
    import pandas as pd
    import os

    # Load the CSV files into DataFrames.
    df1 = pd.read_csv(file1)
    df2 = pd.read_csv(file2)

    # Ensure both DataFrames have a common 'Day' column.
    if "Day" not in df1.columns or "Day" not in df2.columns:
        raise KeyError("Both CSV files must have a 'Day' column for merging.")

    # Merge the DataFrames on 'Day'.
    df_merged = pd.merge(df1, df2, on="Day", how="outer")

    # Rename the sales columns:
    # Assume file1 is the undefined sales table and file2 is the paid sales table.
    df_merged.rename(columns={'Sales_x': 'Sales_undef', 'Sales_y': 'Sales_paid'}, inplace=True)

    # Fill missing values with 0.
    df_merged.fillna(0, inplace=True)

    # Calculate the final Sales as: Sales_paid + (percentage/100) * Sales_undef.
    df_merged["Sales"] = df_merged["Sales_paid"] + (percentage / 100.0) * df_merged["Sales_undef"]

    # Prepare the output directory.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_path = os.path.join(output_dir, output_file)

    # Create a new DataFrame with only 'Day' and 'Sales' to save to CSV.
    df_to_save = df_merged[['Day', 'Sales']].copy()
    df_to_save.to_csv(output_path, index=False)

    # Return the full merged DataFrame as a list of dictionaries.
    return df_merged.to_dict(orient="records")

import os
import pandas as pd

import os
import pandas as pd

def get_consolidate_daily_sales(paid_csv, repurchases_csv, organic_csv, output_file="daily_sales_trend_consolidated.csv"):
    """
    Consolidates daily sales data from three CSV files:
      - paid_csv: CSV file containing paid channel sales (or paid plus undefined trend) with columns: Day, Sales.
      - repurchases_csv: CSV file containing repurchases sales with columns: Day, Sales.
      - organic_csv: CSV file containing organic sales with columns: Day, Sales.
    
    The resulting DataFrame will have the following columns:
      Day, repurchases sales, paid channel sales, organic sales, total sales
    
    All sales values are assumed to be plain numeric strings.
    The method saves the consolidated data to:
        data/daily_sales_trend_consolidated.csv
    and returns the data as a list of dictionaries.
    """
    # Read the CSV files.
    df_paid = pd.read_csv(paid_csv)         # Expected columns: Day, Sales
    df_rep = pd.read_csv(repurchases_csv)     # Expected columns: Day, Sales
    df_org = pd.read_csv(organic_csv)         # Expected columns: Day, Sales

    # Standardize the Day column in each DataFrame to the format YYYY-MM-DD.
    for df in [df_paid, df_rep, df_org]:
        df["Day"] = pd.to_datetime(df["Day"], errors="coerce").dt.strftime("%Y-%m-%d")

    # Rename the Sales columns for clarity.
    df_paid = df_paid.rename(columns={"Sales": "paid channel sales"})
    df_rep = df_rep.rename(columns={"Sales": "repurchases sales"})
    df_org = df_org.rename(columns={"Sales": "organic sales"})

    # Merge the DataFrames on "Day" using an outer join so that all days are included.
    df_merged = pd.merge(df_rep, df_paid, on="Day", how="outer")
    df_merged = pd.merge(df_merged, df_org, on="Day", how="outer")

    # Convert the sales columns to numeric and fill missing values with 0.
    for col in ["repurchases sales", "paid channel sales", "organic sales"]:
        df_merged[col] = pd.to_numeric(df_merged[col], errors="coerce").fillna(0)

    # Calculate the total sales by summing the three channels.
    df_merged["total sales"] = (df_merged["repurchases sales"] +
                                df_merged["paid channel sales"] +
                                df_merged["organic sales"])

    # Reorder the columns.
    df_merged = df_merged[["Day", "repurchases sales", "paid channel sales", "organic sales", "total sales"]]

    # Save the consolidated DataFrame to a CSV file.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    consolidated_csv_path = os.path.join(output_dir, output_file)
    df_merged.to_csv(consolidated_csv_path, index=False)

    # Return the consolidated data as a list of dictionaries.
    return df_merged.to_dict(orient="records")

import os
import pandas as pd
import logging

# (Other functions remain unchanged …)

def get_daily_sales_forecast_arima(consolidated_csv, forecast_periods=10):
    """
    ARIMA-based forecast:
    Loads historical daily sales data from the consolidated CSV file,
    fits a SARIMA model (with weekly seasonality) using statsmodels,
    and returns a forecast for the next forecast_periods days as a list of dictionaries.
    The 'Day' column is returned in the format YYYY-MM-DD.
    """
    try:
        import os
        import pandas as pd
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        # Build the absolute path if necessary
        if not os.path.isabs(consolidated_csv):
            consolidated_csv = os.path.join(os.getcwd(), consolidated_csv)

        # Load the historical data
        df = pd.read_csv(consolidated_csv)
        if 'Day' not in df.columns or 'total sales' not in df.columns:
            raise ValueError("CSV must contain 'Day' and 'total sales' columns.")

        # Convert 'Day' to datetime and set it as the index
        df['Day'] = pd.to_datetime(df['Day'], errors='coerce')
        df.dropna(subset=['Day'], inplace=True)
        df.sort_values('Day', inplace=True)
        df.set_index('Day', inplace=True)

        # Prepare the time series (ensure numeric values)
        ts = pd.to_numeric(df['total sales'], errors='coerce').dropna()

        # Fit a SARIMA model with seasonal period 7 (weekly seasonality)
        model = SARIMAX(
            ts,
            order=(1,1,1),
            seasonal_order=(1,1,1,7),
            enforce_stationarity=False,
            enforce_invertibility=False
        )
        model_fit = model.fit(disp=False)

        # Forecast the next forecast_periods days
        forecast_result = model_fit.get_forecast(steps=forecast_periods)
        forecast_mean = forecast_result.predicted_mean

        # Create a date range for the forecast dates
        last_date = ts.index[-1]
        forecast_dates = pd.date_range(
            start=last_date + pd.Timedelta(days=1),
            periods=forecast_periods,
            freq='D'
        )

        # Build the forecast DataFrame
        forecast_df = pd.DataFrame({
            'Day': forecast_dates,
            'Total Sales': forecast_mean.values
        })

        # Round the forecast values
        forecast_df['Total Sales'] = forecast_df['Total Sales'].round(0).astype(int)

        # **Format the 'Day' column as YYYY-MM-DD**
        forecast_df['Day'] = forecast_df['Day'].dt.strftime('%Y-%m-%d')

        # Return as a list of dictionaries
        return forecast_df.to_dict(orient='records')

    except Exception as e:
        print(f"[ERROR] get_daily_sales_forecast_arima: {e}")
        return []
    
import os
import pandas as pd
import logging

def get_daily_facebook_sales_trend(data_file, output_csv="daily_facebook_sales_trend.csv"):
    """
    Reads the orders CSV at data_file, filters for utm_source == 'facebook',
    aggregates total_value per day, saves a CSV with ['Day','Total Sales'],
    and returns a list of dicts: [{'Day': 'YYYY-MM-DD', 'Total_Sales': 12345}, ...].
    """
    logger = logging.getLogger(__name__)

    # 1) Ensure the file exists
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    # 2) Load the data
    df = pd.read_csv(data_file)
    required_cols = ['order_date', 'total_value', 'utm_source']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Orders file must contain '{col}' column.")

    # 3) Convert order_date to datetime, drop invalid
    df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
    df = df.dropna(subset=['order_date'])

    # 4) Filter only utm_source == 'facebook'
    df_fb = df[df['utm_source'].str.lower() == 'facebook'].copy()
    if df_fb.empty:
        logger.warning("No rows found with utm_source == 'facebook'. Returning empty list.")
        return []

    # 5) Extract the date part
    df_fb['day'] = df_fb['order_date'].dt.date

    # 6) Group by day, sum up total_value
    daily_agg = (
        df_fb.groupby('day')
             .agg(Total_Sales=('total_value', 'sum'))
             .reset_index()
    )

    # 7) Format columns: string dates and integer‐string sales
    daily_agg['Day'] = daily_agg['day'].astype(str)  # 'YYYY-MM-DD'
    daily_agg['Total Sales'] = daily_agg['Total_Sales'].apply(lambda x: str(int(float(x))))

    # 8) Keep only ['Day','Total Sales'] for CSV output
    csv_subset = daily_agg[['Day', 'Total Sales']].copy()

    # 9) Write to data/daily_facebook_sales_trend.csv
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, output_csv)
    csv_subset.to_csv(csv_path, index=False)
    logger.info("Daily Facebook sales trend CSV saved at %s", csv_path)

    # 10) Return full aggregation (including the unsaved Total_Sales column) as list of dicts
    return daily_agg.to_dict(orient='records')


import os
import pandas as pd
import logging

def get_daily_google_sales_trend(data_file, output_csv="daily_google_sales_trend.csv"):
    """
    Lee data_file (ej. "data/daily_orders.csv"), filtra filas con utm_source == 'google',
    agrupa por día y suma total_value. Guarda un CSV con columnas ['Day','Total Sales']
    en data/daily_google_sales_trend.csv y devuelve una lista de dicts:
    [ { 'day': date(...), 'Total_Sales': float, 'Day': 'YYYY-MM-DD', 'Total Sales': '12345' }, ... ]
    """
    logger = logging.getLogger(__name__)

    # 1) Verificar que exista el archivo
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    # 2) Cargar el CSV
    df = pd.read_csv(data_file)
    required_cols = ['order_date', 'total_value', 'utm_source']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Orders file must contain '{col}' column.")

    # 3) Convertir 'order_date' a datetime, eliminar filas inválidas
    df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
    df = df.dropna(subset=['order_date'])

    # 4) Filtrar solo utm_source == 'google' (sin diferenciar mayúsculas/minúsculas)
    df_google = df[df['utm_source'].str.lower() == 'google'].copy()
    if df_google.empty:
        logger.warning("No rows found with utm_source == 'google'. Returning empty list.")
        return []

    # 5) Extraer la parte de fecha (date) de 'order_date'
    df_google['day'] = df_google['order_date'].dt.date

    # 6) Agrupar por día y sumar total_value
    daily_agg = (
        df_google.groupby('day')
                 .agg(Total_Sales=('total_value', 'sum'))
                 .reset_index()
    )

    # 7) Formatear: columna 'Day' como string YYYY-MM-DD, 'Total Sales' como string entero
    daily_agg['Day'] = daily_agg['day'].astype(str)
    daily_agg['Total Sales'] = daily_agg['Total_Sales'].apply(lambda x: str(int(float(x))))

    # 8) CSV subset con ['Day','Total Sales']
    csv_subset = daily_agg[['Day', 'Total Sales']].copy()

    # 9) Guardar en data/daily_google_sales_trend.csv
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, output_csv)
    csv_subset.to_csv(csv_path, index=False)
    logger.info("Daily Google sales trend CSV saved at %s", csv_path)

    # 10) Retornar el agregamiento completo (con Total_Sales) como list of dicts
    return daily_agg.to_dict(orient='records')

import os
import pandas as pd
import logging

def get_daily_wati_sales_trend(data_file, output_csv="daily_wati_sales_trend.csv"):
    """
    Lee data_file (p.ej. "data/daily_orders.csv"), filtra filas con utm_source == 'wati',
    agrupa por día y suma total_value. Guarda un CSV con ['Day','Total Sales']
    en data/daily_wati_sales_trend.csv y devuelve una lista de dicts:
    [ { 'day': date(...), 'Total_Sales': float, 'Day': 'YYYY-MM-DD', 'Total Sales': '12345' }, ... ]
    """
    logger = logging.getLogger(__name__)

    # 1) Verificar que exista el archivo
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    # 2) Cargar el CSV
    df = pd.read_csv(data_file)
    required_cols = ['order_date', 'total_value', 'utm_source']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Orders file must contain '{col}' column.")

    # 3) Convertir 'order_date' a datetime, eliminar filas inválidas
    df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
    df = df.dropna(subset=['order_date'])

    # 4) Filtrar solo utm_source == 'wati' (sin diferenciar mayúsculas/minúsculas)
    df_wati = df[df['utm_source'].str.lower() == 'wati'].copy()
    if df_wati.empty:
        logger.warning("No rows found with utm_source == 'wati'. Returning empty list.")
        return []

    # 5) Extraer la parte de fecha (date) de 'order_date'
    df_wati['day'] = df_wati['order_date'].dt.date

    # 6) Agrupar por día y sumar total_value
    daily_agg = (
        df_wati.groupby('day')
              .agg(Total_Sales=('total_value', 'sum'))
              .reset_index()
    )

    # 7) Formatear: columna 'Day' como string YYYY-MM-DD, 'Total Sales' como string entero
    daily_agg['Day'] = daily_agg['day'].astype(str)
    daily_agg['Total Sales'] = daily_agg['Total_Sales'].apply(lambda x: str(int(float(x))))

    # 8) CSV subset con ['Day','Total Sales']
    csv_subset = daily_agg[['Day', 'Total Sales']].copy()

    # 9) Guardar en data/daily_wati_sales_trend.csv
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, output_csv)
    csv_subset.to_csv(csv_path, index=False)
    logger.info("Daily Wati sales trend CSV saved at %s", csv_path)

    # 10) Retornar el agregamiento completo (con Total_Sales) como list of dicts
    return daily_agg.to_dict(orient='records')

import os
import pandas as pd
import logging

def get_daily_facebook_sales_forecast_arima(
    facebook_csv: str,
    forecast_periods: int = 7
) -> list[dict]:
    """
    Performs a SARIMA forecast on the day-by-day Facebook sales CSV.
    - facebook_csv: path like "data/daily_facebook_sales_trend.csv"
      which must contain columns: 'Day' (YYYY-MM-DD) and 'Total Sales' (string or number).
    - forecast_periods: how many days ahead to predict (default: 7).
    Returns a list of dicts with keys "Day" (YYYY-MM-DD) and "Total Sales" (rounded int).
    """
    logger = logging.getLogger(__name__)

    # 1) Build absolute path if needed
    if not os.path.isabs(facebook_csv):
        facebook_csv = os.path.join(os.getcwd(), facebook_csv)

    # 2) Load the historical Facebook data
    if not os.path.exists(facebook_csv):
        raise FileNotFoundError(f"Facebook CSV not found: {facebook_csv}")
    df = pd.read_csv(facebook_csv)

    # 3) Ensure required columns exist
    if 'Day' not in df.columns or 'Total Sales' not in df.columns:
        raise ValueError("Facebook CSV must contain 'Day' and 'Total Sales' columns.")

    # 4) Convert 'Day' to datetime and sort
    df['Day'] = pd.to_datetime(df['Day'], format='%Y-%m-%d', errors='coerce')
    df = df.dropna(subset=['Day']).copy()
    df = df.sort_values('Day')

    # 5) Rename 'Total Sales' → 'total sales', convert to numeric
    df.rename(columns={'Total Sales': 'total sales'}, inplace=True)
    df['total sales'] = pd.to_numeric(df['total sales'], errors='coerce').fillna(0)

    # 6) Set index to 'Day'
    df.set_index('Day', inplace=True)

    # 7) Build SARIMAX(1,1,1)x(1,1,1,7) for weekly seasonality
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError as e:
        logger.error("[ERROR] statsmodels not installed: %s", e)
        return []

    ts = df['total sales']
    if ts.shape[0] < 3:
        logger.warning("Not enough Facebook data points for ARIMA. Returning empty forecast.")
        return []

    model = SARIMAX(
        ts,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 7),
        enforce_stationarity=False,
        enforce_invertibility=False
    )
    model_fit = model.fit(disp=False)

    # 8) Forecast the next `forecast_periods` days
    forecast_result = model_fit.get_forecast(steps=forecast_periods)
    forecast_mean = forecast_result.predicted_mean  # a pandas Series indexed by the forecast dates

    # 9) Build a date range for the next periods
    last_date = ts.index[-1]
    future_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1),
        periods=forecast_periods,
        freq='D'
    )

    # 10) Construct a DataFrame of forecasted values
    forecast_df = pd.DataFrame({
        'Day': future_dates,
        'Total Sales': forecast_mean.values
    })
    # Round the values to nearest integer
    forecast_df['Total Sales'] = forecast_df['Total Sales'].round(0).astype(int)
    # Format 'Day' as YYYY-MM-DD string
    forecast_df['Day'] = forecast_df['Day'].dt.strftime('%Y-%m-%d')

    # 11) Return as a list of dicts
    return forecast_df.to_dict(orient='records')


import os
import pandas as pd
import logging

def get_daily_google_sales_forecast_arima(
    google_csv: str,
    forecast_periods: int = 7
) -> list[dict]:
    """
    Realiza un pronóstico SARIMAX sobre el CSV que produce get_daily_google_sales_trend().
    - google_csv: ruta al CSV (p.ej. "data/daily_google_sales_trend.csv") que debe contener 
      columnas 'Day' (YYYY-MM-DD) y 'Total Sales' (string o numérico).
    - forecast_periods: cuántos días hacia adelante predecir (por defecto 7).
    Devuelve una lista de dicts [{"Day": "YYYY-MM-DD", "Total Sales": int}, …].
    """
    logger = logging.getLogger(__name__)

    # 1) Obtener la ruta absoluta si es relativa
    if not os.path.isabs(google_csv):
        google_csv = os.path.join(os.getcwd(), google_csv)

    # 2) Cargar el CSV histórico de Google
    if not os.path.exists(google_csv):
        raise FileNotFoundError(f"Google CSV not found: {google_csv}")
    df = pd.read_csv(google_csv)

    # 3) Verificar columnas requeridas
    if 'Day' not in df.columns or 'Total Sales' not in df.columns:
        raise ValueError("Google CSV must contain 'Day' and 'Total Sales' columns.")

    # 4) Convertir 'Day' a datetime y ordenar
    df['Day'] = pd.to_datetime(df['Day'], format='%Y-%m-%d', errors='coerce')
    df = df.dropna(subset=['Day']).copy()
    df = df.sort_values('Day')

    # 5) Renombrar 'Total Sales' → 'total sales', convertir a numérico
    df.rename(columns={'Total Sales': 'total sales'}, inplace=True)
    df['total sales'] = pd.to_numeric(df['total sales'], errors='coerce').fillna(0)

    # 6) Establecer índice en 'Day'
    df.set_index('Day', inplace=True)

    # 7) Importar SARIMAX
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError as e:
        logger.error("[ERROR] statsmodels not installed: %s", e)
        return []

    ts = df['total sales']
    if ts.shape[0] < 3:
        logger.warning("Not enough Google data points for ARIMA. Returning empty forecast.")
        return []

    # 8) Ajustar un SARIMAX(1,1,1)x(1,1,1,7) (semanal)
    model = SARIMAX(
        ts,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 7),
        enforce_stationarity=False,
        enforce_invertibility=False
    )
    model_fit = model.fit(disp=False)

    # 9) Pronosticar los próximos forecast_periods días
    forecast_result = model_fit.get_forecast(steps=forecast_periods)
    forecast_mean = forecast_result.predicted_mean  # pandas Series

    # 10) Generar rango de fechas futuras
    last_date = ts.index[-1]
    future_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1),
        periods=forecast_periods,
        freq='D'
    )

    # 11) Construir DataFrame de pronóstico
    forecast_df = pd.DataFrame({
        'Day': future_dates,
        'Total Sales': forecast_mean.values
    })
    # Redondear a entero
    forecast_df['Total Sales'] = forecast_df['Total Sales'].round(0).astype(int)
    # Formatear 'Day' como string
    forecast_df['Day'] = forecast_df['Day'].dt.strftime('%Y-%m-%d')

    # 12) Retornar lista de dicts
    return forecast_df.to_dict(orient='records')

import os
import pandas as pd
import logging

def get_daily_wati_sales_forecast_arima(
    wati_csv: str,
    forecast_periods: int = 7
) -> list[dict]:
    """
    Realiza un pronóstico SARIMAX sobre el CSV que produce get_daily_wati_sales_trend().
    - wati_csv: ruta al CSV (p.ej. "data/daily_wati_sales_trend.csv"), que debe contener 
      columnas 'Day' (YYYY-MM-DD) y 'Total Sales' (string o numérico).
    - forecast_periods: cuántos días en el futuro predecir (por defecto 7).
    Devuelve una lista de dicts [{"Day": "YYYY-MM-DD", "Total Sales": int}, …].
    """
    logger = logging.getLogger(__name__)

    # 1) Asegurarse de tener la ruta absoluta
    if not os.path.isabs(wati_csv):
        wati_csv = os.path.join(os.getcwd(), wati_csv)

    # 2) Verificar existencia y cargar CSV
    if not os.path.exists(wati_csv):
        raise FileNotFoundError(f"Wati CSV not found: {wati_csv}")
    df = pd.read_csv(wati_csv)

    # 3) Validar que existan las columnas necesarias
    if 'Day' not in df.columns or 'Total Sales' not in df.columns:
        raise ValueError("Wati CSV must contain 'Day' and 'Total Sales' columns.")

    # 4) Convertir 'Day' a datetime y ordenar
    df['Day'] = pd.to_datetime(df['Day'], format='%Y-%m-%d', errors='coerce')
    df = df.dropna(subset=['Day']).copy()
    df = df.sort_values('Day')

    # 5) Renombrar 'Total Sales' → 'total sales' y convertir a numérico
    df.rename(columns={'Total Sales': 'total sales'}, inplace=True)
    df['total sales'] = pd.to_numeric(df['total sales'], errors='coerce').fillna(0)

    # 6) Establecer índice en 'Day'
    df.set_index('Day', inplace=True)

    # 7) Intentar importar SARIMAX
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError as e:
        logger.error("[ERROR] statsmodels not installed: %s", e)
        return []

    ts = df['total sales']
    if ts.shape[0] < 3:
        logger.warning("Not enough Wati data points for ARIMA. Returning empty forecast.")
        return []

    # 8) Ajustar un SARIMAX(1,1,1)x(1,1,1,7) (semanal)
    model = SARIMAX(
        ts,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 7),
        enforce_stationarity=False,
        enforce_invertibility=False
    )
    model_fit = model.fit(disp=False)

    # 9) Pronosticar los próximos forecast_periods días
    forecast_result = model_fit.get_forecast(steps=forecast_periods)
    forecast_mean = forecast_result.predicted_mean  # pandas Series

    # 10) Generar rango de fechas futuras
    last_date = ts.index[-1]
    future_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1),
        periods=forecast_periods,
        freq='D'
    )

    # 11) Construir DataFrame de pronóstico
    forecast_df = pd.DataFrame({
        'Day': future_dates,
        'Total Sales': forecast_mean.values
    })
    # Redondear a entero
    forecast_df['Total Sales'] = forecast_df['Total Sales'].round(0).astype(int)
    # Formatear 'Day' como string
    forecast_df['Day'] = forecast_df['Day'].dt.strftime('%Y-%m-%d')

    # 12) Retornar lista de dicts
    return forecast_df.to_dict(orient='records')
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

def find_customers_with_multiple_purchases(input_file):
    # Cargar los datos
    data = pd.read_csv(input_file)

    # Asegurar que las columnas necesarias existan
    required_columns = ["email", "gender"]
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The input file must contain the following columns: {required_columns}")

    # Contar el número de compras por correo
    purchase_counts = data["email"].value_counts()

    # Filtrar para clientes con más de una compra
    repeated_customers = purchase_counts[purchase_counts > 1]

    # Crear un DataFrame con los resultados
    repeated_customers_df = pd.DataFrame({
        "email": repeated_customers.index,
        "Número de compras": repeated_customers.values
    })

    # Merge para incluir la columna de género
    repeated_customers_df = repeated_customers_df.merge(
        data[["email", "gender"]].drop_duplicates(),
        on="email",
        how="left"
    )

    # Calcular totales y porcentajes por género
    unique_customers_by_gender = data[["email", "gender"]].drop_duplicates()["gender"].value_counts()
    repeated_customers_by_gender = repeated_customers_df["gender"].value_counts()

    stats = []
    for gender in unique_customers_by_gender.index:
        total_unique = unique_customers_by_gender[gender]
        total_repeated = repeated_customers_by_gender.get(gender, 0)
        percentage_repeated = (total_repeated / total_unique) * 100
        stats.append({
            "gender": gender,
            "total_unique": total_unique,
            "total_repeated": total_repeated,
            "percentage_repeated": f"{percentage_repeated:.2f}%"
        })

    return repeated_customers_df, stats

def process_repeated_orders(input_file, gender):
    # Importar el archivo CSV
    data = pd.read_csv(input_file)

    # Asegurar que las columnas necesarias existan
    required_columns = ["gender", "email"]
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The input file must contain the following columns: {required_columns}")

    # Filtrar datos por género
    filtered_data = data[data["gender"] == gender]
    total_orders = len(filtered_data)

    # Identificar órdenes repetidas
    repeated_orders = filtered_data[filtered_data.duplicated(subset="email", keep=False)].copy()

    # Calcular métricas
    total_repeats = len(repeated_orders)
    repurchase_percentage = (total_repeats / total_orders) * 100 if total_orders > 0 else 0

    return {
        "gender": gender,
        "total_orders": total_orders,
        "total_repeats": total_repeats,
        "repurchase_percentage": f"{repurchase_percentage:.2f}%"
    }

def _forecast_monthly_series(series: pd.Series, periods: int = 12) -> pd.Series:
    """
    Forecast a monthly series using Holt-Winters first, then SARIMAX, then fallback.

    series: monthly series indexed by datetime (month start).
    periods: number of future months to forecast.
    """
    series = series.astype(float)

    # Not enough history: repeat last known value
    if len(series) < 6:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

    # 1) Holt-Winters
    try:
        seasonal_periods = 12  # typical yearly seasonality for monthly data
        use_seasonal = len(series) >= (seasonal_periods * 2)

        model = ExponentialSmoothing(
            series,
            trend="add",
            seasonal="add" if use_seasonal else None,
            seasonal_periods=seasonal_periods if use_seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        fc = fit.forecast(periods)
        return fc.clip(lower=0.0)
    except Exception:
        pass

    # 2) SARIMAX fallback
    try:
        seasonal_periods = 12
        seasonal_order = (1, 0, 1, seasonal_periods) if len(series) >= (seasonal_periods * 2) else (0, 0, 0, 0)

        model = SARIMAX(
            series,
            order=(1, 1, 1),
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False)
        fc = fit.forecast(steps=periods)
        return fc.clip(lower=0.0)
    except Exception:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

def get_monthly_repurchases_trend(
    output_file="monthly_repurchases_trend.csv",
    forecast_periods: int = 12,
    return_forecast: bool = True,
    orders_csv_path: str = "data/all_orders.csv",
):
    """
    Builds monthly repurchases summary and (optionally) produces a monthly forecast.

    Data source:
      Always reads from orders_csv_path (default: data/all_orders.csv)

    Date window:
      Includes all orders up to the last day of the previous month (end of previous month).
      Example: if today is Jan 14, 2026, include orders up to Dec 31, 2025.

    Repurchase definition:
      For each email, earliest order_date is the first purchase.
      Any order with order_date > first_order_date is a repurchase (includes same-month repurchases).

    Returns:
      - if return_forecast is False: summary_rows
      - if return_forecast is True: (summary_rows, forecast_rows)

    Also writes a CSV to /data/<output_file> with columns: Month, Sales
    where Sales is the numeric repurchase total value for that month.
    """
    import os
    import logging
    import pandas as pd

    logger = logging.getLogger(__name__)
    logger.info("Building monthly repurchases trend from %s", orders_csv_path)

    # ---------- Read orders ----------
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    # ---------- Clean types ----------
    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    before = len(data)
    data = data.dropna(subset=["order_date", "email"])
    after = len(data)
    if after < before:
        logger.warning("Dropped %s rows with invalid order_date or email", before - after)

    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    # ---------- Cut to end of previous month ----------
    # e.g. if today is 2026-01-14, cutoff becomes 2025-12-31 23:59:59.999...
    today = pd.Timestamp.today().normalize()
    start_current_month = today.replace(day=1)
    end_prev_month = start_current_month - pd.Timedelta(microseconds=1)

    data = data[data["order_date"] <= end_prev_month].copy()
    logger.info("Using orders up to %s (end of previous month)", end_prev_month)

    if data.empty:
        logger.warning("No orders found up to end of previous month. Returning empty results.")
        summary_rows = []
        forecast_rows = []
        # Still write an empty CSV for compatibility
        output_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(output_dir, exist_ok=True)
        pd.DataFrame(columns=["Month", "Sales"]).to_csv(os.path.join(output_dir, output_file), index=False)
        return (summary_rows, forecast_rows) if return_forecast else summary_rows

    # ---------- Identify repeat customers (2+ orders) ----------
    email_counts = data["email"].value_counts(dropna=True)
    repeat_emails = set(email_counts[email_counts > 1].index)

    # ---------- First order per email (datetime) ----------
    first_order_dt = data.groupby("email")["order_date"].min()

    # Join first order date onto each row
    data = data.join(first_order_dt, on="email", rsuffix="_first")

    # Any order strictly after first order is a repurchase
    data["is_repurchase"] = (data["email"].isin(repeat_emails)) & (data["order_date"] > data["order_date_first"])

    # Monthly period for each order
    data["month_year"] = data["order_date"].dt.to_period("M")

    # ---------- Monthly totals (all orders) ----------
    total_orders_per_month = (
        data.groupby("month_year")
        .size()
        .reset_index(name="Total Orders")
    )

    total_sales_per_month = (
        data.groupby("month_year")["total_value"]
        .sum()
        .reset_index(name="Total_Sales_Num")
    )

    # ---------- Monthly repurchases ----------
    repurchase_data = data[data["is_repurchase"]].copy()

    repurchase_orders_per_month = (
        repurchase_data.groupby("month_year")
        .size()
        .reset_index(name="Repurchases")
    )

    repurchase_value_per_month = (
        repurchase_data.groupby("month_year")["total_value"]
        .sum()
        .reset_index(name="Repurchase_Total_Value")
    )

    # Optional integrity log
    logger.info(
        "Repurchase orders: %s (%.2f total_value) across %s repeat customers",
        len(repurchase_data),
        float(repurchase_data["total_value"].sum()) if not repurchase_data.empty else 0.0,
        len(repeat_emails),
    )

    # ---------- Combine summary ----------
    summary = (
        total_orders_per_month
        .merge(repurchase_orders_per_month, on="month_year", how="left")
        .merge(total_sales_per_month, on="month_year", how="left")
        .merge(repurchase_value_per_month, on="month_year", how="left")
        .sort_values("month_year")
    )

    summary["Repurchases"] = summary["Repurchases"].fillna(0).astype(int)
    summary["Repurchase_Total_Value"] = summary["Repurchase_Total_Value"].fillna(0.0)
    summary["Total_Sales_Num"] = summary["Total_Sales_Num"].fillna(0.0)

    summary["Repurchase Sales Percentage (%)"] = summary.apply(
        lambda row: (row["Repurchase_Total_Value"] / row["Total_Sales_Num"]) * 100
        if row["Total_Sales_Num"] > 0 else 0.0,
        axis=1,
    ).map(lambda x: f"{x:.2f}%")

    summary["Month"] = summary["month_year"].apply(lambda x: x.strftime("%Y-%m"))

    # Keep your existing “numeric string” display behavior
    summary["Total Sales"] = summary["Total_Sales_Num"].apply(lambda x: str(int(round(x))))
    summary["Repurchase Total Value"] = summary["Repurchase_Total_Value"].apply(lambda x: str(int(round(x))))

    summary_rows = summary.to_dict(orient="records")

    # ---------- Save CSV (Month, Sales) ----------
    csv_summary = summary[["Month", "Repurchase Total Value"]].rename(
        columns={"Repurchase Total Value": "Sales"}
    )

    output_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, output_file)
    csv_summary.to_csv(csv_path, index=False)
    logger.info("Monthly repurchases trend CSV saved at %s", csv_path)

    # ---------- Forecast ----------
    forecast_rows = []
    if return_forecast:
        ts = csv_summary.copy()
        ts["Month_dt"] = pd.to_datetime(ts["Month"] + "-01", errors="coerce")
        ts["Sales_num"] = pd.to_numeric(ts["Sales"], errors="coerce").fillna(0.0)
        ts = ts.dropna(subset=["Month_dt"]).sort_values("Month_dt")

        if not ts.empty:
            s = ts.set_index("Month_dt")["Sales_num"]

            full_idx = pd.date_range(s.index.min(), s.index.max(), freq="MS")
            s = s.reindex(full_idx, fill_value=0.0)

            fc = _forecast_monthly_series(s, periods=forecast_periods)
            fc_idx = pd.date_range(s.index.max() + pd.offsets.MonthBegin(1), periods=forecast_periods, freq="MS")

            for d, v in zip(fc_idx, fc):
                forecast_rows.append({
                    "Month": d.strftime("%Y-%m"),
                    "Projected Repurchase Sales": float(v),
                })

    if return_forecast:
        return summary_rows, forecast_rows
    return summary_rows



def print_customers_with_multiple_purchases(input_file, output_file):
    import pandas as pd

    # Load the data
    data = pd.read_csv(input_file)

    # Ensure that the required columns exist (including 'order_date')
    required_columns = ["email", "gender", "order_date"]
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The input file must contain the following columns: {required_columns}")

    # Convert the order_date column to datetime (coerce errors if necessary)
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')

    # Count the number of purchases per email
    purchase_counts = data["email"].value_counts()

    # Filter for customers with more than one purchase
    repeated_customers = purchase_counts[purchase_counts > 1]

    # Create a DataFrame with the count of purchases for repeated customers
    repeated_customers_df = pd.DataFrame({
        "email": repeated_customers.index,
        "Número de compras": repeated_customers.values
    })

    # Merge in the gender information (dropping duplicate emails to keep one record per customer)
    repeated_customers_df = repeated_customers_df.merge(
        data[["email", "gender"]].drop_duplicates(),
        on="email",
        how="left"
    )

    # Compute the first purchase date for each customer
    first_purchase = data.groupby("email")["order_date"].min().reset_index()

    # Merge the first purchase date into the repeated customers DataFrame
    repeated_customers_df = repeated_customers_df.merge(
        first_purchase,
        on="email",
        how="left"
    )

    # Rename the order_date column to 'First Purchase'
    repeated_customers_df.rename(columns={"order_date": "First Purchase"}, inplace=True)

    # Format the 'First Purchase' column to show the monthly period format (e.g., "February 2025")
    repeated_customers_df["First Purchase"] = repeated_customers_df["First Purchase"].dt.strftime("%B %Y")

    # Save the results to a CSV file
    repeated_customers_df.to_csv(output_file, index=False)

    # (Optional) Print statistics by gender for debugging or informational purposes
    unique_customers_by_gender = data[["email", "gender"]].drop_duplicates()["gender"].value_counts()
    repeated_customers_by_gender = repeated_customers_df["gender"].value_counts()

    for gender in unique_customers_by_gender.index:
        total_unique = unique_customers_by_gender[gender]
        total_repeated = repeated_customers_by_gender.get(gender, 0)
        percentage_repeated = (total_repeated / total_unique) * 100
        print(f"Gender: {gender} | Total Unique: {total_unique} | Total Repeated: {total_repeated} | Percentage Repeated: {percentage_repeated:.2f}%")

    return output_file

def get_daily_repurchases(repeated_customers_file, data_file):
    import pandas as pd

    # Load the CSV files
    repeated_customers = pd.read_csv(repeated_customers_file)
    data = pd.read_csv(data_file)

    # Validate that the necessary columns exist
    for col in ['email', 'order_date', 'total_value']:
        if col not in data.columns:
            raise ValueError(f"Data file must contain a '{col}' column.")
    if 'email' not in repeated_customers.columns:
        raise ValueError("The repeated customers file must contain an 'email' column.")

    # Convert order_date to datetime and drop rows with invalid dates
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data = data.dropna(subset=['order_date'])

    # Identify orders from customers with multiple purchases
    repeated_emails = set(repeated_customers['email'])
    repeated_data = data[data['email'].isin(repeated_emails)]

    # Group by day to calculate counts and sums for all orders
    daily_total = data.groupby(data['order_date'].dt.date).agg(
        Total_Orders=('order_date', 'size'),
        Total_Value=('total_value', 'sum')
    ).reset_index()
    daily_total.rename(columns={daily_total.columns[0]: 'Day'}, inplace=True)

    # Group by day to calculate counts and sums for repeated orders only
    daily_repeats = repeated_data.groupby(repeated_data['order_date'].dt.date).agg(
        Repetitions=('order_date', 'size'),
        Repetitions_Value=('total_value', 'sum')
    ).reset_index()
    daily_repeats.rename(columns={daily_repeats.columns[0]: 'Day'}, inplace=True)

    # Merge the total and repeated aggregates by Day
    daily_summary = pd.merge(daily_total, daily_repeats, on='Day', how='left')
    daily_summary['Repetitions'] = daily_summary['Repetitions'].fillna(0)
    daily_summary['Repetitions_Value'] = daily_summary['Repetitions_Value'].fillna(0)

    # Calculate the sum for non-repeated orders (Total_Value minus Repetitions_Value)
    daily_summary['Non_Repetitions_Value'] = daily_summary['Total_Value'] - daily_summary['Repetitions_Value']

    # Calculate the daily repurchase percentage (based on order counts)
    daily_summary['Repeat Percentage (%)'] = (
        (daily_summary['Repetitions'] / daily_summary['Total_Orders']) * 100
    ).round(2)

    # Format the currency columns as COP (e.g., "COP $1,234,567")
    for col in ['Total_Value', 'Repetitions_Value', 'Non_Repetitions_Value']:
        daily_summary[col] = daily_summary[col].apply(lambda x: f"COP ${x:,.0f}")

    # Convert the 'Day' column to string for display purposes
    daily_summary['Day'] = daily_summary['Day'].astype(str)

    # Insert a new column "Day Name" beside the "Day" column using the weekday name
    # (pd.to_datetime will convert the string back to datetime for correct day name extraction)
    daily_summary.insert(1, 'Day Name', pd.to_datetime(daily_summary['Day']).dt.day_name())

    # Rename columns for a friendlier display in the template
    daily_summary.rename(columns={
        'Total_Orders': 'Total Orders',
        'Total_Value': 'Total Value',
        'Repetitions_Value': 'Repetitions Value',
        'Non_Repetitions_Value': 'Non-Repetitions Value'
    }, inplace=True)

    return daily_summary.to_dict(orient='records')

import os
import pandas as pd
import numpy as np
import logging
from pandas.tseries.offsets import MonthBegin

def get_monthly_repurchases_forecast(csv_path, months_to_forecast=12):
    """
    Reads a CSV file with columns 'Month' and 'Total Sales', then forecasts the next 
    `months_to_forecast` months of repurchase sales using a simple linear regression 
    approach (via NumPy polynomial fitting).

    :param csv_path: File path to the monthly repurchases trend CSV file.
    :param months_to_forecast: Number of months to forecast.
    :return: A list of dictionaries with the future Month and Projected Repurchase Sales.
    """
    logger = logging.getLogger(__name__)
    
    # Ensure the CSV file exists
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    # Read the CSV file into a DataFrame
    df = pd.read_csv(csv_path)
    
    # Verify required columns exist
    required_columns = ['Month', 'Sales']
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"CSV must contain '{col}' column.")
    
    # Parse the 'Month' column into datetime objects using the format '%Y-%m'
    try:
        df['Month_dt'] = pd.to_datetime(df['Month'], format='%Y-%m')
    except Exception as e:
        raise ValueError(f"Error parsing 'Month' column as datetime: {e}")
    
    # Sort chronologically
    df = df.sort_values(by='Month_dt').reset_index(drop=True)
    
    # Ensure 'Total Sales' is numeric.
    # If it's a formatted currency string, remove formatting (e.g., "$29.386.000")
    if df['Sales'].dtype == object:
        def parse_sales(s):
            if isinstance(s, str):
                # Remove currency symbol and formatting characters
                for char in ['$', '.', ',']:
                    s = s.replace(char, '')
                try:
                    return float(s)
                except Exception:
                    return 0.0
            return float(s)
        df['NumericSales'] = df['Total Sales'].apply(parse_sales)
    else:
        df['NumericSales'] = pd.to_numeric(df['Sales'], errors='coerce').fillna(0)
    
    # Prepare data for linear regression:
    # Use the integer index of the DataFrame as the independent variable
    X = np.arange(len(df))
    y = df['NumericSales'].values.astype(float)
    
    # Fit a linear model (first-degree polynomial) using numpy.polyfit
    slope, intercept = np.polyfit(X, y, 1)
    
    # Generate future indexes for forecasting
    last_index = len(df) - 1
    future_indexes = np.arange(last_index + 1, last_index + 1 + months_to_forecast)
    
    # Predict future repurchase sales using the linear model
    y_future = slope * future_indexes + intercept
    
    # Generate a sequence of future months starting after the last month in the dataset
    last_month = df['Month_dt'].iloc[-1]
    future_months = pd.date_range(start=last_month + MonthBegin(1), periods=months_to_forecast, freq='MS')
    
    # Helper function to format numbers as Colombian Pesos (COP) (e.g., "$29.386.000")
    def format_cop(value: int) -> str:
        s = str(value)
        parts = []
        while len(s) > 3:
            parts.insert(0, s[-3:])
            s = s[:-3]
        parts.insert(0, s)
        return '$' + '.'.join(parts)
    
    # Construct the forecast result as a list of dictionaries
    forecasts = []
    for i, future_date in enumerate(future_months):
        forecast_value = max(int(round(y_future[i])), 0)  # Ensure forecast is non-negative
        month_label = future_date.strftime('%Y-%m')
        forecasts.append({
            'Month': month_label,
            'Projected Repurchase Sales': format_cop(forecast_value)
        })
    
    logger.info("Successfully generated monthly repurchases forecast.")
    return forecasts

# Example usage:
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    csv_path = 'data/monthly_repurchases_trend.csv'
    
    try:
        forecast = get_monthly_repurchases_forecast(csv_path, months_to_forecast=12)
        for item in forecast:
            print(f"{item['Month']}: {item['Projected Repurchase Sales']}")
    except Exception as e:
        print(f"An error occurred: {e}")
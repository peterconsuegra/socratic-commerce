import pandas as pd

def get_daily_sales_trend(repeated_customers_file, data_file):
    import pandas as pd
    import os

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
    daily_summary.insert(1, 'Day Name', pd.to_datetime(daily_summary['Day']).dt.day_name())

    # Rename columns for a friendlier display in the template
    daily_summary.rename(columns={
        'Total_Orders': 'Total Orders',
        'Total_Value': 'Total Value',
        'Repetitions_Value': 'Repetitions Value',
        'Non_Repetitions_Value': 'Non-Repetitions Value'
    }, inplace=True)

    # Save the DataFrame as a CSV file at /data/daily_sales_trend.csv
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    csv_path = os.path.join(output_dir, "daily_sales_trend.csv")
    daily_summary.to_csv(csv_path, index=False)

    return daily_summary.to_dict(orient='records')


import pandas as pd
import numpy as np
import os
import logging

def get_future_daily_sales_projections(csv_path: str = "data/daily_sales_trend.csv", days_to_forecast: int = 7):
    """
    Reads the daily_sales_trend CSV file and forecasts the next `days_to_forecast` days
    of sales using a simple linear regression approach via NumPy's polynomial fitting.
    
    The CSV is expected to have at least the following columns:
      - 'Day': A date string (e.g., "2025-01-13")
      - 'Total Value': A string representing sales (e.g., "COP $3,033,300")
    
    :param csv_path: File path to the daily_sales_trend CSV file.
    :param days_to_forecast: Number of future days to forecast.
    :return: A list of dictionaries with forecasted Day and Projected Sales.
    """
    logger = logging.getLogger(__name__)
    
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    # Read the CSV file
    df = pd.read_csv(csv_path)
    
    # Ensure required columns exist
    if 'Day' not in df.columns or 'Total Value' not in df.columns:
        raise ValueError("CSV must contain 'Day' and 'Total Value' columns.")
    
    # Convert 'Day' to datetime
    try:
        df['Day_dt'] = pd.to_datetime(df['Day'])
    except Exception as e:
        raise ValueError(f"Error parsing 'Day' column as datetime: {e}")
    
    # Sort the DataFrame by day
    df = df.sort_values(by='Day_dt').reset_index(drop=True)
    
    # Helper function to convert the 'Total Value' string (e.g., "COP $3,033,300") to an integer
    def parse_daily_value(value_str: str) -> int:
        # Remove "COP", "$", commas, and extra spaces
        cleaned = value_str.replace("COP", "").replace("$", "").replace(",", "").strip()
        try:
            return int(cleaned)
        except:
            return 0
    
    # Create a numeric column from 'Total Value'
    df['NumericValue'] = df['Total Value'].apply(parse_daily_value)
    
    # Prepare the data for linear regression
    X = np.arange(len(df))
    y = df['NumericValue'].values.astype(float)
    
    # Fit a 1st-degree polynomial (linear regression)
    slope, intercept = np.polyfit(X, y, 1)
    
    # Generate future indices for forecasting
    last_index = len(df) - 1
    future_indexes = np.arange(last_index + 1, last_index + 1 + days_to_forecast)
    
    # Predict future sales values
    y_future = slope * future_indexes + intercept
    
    # Generate future dates starting from the day after the last recorded day
    last_day = df['Day_dt'].iloc[-1]
    future_days = pd.date_range(start=last_day + pd.Timedelta(days=1), periods=days_to_forecast, freq='D')
    
    # Helper function to format numbers in Colombian Peso style with commas
    def format_cop_daily(value: int) -> str:
        return f"COP ${value:,}"
    
    # Compile the forecast results
    forecasts = []
    for i, future_date in enumerate(future_days):
        projected_value = max(int(round(y_future[i])), 0)  # Ensure a non-negative forecast
        forecasts.append({
            'Day': future_date.strftime('%Y-%m-%d'),
            'Projected Sales': format_cop_daily(projected_value)
        })
    
    logger.info("Successfully generated future daily sales projections.")
    return forecasts

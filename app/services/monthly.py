import os
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

def get_monthly_sales_forecast(data_file, forecast_periods=6):
    """
    Reads historical monthly sales data, applies ARIMA model,
    and forecasts the next `forecast_periods` months.
    """
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    # Load the dataset
    df = pd.read_csv(data_file)
    
    # Normalize column names to lowercase and strip spaces
    df.columns = df.columns.str.lower().str.strip()

    # Ensure 'total sales' column exists
    if 'total sales' not in df.columns:
        raise KeyError("The expected column 'Total Sales' (or equivalent) is missing from the dataset.")
    
    # Convert 'Month' to datetime format
    df['month'] = pd.to_datetime(df['month'], format='%Y-%m')
    df = df.sort_values(by='month').reset_index(drop=True)

    # Ensure 'total sales' is numeric
    df['total sales'] = pd.to_numeric(df['total sales'], errors='coerce').fillna(0)

    # Fit ARIMA model
    model = ARIMA(df['total sales'], order=(2, 1, 2))  # Adjust order as needed
    model_fit = model.fit()

    # Generate forecast
    future_months = pd.date_range(df['month'].iloc[-1] + pd.DateOffset(months=1), periods=forecast_periods, freq='M')
    forecast_values = model_fit.forecast(steps=forecast_periods)

    # Prepare forecast DataFrame
    forecast_df = pd.DataFrame({
        'Month': future_months.strftime('%Y-%m'),
        'Forecasted Sales': forecast_values.values
    })

    # Save forecasted data to CSV
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    forecast_csv_path = os.path.join(output_dir, "monthly_sales_forecast.csv")
    forecast_df.to_csv(forecast_csv_path, index=False)
    
    return forecast_df.to_dict(orient='records')

def merge_tables_by_percentage(file1, file2, percentage, output_file):
    """
    Reads two CSV files:
      - file1: CSV containing undefined sales with columns: Month, Sales.
      - file2: CSV containing paid sales with columns: Month, Sales.
    
    For each month in file1 (undefined sales), this function calculates:
      Final Sales = Sales from file2 (if available, else 0) + (percentage/100)*Sales from file1.
    
    The result is saved to the specified output_file (inside the "data" directory)
    and returned as a list of dictionaries.
    """
    import os
    import pandas as pd

    # Read the CSV files.
    df_undef = pd.read_csv(file1)  # Undefined sales (columns: Month, Sales)
    df_paid = pd.read_csv(file2)   # Paid sales (columns: Month, Sales)

    # Ensure the Month column is a string in both DataFrames.
    df_undef["Month"] = df_undef["Month"].astype(str)
    df_paid["Month"] = df_paid["Month"].astype(str)

    # Rename Sales columns for clarity.
    df_undef = df_undef.rename(columns={"Sales": "Sales_undef"})
    df_paid = df_paid.rename(columns={"Sales": "Sales_paid"})

    # Merge using a left join based on the undefined sales file so that the
    # resulting DataFrame has the same data range as the undefined file.
    df_merged = pd.merge(df_undef, df_paid, on="Month", how="left")

    # Convert Sales columns to numeric, filling missing values with 0.
    df_merged["Sales_undef"] = pd.to_numeric(df_merged["Sales_undef"], errors="coerce").fillna(0)
    df_merged["Sales_paid"] = pd.to_numeric(df_merged["Sales_paid"], errors="coerce").fillna(0)

    # Calculate final Sales as:
    # Final Sales = Paid Sales + (percentage/100) * Undefined Sales
    df_merged["Sales"] = df_merged["Sales_paid"] + (percentage / 100.0) * df_merged["Sales_undef"]

    # Round and convert to a plain integer string.
    df_merged["Sales"] = df_merged["Sales"].apply(lambda x: str(int(round(x))))

    # Prepare the final DataFrame with only Month and Sales columns.
    df_result = df_merged[["Month", "Sales"]]

    # Save the result to the specified output file within the "data" directory.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_path = os.path.join(output_dir, output_file)
    df_result.to_csv(output_path, index=False)

    return df_result.to_dict(orient="records")



def consolidate_monthly_sales(paid_csv, repurchases_csv, organic_csv, output_file="monthly_sales_trend_consolidated.csv"):
    """
    Consolidates monthly sales data from three CSV files:
      - paid_csv: CSV file containing paid channel sales (plus undefined trend) with columns: Month, Sales.
      - repurchases_csv: CSV file containing repurchases sales with columns: Month, Sales.
      - organic_csv: CSV file containing organic sales with columns: Month, Sales.
    
    The resulting DataFrame will have the following columns:
      Month, repurchases sales, paid channel sales, organic sales, total sales
    
    All sales values are assumed to be plain numeric strings.
    The method saves the consolidated data to:
        data/monthly_sales_trend_consolidated.csv
    and returns the data as a list of dictionaries.
    """
    import os
    import pandas as pd

    # Load the CSV files.
    df_paid = pd.read_csv(paid_csv)         # Expected columns: Month, Sales
    df_rep = pd.read_csv(repurchases_csv)     # Expected columns: Month, Sales
    df_org = pd.read_csv(organic_csv)         # Expected columns: Month, Sales

    # Standardize the Month column in each DataFrame to YYYY-MM.
    for df in [df_paid, df_rep, df_org]:
        df["Month"] = pd.to_datetime(df["Month"], errors="coerce").dt.strftime("%Y-%m")

    # Rename the Sales columns for clarity.
    df_paid = df_paid.rename(columns={"Sales": "paid channel sales"})
    df_rep = df_rep.rename(columns={"Sales": "repurchases sales"})
    df_org = df_org.rename(columns={"Sales": "organic sales"})

    # Merge the DataFrames on "Month" using an outer join so that all months are included.
    df = pd.merge(df_rep, df_paid, on="Month", how="outer")
    df = pd.merge(df, df_org, on="Month", how="outer")

    # Convert the sales columns to numeric and fill missing values with 0.
    for col in ["repurchases sales", "paid channel sales", "organic sales"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Calculate the total sales by summing the three channels.
    df["total sales"] = df["repurchases sales"] + df["paid channel sales"] + df["organic sales"]

    # Reorder the columns.
    df = df[["Month", "repurchases sales", "paid channel sales", "organic sales", "total sales"]]

    # Save the consolidated DataFrame to a CSV file.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    consolidated_csv_path = os.path.join(output_dir,output_file)
    df.to_csv(consolidated_csv_path, index=False)

    return df.to_dict(orient="records")

import pandas as pd
import os
import logging

def get_monthly_sales_repurchases_trend(repeated_customers_file, data_file, output_file="monthly_sales_repurchases_trend.csv"):
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
        for col in ['email']:
            if col not in repeated_customers.columns or col not in data.columns:
                raise ValueError("Both files must contain an 'email' column.")
        for col in ['order_date', 'total_value']:
            if col not in data.columns:
                raise ValueError(f"The data file must contain a '{col}' column.")
        
        # --- PROCESS ORDERS DATA ---
        logger.debug("Converting order_date column to datetime")
        data['order_date'] = pd.to_datetime(data['order_date'])
        data['month_year'] = data['order_date'].dt.to_period('M')
        
        # Aggregate total orders per month
        logger.debug("Aggregating total orders per month")
        total_orders_per_month = (
            data.groupby('month_year')
            .size()
            .reset_index(name='Total Orders')
        )
        # Aggregate total sales per month
        logger.debug("Aggregating total sales per month")
        total_sales_per_month = (
            data.groupby('month_year')['total_value']
            .sum()
            .reset_index(name='Total_Sales_Num')
        )
        
        # --- PROCESS REPEATED CUSTOMERS DATA ---
        logger.debug("Dropping duplicate emails from repeated customers data")
        repeated_customers_unique = repeated_customers.drop_duplicates(subset=['email'])
        logger.debug("Converting 'First Purchase' to datetime for repeated customers")
        repeated_customers_unique['first_purchase_month'] = (
            pd.to_datetime(repeated_customers_unique['First Purchase'], format='%B %Y', errors='coerce')
            .dt.to_period('M')
        )
        
        # Filter orders to include only those emails from repeated customers
        logger.debug("Filtering orders to only include emails present in repeated customers")
        repeated_emails = set(repeated_customers_unique['email'])
        repeated_orders = data[data['email'].isin(repeated_emails)]
        
        # Merge orders with first purchase month information
        logger.debug("Merging repeated orders with the first purchase month")
        merged = repeated_orders.merge(
            repeated_customers_unique[['email', 'first_purchase_month']],
            on='email',
            how='left'
        )
        
        # Exclude orders that happened in the same month as the first purchase
        logger.debug("Excluding orders from the same month as the first purchase")
        repurchase_data = merged[merged['month_year'] != merged['first_purchase_month']]
        
        # Aggregate repurchase orders count per month.
        logger.debug("Aggregating repurchase orders count per month")
        total_repurchases_per_month = (
            repurchase_data.groupby('month_year')
            .size()
            .reset_index(name='Repurchases')
        )
        
        # Aggregate the total value of repurchase orders per month.
        logger.debug("Aggregating repurchase total value per month")
        repurchase_value_per_month = (
            repurchase_data.groupby('month_year')['total_value']
            .sum()
            .reset_index(name='Repurchase_Total_Value')
        )
        
        # --- COMBINE AGGREGATES INTO SUMMARY ---
        logger.debug("Merging all aggregated data into a summary DataFrame")
        summary = total_orders_per_month.merge(
            total_repurchases_per_month, on='month_year', how='left'
        ).merge(
            total_sales_per_month, on='month_year', how='left'
        ).merge(
            repurchase_value_per_month, on='month_year', how='left'
        )
        summary['Repurchases'] = summary['Repurchases'].fillna(0).astype(int)
        summary['Repurchase_Total_Value'] = summary['Repurchase_Total_Value'].fillna(0)
        
        # Calculate repurchase percentage (this will be kept in the view)
        logger.debug("Calculating repurchase sales percentage")
        summary['Repurchase Sales Percentage (%)'] = summary.apply(
            lambda row: (row['Repurchase_Total_Value'] / row['Total_Sales_Num']) * 100
            if row['Total_Sales_Num'] > 0 else 0,
            axis=1
        )
        
        # Format Total Sales and Repurchase Total Value as plain numeric strings
        logger.debug("Formatting Total Sales and Repurchase Total Value as plain numeric strings")
        summary['Total Sales'] = summary['Total_Sales_Num'].apply(lambda x: str(int(x)))
        summary['Repurchase Total Value'] = summary['Repurchase_Total_Value'].apply(lambda x: str(int(x)))
        summary['Repurchase Sales Percentage (%)'] = summary['Repurchase Sales Percentage (%)'].map(
            lambda x: f"{x:.2f}%"
        )
        
        # Format month_year into the standardized Month format: YYYY-MM
        logger.debug("Formatting month_year to a standardized Month string (YYYY-MM)")
        summary['Month'] = summary['month_year'].apply(lambda x: x.strftime('%Y-%m'))
        
        # --- CREATE A CSV OUTPUT WITH DESIRED COLUMN NAMES ---
        # For the CSV file, include only Month and repurchase total value renamed to 'Sales'
        csv_summary = summary[['Month', 'Repurchase Total Value']].rename(
            columns={'Repurchase Total Value': 'Sales'}
        )
        
        # --- SAVE SUMMARY TO CSV ---
        output_dir = os.path.join(os.getcwd(), "data")
        if not os.path.exists(output_dir):
            logger.debug("Output directory does not exist. Creating directory: %s", output_dir)
            os.makedirs(output_dir)
        #csv_path = os.path.join(output_dir, "monthly_sales_repurchases_trend.csv")
        csv_path = os.path.join(output_dir, output_file)
        logger.debug("Saving CSV summary to file: %s", csv_path)
        csv_summary.to_csv(csv_path, index=False)
        logger.info("CSV summary saved successfully at %s", csv_path)
        
        # Return the full summary as a list of dictionaries for rendering in the view
        return summary.to_dict(orient='records')
    
    except Exception as e:
        logger.exception("An error occurred in get_all_months_by_sales: %s", e)
        raise e

import os
import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA

def get_monthly_sales_forecast(data_file, forecast_periods=6):
    """
    Reads historical monthly sales data, applies ARIMA model,
    and forecasts the next `forecast_periods` months.
    """
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    # Load the dataset
    df = pd.read_csv(data_file)
    
    # Normalize column names to lowercase and strip spaces
    df.columns = df.columns.str.lower().str.strip()

    # Print available columns for debugging
    print("Available columns in CSV:", df.columns.tolist())
    
    # Ensure 'total sales' column exists
    if 'total sales' not in df.columns:
        raise KeyError("The expected column 'Total Sales' (or equivalent) is missing from the dataset.")
    
    # Convert 'Month' to datetime format
    df['month'] = pd.to_datetime(df['month'], format='%Y-%m')
    df = df.sort_values(by='month').reset_index(drop=True)

    # Ensure 'total sales' is numeric
    df['total sales'] = pd.to_numeric(df['total sales'], errors='coerce').fillna(0)

    # Fit ARIMA model
    model = ARIMA(df['total sales'], order=(2, 1, 2))  # Adjust order as needed
    model_fit = model.fit()

    # Generate forecast
    future_months = pd.date_range(df['month'].iloc[-1] + pd.DateOffset(months=1), periods=forecast_periods, freq='M')
    forecast_values = model_fit.forecast(steps=forecast_periods)

    # Prepare forecast DataFrame
    forecast_df = pd.DataFrame({
        'Month': future_months.strftime('%Y-%m'),
        'Forecasted Sales': forecast_values.values
    })

    return forecast_df.to_dict(orient='records')


import os
import pandas as pd

def get_orders_by_gender_per_month(file_path):
    # Load the CSV file
    data = pd.read_csv(file_path)

    # Verify that the required columns exist
    required_columns = ['order_date', 'gender', 'total_value']
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The file must contain the following columns: {required_columns}")

    # Convert the 'order_date' column to datetime type with error handling
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')

    # Check for null values after conversion
    if data['order_date'].isnull().any():
        raise ValueError("The 'order_date' column contains invalid values that could not be converted to datetime.")

    # Convert 'total_value' to numeric
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Extract the year and month as a Period object for calculations
    data['Month'] = data['order_date'].dt.to_period('M')

    # Helper function to get the number of orders and total sales per month and gender
    def get_orders(data, gender):
        filtered_data = data[data['gender'] == gender]
        orders_by_month = (
            filtered_data.groupby('Month')
            .agg(
                Number_of_Orders=('order_date', 'size'),
                Total_Sales=('total_value', 'sum')
            )
            .reset_index()
        )
        # Rename columns for better clarity
        orders_by_month.columns = ['Month', 'Number of Orders', 'Total Sales']
        # Convert the 'Month' column to text for the final output
        orders_by_month['Month'] = orders_by_month['Month'].astype(str)
        # Format 'Total Sales' as Colombian currency (COP)
        orders_by_month['Total Sales'] = orders_by_month['Total Sales'].apply(lambda x: f"${x:,.0f}")
        return orders_by_month.to_dict(orient='records')

    # Get the data for both genders
    orders_male = get_orders(data, 'male')
    orders_female = get_orders(data, 'female')

    return {'male': orders_male, 'female': orders_female}


import pandas as pd
import os
import logging

def get_monthly_sales_trend(data_file, output_csv="monthly_sales_trend.csv"):
    """
    Reads orders from `data_file`, groups them by month, and calculates
    total orders and total sales. Saves the results to the specified CSV file
    in the `data/` directory and returns a list of dictionaries for each month's performance.

    The 'Month' column is formatted as YYYY-MM (e.g., 2024-01, 2024-02).
    
    Args:
        data_file (str): Path to the CSV file containing order data.
        output_csv (str): Name of the output CSV file to store the results.
    
    Returns:
        list[dict]: A list of dictionaries containing 'Month', 'Total Orders', and 'Total Sales'.
    """
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

    # --- 4) Group by month-year (Period) ---
    data['month_year'] = data['order_date'].dt.to_period('M')

    # --- 5) Aggregate total orders and total sales per month ---
    logger.debug("Aggregating total orders and total sales per month")
    monthly_agg = (
        data.groupby('month_year')
        .agg(
            **{
                'Total Orders': ('order_date', 'size'),
                'Total Sales': ('total_value', 'sum'),
            }
        )
        .reset_index()
    )

    # --- 6) Format month_year into 'Month' in YYYY-MM format (e.g., "2024-01") ---
    monthly_agg['Month'] = monthly_agg['month_year'].apply(lambda x: x.strftime('%Y-%m'))

    # --- 7) Convert Total Sales to a plain integer string (no $ or dots) ---
    monthly_agg['Total Sales'] = monthly_agg['Total Sales'].apply(lambda x: str(int(x)))

    # --- 8) Reorder columns for final display ---
    monthly_agg = monthly_agg[['Month', 'Total Orders', 'Total Sales']]

    # --- 9) Save the summary to a CSV file ---
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    csv_path = os.path.join(output_dir, output_csv)
    monthly_agg.to_csv(csv_path, index=False)
    logger.info("Monthly sales trend CSV file saved successfully at %s", csv_path)

    # --- 10) Return the summary as a list of dictionaries ---
    return monthly_agg.to_dict(orient='records')

import os
import pandas as pd
import logging

def get_monthly_sales_paid_trend(data_file, output_csv="monthly_sales_paid_trend.csv"):
    """
    Reads orders from `data_file`, filters only paid channel sales,
    groups them by month, and calculates total orders and total sales.

    Saves the results to the specified CSV file in the `data/` directory
    and returns a list of dictionaries.

    Args:
        data_file (str): Path to the CSV file containing order data.
        output_csv (str): Name of the output CSV file to store the results.
    
    Returns:
        list[dict]: A list of dictionaries containing 'Month', 'Total Orders', and 'Total Sales'.
    """
    logger = logging.getLogger(__name__)

    # --- 1) Read the CSV File ---
    logger.debug("Reading orders CSV file: %s", data_file)
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"Data file not found: {data_file}")

    data = pd.read_csv(data_file)
    logger.debug("Orders data shape: %s", data.shape)

    # --- 2) Validate Required Columns ---
    required_cols = ['order_date', 'total_value', 'utm_medium']
    for col in required_cols:
        if col not in data.columns:
            raise ValueError(f"The data file must contain a '{col}' column.")

    # --- 3) Filter Data Where utm_medium == 'paid' ---
    data = data[data['utm_medium'] == 'paid']
    if data.empty:
        logger.warning("No rows found with utm_medium == 'paid'. Returning empty results.")
        return []

    # --- 4) Convert order_date to datetime ---
    logger.debug("Converting order_date column to datetime")
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data.dropna(subset=['order_date'], inplace=True)

    # --- 5) Group by month-year (Period) ---
    data['month_year'] = data['order_date'].dt.to_period('M')

    # --- 6) Aggregate total orders and total sales per month ---
    logger.debug("Aggregating total orders and total sales per month")
    monthly_agg = (
        data.groupby('month_year')
        .agg(
            **{
                'Total Orders': ('order_date', 'size'),
                'Total Sales': ('total_value', 'sum'),
            }
        )
        .reset_index()
    )

    # --- 7) Format month_year into 'Month' in YYYY-MM format (e.g., "2024-01") ---
    monthly_agg['Month'] = monthly_agg['month_year'].apply(lambda x: x.strftime('%Y-%m'))

    # --- 8) Convert Total Sales to a plain integer string ---
    monthly_agg['Total Sales'] = monthly_agg['Total Sales'].apply(lambda x: str(int(x)))

    # --- 9) Reorder columns for final display ---
    monthly_agg = monthly_agg[['Month', 'Total Orders', 'Total Sales']]

    # --- 10) Save the summary to a CSV file ---
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    csv_path = os.path.join(output_dir, output_csv)
    
    # Create a copy and rename the column for CSV export only.
    df_to_save = monthly_agg.copy()
    df_to_save.rename(columns={'Total Sales': 'Sales'}, inplace=True)
    df_to_save.to_csv(csv_path, index=False)

    logger.info("Monthly sales (paid) trend CSV file saved successfully at %s", csv_path)

    # --- 11) Return the data as a list of dictionaries ---
    return monthly_agg.to_dict(orient='records')

import os
import pandas as pd

def get_monthly_sales_undefined_trend(repurchases_path, paid_path, total_sales_path,output_file="monthly_sales_undefined_trend.csv"):
    import os
    import pandas as pd

    # Load CSV files:
    # - df_sales: from monthly_sales_trend.csv (columns: "Month", "Total Orders", "Total Sales")
    # - df_repurchases: from monthly_sales_repurchases_trend.csv (columns: "Month", "Sales")
    # - df_paid: from monthly_sales_paid_trend.csv (columns: "Month", "Total Orders", "Sales")
    df_sales = pd.read_csv(total_sales_path)
    df_repurchases = pd.read_csv(repurchases_path)
    df_paid = pd.read_csv(paid_path)

    # Ensure Month is a string in all DataFrames.
    for df in [df_sales, df_repurchases, df_paid]:
        df["Month"] = df["Month"].astype(str)

    # Merge the data on "Month":
    # Start with the total sales data.
    df_merged = pd.merge(df_sales, df_repurchases, on="Month", how="left", suffixes=("", "_rep"))
    # Merge with the paid sales (only keep the "Sales" column from paid).
    df_merged = pd.merge(df_merged, df_paid[["Month", "Sales"]], on="Month", how="left", suffixes=("", "_paid"))

    # Fill missing values in the repurchases and paid sales columns with 0.
    df_merged["Sales"] = df_merged["Sales"].fillna(0)           # repurchases sales
    df_merged["Sales_paid"] = df_merged["Sales_paid"].fillna(0)     # paid sales

    # Calculate undefined sales as:
    # undefined_sales = Total Sales (from df_sales) - (repurchases sales + paid sales)
    df_merged["Total Sales Undefined"] = df_merged["Total Sales"] - (df_merged["Sales"] + df_merged["Sales_paid"])

    # Replace any remaining NaN values with 0 and convert to an integer string.
    df_merged["Total Sales Undefined"] = df_merged["Total Sales Undefined"].fillna(0).apply(lambda x: str(int(x)))

    # Prepare the final DataFrame with only the desired columns.
    df_final = df_merged[["Month", "Total Sales Undefined"]].copy()
    # Rename "Total Sales Undefined" to "Sales" for the CSV output.
    df_final.rename(columns={"Total Sales Undefined": "Sales"}, inplace=True)

    # Save the result to CSV.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    #csv_path = os.path.join(output_dir, "monthly_sales_undefined_trend.csv")
    csv_path = os.path.join(output_dir, output_file)
    df_final.to_csv(csv_path, index=False)

    return df_final.to_dict(orient="records")


import csv

import pandas as pd

def recalculate_data(input_file, output_file, percentage):
    """
    Reads a CSV file with columns 'Month', 'Total Orders', and 'Sales', recalculates the Sales value
    as (original_sales * (percentage/100)), writes the updated DataFrame to a new CSV file, and returns
    the updated data as a list of dictionaries.

    The input CSV should have a 'Sales' column formatted as a currency string (e.g., "$27.940.550"),
    where a dollar sign precedes the number and periods are used as thousand separators.
    The output CSV will have the Sales column as a plain numeric string (e.g., "27940550") with no 
    currency formatting.
    """
    import pandas as pd

    # Load CSV file into a DataFrame.
    df = pd.read_csv(input_file, encoding='utf-8')

    # Ensure the Sales column is treated as a string.
    df['Sales'] = df['Sales'].astype(str)

    # Convert the Sales column from a formatted string (e.g., "$27.940.550")
    # to a numeric value by removing the '$' and thousand separators.
    df['Sales_numeric'] = (
        df['Sales']
        .str.replace('$', '', regex=False)
        .str.replace('.', '', regex=False)
        .astype(float)
    )

    # Calculate the new Sales as a percentage of the original.
    df['Sales_numeric'] = (df['Sales_numeric'] * (percentage / 100)).round().astype(int)

    # Instead of formatting as currency, convert the numeric value to a plain string.
    df['Sales'] = df['Sales_numeric'].astype(str)

    # Drop the temporary numeric column.
    df.drop(columns=['Sales_numeric'], inplace=True)

    # Remove the 'Total Orders' column if it exists.
    if 'Total Orders' in df.columns:
        df.drop(columns=['Total Orders'], inplace=True)

    # Write the updated DataFrame to the output CSV file.
    df.to_csv(output_file, index=False, encoding='utf-8')

    # Return the updated data as a list of dictionaries.
    return df.to_dict(orient='records')


def get_consolidated_monthly_sales(paid_trend_csv, repurchases_csv, organic_csv):
    """
    Consolidates monthly sales data from three CSV files:
      - paid_trend_csv: CSV file containing paid channel sales (plus undefined trend) with columns: Month, Sales.
      - repurchases_csv: CSV file containing repurchases sales with columns: Month, Sales.
      - organic_csv: CSV file containing organic sales with columns: Month, Sales.
    
    The output CSV (/data/monthly_sales_trend_consolidated.csv) will have the columns:
      Month, repurchases sales, paid channel sales, organic sales, total sales

    All Sales values are assumed to be plain numeric strings (without formatting) so that they can be summed.
    """
    import os
    import pandas as pd

    # Read the CSV files.
    df_paid = pd.read_csv(paid_trend_csv)
    df_repurchases = pd.read_csv(repurchases_csv)
    df_organic = pd.read_csv(organic_csv)

    # Standardize the Month columns for all DataFrames to the format "YYYY-MM".
    for df in [df_paid, df_repurchases, df_organic]:
        # Convert the Month column to datetime then to the standardized string format.
        df['Month'] = pd.to_datetime(df['Month'], errors='coerce').dt.strftime('%Y-%m')

    # Rename the Sales columns to differentiate the sources.
    df_paid = df_paid.rename(columns={"Sales": "paid channel sales"})
    df_repurchases = df_repurchases.rename(columns={"Sales": "repurchases sales"})
    df_organic = df_organic.rename(columns={"Sales": "organic sales"})

    # Merge the three DataFrames on Month using an outer join.
    df = pd.merge(df_repurchases, df_paid, on="Month", how="outer")
    df = pd.merge(df, df_organic, on="Month", how="outer")

    # Convert the sales columns to numeric (if they aren’t already) and fill missing values with 0.
    for col in ["repurchases sales", "paid channel sales", "organic sales"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Calculate the total sales as the sum of the three channels.
    df["total sales"] = (
        df["repurchases sales"] +
        df["paid channel sales"] +
        df["organic sales"]
    )

    # Reorder columns as specified.
    df = df[["Month", "repurchases sales", "paid channel sales", "organic sales", "total sales"]]

    # Save the consolidated DataFrame to CSV.
    output_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    consolidated_csv_path = os.path.join(output_dir, "monthly_sales_trend_consolidated.csv")
    df.to_csv(consolidated_csv_path, index=False)

    return df.to_dict(orient="records")




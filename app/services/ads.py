import os
import pandas as pd

import os
import pandas as pd

def get_daily_conversions(file_path):
    """
    Reads the WooCommerce orders CSV file and processes sales data 
    grouped by UTM campaign and day.
    """
    # Load the CSV file
    df = pd.read_csv(file_path)

    # Convert order_date to datetime format
    df['order_date'] = pd.to_datetime(df['order_date'])

    # Extract the order day
    df['order_day'] = df['order_date'].dt.date

    # Extract the day of the week
    df['Day of Week'] = pd.to_datetime(df['order_day']).dt.day_name()

    # Aggregate total sales value by UTM campaign and day
    sales_value_by_day = df.groupby(['order_day', 'utm_campaign'])['total_value'].sum().reset_index()

    # Pivot the table to have UTM campaigns as columns and order days as rows
    sales_value_pivot = sales_value_by_day.pivot(index='order_day', columns='utm_campaign', values='total_value').fillna(0)

    # Reset index for visualization
    sales_value_pivot.reset_index(inplace=True)

    # Insert the 'Day of Week' column next to 'order_day'
    sales_value_pivot.insert(1, "Day of Week", pd.to_datetime(sales_value_pivot["order_day"]).dt.day_name())

    # Add a column for the total sales value per day
    sales_value_pivot["Total Sales Value"] = sales_value_pivot.iloc[:, 2:].sum(axis=1)

    # Create a copy for saving to CSV and remove the "Day of Week" column from that copy
    sales_value_csv = sales_value_pivot.copy()
    if "Day of Week" in sales_value_csv.columns:
        sales_value_csv.drop(columns=["Day of Week"], inplace=True)

    # Define the data directory and output CSV path
    data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    csv_path = os.path.join(data_dir, "daily_ads_trend.csv")

    # Save the CSV without the "Day of Week" column
    sales_value_csv.to_csv(csv_path, index=False)

    # Return the original DataFrame as a dictionary (with the Day of Week column intact)
    return sales_value_pivot.to_dict(orient="records")

import os
import pandas as pd
import re

import os
import pandas as pd
import re

def remove_non_alphanumeric(s: str) -> str:
    """
    Removes all non-alphanumeric characters from a string.
    """
    return re.sub(r'[^a-zA-Z0-9]', '', s)

def get_daily_conversions_by_campaign(file_path):
    """
    Returns a list of DataFrames, one per unique (cleaned) UTM campaign,
    each with columns [order_day, <utm_campaign>].
    
    The function cleans the utm_campaign values by removing non-alphanumeric characters,
    groups and pivots the data by day, and converts the order_day values to strings (YYYY-MM-DD)
    before splitting the DataFrame per campaign.
    """
    # Load the CSV file
    df = pd.read_csv(file_path)

    # Convert order_date to datetime and extract order_day as a date
    df['order_date'] = pd.to_datetime(df['order_date'])
    df['order_day'] = df['order_date'].dt.date

    # Clean utm_campaign by filling missing values, converting to string, and removing non-alphanumerics
    df['utm_campaign'] = df['utm_campaign'].fillna('NoCampaign').astype(str).apply(remove_non_alphanumeric)

    # Ensure total_value is numeric
    df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0)

    # Group by order_day and cleaned utm_campaign, summing total_value
    grouped = df.groupby(['order_day', 'utm_campaign'])['total_value'].sum().reset_index()

    # Pivot the data so that each utm_campaign becomes a column
    pivot_df = grouped.pivot(index='order_day', columns='utm_campaign', values='total_value').fillna(0)

    # Reindex the DataFrame to include every day between the min and max dates
    pivot_df.index = pd.to_datetime(pivot_df.index)
    min_day = pivot_df.index.min()
    max_day = pivot_df.index.max()
    full_date_range = pd.date_range(start=min_day, end=max_day, freq='D')
    pivot_df = pivot_df.reindex(full_date_range, fill_value=0)
    pivot_df.index.name = 'order_day'
    pivot_df.reset_index(inplace=True)
    
    # Convert the order_day column to a string formatted as YYYY-MM-DD
    pivot_df['order_day'] = pivot_df['order_day'].apply(
        lambda d: d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    )

    # Create a list of DataFrames (each DataFrame will have columns: order_day and one campaign column)
    dfs = []
    for campaign in pivot_df.columns:
        if campaign == 'order_day':
            continue
        df_campaign = pivot_df[['order_day', campaign]].copy()
        dfs.append(df_campaign)

    return dfs

import os
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
import warnings
import logging

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")  # Suppress ARIMA warnings

def forecast_campaigns(campaign_dfs, forecast_steps=10):
    """
    Takes a list of campaign DataFrames and forecasts future values using an ARIMA(1,1,1) model.
    
    Args:
        campaign_dfs (list): List of DataFrames. Each DataFrame should have an 'order_day' column
                             and one sales column.
        forecast_steps (int): Number of steps (days) to forecast.
    
    Returns:
        tuple: A dictionary grouping forecast data by campaign name and the raw forecast DataFrame.
               The dictionary format is:
               {
                   'CampaignName': {
                       'dates': [...],
                       'forecast_values': [...],
                       'lower_ci': [...],
                       'upper_ci': [...]
                   },
                   ...
               }
    """
    forecast_results = []  # List to hold forecast data for each campaign

    for df in campaign_dfs:
        if "order_day" not in df.columns:
            continue
        # Identify the sales column (assumes one column besides 'order_day')
        campaign_cols = [col for col in df.columns if col != "order_day"]
        if not campaign_cols:
            continue
        campaign_name = campaign_cols[0]

        # Prepare the time series: convert 'order_day' to datetime, sort, and set as index.
        df["order_day"] = pd.to_datetime(df["order_day"])
        df = df.sort_values("order_day")
        df.set_index("order_day", inplace=True)
        ts = df[campaign_name]

        try:
            # Fit an ARIMA(1,1,1) model and forecast 'forecast_steps' ahead.
            model = ARIMA(ts, order=(1, 1, 1))
            model_fit = model.fit()
            forecast_obj = model_fit.get_forecast(steps=forecast_steps)
            forecast_mean = forecast_obj.predicted_mean
            conf_int = forecast_obj.conf_int()

            for f_date, value, lower, upper in zip(
                forecast_mean.index, forecast_mean, conf_int.iloc[:, 0], conf_int.iloc[:, 1]
            ):
                forecast_results.append({
                    "campaign": campaign_name,
                    "forecast_date": f_date.strftime("%Y-%m-%d"),
                    "forecast_value": value,
                    "lower_ci": lower,
                    "upper_ci": upper
                })
        except Exception as ex:
            logger.error(f"Forecasting failed for campaign {campaign_name}: {ex}")
            continue

    # Group forecast data by campaign name for easier access in the template.
    forecast_data_by_campaign = {}
    forecast_df = pd.DataFrame(forecast_results)
    for campaign in forecast_df["campaign"].unique():
        campaign_forecast = forecast_df[forecast_df["campaign"] == campaign]
        forecast_data_by_campaign[campaign] = {
            "dates": campaign_forecast["forecast_date"].tolist(),
            "forecast_values": campaign_forecast["forecast_value"].tolist(),
            "lower_ci": campaign_forecast["lower_ci"].tolist(),
            "upper_ci": campaign_forecast["upper_ci"].tolist()
        }
    return forecast_data_by_campaign, forecast_df

# app/services/ads.py
import pandas as pd

import os
import pandas as pd

def get_wp_campaign_trend(file_path):
    """
    Reads the CSV file from the given file path, filters orders with utm_answer equal to "WPCampaign"
    (case-insensitive), groups the orders by day, saves the resulting DataFrame to /data/wp_campaign_trend.csv,
    and returns a list of dictionaries with the day and total sales.

    Parameters:
        file_path (str): Path to the CSV file containing ads data.

    Returns:
        List[dict]: A list of dictionaries with keys 'date' and 'total_sales'.
    """
    try:
        # Read the CSV file
        df = pd.read_csv(file_path)
        
        # Convert order_date to datetime
        df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
        
        # Ensure utm_answer is a string and filter for "WPCampaign" (case-insensitive)
        df['utm_answer'] = df['utm_answer'].astype(str)
        wp_df = df[df['utm_answer'].str.lower() == "wpcampaign"]
        
        # Extract the date portion
        wp_df['date'] = wp_df['order_date'].dt.date
        
        # Ensure total_value is numeric and then group by date to sum total sales
        wp_df['total_value'] = pd.to_numeric(wp_df['total_value'], errors='coerce').fillna(0)
        trend_df = wp_df.groupby('date').agg(total_sales=('total_value', 'sum')).reset_index()
        
        # Sort the DataFrame by date
        trend_df = trend_df.sort_values('date')
        
        # Build the path to save the CSV file
        csv_path = os.path.join(os.getcwd(), "data", "wp_campaign_trend.csv")
        
        # Save the trend DataFrame to a CSV file without the index
        trend_df.to_csv(csv_path, index=False)
        
        # Return the trend data as a list of dictionaries
        return trend_df.to_dict(orient='records')
    
    except Exception as e:
        raise Exception(f"Failed to process WPCampaign trend data: {e}")

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression

def forecast_ads(csv_file=None):
    """
    Generate a 7-day forecast for ad sales using historical data.
    If csv_file is provided, it should be a CSV file (or file-like object) with:
      - date (in a parseable format)
      - total_sales
    Otherwise, default example data is used.
    The method uses a log-transformation to ensure predictions are non-negative.
    """
    if csv_file:
        # Read the CSV and parse the date column
        df = pd.read_csv(csv_file, parse_dates=['date'])
    else:
        # Default example data
        data = {
            'date': [
                '2025-02-27', '2025-02-28', '2025-03-01', '2025-03-02',
                '2025-03-03', '2025-03-04', '2025-03-05', '2025-03-06', '2025-03-07'
            ],
            'total_sales': [132600.0, 308500.0, 896200.0, 200300.0, 268300.0, 71400.0, 76200.0, 79900.0, 35000.0]
        }
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])

    # Ensure the data is sorted by date
    df.sort_values('date', inplace=True)

    # Create a day-of-week column and generate dummy variables (using Monday as the base)
    df['day_of_week'] = df['date'].dt.day_name()
    dummies = pd.get_dummies(df['day_of_week'], prefix='dow', drop_first=True)
    df = pd.concat([df, dummies], axis=1)

    # Create a time trend variable (day number starting at 0)
    df['day_num'] = (df['date'] - df['date'].min()).dt.days

    # Define features: time trend + day-of-week dummies
    feature_cols = ['day_num'] + list(dummies.columns)
    X = df[feature_cols]
    
    # Use a log transformation on the target variable
    # (we add a small constant if needed, but here total_sales > 0 so it's fine)
    y = np.log(df['total_sales'])

    # Fit the regression model in log-space
    model = LinearRegression().fit(X, y)

    # Generate forecast dates for the next 7 days
    forecast_dates = pd.date_range(start=df['date'].max() + pd.Timedelta(days=1), periods=7)
    forecast_df = pd.DataFrame({'date': forecast_dates})
    forecast_df['day_of_week'] = forecast_df['date'].dt.day_name()
    forecast_df['day_num'] = (forecast_df['date'] - df['date'].min()).dt.days

    # Create dummy variables for the forecast dates, aligning with training dummies
    forecast_dummies = pd.get_dummies(forecast_df['day_of_week'], prefix='dow')
    forecast_dummies = forecast_dummies.reindex(columns=dummies.columns, fill_value=0)
    forecast_X = pd.concat([forecast_df[['day_num']], forecast_dummies], axis=1)

    # Predict in log-space, then transform back using the exponential
    forecast_df['predicted_log'] = model.predict(forecast_X)
    forecast_df['predicted_sales'] = np.exp(forecast_df['predicted_log'])

    # Return the forecast as a list of dictionaries
    return forecast_df[['date', 'day_of_week', 'predicted_sales']].to_dict(orient='records')

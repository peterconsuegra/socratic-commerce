import pandas as pd

def get_top_ten_sundays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime (using errors='coerce' to safely handle invalid dates)
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data = data.dropna(subset=['order_date'])

    # Extract the date part and create a Weekday column
    data['Date'] = data['order_date'].dt.date
    data['Weekday'] = data['order_date'].dt.day_name()

    # Filter for Sunday rows (Sunday = 6)
    sunday_data = data[data['order_date'].dt.dayofweek == 6]

    if sunday_data.empty:
        return []  # Return an empty list if there are no Sunday orders

    # Group by Date and sum the total sales for Sundays
    sunday_sales = sunday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Re-add the Weekday column (will be 'Sunday' for all rows)
    sunday_sales['Weekday'] = pd.to_datetime(sunday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Sunday dates
    top_sundays = sunday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the sales value as currency (e.g., "$12.345")
    top_sundays['Sales'] = top_sundays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_sundays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')

def get_top_ten_saturdays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime (safely) and drop rows where conversion fails
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data = data.dropna(subset=['order_date'])

    # Extract the date part and add a Weekday column
    data['Date'] = data['order_date'].dt.date
    data['Weekday'] = data['order_date'].dt.day_name()

    # Use the dayofweek attribute to filter for Saturday (Saturday = 5)
    saturday_data = data[data['order_date'].dt.dayofweek == 5]

    if saturday_data.empty:
        return []  # Optionally log that no Saturday orders were found

    # Group by Date and sum the total sales for Saturdays
    saturday_sales = saturday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Re-add the Weekday column (should be "Saturday" for all rows)
    saturday_sales['Weekday'] = pd.to_datetime(saturday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Saturday dates
    top_saturdays = saturday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the total sales as currency (e.g., "$12.345")
    top_saturdays['Sales'] = top_saturdays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_saturdays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')

def get_top_ten_fridays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime (using errors='coerce' to handle any bad formats)
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data = data.dropna(subset=['order_date'])  # Drop rows where date conversion failed

    # Extract the date part and the weekday name
    data['Date'] = data['order_date'].dt.date
    data['Weekday'] = data['order_date'].dt.day_name()

    # Use the dayofweek attribute to filter for Friday (Friday = 4)
    friday_data = data[data['order_date'].dt.dayofweek == 4]

    if friday_data.empty:
        # Optionally, log or print a message here to indicate no Friday orders were found.
        return []

    # Group by Date and sum the total sales for Fridays
    friday_sales = friday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Re-add the Weekday column (should be 'Friday' for all rows)
    friday_sales['Weekday'] = pd.to_datetime(friday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Friday dates
    top_fridays = friday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the total sales as currency (e.g., "$12.345")
    top_fridays['Sales'] = top_fridays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_fridays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')



def get_top_ten_thursdays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime and extract the date part
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['Date'] = data['order_date'].dt.date

    # Create a Weekday column (e.g., Monday, Tuesday, etc.)
    data['Weekday'] = data['order_date'].dt.day_name()

    # Filter for Thursday rows
    thursday_data = data[data['Weekday'] == 'Thursday']

    # Group by Date and sum the total sales
    thursday_sales = thursday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Add the Weekday column back (it will be "Thursday" for all rows)
    thursday_sales['Weekday'] = pd.to_datetime(thursday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Thursday dates
    top_thursdays = thursday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the total sales as currency (e.g., "$12.345")
    top_thursdays['Sales'] = top_thursdays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_thursdays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')


def get_top_ten_wednesdays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime and extract the date part
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['Date'] = data['order_date'].dt.date

    # Create a Weekday column (e.g., Monday, Tuesday, etc.)
    data['Weekday'] = data['order_date'].dt.day_name()

    # Filter for Wednesday rows
    wednesday_data = data[data['Weekday'] == 'Wednesday']

    # Group by Date and sum the total sales
    wednesday_sales = wednesday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Add the Weekday column back (it will be "Wednesday" for all rows)
    wednesday_sales['Weekday'] = pd.to_datetime(wednesday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Wednesday dates
    top_wednesdays = wednesday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the total sales as currency (e.g., "$12.345")
    top_wednesdays['Sales'] = top_wednesdays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_wednesdays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')


def get_top_ten_tuesdays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime and extract the date part
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['Date'] = data['order_date'].dt.date

    # Create a Weekday column (e.g., Monday, Tuesday, etc.)
    data['Weekday'] = data['order_date'].dt.day_name()

    # Filter for Tuesday rows
    tuesday_data = data[data['Weekday'] == 'Tuesday']

    # Group by Date and sum the total sales
    tuesday_sales = tuesday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Add a Weekday column back (it will be "Tuesday" for all rows)
    tuesday_sales['Weekday'] = pd.to_datetime(tuesday_sales['Date']).dt.day_name()

    # Sort by sales descending and select the top 10 Tuesday dates
    top_tuesdays = tuesday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the total sales as currency (e.g., "$12.345")
    top_tuesdays['Sales'] = top_tuesdays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Prepare the final result with Date, Weekday, and Sales columns
    result = top_tuesdays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')

def get_top_ten_mondays_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Ensure the file has the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime and extract the date part
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['Date'] = data['order_date'].dt.date

    # Add a Weekday column
    data['Weekday'] = data['order_date'].dt.day_name()

    # Filter for Monday rows
    monday_data = data[data['Weekday'] == 'Monday']

    # Group by Date and sum the sales for each Monday
    monday_sales = monday_data.groupby('Date', as_index=False)['total_value'].sum()

    # Add the weekday column (should be "Monday" for every row)
    monday_sales['Weekday'] = pd.to_datetime(monday_sales['Date']).dt.day_name()

    # Sort the days by sales descending and take the top 10
    top_mondays = monday_sales.sort_values(by='total_value', ascending=False).head(10)

    # Format the sales value as currency (e.g., "$12.345")
    top_mondays['Sales'] = top_mondays['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Select and rename columns for clarity
    result = top_mondays[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert dates to strings for display

    return result.to_dict(orient='records')


def get_top_twenty_days_by_sales(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Ensure the file has the required columns
    if 'order_date' not in data.columns or 'total_value' not in data.columns:
        raise ValueError("The CSV file must contain 'order_date' and 'total_value' columns.")

    # Convert order_date to datetime and extract the date part
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['Date'] = data['order_date'].dt.date

    # Group by date and sum the total sales
    daily_sales = data.groupby('Date', as_index=False)['total_value'].sum()

    # Add a new column with the weekday name (e.g., Monday, Tuesday, etc.)
    daily_sales['Weekday'] = pd.to_datetime(daily_sales['Date']).dt.day_name()

    # Sort days by sales descending and take the top 10
    top_days = daily_sales.sort_values(by='total_value', ascending=False).head(20)

    # Format the sales value as currency (e.g., "$12.345")
    top_days['Sales'] = top_days['total_value'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Select and rename columns for clarity
    result = top_days[['Date', 'Weekday', 'Sales']]
    result['Date'] = result['Date'].astype(str)  # Convert date objects to strings for display

    return result.to_dict(orient='records')


def get_top_cities_by_gender(file_path):
    # Cargar el archivo CSV
    data = pd.read_csv(file_path)

    # Verificar que las columnas requeridas existan
    required_columns = ['city', 'gender', 'total_value']
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"El archivo debe contener las columnas: {required_columns}")

    # Función auxiliar para obtener el top 10 de ciudades por género
    def get_top_cities(data, gender):
        filtered_data = data[data['gender'] == gender]
        top_cities = (
            filtered_data
            .groupby('city')
            .agg(Cantidad_de_Ventas=('city', 'size'),
                 Total_Ventas=('total_value', 'sum'))
            .reset_index()
            .sort_values(by='Cantidad_de_Ventas', ascending=False)
            .head(10)  # Mostrar solo las 10 ciudades principales
        )
        # Formatear Total Ventas como COP
        top_cities['Total_Ventas'] = top_cities['Total_Ventas'].apply(lambda x: f"${int(x):,}".replace(',', '.'))
        top_cities.columns = ['Ciudad', 'Cantidad de Ventas', 'Total Ventas']
        return top_cities.to_dict(orient='records')

    # Obtener los datos para ambos géneros
    top_cities_male = get_top_cities(data, 'male')
    top_cities_female = get_top_cities(data, 'female')

    return {'male': top_cities_male, 'female': top_cities_female}

def get_top_hours_by_gender(file_path):
    # Load the CSV file
    data = pd.read_csv(file_path)

    # Verify that the required columns exist
    required_columns = ['order_date', 'gender', 'total_value']
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The file must contain the following columns: {required_columns}")

    # Convert the 'order_date' column to datetime type
    data['order_date'] = pd.to_datetime(data['order_date'])

    # Extract the hour from each order
    data['Order Hour'] = data['order_date'].dt.hour

    # Create a DataFrame with all hours of the day (0 to 23)
    all_hours = pd.DataFrame({'Order Hour': range(24)})

    # Helper function to format the hour in AM - PM format
    def format_hour(hour):
        if hour == 0:
            return "12 AM"
        elif hour < 12:
            return f"{hour} AM"
        elif hour == 12:
            return "12 PM"
        else:
            return f"{hour - 12} PM"

    # Helper function to get all 24 hours by gender, organized by Total Sales
    def get_top_hours(data, gender):
        filtered_data = data[data['gender'] == gender]
        top_hours = (
            filtered_data
            .groupby('Order Hour')
            .agg(Number_of_Sales=('Order Hour', 'size'),
                 Total_Sales=('total_value', 'sum'))
            .reset_index()
        )
        # Merge with all hours of the day to ensure all 24 hours are included
        top_hours = all_hours.merge(top_hours, on='Order Hour', how='left').fillna(0)
        top_hours['Number_of_Sales'] = top_hours['Number_of_Sales'].astype(int)
        # Format Total Sales as currency
        top_hours['Total_Sales'] = top_hours['Total_Sales'].apply(lambda x: f"${int(x):,}".replace(',', '.'))
        # Format the hour in AM - PM
        top_hours['Order Hour'] = top_hours['Order Hour'].apply(format_hour)
        top_hours.columns = ['Hour', 'Number of Sales', 'Total Sales']
        # Sort by Total Sales (convert to number for comparison)
        top_hours['Total Sales (num)'] = top_hours['Total Sales'].str.replace('[^0-9]', '', regex=True).astype(int)
        top_hours = top_hours.sort_values(by='Total Sales (num)', ascending=False).drop(columns=['Total Sales (num)'])
        return top_hours.to_dict(orient='records')

    # Get the data for both genders
    top_hours_male = get_top_hours(data, 'male')
    top_hours_female = get_top_hours(data, 'female')

    return {'male': top_hours_male, 'female': top_hours_female}


# Service to calculate the top 3 days by gender
def get_top_days_of_the_week(file_path):
    # Load the CSV file
    data = pd.read_csv(file_path)

    # Convert the 'order_date' column to datetime type
    data['order_date'] = pd.to_datetime(data['order_date'])

    # Extract the day of the week (0: Monday, 6: Sunday)
    data['Day of the Week'] = data['order_date'].dt.dayofweek

    # Group and calculate sales for males
    male_sales = (data[data['gender'] == 'male']
                  .groupby('Day of the Week')
                  .agg(Orders=('order_id', 'count'),
                       Total_Sales=('total_value', 'sum'))
                  .sort_values(by='Orders', ascending=False))

    # Group and calculate sales for females
    female_sales = (data[data['gender'] == 'female']
                    .groupby('Day of the Week')
                    .agg(Orders=('order_id', 'count'),
                         Total_Sales=('total_value', 'sum'))
                    .sort_values(by='Orders', ascending=False))

    # Format Total_Sales as a string with separators
    male_sales['Total_Sales'] = male_sales['Total_Sales'].apply(lambda x: f"{x:,.2f}")
    female_sales['Total_Sales'] = female_sales['Total_Sales'].apply(lambda x: f"{x:,.2f}")

    return {
        'male': male_sales.reset_index().to_dict(orient='records'),
        'female': female_sales.reset_index().to_dict(orient='records')
    }


def get_top_days_of_month_by_gender(file_path):
    # Load the CSV file
    data = pd.read_csv(file_path)

    # Verify that the required columns exist
    required_columns = ['order_date', 'gender', 'total_value']
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The file must contain the following columns: {required_columns}")

    # Convert the 'order_date' column to datetime
    data['order_date'] = pd.to_datetime(data['order_date'])

    # Extract the day of the month from each order
    data['Day of the Month'] = data['order_date'].dt.day

    # Convert the 'total_value' column to numeric
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Create a DataFrame with all 30 days of the month
    all_days = pd.DataFrame({'Day of the Month': range(1, 31)})

    # Helper function to calculate data by gender
    def get_days_data(data, gender):
        filtered_data = data[data['gender'] == gender]

        # Group by day of the month and calculate the number of orders and total sales
        aggregated_data = (
            filtered_data.groupby('Day of the Month')
            .agg(
                Number_of_Orders=('order_date', 'size'),
                Total_Sales=('total_value', 'sum')
            )
            .reset_index()
        )

        # Combine with the DataFrame of all days of the month
        complete_data = all_days.merge(aggregated_data, on='Day of the Month', how='left')
        complete_data['Number_of_Orders'] = complete_data['Number_of_Orders'].fillna(0).astype(int)
        complete_data['Total_Sales'] = complete_data['Total_Sales'].fillna(0)

        # Format Total Sales as currency (COP) without decimals
        complete_data['Total_Sales'] = complete_data['Total_Sales'].apply(lambda x: f"${x:,.0f}")

        # Sort by Total Sales in descending order
        complete_data = complete_data.sort_values(by='Total_Sales', ascending=False)

        return complete_data.to_dict(orient='records')

    # Get the data for both genders
    days_data_male = get_days_data(data, 'male')
    days_data_female = get_days_data(data, 'female')

    return {'male': days_data_male, 'female': days_data_female}

def get_top_months_by_sales(repeated_customers_file, data_file):
    """
    Reads both the orders data and the repeated customers file, then aggregates monthly sales data.
    For each month, the returned information includes:
      - Month: Formatted month-year (e.g., "December 2024")
      - Total Orders: Total orders in that month
      - Repurchases: Number of orders from customers identified as repeated
      - Repurchase Percentage (%): The percentage of orders coming from repeated customers
      - Total Sales: Total sales in that month, formatted as currency
    Only the top 10 months with the highest total sales are returned.
    """
    import pandas as pd

    # --- Read the CSV files ---
    repeated_customers = pd.read_csv(repeated_customers_file)
    data = pd.read_csv(data_file)

    # --- Validate required columns ---
    if 'email' not in repeated_customers.columns or 'email' not in data.columns:
        raise ValueError("Both files must contain an 'email' column.")
    if 'order_date' not in data.columns:
        raise ValueError("The data file must contain an 'order_date' column.")
    if 'total_value' not in data.columns:
        raise ValueError("The data file must contain a 'total_value' column.")

    # --- Convert dates and extract the monthly period ---
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['month_year'] = data['order_date'].dt.to_period('M')

    # --- Filter data to include only repeated customers ---
    repeated_emails = set(repeated_customers['email'])
    filtered_data = data[data['email'].isin(repeated_emails)]

    # --- Aggregate the information ---
    # Repurchases: count orders from repeated customers per month
    total_repurchases_per_month = filtered_data.groupby('month_year').size().reset_index(name='Repurchases')

    # Total Orders: count all orders per month
    total_orders_per_month = data.groupby('month_year').size().reset_index(name='Total Orders')

    # Total Sales: sum the 'total_value' column per month
    total_sales_per_month = data.groupby('month_year')['total_value'].sum().reset_index(name='Total_Sales_Num')

    # --- Combine the aggregated information ---
    summary = total_repurchases_per_month.merge(total_orders_per_month, on='month_year', how='left')
    summary = summary.merge(total_sales_per_month, on='month_year', how='left')

    # Calculate the repurchase percentage
    summary['Repurchase Percentage (%)'] = (summary['Repurchases'] / summary['Total Orders']) * 100

    # Format the total sales as currency (e.g., "$1.234")
    summary['Total Sales'] = summary['Total_Sales_Num'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Convert the period to a readable string (e.g., "December 2024")
    summary['Month'] = summary['month_year'].apply(lambda x: x.strftime('%B %Y'))

    # Sort the months by total sales (from highest to lowest)
    summary = summary.sort_values(by='Total_Sales_Num', ascending=False)

    # Limit the results to the top 10 months
    summary = summary.head(10)

    # Select and reorder the desired columns
    summary = summary[['Month', 'Total Orders', 'Repurchases', 'Repurchase Percentage (%)', 'Total Sales']]

    # Format the repurchase percentage to two decimals followed by '%'
    summary['Repurchase Percentage (%)'] = summary['Repurchase Percentage (%)'].map(lambda x: f"{x:.2f}%")

    # Convert to a list of dictionaries for easy passing to an HTML template
    return summary.to_dict(orient='records')

def get_top_10_months_by_sales(repeated_customers_file, data_file):
    """
    Reads both the orders data and the refined repeated customers file, then aggregates monthly sales data.
    For each month, the returned information includes:
      - Month: Formatted month-year (e.g., "December 2023")
      - Total Orders: Total orders in that month
      - Repurchases: Number of orders from repeated customers (excluding orders made in the same month as the customer's first purchase)
      - Repurchase Total Value: Sum of total_value from repurchase orders (formatted as currency)
      - Total Sales: Total sales in that month, formatted as currency
      - Repurchase Sales Percentage (%): The percentage of total sales that comes from repurchases
      
    Only the top 10 months (based on Total Sales) are returned.
    
    Note: Only orders for emails that appear in the repeated customers file will be processed.
    """
    # --- Read CSV files ---
    repeated_customers = pd.read_csv(repeated_customers_file)
    data = pd.read_csv(data_file)

    # --- Validate required columns ---
    for col in ['email']:
        if col not in repeated_customers.columns or col not in data.columns:
            raise ValueError("Both files must contain an 'email' column.")
    for col in ['order_date', 'total_value']:
        if col not in data.columns:
            raise ValueError(f"The data file must contain a '{col}' column.")

    # --- Process orders data ---
    data['order_date'] = pd.to_datetime(data['order_date'])
    data['month_year'] = data['order_date'].dt.to_period('M')
    
    # Aggregate total orders and total sales per month (numeric value) from all orders
    total_orders_per_month = (
        data.groupby('month_year')
            .size()
            .reset_index(name='Total Orders')
    )
    total_sales_per_month = (
        data.groupby('month_year')['total_value']
            .sum()
            .reset_index(name='Total_Sales_Num')
    )

    # --- Process repeated customers using the refined data ---
    # Drop duplicate emails so that each customer appears only once.
    repeated_customers_unique = repeated_customers.drop_duplicates(subset=['email'])
    # Convert the "First Purchase" column (e.g., "December 2023") to a datetime and then to a monthly period.
    repeated_customers_unique['first_purchase_month'] = (
        pd.to_datetime(repeated_customers_unique['First Purchase'], format='%B %Y', errors='coerce')
          .dt.to_period('M')
    )

    # --- Filter orders to include only those for emails in the refined repeated customers file ---
    repeated_emails = set(repeated_customers_unique['email'])
    repeated_orders = data[data['email'].isin(repeated_emails)]

    # Merge repeated orders with the corresponding first purchase month.
    merged = repeated_orders.merge(
        repeated_customers_unique[['email', 'first_purchase_month']],
        on='email',
        how='left'
    )

    # Exclude orders that occurred in the same month as the first purchase.
    repurchase_data = merged[merged['month_year'] != merged['first_purchase_month']]

    # Aggregate repurchase orders count per month.
    total_repurchases_per_month = (
        repurchase_data.groupby('month_year')
                      .size()
                      .reset_index(name='Repurchases')
    )
    
    # Aggregate the total value of repurchase orders per month.
    repurchase_value_per_month = (
        repurchase_data.groupby('month_year')['total_value']
                       .sum()
                       .reset_index(name='Repurchase_Total_Value')
    )

    # --- Combine all monthly aggregates ---
    summary = total_orders_per_month.merge(
        total_repurchases_per_month, on='month_year', how='left'
    ).merge(
        total_sales_per_month, on='month_year', how='left'
    ).merge(
        repurchase_value_per_month, on='month_year', how='left'
    )
    summary['Repurchases'] = summary['Repurchases'].fillna(0).astype(int)
    summary['Repurchase_Total_Value'] = summary['Repurchase_Total_Value'].fillna(0)

    # Calculate the percentage of total sales coming from repurchases.
    summary['Repurchase Sales Percentage (%)'] = summary.apply(
        lambda row: (row['Repurchase_Total_Value'] / row['Total_Sales_Num']) * 100 
                    if row['Total_Sales_Num'] > 0 else 0,
        axis=1
    )

    # --- Sort and restrict to the top 10 months by Total Sales ---
    summary = summary.sort_values(by='Total_Sales_Num', ascending=False).head(10)

    # --- Format numeric columns ---
    summary['Total Sales'] = summary['Total_Sales_Num'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )
    summary['Repurchase Total Value'] = summary['Repurchase_Total_Value'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )
    summary['Repurchase Sales Percentage (%)'] = summary['Repurchase Sales Percentage (%)'].map(
        lambda x: f"{x:.2f}%"
    )

    # Convert the period to a readable string (e.g., "December 2023")
    summary['Month'] = summary['month_year'].apply(lambda x: x.strftime('%B %Y'))

    # Reorder the columns as desired.
    summary = summary[['Month', 'Total Orders', 'Repurchases', 'Repurchase Total Value', 'Total Sales', 'Repurchase Sales Percentage (%)']]

    return summary.to_dict(orient='records')

def get_top_ten_utm_campaigns(file_path):
    import pandas as pd
    import re

    def remove_non_alphanumeric(s: str) -> str:
        """
        Removes all non-alphanumeric characters from a string.
        """
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate the presence of the 'utm_campaign' column
    if 'utm_campaign' not in data.columns:
        raise ValueError("The CSV file must contain the 'utm_campaign' column.")

    # Clean the utm_campaign values by removing non-alphanumeric characters
    data['utm_campaign'] = data['utm_campaign'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and handle conversion issues
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by the cleaned utm_campaign to calculate order counts and total sales
    campaign_summary = data.groupby('utm_campaign').agg(
        Order_Count=('utm_campaign', 'size'),
        Total_Sales=('total_value', 'sum')
    ).reset_index()

    # Format the total sales as a currency string
    campaign_summary['Sales'] = campaign_summary['Total_Sales'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )

    # Sort campaigns by total sales in descending order and get the top 10
    top_campaigns = campaign_summary.sort_values(by='Total_Sales', ascending=False).head(50)

    return top_campaigns.to_dict(orient='records')

# app/services/utm_ranking.py
import pandas as pd

def get_utm_answer_ranking(file_path):
    # Load the CSV data
    data = pd.read_csv(file_path)
    
    # Ensure the required columns exist
    required_columns = ['utm_answer', 'order_id', 'total_value']
    if not all(col in data.columns for col in required_columns):
        raise ValueError("CSV must contain 'utm_answer', 'order_id' and 'total_value' columns.")

    # Convert total_value to numeric and fill any conversion issues with 0
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)
    
    # Group by utm_answer to count orders and sum total sales
    ranking = data.groupby('utm_answer').agg(
        order_count=('order_id', 'count'),
        total_sales=('total_value', 'sum')
    ).reset_index()

    # Sort ranking by total sales in descending order
    ranking = ranking.sort_values(by='total_sales', ascending=False)
    
    # Format total_sales as a currency string
    ranking['total_sales'] = ranking['total_sales'].apply(lambda x: f"${int(x):,}".replace(",", "."))
    
    return ranking.to_dict(orient='records')

# app/services/undefined_campaign_ranking.py
import pandas as pd

def get_top_twenty_days_by_undefined_campaign(file_path):
    # Load CSV data
    data = pd.read_csv(file_path)
    
    # Filter rows where utm_campaign is "undefined" (case-insensitive)
    data = data[data['utm_campaign'].astype(str).str.lower() == 'undefined']
    
    # Convert order_date to datetime and drop invalid dates
    data['order_date'] = pd.to_datetime(data['order_date'], errors='coerce')
    data = data.dropna(subset=['order_date'])
    
    # Extract the date (without time)
    data['Date'] = data['order_date'].dt.date
    
    # Group by Date, counting orders and summing total sales
    summary = data.groupby('Date').agg(
        total_orders=('order_id', 'count'),
        total_sales=('total_value', lambda x: pd.to_numeric(x, errors='coerce').sum())
    ).reset_index()
    
    # Sort by total orders (descending) and take the top 20 days
    summary = summary.sort_values(by='total_orders', ascending=False).head(20)
    
    # Format total_sales as a currency string (e.g. "$12.345")
    summary['total_sales'] = summary['total_sales'].apply(lambda x: f"${int(x):,}".replace(",", "."))
    summary['Date'] = summary['Date'].astype(str)
    
    return summary.to_dict(orient='records')


def get_top_ten_utm_content_by_sales(file_path):
    """
    Reads the CSV file at file_path and aggregates data by the 'utm_content' column.
    It calculates the number of orders and the total sales for each unique utm_content,
    formats the total sales as a currency string, sorts the results in descending order
    by total sales, and returns the top 10 entries.
    """
    import pandas as pd
    import re

    def remove_non_alphanumeric(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the 'utm_content' column
    if 'utm_content' not in data.columns:
        raise ValueError("The CSV file must contain the 'utm_content' column.")

    # Clean the utm_content values by removing non-alphanumeric characters
    data['utm_content'] = data['utm_content'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and handle conversion issues
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by the cleaned utm_content to calculate order counts and total sales
    content_summary = data.groupby('utm_content').agg(
        Order_Count=('utm_content', 'size'),
        Total_Sales=('total_value', 'sum')
    ).reset_index()

    # Format the total sales as a currency string
    content_summary['Sales'] = content_summary['Total_Sales'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )

    # Sort by total sales in descending order and take the top 10
    top_content = content_summary.sort_values(by='Total_Sales', ascending=False).head(10)

    return top_content.to_dict(orient='records')

def get_top_ten_utm_source_by_sales(file_path):
    """
    Reads the CSV file at file_path and aggregates data by the 'utm_source' column.
    It calculates the number of orders and the total sales for each unique utm_source,
    formats the total sales as a currency string, sorts the results in descending order
    by total sales, and returns the top 10 entries.
    """
    import pandas as pd
    import re

    def remove_non_alphanumeric(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the 'utm_source' column
    if 'utm_source' not in data.columns:
        raise ValueError("The CSV file must contain the 'utm_source' column.")

    # Clean the utm_source values by removing non-alphanumeric characters
    data['utm_source'] = data['utm_source'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and handle conversion issues
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by the cleaned utm_source to calculate order counts and total sales
    source_summary = data.groupby('utm_source').agg(
        Order_Count=('utm_source', 'size'),
        Total_Sales=('total_value', 'sum')
    ).reset_index()

    # Format the total sales as a currency string (e.g., "$12.345")
    source_summary['Sales'] = source_summary['Total_Sales'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )

    # Sort by total sales in descending order and select the top 10
    top_sources = source_summary.sort_values(by='Total_Sales', ascending=False).head(10)

    return top_sources.to_dict(orient='records')

def get_top_ten_utm_medium_by_sales(file_path):
    """
    Reads the CSV file at file_path and aggregates data by the 'utm_medium' column.
    It calculates the number of orders and the total sales for each unique utm_medium,
    formats the total sales as a currency string, sorts the results in descending order
    by total sales, and returns the top 10 entries.
    """
    import pandas as pd
    import re

    def remove_non_alphanumeric(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the 'utm_medium' column
    if 'utm_medium' not in data.columns:
        raise ValueError("The CSV file must contain the 'utm_medium' column.")

    # Clean the utm_medium values by removing non-alphanumeric characters
    data['utm_medium'] = data['utm_medium'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and handle conversion issues
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by the cleaned utm_medium to calculate order counts and total sales
    medium_summary = data.groupby('utm_medium').agg(
        Order_Count=('utm_medium', 'size'),
        Total_Sales=('total_value', 'sum')
    ).reset_index()

    # Format the total sales as a currency string (e.g., "$12.345")
    medium_summary['Sales'] = medium_summary['Total_Sales'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )

    # Sort by total sales in descending order and select the top 10
    top_mediums = medium_summary.sort_values(by='Total_Sales', ascending=False).head(10)

    return top_mediums.to_dict(orient='records')

def get_top_ten_utm_term_by_sales(file_path):
    """
    Reads the CSV file at file_path and aggregates data by the 'utm_term' column.
    It calculates the number of orders and the total sales for each unique utm_term,
    formats the total sales as a currency string, sorts the results in descending order
    by total sales, and returns the top 10 entries.
    """
    import pandas as pd
    import re

    def remove_non_alphanumeric(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the CSV contains the 'utm_term' column
    if 'utm_term' not in data.columns:
        raise ValueError("The CSV file must contain the 'utm_term' column.")

    # Clean the utm_term values by removing non-alphanumeric characters
    data['utm_term'] = data['utm_term'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and handle conversion issues
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by the cleaned utm_term to calculate order counts and total sales
    term_summary = data.groupby('utm_term').agg(
        Order_Count=('utm_term', 'size'),
        Total_Sales=('total_value', 'sum')
    ).reset_index()

    # Format the total sales as a currency string (e.g., "$12.345")
    term_summary['Sales'] = term_summary['Total_Sales'].apply(
        lambda x: f"${int(x):,}".replace(",", ".")
    )

    # Sort by total sales in descending order and select the top 10
    top_terms = term_summary.sort_values(by='Total_Sales', ascending=False).head(10)

    return top_terms.to_dict(orient='records')


def get_utm_content_ranking_by_gender(file_path, gender='male', top_n=10):
    import pandas as pd
    import re

    # Helper function to clean non-alphanumeric characters from a string
    def remove_non_alphanumeric(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s)

    # Load the CSV file
    data = pd.read_csv(file_path)

    # Validate that the required columns exist
    required_columns = ['utm_content', 'total_value', 'gender', 'order_id']
    for col in required_columns:
        if col not in data.columns:
            raise ValueError(f"The CSV file must contain the '{col}' column.")

    # Filter the data to only include rows for the specified gender (default: male)
    data = data[data['gender'].str.lower() == gender.lower()]

    # Clean the utm_content values by removing non-alphanumeric characters
    data['utm_content'] = data['utm_content'].astype(str).apply(remove_non_alphanumeric)

    # Ensure the total_value column is numeric and replace any conversion issues with 0
    data['total_value'] = pd.to_numeric(data['total_value'], errors='coerce').fillna(0)

    # Group by utm_content to calculate the number of orders and total sales
    ranking = data.groupby('utm_content').agg(
        orders=('order_id', 'count'),
        total_sales=('total_value', 'sum')
    ).reset_index()

    # Sort the results by total sales in descending order
    ranking = ranking.sort_values(by='total_sales', ascending=False)

    # Limit the ranking to top_n entries
    ranking = ranking.head(top_n)

    # Format total sales as a currency string (e.g., "$12.345")
    ranking['total_sales'] = ranking['total_sales'].apply(lambda x: f"${int(x):,}".replace(",", "."))

    # Return the ranking as a list of dictionaries
    return ranking.to_dict(orient='records')


def get_order_percentage_by_city(file_path):
    import pandas as pd

    # Load the CSV file
    data = pd.read_csv(file_path)
    
    # Ensure the CSV contains a 'city' column
    if 'city' not in data.columns:
        raise ValueError("The CSV file must contain a 'city' column.")
    
    # Total number of orders
    total_orders = len(data)
    
    # Group by city and count the number of orders per city
    grouped = data.groupby('city').size().reset_index(name='Order_Count')
    
    # Calculate the percentage of orders for each city
    grouped['Percentage'] = (grouped['Order_Count'] / total_orders * 100).round(2)
    
    # Rename columns for clarity in the HTML table
    grouped.rename(columns={
        'city': 'City', 
        'Order_Count': 'Order Count', 
        'Percentage': 'Percentage (%)'
    }, inplace=True)
    
    # Sort by percentage in descending order
    grouped = grouped.sort_values(by='Percentage (%)', ascending=False)
    
    # Return the results as a list of dictionaries for easy rendering in Jinja2
    return grouped.to_dict(orient='records')
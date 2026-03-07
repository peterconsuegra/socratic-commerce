import pandas as pd

#inputs
#Ordenes depuradas desde woocommerce con genero
input_file = "woo2024/data.csv"  

# Function to process the .csv file
def process_repeated_orders(input_file, gender):
    # Import the .csv file
    data = pd.read_csv(input_file)

    # Ensure necessary columns exist
    required_columns = ["gender", "Correo electrónico (facturación)"]
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The input file must contain the following columns: {required_columns}")

    # Filter for orders with the specified gender
    filtered_data = data[data["gender"] == gender]
    total_orders =  len(filtered_data)
    # Identify orders that repeat the email for the specified gender
    repeated_orders = filtered_data[filtered_data.duplicated(subset="Correo electrónico (facturación)", keep=False)].copy()

    # Print total orders
    total_rows = len(repeated_orders)
    print(f"Total {gender} orders: {total_orders}")
    print(f"Total repurchase {gender} orders: {total_rows}")
    repurchase_percentage = (total_rows / total_orders) * 100
    print(f"Total repurchase {gender}: {repurchase_percentage:.2f}%")
    
    # Add a column to indicate the gender
    repeated_orders.loc[:, "Processed Gender"] = gender

    # Return the repeated orders
    return repeated_orders

try:
    print("Processing male data...")
    male_repeated_orders = process_repeated_orders(input_file, "male")
    
    print("\nProcessing female data...")
    female_repeated_orders = process_repeated_orders(input_file, "female")

except Exception as e:
    print(f"An error occurred: {e}")

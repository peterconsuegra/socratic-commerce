import pandas as pd

def find_customers_with_multiple_purchases(input_file, output_file):
    # Load the data
    data = pd.read_csv(input_file)

    # Ensure the required columns exist
    required_columns = ["Correo electrónico (facturación)", "gender"]
    if not all(col in data.columns for col in required_columns):
        raise ValueError(f"The input file must contain the following columns: {required_columns}")

    # Group by email and count the number of occurrences for each email
    purchase_counts = data["Correo electrónico (facturación)"].value_counts()

    # Filter for customers with more than one purchase
    repeated_customers = purchase_counts[purchase_counts > 1]

    # Create a DataFrame with the results
    repeated_customers_df = pd.DataFrame({
        "Correo electrónico (facturación)": repeated_customers.index,
        "Número de compras": repeated_customers.values
    })

    # Merge with the original data to include the gender column
    repeated_customers_df = repeated_customers_df.merge(
        data[["Correo electrónico (facturación)", "gender"]].drop_duplicates(),
        on="Correo electrónico (facturación)",
        how="left"
    )

    # Save the results to a CSV file
    repeated_customers_df.to_csv(output_file, index=False)

    # Calculate the total unique customers by gender
    unique_customers_by_gender = data[["Correo electrónico (facturación)", "gender"]].drop_duplicates()["gender"].value_counts()

    # Calculate the total repeated customers by gender
    repeated_customers_by_gender = repeated_customers_df["gender"].value_counts()

    # Calculate and display the percentage of repeated customers by gender
    for gender in unique_customers_by_gender.index:
        total_unique = unique_customers_by_gender[gender]
        total_repeated = repeated_customers_by_gender.get(gender, 0)
        percentage_repeated = (total_repeated / total_unique) * 100
        print(f"Gender: {gender} | Total Unique: {total_unique} | Total Repeated: {total_repeated} | Percentage Repeated: {percentage_repeated:.2f}%")

    print(f"Results saved to {output_file}")

# Example usage
input_file = "woo2024/data.csv"  # Replace with the path to your input file
output_file = "woo2024/repeated_customers.csv"  # Replace with the path to your output file

try:
    find_customers_with_multiple_purchases(input_file, output_file)
except Exception as e:
    print(f"An error occurred: {e}")

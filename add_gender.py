import pandas as pd
import gender_guesser.detector as gender

# Carga .csv file sin ordenes duplicadas
file_path = 'saveaplaya.petetesting.com-chatgptfile-woo-2024-01-01_to_2024-12-31.csv'  # Replace with your actual file path
sales_data = pd.read_csv(file_path)

# Initialize the gender detector
detector = gender.Detector()

# Infer gender based on first name
def get_gender(name):
    if isinstance(name, str) and len(name.split()) > 0:
        result = detector.get_gender(name.split()[0])  # Use the first name
        if result in ['male', 'mostly_male']:
            return 'male'
        elif result in ['female', 'mostly_female']:
            return 'female'
        else:
            return 'unknown'
    return 'unknown'

# Apply the gender function to the 'fn' column (first name)
sales_data['gender'] = sales_data['fn'].apply(get_gender)

# Summarize sales by gender
sales_by_gender = sales_data.groupby('gender')['total_sale'].sum()

# Calculate percentage
sales_by_gender_percentage = (sales_by_gender / sales_by_gender.sum()) * 100

# Print the results
print("Sales by Gender:")
print(sales_by_gender)
print("\nSales by Gender (Percentage):")
print(sales_by_gender_percentage)

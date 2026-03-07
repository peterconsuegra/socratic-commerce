def format_cop(value):
    """
    Format a numeric value as Colombian Pesos (COP).
    Example: 1234567 -> "COP $1.234.567"
    """
    try:
        # Convert the value to float (in case it's passed as a string)
        number = float(value)
    except (TypeError, ValueError):
        # If conversion fails, return the original value
        return value

    # Format the number with no decimals, comma as thousands separator,
    # then replace commas with dots to match Colombian style.
    formatted = "COP ${:,.0f}".format(number).replace(",", ".")
    return formatted

def get_value(d, key):
    """Return the value for key in dict d, or None if not found."""
    return d.get(key)
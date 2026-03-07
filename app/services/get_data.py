# app/services/get_data.py

import csv
import os
import requests
from gender_guesser.detector import Detector


def load_name_to_gender_mapping(file_path):
    """
    Loads a custom name-to-gender mapping from a CSV file.
    """
    name_to_gender = {}
    try:
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = (row.get("name") or "").strip().lower()
                gender = (row.get("gender") or "").strip().lower()
                if name:
                    name_to_gender[name] = gender
    except Exception as e:
        print(f"Error loading name-to-gender mapping: {e}")
    return name_to_gender


def get_gender_with_custom_mapping(name, detector, name_to_gender):
    name_lower = (name or "").strip().lower()
    if not name_lower:
        return "unknown"

    if name_lower in name_to_gender:
        return name_to_gender[name_lower]

    guessed_gender = detector.get_gender(name)
    if guessed_gender in ["mostly_female", "female"]:
        return "female"
    if guessed_gender in ["mostly_male", "male"]:
        return "male"
    return "unknown"


def _clean_str(v):
    if v is None:
        return ""
    return str(v).strip()


DAILY_SALES_SCHEMA = [
    "order_id",
    "order_date",
    "name",
    "email",
    "city",
    "state",
    "order_lat",
    "order_lng",
    "total_value",
    "product",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_answer",
    "address_1",
    "gender",
]


def _build_daily_sales_row(item: dict, detector: Detector, name_to_gender: dict) -> dict:
    name_raw = _clean_str(item.get("name"))
    name_title = name_raw.title() if name_raw else ""

    first_name = name_title.split()[0] if name_title else ""
    gender = get_gender_with_custom_mapping(first_name, detector, name_to_gender) if first_name else "unknown"

    utm_source = _clean_str(item.get("utm_source")).lower()
    utm_medium = _clean_str(item.get("utm_medium"))
    utm_campaign = _clean_str(item.get("utm_campaign"))
    utm_term = _clean_str(item.get("utm_term"))
    utm_content = _clean_str(item.get("utm_content"))
    utm_answer = _clean_str(item.get("utm_answer"))

    row = {
        "order_id": _clean_str(item.get("order_id")),
        "order_date": _clean_str(item.get("order_date")),
        "name": name_title,
        "email": _clean_str(item.get("email")),
        "city": _clean_str(item.get("city")),
        "state": _clean_str(item.get("state")),
        "order_lat": _clean_str(item.get("order_lat")),
        "order_lng": _clean_str(item.get("order_lng")),
        "total_value": _clean_str(item.get("total_value")),
        "product": _clean_str(item.get("product")),
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "utm_term": utm_term,
        "utm_content": utm_content,
        "utm_answer": utm_answer,
        "address_1": _clean_str(item.get("address_1")),
        "gender": gender,
    }

    return {k: row.get(k, "") for k in DAILY_SALES_SCHEMA}


def fetch_json_and_create_csv(
    json_data,
    output_file,
    name_to_gender_file,
    include_address_1=False,
    force_schema=None,
    row_builder=None,
    drop_columns=None,
):
    try:
        if drop_columns is None:
            drop_columns = ["shipping"]

        if not isinstance(json_data, list):
            raise ValueError("Expected JSON data to be a list of objects.")
        if not json_data:
            raise ValueError("Expected JSON data list to be non-empty.")

        name_to_gender = load_name_to_gender_mapping(name_to_gender_file)
        detector = Detector()

        rows = []
        for item in json_data:
            if not isinstance(item, dict):
                continue

            if row_builder:
                row = row_builder(item, detector, name_to_gender)
                rows.append(row)
                continue

            if "name" in item and item["name"]:
                item["name"] = str(item["name"]).title()

            first_name = str(item.get("name", "")).split()[0] if item.get("name") else ""
            item["gender"] = (
                get_gender_with_custom_mapping(first_name, detector, name_to_gender)
                if first_name
                else "unknown"
            )

            if include_address_1:
                item["address_1"] = _clean_str(item.get("address_1"))

            rows.append(item)

        if force_schema:
            headers = [h for h in list(force_schema) if h not in set(drop_columns)]
        else:
            headers = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for k in row.keys():
                    if k in set(drop_columns):
                        continue
                    if k not in seen:
                        seen.add(k)
                        headers.append(k)

        drop_set = set(drop_columns)

        with open(output_file, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                filtered_row = {k: v for k, v in row.items() if k not in drop_set}
                writer.writerow(filtered_row)

        return f"CSV file successfully created at: {output_file}"

    except Exception as e:
        return f"An error occurred during CSV creation: {e}"


def fetch_orders_and_write_csv(
    *,
    orders_url: str,
    api_key: str,
    file_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    cwd: str | None = None,
    timeout: int = 60,
) -> tuple[bool, dict]:
    """
    Calls the Orders API and writes the daily sales CSV.

    If start_date and end_date are provided, sends them as query params.
    Otherwise, calls the endpoint without date params.

    Returns: (ok, payload)
      - ok=True: payload has message + csv_url
      - ok=False: payload has message
    """
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        params = None
        if start_date and end_date:
            params = {
                "start_date": start_date,
                "end_date": end_date,
            }

        response = requests.get(
            orders_url,
            headers=headers,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()

        json_data = response.json()
        if not json_data:
            return False, {"message": "No data returned from the API."}

        base_dir = cwd or os.getcwd()
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        csv_path = os.path.join(data_dir, file_name)

        name_without_ext = os.path.splitext(file_name)[0]
        filtered_file_name = f"{name_without_ext}_filtered.csv"
        filtered_csv_path = os.path.join(data_dir, filtered_file_name)

        for path in [csv_path, filtered_csv_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

        name_map_file = os.path.join(data_dir, "name_to_gender.csv")

        message = fetch_json_and_create_csv(
            json_data=json_data,
            output_file=csv_path,
            name_to_gender_file=name_map_file,
            force_schema=DAILY_SALES_SCHEMA,
            row_builder=_build_daily_sales_row,
            drop_columns=["shipping"],
        )

        if "successfully" in (message or "").lower():
            return True, {
                "message": f"data/{file_name} created successfully!",
                "csv_url": f"/static/data/{file_name}",
            }

        return False, {"message": f"Error creating CSV: {message}"}

    except requests.exceptions.RequestException as e:
        return False, {"message": f"Request error occurred: {e}"}
    except Exception as e:
        return False, {"message": f"An unexpected error occurred: {e}"}
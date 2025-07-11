import streamlit as st
import requests
import csv
import pandas as pd
import io
import tempfile
import os

BASE_URL = "https://test.buurfashion.itsperfect.it/api/v3"

# --- Helper Functions ---

def get_bearer_token(username, password):
    url = f"{BASE_URL}/authentication"
    data = {"username": username, "password": password}
    resp = requests.post(url, json=data)
    resp.raise_for_status()
    return resp.json()["token"]

def get_all_puts(token):
    url = f"{BASE_URL}/puts"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def get_put_lines(token, put_id):
    url = f"{BASE_URL}/puts/{put_id}/lines"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def fetch_put_lines_csv(username, password):
    token = get_bearer_token(username, password)
    puts = get_all_puts(token)
    put_ids = [put["id"] for put in puts]

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["put_id", "line_id", "item_number", "color_number"])
    for put_id in put_ids:
        lines = get_put_lines(token, put_id)
        for line in lines:
            line_id = line.get("id")
            item_number = (line.get("item") or {}).get("item_number", "")
            color_number = (line.get("color") or {}).get("color_number", "")
            writer.writerow([put_id, line_id, item_number, color_number])
    csv_buffer.seek(0)
    return csv_buffer

def merge_put_lines_to_excel(excel_file, csv_buffer):
    # Read files into DataFrames
    df_excel = pd.read_excel(excel_file)
    df_csv = pd.read_csv(csv_buffer)
    
    # Standardize column names
    df_excel.columns = [c.strip() for c in df_excel.columns]
    df_csv.columns = [c.strip() for c in df_csv.columns]
    
    # Mapping for quick lookup: (item_number, color_number) -> line_id
    mapping = {
        (str(row['item_number']), str(row['color_number'])): str(row['line_id'])
        for _, row in df_csv.iterrows()
    }
    # Function to fill in the PUT column
    def fill_put(row):
        if pd.isna(row['PUT']) or str(row['PUT']).strip() == "":
            key = (str(row['Artikelnummer']).strip(), str(row['Kleurnummer']).strip())
            return mapping.get(key, "")
        else:
            return row['PUT']
    
    df_excel['PUT'] = df_excel.apply(fill_put, axis=1)
    return df_excel

# --- Streamlit UI ---

st.title("Fetch PUT Lines and Merge to 'Check Bas' Excel File")

# Step 1: Login and fetch PUT lines as CSV
st.header("Step 1: Login to fetch PUT lines")
with st.form("auth_form"):
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    submitted = st.form_submit_button("Fetch PUT Lines")

csv_buffer = None
if submitted:
    try:
        with st.spinner("Fetching data and creating CSV..."):
            csv_buffer = fetch_put_lines_csv(username, password)
            st.success("PUT lines fetched successfully! Proceed to upload your 'Check Bas' Excel file.")
            st.session_state['csv_buffer'] = csv_buffer.getvalue()
    except Exception as e:
        st.error(f"Error: {e}")

# Step 2: Upload Excel and merge
st.header("Step 2: Upload and update your 'Check Bas' Excel file")

if 'csv_buffer' in st.session_state:
    excel_file = st.file_uploader("Upload 'Check Bas' Excel (.xlsx)", type=["xlsx"])
    if excel_file:
        # Convert session_state CSV back to buffer
        csv_buffer = io.StringIO(st.session_state['csv_buffer'])
        try:
            with st.spinner("Processing Excel file..."):
                df_updated = merge_put_lines_to_excel(excel_file, csv_buffer)
            st.success("Excel updated! Download your file below.")
            st.dataframe(df_updated.head())
            # Download link
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_updated.to_excel(writer, index=False)
            output.seek(0)
            st.download_button(
                label="Download Updated Excel",
                data=output,
                file_name="check_bas_updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        except Exception as e:
            st.error(f"Error updating Excel file: {e}")
else:
    st.info("First fetch the PUT lines by logging in above.")


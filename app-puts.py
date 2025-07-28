import streamlit as st
import requests
import csv
import pandas as pd
import io
import time

BASE_URL = "https://buurfashion.itsperfect.it/api/v3"
MAX_RATE_LIMIT_SLEEP = 10

# --- Helper Functions ---

def get_bearer_token(username, password):
    url = f"{BASE_URL}/authentication"
    data = {"username": username, "password": password}
    resp = requests.post(url, json=data)
    resp.raise_for_status()
    resp_data = resp.json()
    token = resp_data["token"]
    expires_in = resp_data.get("expires_in", 1740)
    expiry_timestamp = time.time() + expires_in - 60
    return token, expiry_timestamp

def ensure_valid_token():
    if (
        "token" not in st.session_state or
        "token_expiry" not in st.session_state or
        time.time() > st.session_state["token_expiry"]
    ):
        username = st.session_state["username"]
        password = st.session_state["password"]
        token, expiry = get_bearer_token(username, password)
        st.session_state["token"] = token
        st.session_state["token_expiry"] = expiry
    return st.session_state["token"]

def handle_rate_limits(resp):
    bucket_size = int(resp.headers.get("X-Bucket-Size", 100))
    marbles = int(resp.headers.get("X-Marbles-In-Bucket", 0))
    remaining = int(resp.headers.get("X-Remaining-Requests", bucket_size))
    if marbles >= bucket_size - 2 or remaining <= 2:
        calculated_wait = (marbles - remaining + 2) * 4
        wait_time = min(max(calculated_wait, 1), MAX_RATE_LIMIT_SLEEP)
        print(f"[handle_rate_limits] Bucket full/near. Sleeping {wait_time}s (calculated {calculated_wait}s).")
        time.sleep(wait_time)
    return

def safe_get(url, headers, params=None, log_text=None):
    while True:
        print(f"[safe_get] Making API call: {url} params={params}")
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            print("[safe_get] 429 received. Sleeping 3 seconds before retry.")
            time.sleep(3)
            continue
        handle_rate_limits(resp)
        return resp

def normalize_item_number(x):
    s = str(x).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s.zfill(7) if s.isdigit() else s

def normalize_order_number(x):
    """Normalize order number as string, removing any trailing .0."""
    s = str(x).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s

def normalize_kleurnummer(kleurnummer):
    kleurnummer_str = str(kleurnummer).strip()
    if kleurnummer_str.endswith('.0'):
        kleurnummer_str = kleurnummer_str[:-2]
    if kleurnummer_str.isdigit():
        return kleurnummer_str.zfill(3 if len(kleurnummer_str) <= 2 else 4)
    return kleurnummer_str

def strip_leading_zeros(x):
    s = str(x).lstrip('0')
    return s if s else '0'

def get_all_puts():
    token = ensure_valid_token()
    put_ids = []
    current_page = 1
    while True:
        url = f"{BASE_URL}/puts?status=0&limit=1000&page={current_page}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = safe_get(url, headers, log_text=f"[GET] /puts?status=0&limit=1000&page={current_page}")
        resp.raise_for_status()
        data = resp.json()
        put_ids.extend([put["id"] for put in data if "id" in put])

        # Handle pagination via response headers
        try:
            current = int(resp.headers.get('X-Pagination-Current-Page', current_page))
            total_pages = int(resp.headers.get('X-Pagination-Page-Count', current))
        except Exception:
            break  # fallback: if headers missing, just exit

        if current >= total_pages:
            break
        current_page += 1
    return put_ids


def get_put_lines(put_id):
    token = ensure_valid_token()
    url = f"{BASE_URL}/puts/{put_id}/lines?limit=1000"
    headers = {"Authorization": f"Bearer {token}"}
    resp = safe_get(url, headers, log_text=f"[GET] /puts/{put_id}/lines")
    resp.raise_for_status()
    return resp.json()

def fetch_put_lines_csv(username, password):
    st.session_state["username"] = username
    st.session_state["password"] = password

    put_ids = get_all_puts()

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["put_id", "line_id", "po_number", "item_number", "color_number", "quantity"])
    n_puts = len(put_ids)
    progress = st.progress(0)
    for idx, put_id in enumerate(put_ids):
        log_msg = f"Processing PUT {idx+1}/{n_puts} (PUT ID: {put_id})"
        print(log_msg)
        lines = get_put_lines(put_id)
        for line in lines:
            item = line.get("item") or {}
            color = line.get("color") or {}
            line_id = line.get("id")
            po_number = line.get("order_id")
            item_number = str(item.get("item_number", "")).strip()
            color_number = str(color.get("color_number", "")).strip()
            # --- get quantity, strip decimals ---
            raw_quantity = str(line.get("quantity", "0")).strip()
            quantity = raw_quantity.split(".")[0] if "." in raw_quantity else raw_quantity
            writer.writerow([
                put_id, line_id, po_number, normalize_item_number(item_number), normalize_kleurnummer(color_number), quantity
            ])
        progress.progress((idx + 1) / n_puts)
    csv_buffer.seek(0)
    return csv_buffer

def merge_put_lines_to_excel(excel_file, csv_buffer):
    df_excel = pd.read_excel(excel_file)
    df_csv = pd.read_csv(csv_buffer)
    df_excel.columns = [c.strip() for c in df_excel.columns]
    df_csv.columns = [c.strip() for c in df_csv.columns]

    df_csv['item_number'] = df_csv['item_number'].apply(normalize_item_number)
    df_csv['color_number'] = df_csv['color_number'].apply(normalize_kleurnummer)
    df_csv['po_number'] = df_csv['po_number'].apply(normalize_order_number)
    df_csv['put_id'] = df_csv['put_id'].astype(str).str.strip()
    df_csv['quantity'] = pd.to_numeric(df_csv['quantity'], errors='coerce').fillna(0).astype(int)
    df_excel['Artikelnummer'] = df_excel['Artikelnummer'].apply(normalize_item_number)
    df_excel['Kleurnummer'] = df_excel['Kleurnummer'].apply(normalize_kleurnummer)
    df_excel['Ordernr.'] = df_excel['Ordernr.'].apply(normalize_order_number)

    mapping = {}
    for _, row in df_csv.iterrows():
        key = (
            str(row['po_number']),
            str(row['item_number']),
            str(row['color_number'])
        )
        if key not in mapping:
            mapping[key] = str(row['put_id'])

    def fill_put(row):
        if 'PUT' not in row or pd.isna(row['PUT']) or str(row['PUT']).strip() == "":
            key = (
                str(row['Ordernr.']),
                str(row['Artikelnummer']),
                str(row['Kleurnummer'])
            )
            return mapping.get(key, "")
        else:
            return row['PUT']

    df_excel['PUT'] = df_excel.apply(fill_put, axis=1)
    df_excel['Artikelnummer'] = df_excel['Artikelnummer'].apply(strip_leading_zeros)
    df_excel['Ordernr.'] = df_excel['Ordernr.'].apply(normalize_order_number)

    # Use the input column name exactly as in the input file
    # Default to 'Received Quantity' if present, otherwise try 'Received Quantities'
    col_name = None
    for possible in ['Received Quantity', 'Received Quantities']:
        if possible in df_excel.columns:
            col_name = possible
            break
    if not col_name:
        # Default to singular and add if missing
        col_name = 'Received Quantity'
        df_excel.insert(8, col_name, "")

    received_quantity = df_csv.groupby('put_id')['quantity'].sum().astype(int).to_dict()

    def should_update(q):
        # Update if NaN, empty, blank, or 0
        if pd.isna(q):
            return True
        s = str(q).strip()
        return s == "" or s == "0"

    def get_received_qty(row):
        existing = row.get(col_name, "")
        if should_update(existing):
            put_id = str(row['PUT']).strip()
            return received_quantity.get(put_id, "")
        else:
            return existing

    df_excel[col_name] = df_excel.apply(get_received_qty, axis=1)

    return df_excel

# --- Streamlit UI ---

st.markdown(
    """
    <div style="display: flex; justify-content: center; align-items: center; margin-bottom: 28px;">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2384.63 788.34" width="150">
            <path class="path-fill" d="M303.71,370.72c87.51-20.59,119.68-100.38,119.68-171.16,0-105.52-61.77-191.75-209.76-191.75H0v387.45l234.22-.09c141.56,0,185.31,83.65,185.31,172.45s-45.04,172.44-185.31,172.44H43.76v-228.56H0v268.45h234.22c160.86,0,229.06-97.8,229.06-212.34,0-86.22-43.75-178.87-159.57-196.89ZM213.63,355.28H43.76V47.71h169.87c127.4,0,166.01,72.07,166.01,151.85,0,81.08-38.61,155.72-166.01,155.72Z"></path>
            <g>
                <path class="path-fill" d="M611.74,7.81h205.91v17.47h-185.69v131.45h167.3v17.47h-167.3v161.78h-20.22V7.81h0Z"></path>
                <path class="path-fill" d="M922.43,7.81h22.06l129.62,328.16h-21.6l-41.37-105.25h-157.18l-41.82,105.25h-21.6L922.43,7.81ZM1004.71,213.26l-70.32-185.68h-.92l-73.08,185.68h144.32Z"></path>
                <path class="path-fill" d="M1313.56,101.58c-.62-14.09-3.6-26.35-8.96-36.77-5.37-10.41-12.64-19.15-21.83-26.2-9.2-7.04-20.15-12.33-32.86-15.86-12.72-3.52-26.59-5.29-41.6-5.29-9.2,0-19.08,1.08-29.65,3.22-10.56,2.15-20.38,5.9-29.42,11.26s-16.47,12.57-22.29,21.6c-5.83,9.04-8.74,20.3-8.74,33.78s3.22,23.9,9.66,32.18c6.43,8.27,14.85,14.94,25.27,19.99s22.36,9.12,35.85,12.18c13.48,3.07,27.13,5.98,40.91,8.73,14.09,2.76,27.81,6.05,41.14,9.88,13.33,3.83,25.27,9.04,35.85,15.63,10.56,6.59,19.08,15.17,25.51,25.74,6.43,10.57,9.64,23.83,9.64,39.76,0,17.16-3.67,31.56-11.02,43.21-7.35,11.65-16.63,21.14-27.81,28.49-11.18,7.35-23.59,12.64-37.23,15.86-13.63,3.22-26.73,4.83-39.3,4.83-19.3,0-37.39-2.07-54.23-6.21-16.85-4.14-31.56-10.87-44.12-20.22-12.58-9.34-22.46-21.37-29.65-36.08-7.2-14.71-10.64-32.63-10.34-53.77h20.23c-.92,18.08,1.67,33.32,7.8,45.73s14.63,22.6,25.51,30.57c10.88,7.97,23.68,13.71,38.39,17.23,14.71,3.53,30.18,5.29,46.41,5.29,9.8,0,20.3-1.22,31.48-3.68,11.18-2.45,21.46-6.58,30.8-12.41,9.34-5.82,17.15-13.48,23.44-22.98,6.28-9.49,9.42-21.44,9.42-35.85s-3.21-25.05-9.64-33.78c-6.45-8.73-14.94-15.78-25.51-21.14-10.58-5.36-22.52-9.65-35.86-12.87-13.33-3.22-27.03-6.21-41.12-8.96-13.79-2.76-27.43-5.98-40.91-9.65s-25.43-8.65-35.85-14.94c-10.42-6.28-18.84-14.4-25.27-24.36-6.43-9.95-9.66-22.75-9.66-38.38s3.29-29.02,9.88-40.21c6.59-11.18,15.17-20.22,25.75-27.12,10.56-6.89,22.43-11.95,35.61-15.17C1182.41,1.62,1195.45,0,1208.31,0c17.15,0,33.17,1.92,48.03,5.75,14.85,3.83,27.88,9.81,39.06,17.92,11.18,8.12,20.15,18.62,26.89,31.48,6.74,12.86,10.58,28.35,11.5,46.42h-20.23Z"></path>
                <path class="path-fill" d="M1390.77,7.81h20.23v148.91h209.58V7.81h20.22v328.16h-20.22v-161.77h-209.58v161.78h-20.23V7.81Z"></path>
                <path class="path-fill" d="M1700.55,7.81h20.22v328.16h-20.22V7.81Z"></path>
                <path class="path-fill" d="M2077.89,171.9c0,23.9-3.53,46.35-10.58,67.33-7.05,20.99-17.23,39.23-30.56,54.69-13.33,15.48-29.64,27.66-48.95,36.54-19.3,8.88-41.22,13.33-65.72,13.33s-46.49-4.45-65.95-13.33c-19.46-8.88-35.85-21.06-49.18-36.54-13.34-15.47-23.52-33.7-30.57-54.69-7.05-20.98-10.56-43.43-10.56-67.33s3.51-46.34,10.56-67.33c7.05-20.98,17.23-39.22,30.57-54.69,13.33-15.47,29.72-27.65,49.18-36.54,19.46-8.89,41.44-13.34,65.95-13.34s46.43,4.45,65.72,13.33c19.31,8.89,35.63,21.07,48.95,36.54,13.33,15.48,23.51,33.71,30.56,54.69,7.06,21,10.58,43.44,10.58,67.34ZM1786.04,171.9c0,21.14,3.07,41.06,9.2,59.75,6.12,18.69,15.01,35.01,26.65,48.95,11.64,13.95,25.89,25.05,42.74,33.32,16.85,8.27,36.01,12.41,57.45,12.41s40.52-4.13,57.23-12.41c16.69-8.27,30.86-19.37,42.52-33.32,11.64-13.94,20.52-30.25,26.65-48.95,6.13-18.68,9.2-38.61,9.2-59.75s-3.07-41.06-9.2-59.75c-6.13-18.69-15.01-35-26.65-48.95-11.66-13.94-25.82-25.05-42.52-33.32-16.71-8.27-35.78-12.41-57.23-12.41s-40.6,4.14-57.45,12.41c-16.85,8.27-31.1,19.38-42.74,33.32-11.64,13.94-20.54,30.26-26.65,48.95-6.14,18.69-9.2,38.61-9.2,59.75Z"></path>
                <path class="path-fill" d="M2122.94,7.81h25.27l207.29,301.05h.92V7.81h20.22v328.16h-23.9l-208.66-303.34h-.92v303.35h-20.22V7.81h0Z"></path>
                <path class="path-fill" d="M611.74,450.05h132.36c19.74,0,36.24,2.47,49.51,7.4,13.26,4.94,23.91,11.42,31.94,19.44,8.01,8.03,13.8,17.2,17.35,27.54,3.54,10.34,5.33,20.75,5.33,31.24,0,8.95-1.55,17.44-4.64,25.46s-7.48,15.12-13.19,21.29c-5.71,6.18-12.49,11.34-20.36,15.5s-16.58,6.87-26.15,8.1l.93.93c2.16-.3,7.25.62,15.27,2.78,8.03,2.16,16.43,6.25,25.22,12.26,8.8,6.02,16.66,14.35,23.6,24.99s10.41,24.3,10.41,40.95c0,15.12-2.78,28.39-8.33,39.8-5.55,11.42-13.35,21.06-23.37,28.92-10.03,7.87-22.15,13.81-36.33,17.82-14.19,4.01-29.92,6.02-47.2,6.02h-132.35v-330.44ZM744.1,599.99c28.07,0,49.05-6.25,62.93-18.74,13.88-12.5,20.82-28.93,20.82-49.29,0-12.03-2.3-22.13-6.94-30.31-4.62-8.17-10.8-14.81-18.51-19.9-7.72-5.09-16.58-8.71-26.61-10.88-10.03-2.16-20.6-3.24-31.7-3.24h-112v132.35h112.01ZM744.1,762.89c29.62,0,52.83-6.25,69.65-18.74,16.81-12.49,25.22-31.24,25.22-56.23,0-14.19-2.94-25.83-8.8-34.94-5.87-9.09-13.35-16.27-22.44-21.52-9.11-5.24-19.29-8.87-30.55-10.88-11.27-2-22.29-3.01-33.09-3.01h-112v145.32h112.01Z"></path>
                <path class="path-fill" d="M913,450.05h137.91c14.5,0,28.08,1.47,40.73,4.4s23.75,7.72,33.31,14.35c9.56,6.64,17.05,15.35,22.45,26.15,5.39,10.8,8.09,24.22,8.09,40.26,0,22.21-6.24,40.95-18.74,56.23-12.49,15.27-29.84,24.76-52.06,28.46v.92c15.11,1.85,27.08,5.86,35.86,12.03,8.8,6.18,15.35,13.73,19.67,22.68,4.32,8.95,7.1,18.82,8.33,29.62s1.85,21.6,1.85,32.39v18.98c0,6.18.3,11.96.93,17.36.61,5.4,1.62,10.41,3.01,15.04,1.39,4.63,3.31,8.49,5.79,11.57h-22.68c-4.33-7.71-6.72-16.73-7.18-27.07-.46-10.34-.69-21.06-.69-32.17s-.46-22.13-1.39-33.09c-.93-10.95-3.87-20.74-8.8-29.39-4.94-8.63-12.73-15.57-23.37-20.82-10.65-5.24-25.68-7.87-45.13-7.87h-117.52v150.41h-20.37v-330.44ZM1050.91,612.49c12.04,0,23.22-1.46,33.55-4.4,10.33-2.93,19.21-7.48,26.61-13.65,7.4-6.17,13.26-14.03,17.59-23.6,4.32-9.56,6.48-20.98,6.48-34.25,0-12.34-2.32-22.91-6.94-31.7-4.62-8.79-10.81-15.97-18.52-21.52s-16.65-9.56-26.84-12.03c-10.17-2.46-20.82-3.7-31.94-3.7h-117.53v144.85h117.54Z"></path>
                <path class="path-fill" d="M1312.38,450.05h22.21l130.52,330.43h-21.75l-41.66-105.98h-158.27l-42.11,105.98h-21.75l132.81-330.43ZM1395.22,656.91l-70.8-186.97h-.93l-73.58,186.97h145.31Z"></path>
                <path class="path-fill" d="M1498.42,450.05h25.45l208.72,303.13h.93v-303.13h20.36v330.43h-24.07l-210.1-305.44h-.93v305.44h-20.36v-330.43Z"></path>
                <path class="path-fill" d="M1821.9,450.05h108.3c29.61,0,54.22,4.63,73.81,13.89,19.59,9.25,35.25,21.6,46.97,37.02,11.72,15.43,19.98,33.02,24.76,52.76,4.78,19.75,7.18,40.11,7.18,61.09,0,23.45-3.01,45.28-9.03,65.49-6.01,20.21-15.34,37.72-27.99,52.53-12.66,14.81-28.63,26.46-47.9,34.94-19.29,8.49-42.19,12.73-68.72,12.73h-107.38v-330.45ZM1931.11,762.89c15.43,0,30.93-2.39,46.52-7.18,15.56-4.78,29.69-12.8,42.34-24.06,12.65-11.26,22.92-26.38,30.78-45.36,7.87-18.97,11.79-42.81,11.79-71.5,0-22.52-2.24-41.8-6.7-57.85-4.48-16.04-10.35-29.54-17.59-40.49-7.25-10.95-15.58-19.59-24.98-25.92-9.42-6.32-19.14-11.18-29.16-14.58-10.03-3.39-20.07-5.63-30.08-6.71-10.03-1.08-19.06-1.62-27.08-1.62h-84.68v295.26h88.84Z"></path>
                <path class="path-fill" d="M2355.95,544.46c-.62-14.19-3.63-26.53-9.02-37.02-5.4-10.48-12.73-19.28-21.98-26.38-9.26-7.09-20.29-12.42-33.09-15.97-12.81-3.54-26.77-5.32-41.89-5.32-9.26,0-19.21,1.08-29.86,3.24-10.64,2.16-20.52,5.94-29.62,11.34s-16.58,12.65-22.44,21.75c-5.87,9.1-8.8,20.44-8.8,34.01s3.25,24.07,9.72,32.4c6.48,8.33,14.96,15.04,25.45,20.13,10.49,5.09,22.52,9.18,36.1,12.26,13.58,3.09,27.31,6.02,41.2,8.79,14.19,2.78,28,6.1,41.42,9.95,13.42,3.86,25.45,9.1,36.1,15.73,10.64,6.64,19.21,15.27,25.68,25.92,6.48,10.64,9.71,23.99,9.71,40.03,0,17.28-3.7,31.78-11.1,43.5-7.4,11.73-16.74,21.29-28,28.69-11.26,7.4-23.75,12.73-37.49,15.97-13.72,3.24-26.92,4.86-39.57,4.86-19.43,0-37.65-2.08-54.6-6.25-16.97-4.17-31.78-10.95-44.43-20.36-12.66-9.41-22.61-21.52-29.86-36.33s-10.72-32.86-10.41-54.14h20.37c-.93,18.21,1.68,33.55,7.85,46.04,6.17,12.5,14.73,22.76,25.68,30.78,10.95,8.02,23.84,13.81,38.65,17.35,14.81,3.55,30.39,5.32,46.73,5.32,9.87,0,20.44-1.23,31.7-3.7,11.26-2.46,21.6-6.63,31.01-12.49,9.4-5.86,17.27-13.57,23.6-23.14,6.32-9.56,9.48-21.59,9.48-36.1s-3.23-25.22-9.71-34.01c-6.49-8.79-15.05-15.89-25.68-21.29-10.65-5.39-22.68-9.72-36.11-12.96-13.42-3.24-27.22-6.25-41.41-9.02-13.88-2.78-27.62-6.02-41.2-9.72-13.58-3.7-25.61-8.71-36.1-15.04-10.49-6.32-18.97-14.5-25.45-24.53-6.48-10.02-9.72-22.91-9.72-38.64s3.31-29.22,9.95-40.49c6.64-11.26,15.27-20.36,25.92-27.31,10.64-6.94,22.59-12.03,35.86-15.27,13.26-3.24,26.39-4.86,39.34-4.86,17.27,0,33.39,1.93,48.36,5.79,14.96,3.86,28.07,9.88,39.33,18.05,11.26,8.18,20.29,18.74,27.08,31.7s10.65,28.54,11.58,46.74h-20.33Z"></path>
            </g>
        </svg>
    </div>
    """,
    unsafe_allow_html=True)

st.title("B Fashion Brands Puts Updater")


# Step 1: Login and fetch PUT lines as CSV
st.header("Stap 1: Login om PUT lines op te halen")
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
            # ---- DOWNLOAD BUTTON FOR CSV BUFFER ----
            st.download_button(
                label="Download PUT lines as CSV",
                data=st.session_state['csv_buffer'],
                file_name="put_lines.csv",
                mime="text/csv"
            )
    except Exception as e:
        st.error(f"Error: {e}")

# Step 2: Upload Excel and merge
st.header("Stap 2: Upload en update 'Check Bas' Excel file")

if 'csv_buffer' in st.session_state:
    excel_file = st.file_uploader("Upload 'Check Bas' Excel (.xlsx)", type=["xlsx"])
    if excel_file:
        csv_buffer = io.StringIO(st.session_state['csv_buffer'])
        try:
            with st.spinner("Processing Excel file..."):
                df_updated = merge_put_lines_to_excel(excel_file, csv_buffer)
            st.success("Excel updated! Download your file below.")
            st.dataframe(df_updated.head())
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

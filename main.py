import os
import re
import pandas as pd
import requests
import traceback
from bs4 import BeautifulSoup
from datetime import datetime

# Environment variables
SHEETS_CSV_URL = os.getenv('SHEETS_CSV_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

if not SHEETS_CSV_URL:
    raise ValueError("SHEETS_CSV_URL environment variable is not set")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is not set")

CALENDAR_URL = "https://crossfitzlin.inrs.cz/rs/kalendar_vypis"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def fetch_calendar_html():
    response = requests.get(CALENDAR_URL, headers=HEADERS)
    response.raise_for_status()
    return response.text

def check_spot_availability(soup, date, time):
    print("DEBUG: Looking for the table header...", flush=True)
    header_table = soup.find('table', class_='wk-table-top')
    if not header_table:
        return False, None
    
    header_row = header_table.find('tr', class_='wk-days')
    if not header_row:
        return False, None
    
    header_cells = header_row.find_all('th')
    simple_date = ".".join(date.split('.')[:2])
    
    col_index = None
    for i, cell in enumerate(header_cells):
        cell_text = cell.get_text(strip=True)
        if simple_date in cell_text:
            col_index = i
            print(f"DEBUG: Found column for date '{simple_date}' at Index {i}.", flush=True)
            break
            
    if col_index is None:
        return False, None
    
    data_table = soup.find('table', class_='table-data')
    if not data_table:
        return False, None
    
    data_row = data_table.find('tr', class_='tb-sloupce-dnu')
    data_columns = data_row.find_all('td', class_='tb-sloupec')
    
    # Logic to handle index mapping (skipping time column if necessary)
    # Most likely your calendar has 8 columns (Time + 7 days)
    actual_col_index = col_index
    if len(data_columns) == 7 and col_index > 0:
        actual_col_index = col_index - 1

    if actual_col_index >= len(data_columns):
        return False, None

    target_column = data_columns[actual_col_index]
    lessons = target_column.find_all('div', class_='jedna-lekce-vypis')
    
    for lesson in lessons:
        time_span = lesson.find('span', class_='cas-od')
        if not time_span: continue
        
        if time_span.get_text(strip=True) == time:
            capacity_text = lesson.get_text(strip=True)
            match = re.search(r'(\d+)\s*/\s*(\d+)', capacity_text)
            if match:
                occupied = int(match.group(1))
                total = int(match.group(2))
                return occupied < total, f"{occupied} / {total}"
    return False, None

def send_notification(date, time, chat_id):
    """
    MODIFIED: Now sends separate date and time fields for easier Make.com processing.
    """
    payload = {
        "value1": f"MÍSTO JE VOLNÉ! {date} v {time}",
        "date": date,
        "time": time,
        "chat_id": str(chat_id)
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"Notification sent for {date} at {time}", flush=True)
    except Exception as e:
        print(f"Error sending webhook: {e}", flush=True)

def main():
    print(f"Fetching CSV from Google Sheets...", flush=True)
    try:
        df = pd.read_csv(SHEETS_CSV_URL)
    except Exception as e:
        print(f"Error reading CSV: {e}", flush=True)
        return
    
    print(f"Fetching calendar...", flush=True)
    html = fetch_calendar_html()
    soup = BeautifulSoup(html, 'html.parser')
    
    now = datetime.now()

    for index, row in df.iterrows():
        try:
            date_str = str(row['Date']).strip()
            time_str = str(row['Time']).strip()
            status = str(row['Status']).strip()
            chat_id = row['ChatID']
            
            if status.lower() != 'active':
                continue

            # POJISTKA: Ignorovat lekce v minulosti
            try:
                lesson_dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
                if lesson_dt < now:
                    print(f"Skipping past lesson: {date_str} {time_str}", flush=True)
                    continue
            except:
                pass # Pokud je formát data špatně, raději pokračovat
            
            print(f"Checking: {date_str} at {time_str}", flush=True)
            is_available, cap = check_spot_availability(soup, date_str, time_str)
            
            if is_available:
                send_notification(date_str, time_str, chat_id)
            else:
                print(f"Status: Full or Not Found ({cap})", flush=True)
                
        except Exception as e:
            print(f"Error in row {index}: {e}", flush=True)
            continue

if __name__ == "__main__":
    main()

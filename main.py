import os
import re
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Environment variables
SHEETS_CSV_URL = os.getenv('SHEETS_CSV_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

if not SHEETS_CSV_URL:
    raise ValueError("SHEETS_CSV_URL environment variable is not set")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is not set")

# URL to scrape
CALENDAR_URL = "https://crossfitzlin.inrs.cz/rs/kalendar_vypis"

# Headers for web scraping
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def fetch_calendar_html():
    """Fetch the calendar HTML from the website."""
    response = requests.get(CALENDAR_URL, headers=HEADERS)
    response.raise_for_status()
    return response.text


def check_spot_availability(soup, date, time):
    """
    Check availability by finding the specific lesson link in the correct date column.
    Robust version: Does not rely on <thead> or <tbody> tags being present.
    """
    
    # 1. FIND THE MAIN TABLE
    table = soup.find('table')
    if not table:
        print("DEBUG: No table found on the page.", flush=True)
        return False, None

    # 2. FIND THE COLUMN INDEX FOR THE DATE
    # FIX: Don't look for 'thead'. Just find the first row ('tr') in the table.
    rows = table.find_all('tr')
    if not rows:
        print("DEBUG: Table found, but it has no rows.", flush=True)
        return False, None

    header_row = rows[0] # Assume first row is header
    headers = header_row.find_all(['th', 'td']) # Sometimes headers use td instead of th
    
    col_index = None
    simple_date = ".".join(date.split('.')[:2]) # "29.12.2025" -> "29.12"
    
    for i, th in enumerate(headers):
        th_text = th.get_text(strip=True)
        if simple_date in th_text:
            col_index = i
            print(f"DEBUG: Found column for date '{simple_date}' at Index {i}. Header: '{th_text}'", flush=True)
            break
            
    if col_index is None:
        print(f"DEBUG: Date '{date}' (searched as '{simple_date}') not found in header.", flush=True)
        # Debug: Print headers to help diagnose
        debug_headers = [h.get_text(strip=True) for h in headers]
        print(f"DEBUG: Visible headers: {debug_headers}", flush=True)
        return False, None

    # 3. SCAN ALL REMAINING ROWS FOR THE LESSON
    print(f"DEBUG: Scanning {len(rows)-1} rows for lessons...", flush=True)

    # Start from index 1 (skip the header row)
    for row_idx, row in enumerate(rows[1:], start=1):
        cells = row.find_all('td')
        
        # Safety: Ensure row has enough cells
        if len(cells) <= col_index:
            continue
            
        target_cell = cells[col_index]
        
        # 4. FIND THE LESSON LINK (The Anchor)
        # Look for <a class="lekce-link"> inside this cell
        lessons = target_cell.find_all('a', class_='lekce-link')
        
        for lesson in lessons:
            # Check the TIME inside this lesson
            time_span = lesson.find('span', class_='cas-od')
            if not time_span:
                continue
                
            found_time = time_span.get_text(strip=True)
            
            # Compare found time with target time (e.g., "15:00")
            if found_time == time:
                print(f"DEBUG: MATCH! Found lesson at {time} in Row {row_idx}.", flush=True)
                
                # 5. GET CAPACITY
                full_text = lesson.get_text(strip=True)
                print(f"DEBUG: Raw Lesson Text: '{full_text}'", flush=True)
                
                # Regex search for "Number / Number"
                match = re.search(r'(\d+)\s*/\s*(\d+)', full_text)
                
                if match:
                    occupied = int(match.group(1))
                    total = int(match.group(2))
                    
                    is_free = occupied < total
                    print(f"DEBUG: Status -> Occupied: {occupied}, Total: {total}. Free spot? {is_free}", flush=True)
                    return is_free, f"{occupied} / {total}"
                else:
                    print("DEBUG: Lesson found, but could not parse capacity numbers.", flush=True)
                    return False, "Parse Error"

    print(f"DEBUG: Finished scanning. Lesson at {time} not found in this column.", flush=True)
    return False, None


def send_notification(date, time, chat_id):
    """Send a POST request to the webhook with notification data."""
    message = f"MÍSTO JE VOLNÉ! {date} v {time}"
    payload = {
        "value1": message,
        "chat_id": str(chat_id)
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"Found free spot! Notification sent for {date} at {time}", flush=True)
    except Exception as e:
        print(f"Error sending webhook: {e}", flush=True)


def main():
    """Main function to process CSV and check availability."""
    print(f"Fetching CSV from {SHEETS_CSV_URL}", flush=True)
    try:
        df = pd.read_csv(SHEETS_CSV_URL)
    except Exception as e:
        print(f"Critical Error reading CSV: {e}", flush=True)
        return
    
    # Validate required columns
    required_columns = ['Date', 'Time', 'Status', 'ChatID']
    for col in required_columns:
        if col not in df.columns:
            print(f"Error: CSV is missing required column: {col}", flush=True)
            return
    
    print(f"Fetching calendar from {CALENDAR_URL}", flush=True)
    try:
        html = fetch_calendar_html()
        soup = BeautifulSoup(html, 'html.parser')
    except Exception as e:
        print(f"Critical Error fetching calendar: {e}", flush=True)
        return
    
    # Process each row
    for index, row in df.iterrows():
        try:
            date = str(row['Date']).strip()
            time = str(row['Time']).strip()
            status = str(row['Status']).strip()
            chat_id = row['ChatID']
            
            if status.lower() != 'active':
                continue
            
            print(f"--- Checking availability for {date} at {time} (ChatID: {chat_id}) ---", flush=True)
            
            is_available, capacity_text = check_spot_availability(soup, date, time)
            
            if is_available:
                send_notification(date, time, chat_id)
            else:
                print(f"Status: Full or Not Found (Capacity: {capacity_text})", flush=True)
                
        except Exception as e:
            print(f"Error processing row {index}: {e}", flush=True)
            continue
    
    print("Processing complete.", flush=True)


if __name__ == "__main__":
    main()

import os
import re
import pandas as pd
import requests
import traceback
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
    Check availability in the CrossFit calendar.
    ROBUST VERSION: Works with the actual HTML structure.
    
    The calendar uses <td> elements with class "tb-sloupec" for columns.
    Each column contains lessons wrapped in <div class="lekce-wrapper-YYYY-MM-DD">.
    Inside lessons, there are <a> tags (not with class "lekce-link" but with class "jedna-lekce-vypis").
    """
    
    # 1. FIND THE HEADER ROW TO IDENTIFY COLUMNS
    print("DEBUG: Looking for the table header...", flush=True)
    
    # The header is in a table with class "wk-table-top"
    header_table = soup.find('table', class_='wk-table-top')
    if not header_table:
        print("DEBUG: Header table not found.", flush=True)
        return False, None
    
    # Find the header row
    header_row = header_table.find('tr', class_='wk-days')
    if not header_row:
        print("DEBUG: Header row not found.", flush=True)
        return False, None
    
    # Get all <th> elements (column headers)
    header_cells = header_row.find_all('th')
    
    # Format date from "29.12.2025" to "29.12" to match headers like "29.12.2025"
    simple_date = ".".join(date.split('.')[:2])
    
    col_index = None
    for i, cell in enumerate(header_cells):
        cell_text = cell.get_text(strip=True)
        if simple_date in cell_text:
            col_index = i
            print(f"DEBUG: Found column for date '{simple_date}' at Index {i}. Header text: '{cell_text}'", flush=True)
            break
    
    if col_index is None:
        print(f"DEBUG: Date '{simple_date}' not found in headers.", flush=True)
        headers_debug = [c.get_text(strip=True) for c in header_cells]
        print(f"DEBUG: Header content: {headers_debug}", flush=True)
        return False, None
    
    # 2. FIND THE DATA TABLE
    print("DEBUG: Looking for the data table...", flush=True)
    data_table = soup.find('table', class_='table-data')
    if not data_table:
        print("DEBUG: Data table not found.", flush=True)
        return False, None
    
    # 3. FIND THE CORRECT COLUMN IN THE DATA TABLE
    # The structure is: <tr class="tb-sloupce-dnu"> contains <td class="tb-sloupec"> for each day
    data_row = data_table.find('tr', class_='tb-sloupce-dnu')
    if not data_row:
        print("DEBUG: Data row (tb-sloupce-dnu) not found.", flush=True)
        return False, None
    
    # Get all <td> elements with class "tb-sloupec" (skip the first one which is for time labels)
    data_columns = data_row.find_all('td', class_='tb-sloupec')
    
    if col_index > len(data_columns):
        print(f"DEBUG: Column index {col_index} out of range. Found {len(data_columns)} data columns.", flush=True)
        return False, None
    
    # Adjust index because first <td> in header is the time column (width: 60px)
    # So we need to subtract 1 from col_index
    actual_col_index = col_index - 1
    
    if actual_col_index < 0 or actual_col_index >= len(data_columns):
        print(f"DEBUG: Adjusted column index {actual_col_index} is out of range.", flush=True)
        return False, None
    
    target_column = data_columns[actual_col_index]
    print(f"DEBUG: Analyzing column at index {actual_col_index}", flush=True)
    
    # 4. FIND ALL LESSONS IN THIS COLUMN
    # Lessons are inside <div class="lekce-wrapper-YYYY-MM-DD">
    # Each lesson is a <div class="jedna-lekce-vypis">
    lessons = target_column.find_all('div', class_='jedna-lekce-vypis')
    
    print(f"DEBUG: Found {len(lessons)} lessons in this column", flush=True)
    
    for idx, lesson in enumerate(lessons):
        # Find the time span
        time_span = lesson.find('span', class_='cas-od')
        
        if not time_span:
            continue
        
        found_time = time_span.get_text(strip=True)
        
        # Compare with target time
        if found_time == time:
            print(f"DEBUG: MATCH! Found lesson at {time} (lesson index {idx})", flush=True)
            
            # 6. GET CAPACITY
            # Look for the capacity info inside <div class="lekce-telo-obsazenost">
            capacity_div = lesson.find('div', class_='lekce-telo-obsazenost')
            
            if not capacity_div:
                print("DEBUG: Capacity div not found. Checking if lesson has no capacity display...", flush=True)
                # Some lessons might not show capacity if they have no limits
                # Check the full text for patterns
                full_text = lesson.get_text(strip=True)
                print(f"DEBUG: Raw Lesson Text: '{full_text}'", flush=True)
                
                # Try to find capacity pattern in full text
                match = re.search(r'(\d+)\s*/\s*(\d+)', full_text)
                
                if match:
                    occupied = int(match.group(1))
                    total = int(match.group(2))
                    is_free = occupied < total
                    print(f"DEBUG: Status -> Occupied: {occupied}, Total: {total}. Free spot? {is_free}", flush=True)
                    return is_free, f"{occupied} / {total}"
                else:
                    print("DEBUG: No capacity info found. Assuming spots available.", flush=True)
                    return True, "Unknown capacity"
            
            # Get the capacity text
            capacity_text = capacity_div.get_text(strip=True)
            print(f"DEBUG: Capacity text: '{capacity_text}'", flush=True)
            
            # Extract numbers using regex
            match = re.search(r'(\d+)\s*/\s*(\d+)', capacity_text)
            
            if match:
                occupied = int(match.group(1))
                total = int(match.group(2))
                is_free = occupied < total
                print(f"DEBUG: Status -> Occupied: {occupied}, Total: {total}. Free spot? {is_free}", flush=True)
                return is_free, f"{occupied} / {total}"
            else:
                print("DEBUG: Regex failed to parse capacity.", flush=True)
                return False, "Parse Error"
    
    print(f"DEBUG: Lesson at {time} not found in this column.", flush=True)
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
            print(f"Error processing row {index}:", flush=True)
            traceback.print_exc()
            continue
    
    print("Processing complete.", flush=True)


if __name__ == "__main__":
    main()

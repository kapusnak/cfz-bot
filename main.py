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


def parse_capacity(capacity_text):
    """
    Parse capacity text like "18 / 18" or "2 / 18" into (occupied, total).
    Returns (None, None) if parsing fails.
    """
    print(f"DEBUG parse_capacity: Input text = '{capacity_text}' (Type: {type(capacity_text)})", flush=True)
    match = re.search(r'(\d+)\s*/\s*(\d+)', capacity_text)
    if match:
        occupied_str = match.group(1)
        total_str = match.group(2)
        print(f"DEBUG parse_capacity: Regex matched - occupied_str='{occupied_str}', total_str='{total_str}'", flush=True)
        occupied = int(occupied_str)
        total = int(total_str)
        print(f"DEBUG parse_capacity: Converted - occupied={occupied} (Type: {type(occupied)}), total={total} (Type: {type(total)})", flush=True)
        return occupied, total
    print(f"DEBUG parse_capacity: No regex match found in '{capacity_text}'", flush=True)
    return None, None


def find_date_column(soup, target_date):
    """
    Find the column that contains the target date using "Header Index -> Body Cell" strategy.
    The date format in CSV is "29.12.2025", and website might have "Pondělí 29.12.2025".
    """
    # Step 1: Find the Table - Locate the main calendar table
    table = soup.find('table')
    if not table:
        # Fallback: Look for divs that contain the date (non-table structure)
        print(f"DEBUG: No table found, trying div fallback for date: {target_date}", flush=True)
        all_divs = soup.find_all('div')
        for div in all_divs:
            div_text = div.get_text(strip=True)
            if target_date in div_text:
                parent = div.find_parent()
                if parent:
                    return parent
                return div
        return None
    
    # Step 2: Find Column Index - Find the header (th) that contains the target date
    thead = table.find('thead')
    header_row = None
    
    if thead:
        header_row = thead.find('tr')
    else:
        # If no thead, look for first tr with th elements
        for tr in table.find_all('tr'):
            if tr.find('th'):
                header_row = tr
                break
    
    if not header_row:
        print(f"DEBUG: No header row found in table", flush=True)
        return None
    
    # Get all header cells (th) in the header row
    header_cells = header_row.find_all('th')
    
    # Find the header that contains the target date and get its index
    header_index = None
    for index, th in enumerate(header_cells):
        th_text = th.get_text(strip=True)
        if target_date in th_text:
            header_index = index
            print(f"DEBUG: Found date in header index {index}. Switching to body column.", flush=True)
            break
    
    if header_index is None:
        print(f"DEBUG: Could not find date '{target_date}' in any header cell", flush=True)
        return None
    
    # Step 3: Find Body Column - Find the corresponding td at the same index
    tbody = table.find('tbody')
    body_rows = []
    
    if tbody:
        body_rows = tbody.find_all('tr')
    else:
        # If no tbody, get all tr elements except the header row
        all_rows = table.find_all('tr')
        body_rows = [row for row in all_rows if row != header_row]
    
    # Return the first body row's td at the matching index
    for row in body_rows:
        row_cells = row.find_all('td')
        if header_index < len(row_cells):
            return row_cells[header_index]
    
    print(f"DEBUG: Could not find corresponding td at index {header_index} in body rows", flush=True)
    return None


def find_time_block_in_column(column_element, target_time):
    """
    Find the time block within a date column that starts with target_time.
    Returns the element containing the time block, or None.
    """
    if not column_element:
        return None
    
    # Look for elements containing time ranges like "15:00 - 16:00"
    # The target_time is just "15:00", so we need to match blocks starting with it
    all_elements = column_element.find_all(['div', 'span', 'p', 'td', 'li'])
    
    for elem in all_elements:
        text = elem.get_text(strip=True)
        # Check if text starts with the target time (e.g., "15:00")
        if text.startswith(target_time):
            return elem
        # Also check for time ranges like "15:00 - 16:00"
        if f"{target_time} -" in text or f"{target_time}-" in text:
            return elem
    
    return None


def check_spot_availability(soup, date, time):
    """
    Check if a spot is available for the given date and time.
    Iterates through ALL rows in tbody to find the lesson, skipping navigation rows.
    Returns (is_available, capacity_text) where is_available is True if spot is free.
    """
    # Step 1: Find Column Index - Find the header (th) that contains the target date
    table = soup.find('table')
    if not table:
        print(f"DEBUG: No table found", flush=True)
        return False, None
    
    thead = table.find('thead')
    header_row = None
    
    if thead:
        header_row = thead.find('tr')
    else:
        # If no thead, look for first tr with th elements
        for tr in table.find_all('tr'):
            if tr.find('th'):
                header_row = tr
                break
    
    if not header_row:
        print(f"DEBUG: No header row found in table", flush=True)
        return False, None
    
    # Get all header cells (th) in the header row
    header_cells = header_row.find_all('th')
    
    # Find the header that contains the target date and get its index
    col_index = None
    for index, th in enumerate(header_cells):
        th_text = th.get_text(strip=True)
        if date in th_text:
            col_index = index
            print(f"DEBUG: Found date in header index {index}.", flush=True)
            break
    
    if col_index is None:
        print(f"DEBUG: Could not find date '{date}' in any header cell", flush=True)
        return False, None
    
    # Step 2: Iterate Rows (The Fix) - Loop through EVERY row in the tbody
    tbody = table.find('tbody')
    if not tbody:
        print(f"DEBUG: No tbody found in table", flush=True)
        return False, None
    
    body_rows = tbody.find_all('tr')
    print(f"DEBUG: Found {len(body_rows)} rows in tbody. Iterating through all rows...", flush=True)
    
    # Loop through EVERY row (tr) in the tbody
    for row_index, row in enumerate(body_rows):
        # Get the cell (td) at the col_index
        row_cells = row.find_all('td')
        if col_index >= len(row_cells):
            print(f"DEBUG: Row {row_index} does not have enough cells. Continuing...", flush=True)
            continue
        
        cell = row_cells[col_index]
        
        # Target the Anchor: Search INSIDE this cell for a span with class 'cas-od'
        cas_od_spans = cell.find_all('span', class_='cas-od')
        
        if not cas_od_spans:
            print(f"DEBUG: Row {row_index} does not contain time {time}. Continuing...", flush=True)
            continue
        
        # Check Time: Check if that span's text matches the target time (e.g. "15:00")
        target_span = None
        for span in cas_od_spans:
            span_text = span.get_text(strip=True)
            if span_text == time:  # Exact match
                target_span = span
                print(f"DEBUG: Found 'span.cas-od' for time {time} in row {row_index}", flush=True)
                break
        
        # If found:
        if target_span:
            # Get the parent element of the span (the lesson container)
            lesson_box = target_span.find_parent()
            if not lesson_box:
                print("DEBUG: Could not find parent element of span.cas-od", flush=True)
                continue
            
            # Extract all text from this parent
            lesson_box_text = lesson_box.get_text(strip=True)
            print(f"DEBUG: Lesson Box Text: \"{lesson_box_text}\"", flush=True)
            
            # Parse numbers using Regex (\d+)\s*/\s*(\d+)
            match = re.search(r'(\d+)\s*/\s*(\d+)', lesson_box_text)
            if not match:
                print("DEBUG: No pattern match found in lesson box text", flush=True)
                return False, lesson_box_text
            
            # Extract the captured groups and convert to int
            occupied = int(match.group(1))
            total = int(match.group(2))
            print(f"DEBUG: Parsed: Occupied {occupied}, Total {total}", flush=True)
            
            # Check capacity (occupied < total) and return True/False
            is_available = occupied < total
            print(f"DEBUG: Logic check -> {occupied} < {total} is {is_available}", flush=True)
            
            # BREAK the loop (stop searching once found)
            return is_available, lesson_box_text
    
    # If the loop finishes without finding anything, print failure
    print(f"DEBUG: FAILED to find lesson with time '{time}' in any row after checking {len(body_rows)} rows.", flush=True)
    return False, None


def send_notification(date, time, chat_id):
    """Send a POST request to the webhook with notification data."""
    message = f"MÍSTO JE VOLNÉ! {date} v {time}"
    payload = {
        "value1": message,
        "chat_id": str(chat_id)
    }
    
    response = requests.post(WEBHOOK_URL, json=payload)
    response.raise_for_status()
    print(f"Found free spot! Notification sent for {date} at {time}", flush=True)


def main():
    """Main function to process CSV and check availability."""
    # Read CSV from URL
    print(f"Fetching CSV from {SHEETS_CSV_URL}", flush=True)
    df = pd.read_csv(SHEETS_CSV_URL)
    
    # Validate required columns
    required_columns = ['Date', 'Time', 'Status', 'ChatID']
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"CSV is missing required column: {col}")
    
    # Fetch calendar HTML once
    print(f"Fetching calendar from {CALENDAR_URL}", flush=True)
    html = fetch_calendar_html()
    soup = BeautifulSoup(html, 'html.parser')
    
    # Process each row
    for index, row in df.iterrows():
        try:
            date = str(row['Date']).strip()
            time = str(row['Time']).strip()
            status = str(row['Status']).strip()
            chat_id = row['ChatID']
            
            # Skip if status is not Active (optional check)
            if status.lower() != 'active':
                continue
            
            print(f"Checking availability for {date} at {time} (ChatID: {chat_id})", flush=True)
            
            # Check if spot is available
            is_available, capacity_text = check_spot_availability(soup, date, time)
            
            if is_available:
                print("Found free spot!", flush=True)
                print(f"Free spot found! Capacity: {capacity_text}", flush=True)
                send_notification(date, time, chat_id)
            else:
                print(f"No free spot available. Capacity: {capacity_text}", flush=True)
                
        except Exception as e:
            print(f"Error processing row {index}: {e}", flush=True)
            # Continue with next row
            continue
    
    print("Processing complete.", flush=True)


if __name__ == "__main__":
    main()


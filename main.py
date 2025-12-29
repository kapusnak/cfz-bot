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
    Find the column that contains the target date.
    The date format in CSV is "29.12.2025", and website might have "Pondělí 29.12.2025".
    Handles both div-based and table-based calendar structures.
    """
    # First, try to find in table headers/cells (common calendar structure)
    for th in soup.find_all(['th', 'td']):
        th_text = th.get_text(strip=True)
        if target_date in th_text:
            # Find the column - if it's a th, find the corresponding column
            # If it's a td, find its parent row and then the column
            parent_row = th.find_parent('tr')
            if parent_row:
                # Get all cells in the row to find the column index
                cells = parent_row.find_all(['th', 'td'])
                try:
                    col_index = cells.index(th)
                    # Find all rows and get the same column index
                    table = parent_row.find_parent(['table', 'tbody', 'thead'])
                    if table:
                        all_rows = table.find_all('tr')
                        # Return the first data row (skip header if needed)
                        for row in all_rows:
                            row_cells = row.find_all(['th', 'td'])
                            if col_index < len(row_cells):
                                return row_cells[col_index]
                except ValueError:
                    pass
            return th
    
    # Fallback: Look for divs that contain the date
    all_divs = soup.find_all('div')
    
    for div in all_divs:
        div_text = div.get_text(strip=True)
        # Check if the div text contains the target date
        if target_date in div_text:
            # Try to find the parent column or the column itself
            # Look for common calendar structures
            parent = div.find_parent()
            if parent:
                return parent
            return div
    
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
    Returns (is_available, capacity_text) where is_available is True if spot is free.
    """
    # Step 1: Find the date column
    date_column = find_date_column(soup, date)
    if not date_column:
        print(f"DEBUG: Could not find day column for date: {date}", flush=True)
        return False, None
    
    print("DEBUG: Found day column.", flush=True)
    
    # Step 2: Find the time block within that column
    time_block = find_time_block_in_column(date_column, time)
    if not time_block:
        print(f"DEBUG: Could not find time block for time: {time}", flush=True)
        return False, None
    
    print("DEBUG: Found time block.", flush=True)
    
    # CRITICAL: Print the HTML content of the found time block
    print("DEBUG: Block HTML: \n" + time_block.prettify(), flush=True)
    
    # Step-by-Step Parsing: Try to find div.lekce-telo-obsazeno
    capacity_element = time_block.find('div', class_='lekce-telo-obsazeno')
    if not capacity_element:
        print("DEBUG: CAPACITY CLASS NOT FOUND inside time block!", flush=True)
        return False, None
    
    # Extract & Clean - Get the text with strip=True
    text = capacity_element.get_text(strip=True)
    print(f"DEBUG: Raw capacity text: \"{text}\"", flush=True)
    
    # Regex Parsing - Use robust regex to find numbers
    match = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if not match:
        print("DEBUG: No pattern match found in text", flush=True)
        return False, text
    
    # Extract the captured groups and convert to int
    occupied = int(match.group(1))
    total = int(match.group(2))
    print(f"DEBUG: Parsed numbers -> Occupied: {occupied}, Total: {total}", flush=True)
    
    # Logic: Only send webhook if we successfully parsed numbers AND occupied < total
    is_available = occupied < total
    print(f"DEBUG: Logic check -> {occupied} < {total} is {is_available}", flush=True)
    
    return is_available, text


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


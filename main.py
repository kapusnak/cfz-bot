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
    Uses div-based column layout approach (Parent Container -> Child Search).
    Returns (is_available, capacity_text) where is_available is True if spot is free.
    """
    # Step 1: Find the Day Container
    # Search for an element (likely a div) that contains the text of the date
    date_element = None
    for div in soup.find_all('div'):
        div_text = div.get_text(strip=True)
        # Check if the div text contains the date (e.g., "29.12.2025" or "29.12")
        if date in div_text:
            date_element = div
            break
    
    if not date_element:
        print(f"DEBUG: Could not find element containing date: {date}", flush=True)
        return False, None
    
    # CRITICAL: Use recursive search or "find parent" approach to identify the MAIN container
    # Look for a div with a class like 'kalendar-den' or the closest parent div that wraps the date text
    day_container = None
    
    # First, try to find a parent with class 'kalendar-den'
    current = date_element
    for _ in range(10):  # Limit recursion depth
        parent = current.find_parent('div')
        if not parent:
            break
        # Check if parent has class 'kalendar-den'
        if parent.get('class') and 'kalendar-den' in parent.get('class', []):
            day_container = parent
            break
        current = parent
    
    # If not found by class, use the closest parent div that likely wraps the column
    if not day_container:
        # Find the parent div that contains both the date and likely the lessons
        # Look for a div that has the date element and potentially other lesson divs
        current = date_element
        for _ in range(10):  # Limit recursion depth
            parent = current.find_parent('div')
            if not parent:
                day_container = current  # Use current as fallback
                break
            # Check if this parent seems to be a container (has multiple children or specific structure)
            children = parent.find_all('div', recursive=False)
            if len(children) > 1:  # Has multiple direct children, likely a container
                day_container = parent
                break
            current = parent
    
    if not day_container:
        day_container = date_element.find_parent('div')
        if not day_container:
            day_container = date_element
    
    container_text = day_container.get_text(strip=True)[:100]  # First 100 chars for debug
    print(f"DEBUG: Found Day Container with text: {container_text}...", flush=True)
    
    # Step 2: Find the Time Block (Inside the Day Container)
    # Search ONLY inside the day_container
    # Look for any child div whose text starts with the target time
    time_block = None
    all_divs = day_container.find_all('div', recursive=True)
    
    for div in all_divs:
        # Use .get_text(strip=True) to clean the text before checking
        div_text = div.get_text(strip=True)
        # Check if text starts with the target time (e.g., "15:00")
        if div_text.startswith(time):
            time_block = div
            break
    
    if not time_block:
        print(f"DEBUG: FAILED to find block starting with '{time}' inside day container.", flush=True)
        print(f"DEBUG: Day Container HTML:\n{day_container.prettify()}", flush=True)
        return False, None
    
    time_block_text = time_block.get_text(strip=True)[:100]  # First 100 chars for debug
    print(f"DEBUG: Found Time Block: {time_block_text}...", flush=True)
    
    # Step 3: Check Capacity
    # Once the time block div is found, look inside it for div.lekce-telo-obsazeno
    capacity_element = time_block.find('div', class_='lekce-telo-obsazeno')
    if not capacity_element:
        print("DEBUG: CAPACITY CLASS NOT FOUND inside time block!", flush=True)
        print(f"DEBUG: Time Block HTML:\n{time_block.prettify()}", flush=True)
        return False, None
    
    # Extract & Clean - Get the text with strip=True
    text = capacity_element.get_text(strip=True)
    print(f"DEBUG: Raw capacity text: \"{text}\"", flush=True)
    
    # Step 4: Parse the "17 / 18" using the existing Regex logic
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


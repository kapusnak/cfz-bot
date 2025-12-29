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
    Uses lesson wrapper divs to identify the correct date column.
    """
    
    try:
        print(f"DEBUG: Checking availability for {date} at {time}", flush=True)
        
        # Convert date from "DD.MM.YYYY" to "YYYY-MM-DD" format for wrapper matching
        try:
            date_parts = date.split('.')
            if len(date_parts) == 3:
                formatted_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
                wrapper_class = f"lekce-wrapper-{formatted_date}"
                print(f"DEBUG: Looking for wrapper class: {wrapper_class}", flush=True)
            else:
                print(f"DEBUG: Invalid date format: {date}", flush=True)
                return False, None
        except Exception as e:
            print(f"DEBUG: Error formatting date: {e}", flush=True)
            return False, None
        
        # Find the wrapper div for the specific date
        wrapper_div = soup.find('div', class_=wrapper_class)
        
        if not wrapper_div:
            print(f"DEBUG: Wrapper div with class '{wrapper_class}' not found", flush=True)
            # Try to find any wrapper to see what's available
            all_wrappers = soup.find_all('div', class_=lambda x: x and 'lekce-wrapper-' in str(x))
            if all_wrappers:
                print(f"DEBUG: Found {len(all_wrappers)} wrapper divs in total", flush=True)
                sample_classes = [w.get('class') for w in all_wrappers[:3]]
                print(f"DEBUG: Sample wrapper classes: {sample_classes}", flush=True)
            return False, None
        
        print(f"DEBUG: Found wrapper div for date {date}", flush=True)
        
        # Find all lessons within this date's wrapper
        lessons = wrapper_div.find_all('div', class_='jedna-lekce-vypis')
        print(f"DEBUG: Found {len(lessons)} lessons in this date's wrapper", flush=True)
        
        if len(lessons) == 0:
            print("DEBUG: No lessons found in wrapper", flush=True)
            return False, None
        
        # Search for the lesson with matching time
        for idx, lesson in enumerate(lessons):
            # Find the time span
            time_span = lesson.find('span', class_='cas-od')
            
            if not time_span:
                continue
            
            found_time = time_span.get_text(strip=True)
            
            # Compare with target time
            if found_time == time:
                print(f"DEBUG: MATCH! Found lesson at {time} (lesson index {idx})", flush=True)
                
                # Get lesson activity name for additional context
                activity_link = lesson.find('a', class_='lekce-telo-aktivita')
                activity_name = activity_link.get_text(strip=True) if activity_link else "Unknown"
                print(f"DEBUG: Activity: {activity_name}", flush=True)
                
                # Check capacity
                # Look for <span class="cisla"> which contains the capacity
                capacity_span = lesson.find('span', class_='cisla')
                
                if not capacity_span:
                    print("DEBUG: 'cisla' span not found. Checking for capacity in full text...", flush=True)
                    # Check the full text for patterns
                    full_text = lesson.get_text(strip=True)
                    
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
                
                # Get the capacity text from the span
                capacity_text = capacity_span.get_text(strip=True)
                print(f"DEBUG: Capacity span text: '{capacity_text}'", flush=True)
                
                # Extract numbers using regex
                match = re.search(r'(\d+)\s*/\s*(\d+)', capacity_text)
                
                if match:
                    occupied = int(match.group(1))
                    total = int(match.group(2))
                    is_free = occupied < total
                    print(f"DEBUG: Status -> Occupied: {occupied}, Total: {total}. Free spot? {is_free}", flush=True)
                    return is_free, f"{occupied} / {total}"
                else:
                    print("DEBUG: Regex failed to parse capacity from 'cisla' span.", flush=True)
                    # Try full text as fallback
                    full_text = lesson.get_text(strip=True)
                    match = re.search(r'(\d+)\s*/\s*(\d+)', full_text)
                    if match:
                        occupied = int(match.group(1))
                        total = int(match.group(2))
                        is_free = occupied < total
                        print(f"DEBUG: Found in full text -> Occupied: {occupied}, Total: {total}. Free spot? {is_free}", flush=True)
                        return is_free, f"{occupied} / {total}"
                    return False, "Parse Error"
        
        print(f"DEBUG: Lesson at {time} not found in wrapper for {date}", flush=True)
        return False, None
        
    except Exception as e:
        print(f"DEBUG: Exception in check_spot_availability: {e}", flush=True)
        traceback.print_exc()
        return False, None


def send_notification(date, time, chat_id):
    """
    Send a POST request to the webhook with notification data.
    
    Sends both a formatted message (value1) for Telegram AND separate fields
    (date, time, chat_id) for easy Google Sheets searching in Make.com.
    """
    message = f"MÍSTO JE VOLNÉ! {date} v {time}"
    
    # Payload with separate fields for Make.com logic
    payload = {
        "value1": message,      # For Telegram notification
        "date": date,           # Separate field for Google Sheets search
        "time": time,           # Separate field for Google Sheets search
        "chat_id": str(chat_id) # Separate field for Google Sheets search
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"✓ Found free spot! Notification sent for {date} at {time} (ChatID: {chat_id})", flush=True)
    except Exception as e:
        print(f"✗ Error sending webhook: {e}", flush=True)


def is_in_past(date_str, time_str):
    """
    Check if the given date and time are in the past.
    
    Args:
        date_str: Date in format "DD.MM.YYYY"
        time_str: Time in format "HH:MM"
    
    Returns:
        True if the datetime is in the past, False otherwise
    """
    try:
        # Parse date and time
        date_parts = date_str.split('.')
        time_parts = time_str.split(':')
        
        if len(date_parts) != 3 or len(time_parts) != 2:
            print(f"DEBUG: Invalid date/time format: {date_str} {time_str}", flush=True)
            return False
        
        # Create datetime object
        lesson_datetime = datetime(
            year=int(date_parts[2]),
            month=int(date_parts[1]),
            day=int(date_parts[0]),
            hour=int(time_parts[0]),
            minute=int(time_parts[1])
        )
        
        # Compare with current time
        now = datetime.now()
        is_past = lesson_datetime < now
        
        if is_past:
            print(f"DEBUG: Skipping past lesson: {date_str} at {time_str}", flush=True)
        
        return is_past
        
    except Exception as e:
        print(f"DEBUG: Error checking if date is in past: {e}", flush=True)
        # If we can't parse the date, don't skip it (safer to check than to miss)
        return False


def main():
    """Main function to process CSV and check availability."""
    print("=" * 60, flush=True)
    print("CrossFit Zlín Spot Checker - Starting", flush=True)
    print("=" * 60, flush=True)
    
    print(f"Fetching CSV from Google Sheets...", flush=True)
    try:
        df = pd.read_csv(SHEETS_CSV_URL)
        print(f"✓ CSV loaded: {len(df)} rows found", flush=True)
    except Exception as e:
        print(f"✗ Critical Error reading CSV: {e}", flush=True)
        return
    
    # Validate required columns
    required_columns = ['Date', 'Time', 'Status', 'ChatID']
    for col in required_columns:
        if col not in df.columns:
            print(f"✗ Error: CSV is missing required column: {col}", flush=True)
            print(f"Available columns: {list(df.columns)}", flush=True)
            return
    
    print(f"Fetching calendar from {CALENDAR_URL}...", flush=True)
    try:
        html = fetch_calendar_html()
        soup = BeautifulSoup(html, 'html.parser')
        print("✓ Calendar HTML fetched and parsed", flush=True)
    except Exception as e:
        print(f"✗ Critical Error fetching calendar: {e}", flush=True)
        return
    
    # Process each row
    active_count = 0
    skipped_past = 0
    found_spots = 0
    
    for index, row in df.iterrows():
        try:
            date = str(row['Date']).strip()
            time = str(row['Time']).strip()
            status = str(row['Status']).strip()
            chat_id = row['ChatID']
            
            # Skip inactive rows
            if status.lower() != 'active':
                continue
            
            active_count += 1
            
            # Skip past lessons
            if is_in_past(date, time):
                skipped_past += 1
                continue
            
            print(f"\n--- Checking: {date} at {time} (ChatID: {chat_id}) ---", flush=True)
            
            is_available, capacity_text = check_spot_availability(soup, date, time)
            
            if is_available:
                found_spots += 1
                send_notification(date, time, chat_id)
            else:
                print(f"Status: Full or Not Found (Capacity: {capacity_text})", flush=True)
                
        except Exception as e:
            print(f"✗ Error processing row {index}:", flush=True)
            traceback.print_exc()
            continue
    
    print("\n" + "=" * 60, flush=True)
    print("Processing complete!", flush=True)
    print(f"  Active rows checked: {active_count}", flush=True)
    print(f"  Past lessons skipped: {skipped_past}", flush=True)
    print(f"  Free spots found: {found_spots}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()

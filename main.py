import os
import re
import pandas as pd
import requests
import traceback
from bs4 import BeautifulSoup
from datetime import datetime

# --- NASTAVENÍ ---
SHEETS_CSV_URL = os.getenv('SHEETS_CSV_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
CALENDAR_URL = "https://crossfitzlin.inrs.cz/rs/kalendar_vypis"

# False = Ostrý provoz (hlásí realitu)
# True = Simulace (pro testování 'waiting' stavu)
SIMULATE_FULL_LESSON = False

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

if not SHEETS_CSV_URL or not WEBHOOK_URL:
    raise ValueError("Chybí environment variables (SHEETS_CSV_URL nebo WEBHOOK_URL)")

def fetch_calendar_html():
    response = requests.get(CALENDAR_URL, headers=HEADERS)
    response.raise_for_status()
    return response.text

def check_spot_availability(soup, date, time):
    """
    Hledá lekci a kontroluje kapacitu.
    Vrací: (Je_Volno, Text_Kapacity)
    """
    try:
        # 1. Najdi wrapper pro konkrétní den
        try:
            date_parts = date.split('.')
            formatted_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
            wrapper_class = f"lekce-wrapper-{formatted_date}"
        except:
            return False, "Chybný formát data"
        
        wrapper_div = soup.find('div', class_=wrapper_class)
        if not wrapper_div: 
            return False, "Lekce pro tento den nenalezeny"
        
        # 2. Najdi lekci v konkrétním čase
        lessons = wrapper_div.find_all('div', class_='jedna-lekce-vypis')
        target_lesson = None
        
        for lesson in lessons:
            time_span = lesson.find('span', class_='cas-od')
            if time_span and time_span.get_text(strip=True) == time:
                target_lesson = lesson
                break
        
        if not target_lesson: 
            return False, "Lekce v tento čas neexistuje"

        # 3. PŘÍSNÁ KONTROLA KAPACITY
        # Hledáme text, ale bereme v potaz JEN čísla, kde je celková kapacita 18.
        
        # Nejdřív zkusíme span 'cisla'
        capacity_span = target_lesson.find('span', class_='cisla')
        full_text = target_lesson.get_text(" ", strip=True)
        text_to_search = capacity_span.get_text(strip=True) if capacity_span else full_text
        
        # Najdeme všechny dvojice čísel (X / Y)
        matches = re.findall(r'(\d+)\s*/\s*(\d+)', text_to_search)
        
        for occupied_str, total_str in matches:
            occ = int(occupied_str)
            tot = int(total_str)
            
            # === ZDE JE TA POJISTKA ===
            if tot == 18:
                print(f"DEBUG: Nalezena validní kapacita: {occ}/{tot}", flush=True)
                
                if SIMULATE_FULL_LESSON:
                    return False, "18/18 (Simulace)"
                
                return occ < tot, f"{occ}/{tot}"
            else:
                # Našli jsme třeba datum 19/1 nebo rok 2026/01 -> Ignorujeme
                print(f"DEBUG: Ignoruji číslo {occ}/{tot} (není to kapacita 18)", flush=True)
        
        return False, "Kapacita nenalezena"

    except Exception as e:
        print(f"Error checking availability: {e}", flush=True)
        traceback.print_exc()
        return False, "Error"

def send_notification(date, time, chat_id, message, status_update):
    payload = {
        "value1": message,
        "date": date,
        "time": time,
        "chat_id": str(chat_id),
        "status_update": status_update
    }
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print(f"✓ Webhook sent! Status: {status_update} | Date: {date} {time}", flush=True)
    except Exception as e:
        print(f"✗ Error sending webhook: {e}", flush=True)

def is_in_past(date_str, time_str):
    try:
        date_parts = date_str.split('.')
        time_parts = time_str.split(':')
        lesson_datetime = datetime(
            year=int(date_parts[2]), month=int(date_parts[1]), day=int(date_parts[0]),
            hour=int(time_parts[0]), minute=int(time_parts[1])
        )
        if lesson_datetime < datetime.now():
            print(f"DEBUG: Skipping past lesson: {date_str} {time_str}", flush=True)
            return True
        return False
    except:
        return False

def main():
    print("==========================================", flush=True)
    print("CrossFit Zlín Checker - START", flush=True)
    if SIMULATE_FULL_LESSON: print("!!! SIMULATION MODE ACTIVE !!!", flush=True)
    print("==========================================", flush=True)

    try:
        df = pd.read_csv(SHEETS_CSV_URL)
        print(f"✓ CSV loaded ({len(df)} rows)", flush=True)
    except Exception as e:
        print(f"✗ Error reading CSV: {e}", flush=True)
        return

    try:
        html = fetch_calendar_html()
        soup = BeautifulSoup(html, 'html.parser')
    except Exception as e:
        print(f"✗ Error fetching calendar: {e}", flush=True)
        return

    for index, row in df.iterrows():
        try:
            date = str(row['Date']).strip()
            time = str(row['Time']).strip()
            status = str(row['Status']).strip().lower()
            chat_id = row['ChatID']

            if status not in ['active', 'waiting']: continue
            if not SIMULATE_FULL_LESSON and is_in_past(date, time): continue

            print(f"--- Checking: {date} {time} [{status}] ---", flush=True)
            is_free, cap_text = check_spot_availability(soup, date, time)

            # 1. VOLNO
            if is_free:
                msg = f"🟢 NA LEKCI SE UVOLNILO MÍSTO! {date} v {time} ({cap_text}) https://crossfitzlin.inrs.cz/rs/kalendar_vypis"
                send_notification(date, time, chat_id, msg, "done")
            
            # 2. PLNO -> Active (Začínáme hlídat)
            elif not is_free and status == 'active':
                # Posíláme info jen pokud jsme našli validní kapacitu (obsahuje /18)
                if "/18" in cap_text:
                    msg = f"🟡 Lekce je plná ({cap_text}). Přepínám na hlídání."
                    send_notification(date, time, chat_id, msg, "waiting")
                else:
                    print(f"DEBUG: Nenalezena validní kapacita ({cap_text}), neměním status.", flush=True)

            # 3. PLNO -> Waiting (Už hlídáme)
            elif not is_free and status == 'waiting':
                print(f"Stále plno ({cap_text}). Mlčím.", flush=True)

        except Exception as e:
            print(f"✗ Error row {index}: {e}", flush=True)

if __name__ == "__main__":
    main()

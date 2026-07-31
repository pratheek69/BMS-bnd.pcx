import os
import re
import json
import time
import subprocess
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
from curl_cffi import requests as cffi_requests

class Config:
    """Central configuration for the BookMyShow Scraper."""
    DATES = ["20260801"] 
    VENUE_CODE = "PRHN"
    EVENT_CODE = "ET00502600" 
    STATE_FILE = "seats.json"
    MAX_RUNTIME_SECONDS = (5 * 3600) + (45 * 60) 
    NTFY_URL = "https://ntfy.sh/bnd_pcx"
    
    # --- FILTERS ---
    TARGET_TIME_RANGE = ("11:00", "18:00") 
    TARGET_SCREEN_ATTRIBUTE = "PCX SCREEN"

    PROXIES = {
        "http": "socks5://127.0.0.1:40000",
        "https": "socks5://127.0.0.1:40000"
    }

    GET_HEADERS = {
        "Host": "in.bookmyshow.com",
        "Content-Type": "application/json",
        "X-Latitude": "17.385044",
        "X-Subregion-Code": "HYD",
        "X-App-Code": "MOBAND2",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
        "X-App-Version": "18.2.3",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive"
    }

    POST_HEADERS = {
        "Host": "services-in.bookmyshow.com",
        "X-Timeout": "10",
        "X-Latitude": "17.385044",
        "X-Subregion-Code": "HYD",
        "X-App-Code": "MOBAND2",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
        "X-App-Version": "18.2.3",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate"
    }


class Utils:
    """Helper functions for formatting and notification."""
    
    @staticmethod
    def is_within_time_range(show_time_str: str, time_range: Optional[tuple]) -> bool:
        if not time_range:
            return True
        try:
            show_time = datetime.strptime(show_time_str.strip(), "%I:%M %p").time()
            start_time = datetime.strptime(time_range[0], "%H:%M").time()
            end_time = datetime.strptime(time_range[1], "%H:%M").time()
            return start_time <= show_time <= end_time
        except ValueError as e:
            print(f"    -> ⚠️ Time parsing error for '{show_time_str}': {e}")
            return True

    @staticmethod
    def humanize_date(date_str: str) -> str:
        dt = datetime.strptime(date_str, "%Y%m%d")
        day = dt.day
        if 11 <= (day % 100) <= 13:
            suffix = 'th'
        else:
            suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
        return f"{day}{suffix} {dt.strftime('%B')}"

    @staticmethod
    def trigger_ntfy(message: str, booking_url: str, priority: str = "urgent") -> None:
        print(f"\n[!] ALERTING VIA NTFY:\n{message}")
        try:
            resp = requests.post(
                Config.NTFY_URL,
                data=message.encode("utf-8"),
                headers={
                    "Priority": priority,
                    "Title": "PCX BND Alert",
                    "Click": booking_url
                },
                timeout=10
            )
            print(f"    -> Ntfy ping sent! Status: {resp.status_code}")
        except Exception as e:
            print(f"    -> Ntfy ping failed: {e}")


class GitStateManager:
    """Handles all Git operations and JSON state synchronization."""
    def __init__(self, state_file: str):
        self.state_file = state_file

    def quiet_git_pull(self) -> None:
        subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
        subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

    def quiet_git_push(self) -> bool:
        res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
        return res.returncode == 0

    def read_local_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                print(f"[STATE] ⚠️ JSON Error reading state: {e}")
        return {}

    def load_state(self) -> Dict[str, Any]:
        self.quiet_git_pull()
        return self.read_local_state()

    def save_state(self, deltas: Dict[str, Any], commit_msg: str) -> Dict[str, Any]:
        for attempt in range(3):
            self.quiet_git_pull()
            latest_state = self.read_local_state()
            latest_state.update(deltas)
                
            with open(self.state_file, "w") as f:
                json.dump(latest_state, f, indent=2)
                
            subprocess.run(["git", "add", self.state_file], capture_output=True, check=False)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            
            if self.state_file in status.stdout:
                print(f"[GIT] Committing changes to {self.state_file} (Attempt {attempt+1})...")
                subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
                
                if self.quiet_git_push():
                    print("[GIT] Successfully pushed merged state to repository.")
                    return latest_state
                
                print(f"[GIT] Push attempt {attempt+1} failed. Retrying merge...")
                time.sleep(2)
            else:
                print("[GIT] Merged state is identical to remote. Nothing to push.")
                return latest_state
                
        print("[GIT] ❌ Failed to push after 3 attempts. Local memory updated with last known merge.")
        return latest_state


class BookMyShowScraper:
    """Core orchestrator for fetching and parsing seat layouts."""
    def __init__(self):
        self.use_warp = False 
        self.state_manager = GitStateManager(Config.STATE_FILE)

    def toggle_warp(self) -> None:
        if self.use_warp:
            print("    -> 🚨 [IP ROTATION] WARP ON -> OFF (Switching to Runner IP)...")
            subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
            time.sleep(2)
        else:
            print("    -> 🚨 [IP ROTATION] WARP OFF -> ON (Switching to Cloudflare Proxy)...")
            subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
            time.sleep(8) 
        self.use_warp = not self.use_warp

    def make_request(self, method: str, url: str, max_retries: int = 3, **kwargs) -> Optional[Any]:
        for attempt in range(1, max_retries + 1):
            proxies = Config.PROXIES if self.use_warp else None
            try:
                func = cffi_requests.get if method.upper() == 'GET' else cffi_requests.post
                resp = func(url, proxies=proxies, impersonate="chrome", timeout=15, **kwargs)
                
                print(f"    -> Status: {resp.status_code} (Using WARP: {self.use_warp})")
                
                # FIXED: Now handles both Rate Limits (429) AND Security Blocks (403)
                if resp.status_code in [403, 429]:
                    print(f"    -> ⚠️ Blocked/Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                    if attempt < max_retries:
                        self.toggle_warp()
                        print("    -> Retrying request...")
                        continue
                    print("    -> ❌ Max retries reached.")
                return resp
                
            except Exception as e:
                error_msg = str(e).split('first for more details.')[0].strip()
                print(f"    -> ⚠️ Network exception on attempt {attempt}: {error_msg}")
                if attempt < max_retries:
                    # FIXED: Turn WARP ON if standard IP is blackholed, or OFF if proxy dies
                    if not self.use_warp:
                        print("    -> 🚨 Runner IP blackholed! Turning WARP ON to escape...")
                        self.toggle_warp()
                    else:
                        print("    -> 🚨 Proxy dead! Forcing WARP OFF to recover...")
                        self.toggle_warp()
                    print("    -> Retrying request...")
                    continue
        return None

    def fetch_sessions(self) -> List[Dict[str, str]]:
        sessions = []
        for date_code in Config.DATES:
            url = f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary?eventCode={Config.EVENT_CODE}&dateCode={date_code}&regionCode=HYD&venueCode={Config.VENUE_CODE}"
            resp = self.make_request('GET', url, headers=Config.GET_HEADERS)
            if not resp or resp.status_code != 200:
                continue
                
            try:
                shows = resp.json().get("data", {}).get("showTimes", [])
                
                valid_shows = []
                for s in shows:
                    time_ok = Utils.is_within_time_range(s.get("showTime", ""), Config.TARGET_TIME_RANGE)
                    attr_ok = (Config.TARGET_SCREEN_ATTRIBUTE is None) or (s.get("attributes") == Config.TARGET_SCREEN_ATTRIBUTE)
                    
                    if time_ok and attr_ok:
                        valid_shows.append(s)
                
                for show in valid_shows:
                    sessions.append({
                        "sessionId": show["sessionId"],
                        "dateCode": show["showDateCode"],
                        "time": show["showTime"]
                    })
                    
            except Exception as e:
                # FIXED: Silences the "Expecting value" error when movies aren't listed yet
                if "Expecting value" not in str(e):
                    print(f"    -> JSON Parse error for {date_code}: {e}")
                
        return sessions

    def fetch_seat_layout(self, session_id: str) -> str:
        url = "https://services-in.bookmyshow.com/doTrans.aspx"
        payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={Config.VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
        
        print(f"    -> [POST] {url} (Session: {session_id})")
        resp = self.make_request('POST', url, headers=Config.POST_HEADERS, data=payload)
        
        if resp and resp.status_code == 200:
            try:
                return resp.json().get("BookMyShow", {}).get("strData", "")
            except Exception as e:
                print(f"    -> Exception parsing layout {session_id}: {e}")
        return ""

    @staticmethod
    def parse_layout(str_data: str) -> Dict[str, List[str]]:
        if not str_data: return {}
        
        parts = str_data.split("||")
        rows_data = parts[1] if len(parts) > 1 else parts[0]
        
        available_seats = {}
        for row in rows_data.split("|"):
            if ":" not in row: continue
            
            elements = row.split(":")
            row_letter = elements[1]
            
            # FIXED: Capture group (\d+) moved to the end to grab the REAL seat number!
            seats = [m.group(1) for s in elements[2:] if (m := re.search(r"[A-Z]1\d+\+(\d+)", s))]
            
            if seats:
                available_seats[row_letter] = seats
                
        return available_seats

    def run(self):
        start_time = time.time()
        print("="*50 + "\n🚀 STARTING BMS ADVANCE BOOKING TRACKER\n" + "="*50)
        print(f"Tracking Venue: {Config.VENUE_CODE} | Event: {Config.EVENT_CODE}")
        print(f"Time Range Filter: {Config.TARGET_TIME_RANGE}")
        
        state = self.state_manager.load_state()
        is_first_run = not bool(state)
        shows_are_live = not is_first_run
        
        if shows_are_live:
            print(f"[STATE] Loaded existing state for {len(state)} sessions. Moving to seat tracking.")
        else:
            print("[STATE] No previous state found. Monitoring for advance booking drop...")

        cycle_count = 1
        while (time.time() - start_time) < Config.MAX_RUNTIME_SECONDS:
            print(f"\n{'='*50}\n🔄 POLLING CYCLE {cycle_count}\n{'='*50}")
            
            state = self.state_manager.load_state()
            deltas = {}
            
            # ---------------------------------------------------------
            # PHASE 1: WAITING FOR SHOWS TO BE LISTED
            # ---------------------------------------------------------
            target_sessions = self.fetch_sessions()
            total_sessions = len(target_sessions)
            
            if total_sessions == 0:
                print(f"    -> 🔴 Not listed yet. Sleeping for 30 seconds before checking again...")
                time.sleep(30) 
                cycle_count += 1
                continue 
                
            if not shows_are_live:
                print(f"\n    -> 🟢🚨 ADVANCE BOOKINGS JUST OPENED! Found {total_sessions} shows matching filters!")
                shows_are_live = True
                
                booking_url = f"https://in.bookmyshow.com/buytickets/{Config.EVENT_CODE}-hyderabad/movie-hyd-{Config.VENUE_CODE}-MT/"
                msg = (f"🚨 ADVANCE BOOKING OPEN! 🚨\n\n"f"The movie is now live at {Config.VENUE_CODE}!\n"f"{total_sessions} valid shows have been added.\n\n"f"Book IMMEDIATELY:\n{booking_url}")
                Utils.trigger_ntfy(msg, booking_url, priority="max")
                
                time.sleep(10) 

            # ---------------------------------------------------------
            # PHASE 2: STANDARD SEAT TRACKING 
            # ---------------------------------------------------------
            for index, session in enumerate(target_sessions, 1):
                s_id, s_date, s_time = session["sessionId"], session["dateCode"], session["time"]
                
                print(f"\n[{index}/{total_sessions}] Checking Session {s_id} ({s_date} @ {s_time})\n    -> Sleeping for 30s...")
                time.sleep(30)
                
                # FIXED: The Emergency Brake to prevent GitHub limits from crashing the script
                if (time.time() - start_time) > Config.MAX_RUNTIME_SECONDS:
                    print("    -> ⏳ Mid-cycle time limit reached! Breaking out to ensure safe shutdown.")
                    break
                
                str_data = self.fetch_seat_layout(s_id)
                if not str_data:
                    print("    -> Error: Received empty strData.")
                    continue
                    
                current_seats = self.parse_layout(str_data)
                current_total = sum(len(seats) for seats in current_seats.values())
                print(f"    -> Parse successful. Available Seats: {current_total}")
                
                session_state = state.setdefault(s_id, {"date": s_date, "time": s_time, "total": 0, "rows": {}})
                previous_total = session_state.get("total", 0)
                previous_rows = session_state.get("rows", {})
                
                newly_unblocked = 0
                unblocked_rows = []
                
                for row, seats in current_seats.items():
                    new_seats = set(seats) - set(previous_rows.get(row, []))
                    if new_seats:
                        newly_unblocked += len(new_seats)
                        unblocked_rows.append(row)
                
                if newly_unblocked > 0:
                    print(f"    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked} new seats!")
                    
                    if not is_first_run:
                        if newly_unblocked >= 6:
                            booking_url = f"https://in.bookmyshow.com/movies/HYD/seat-layout/{Config.EVENT_CODE}/{Config.VENUE_CODE}/{s_id}/{s_date}"
                            msg = (f"🎬 BND Seats Available!\n\n{', '.join(sorted(unblocked_rows))} rows unblocked\n"f"{newly_unblocked} seats available\n{Utils.humanize_date(s_date)} • {s_time}\n\nBook now:\n{booking_url}")
                            Utils.trigger_ntfy(msg, booking_url)
                        else:
                            print(f"    -> 🟡 Less than 6 seats unblocked. Skipping notification.")
                    
                    session_state.update({"rows": current_seats, "total": current_total})
                    deltas[s_id] = session_state

                elif current_total < previous_total:
                    print(f"    -> 🔴 Seats booked. Total dropped from {previous_total} to {current_total}.")
                    session_state.update({"rows": current_seats, "total": current_total})
                    deltas[s_id] = session_state
                else:
                    print("    -> ⚪ No changes detected.")

            if deltas:
                print("\n[STATE] Cycle finished. Changes detected, merging and saving to Git...")
                state = self.state_manager.save_state(deltas, f"State update at cycle {cycle_count}")
            else:
                print("\n[STATE] Cycle finished. No changes detected.")
                
            if is_first_run and shows_are_live:
                is_first_run = False
                print("[STATE] First run baseline has been successfully established.")
                
            cycle_count += 1
            
        print(f"\n🏁 Time limit reached. Gracefully shutting down.")


if __name__ == "__main__":
    scraper = BookMyShowScraper()
    scraper.run()
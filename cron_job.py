import os
import gzip
import shutil
import json
import csv
import requests
from datetime import datetime

# ====================================================
# 1. ENVIRONMENT VARIABLES & SETTING ARRANGEMENTS
# ====================================================
# Uses your production authentication profile
USERNAME = os.environ.get("NROD_USERNAME", "tom.smith.199202@gmail.com")
PASSWORD = os.environ.get("NROD_PASSWORD", "Wilkinson.02")

# Pivot Strategy: Target TODAY's live calendar footprint instead of tomorrow
target_date_obj = datetime.now()
target_date_str = target_date_obj.strftime("%Y-%m-%d")
today_day_index = target_date_obj.weekday()

# Static production file names for your live display app to target
OUTPUT_CSV = "gwr_timetable_today.csv"
OUTPUT_JSON = "gwr_timetable_today.json"

GZ_TARGET = "schedule.json.gz"
INPUT_FILE = "schedule.json"

# Master GWR Service Group Lookup Table
SERVICE_GROUP_LOOKUP = {
    "25370002": "EF01", "25375002": "EF02", "25390003": "EF03", "25392003": "EF03",
    "25397003": "EF04", "25396002": "EF04", "25506005": "EF05", "25507005": "EF05",
    "25517005": "EF05", "25516005": "EF06", "25513005": "EF07", "25514005": "EF07",
    "25518007": "EF07", "25519007": "EF07", "25524005": "EF07", "25508006": "EF08",
    "25509007": "EF08", "25510006": "EF08", "25511007": "EF08", "25521007": "EF09",
    "25522007": "EF09", "25460001": "EF10", "25466001": "EF10", "25467001": "EF10",
    "25484001": "EF10", "25486001": "EF10", "25488001": "EF10", "25462001": "EF11",
    "25480001": "EF11", "25482001": "EF11", "25473001": "EF12", "25474001": "EF12",
    "25476001": "EF12", "25477001": "EF12", "25478001": "EF12", "25479001": "EF12",
    "25470001": "EF13", "25471001": "EF13", "25485001": "EF13"
}

# ====================================================
# 2. AUTOMATED LIVE DOWNLOAD & UNZIP ENGINE
# ====================================================
print(f"--- Starting Download for Target Date: {target_date_str} ---")
URL = "https://datafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full"

try:
    response = requests.get(URL, auth=(USERNAME, PASSWORD), stream=True)
    if response.status_code == 200:
        with open(GZ_TARGET, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("Successfully downloaded compressed database archive.")
        
        print("Decompressing binary streaming layers into raw schedule entries...")
        with gzip.open(GZ_TARGET, "rb") as f_in:
            with open(INPUT_FILE, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        print("Extraction layer complete.")
    else:
        print(f"❌ Critical Connection Refusal: Server returned status {response.status_code}")
        exit(1)
except Exception as e:
    print(f"❌ Network socket connection error: {e}")
    exit(1)

# ====================================================
# 3. PARSING, OVERLAY FILTERING & ENGINE COMPILATION
# ====================================================
print("Scanning dataset records against day rules...")
active_schedules = {}

with open(INPUT_FILE, "r", encoding="utf-8") as file:
    for line in file:
        if not line.strip(): 
            continue
        try: 
            data = json.loads(line)
        except json.JSONDecodeError: 
            continue

        if "JsonScheduleV1" in data:
            schedule = data["JsonScheduleV1"]
            
            # Keep only Great Western Railway operations
            if schedule.get("atoc_code") != "GW": 
                continue
                
            segments = schedule.get("schedule_segment", {})
            new_segments = schedule.get("new_schedule_segment", {})
            locations = segments.get("schedule_location", [])
            if not locations: 
                continue

            # --- RULE 1: DATE FOOTPRINT RANGE VALIDATION ---
            start_date = schedule.get("schedule_start_date", "9999-12-31")
            end_date = schedule.get("schedule_end_date", "1900-01-01")
            if not (start_date <= target_date_str <= end_date):
                continue

            # --- RULE 2: DAY OF THE WEEK RUN INTERPRETATION ---
            days_run = schedule.get("schedule_days_runs", "0000000")
            if days_run[today_day_index] == "0":
                continue

            # --- HEADCODE RECOVERY & CLEANING ---
            headcode = new_segments.get("signalling_id", "").strip()
            if not headcode or headcode.isdigit():
                backup = segments.get("signalling_id", "").strip()
                if backup and not backup.isdigit(): 
                    headcode = backup
            if not headcode or headcode.isdigit():
                cif = segments.get("CIF_headcode", "").strip()
                if cif: 
                    headcode = cif
            
            # Reject freight, empty stock moves, or non-passenger markers
            if not headcode or headcode.startswith(('0', '3', '5')): 
                continue

            # --- RULE 3: STP OVERLAY PRIORITIZATION ENGINE ---
            stp = schedule.get("CIF_stp_indicator", "P")
            unique_key = (schedule.get("CIF_train_uid"), headcode)
            ranks = {"P": 1, "O": 2, "A": 2, "C": 3}
            
            if unique_key in active_schedules:
                if ranks.get(stp, 1) >= ranks.get(active_schedules[unique_key]["stp"], 1):
                    active_schedules[unique_key] = {"stp": stp, "headcode": headcode, "segments": segments, "locations": locations}
            else:
                active_schedules[unique_key] = {"stp": stp, "headcode": headcode, "segments": segments, "locations": locations}

# ====================================================
# 4. STRUCTURED DATA EXPORT TARGETS
# ====================================================
json_out = {}
total_saved = 0

with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as csv_file:
    writer = csv.DictWriter(csv_file, fieldnames=["TRAIN_ID", "HEADCODE", "SERVICE_GROUP", "ORIGIN_DEP_TIME", "ROUTE_START", "ROUTE_END"])
    writer.writeheader()

    for key, info in active_schedules.items():
        # Cleanly bypass cancellations
        if info["stp"] == "C": 
            continue

        locs, segs = info["locations"], info["segments"]
        dep = locs[0].get("public_departure") or locs[0].get("departure", "0000")
        f_dep = f"{dep[:2]}:{dep[2:4]}" if len(dep) >= 4 else "00:00"
        
        svc_code = segs.get("CIF_train_service_code", "Unknown").strip()
        svc_group = SERVICE_GROUP_LOOKUP.get(svc_code, "Unmapped")
        
        train_uid = key[0]  # This is the unique identifier (e.g., GW5031)

        # Layout Target A: Flat Spreadsheet rows
        writer.writerow({
            "TRAIN_ID": train_uid, "HEADCODE": info["headcode"], "SERVICE_GROUP": svc_group,
            "ORIGIN_DEP_TIME": f_dep, "ROUTE_START": locs[0].get("tiploc_code"), "ROUTE_END": locs[-1].get("tiploc_code")
        })

        # Fix: Save using the unique TRAIN_ID as the key so headcodes never overwrite each other!
        json_out[train_uid] = {
            "headcode": info["headcode"],
            "serviceGroup": svc_group, 
            "origin": locs[0].get("tiploc_code"),
            "destination": locs[-1].get("tiploc_code"), 
            "departure": f_dep
        }
        total_saved += 1

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(json_out, f, indent=2)

print(f"🎉 Run Complete. Successfully tracked {total_saved} GWR services live for today ({target_date_str}).")
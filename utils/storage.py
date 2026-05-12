import os
import json
import uuid
import datetime

# ---------------------------------------------------------------------------
# Supabase client setup
# ---------------------------------------------------------------------------
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
_sb = None

if _SUPABASE_URL and _SUPABASE_KEY:
    try:
        from supabase import create_client
        _sb = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    except Exception as exc:
        print(f"[storage] Supabase init failed, falling back to local files: {exc}")

# ---------------------------------------------------------------------------
# Local filesystem setup
# ---------------------------------------------------------------------------
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
EVENTS_DIR = os.path.join(DATA_DIR, "events")
if not _sb:
    os.makedirs(EVENTS_DIR, exist_ok=True)

def generate_id():
    return str(uuid.uuid4())[:8]

def get_events():
    if _sb:
        res = _sb.table("events").select("id, name, created, filename, fighter_count").order("created", desc=True).execute()
        return res.data if res.data else []
        
    # Local fallback
    events = []
    if os.path.exists(EVENTS_DIR):
        for eid in os.listdir(EVENTS_DIR):
            info_file = os.path.join(EVENTS_DIR, eid, "info.json")
            if os.path.exists(info_file):
                try:
                    with open(info_file, "r") as f:
                        events.append(json.load(f))
                except Exception:
                    pass
    events.sort(key=lambda x: x.get("created", ""), reverse=True)
    return events

def get_event(event_id):
    if _sb:
        ev_res = _sb.table("events").select("id, name, created, filename, fighter_count").eq("id", event_id).execute()
        if not ev_res.data:
            return None
        event = ev_res.data[0]
        
        days_res = _sb.table("event_days").select("id, name, type, created").eq("event_id", event_id).order("created").execute()
        event["days"] = days_res.data if days_res.data else []
        return event

    info_file = os.path.join(EVENTS_DIR, event_id, "info.json")
    if os.path.exists(info_file):
        with open(info_file, "r") as f:
            event = json.load(f)
            
            days_dir = os.path.join(EVENTS_DIR, event_id, "days")
            days = []
            if os.path.exists(days_dir):
                for df in os.listdir(days_dir):
                    if df.endswith(".json"):
                        try:
                            with open(os.path.join(days_dir, df), "r") as dfile:
                                day_data = json.load(dfile)
                                days.append({
                                    "id": df.replace(".json", ""),
                                    "name": day_data.get("name", "Unnamed Day"),
                                    "type": day_data.get("type", "categories"),
                                    "created": day_data.get("created", "")
                                })
                        except Exception:
                            pass
            event["days"] = sorted(days, key=lambda x: x.get("created", ""))
            return event
    return None

def create_event(name, fighters, filename=""):
    event_id = generate_id()
    created_ts = datetime.datetime.now().isoformat()
    
    if _sb:
        data = {
            "id": event_id,
            "name": name,
            "created": created_ts,
            "filename": filename,
            "fighter_count": len(fighters),
            "fighters": fighters
        }
        _sb.table("events").insert(data).execute()
        return event_id

    # Local fallback
    event_dir = os.path.join(EVENTS_DIR, event_id)
    os.makedirs(event_dir, exist_ok=True)
    
    info = {
        "id": event_id,
        "name": name,
        "created": created_ts,
        "filename": filename,
        "fighter_count": len(fighters)
    }
    with open(os.path.join(event_dir, "info.json"), "w") as f:
        json.dump(info, f, indent=2)
        
    with open(os.path.join(event_dir, "registrations.json"), "w") as f:
        json.dump(fighters, f, indent=2)
        
    return event_id

def get_event_registrations(event_id):
    if _sb:
        res = _sb.table("events").select("fighters").eq("id", event_id).execute()
        if res.data and res.data[0].get("fighters"):
            return res.data[0]["fighters"]
        return []

    # Local fallback
    reg_file = os.path.join(EVENTS_DIR, event_id, "registrations.json")
    if os.path.exists(reg_file):
        with open(reg_file, "r") as f:
            return json.load(f)
    return []

def add_event_day(event_id, day_name, day_type, rows, raw_draws=None):
    day_id = generate_id()
    created_ts = datetime.datetime.now().isoformat()
    
    if _sb:
        data = {
            "id": day_id,
            "event_id": event_id,
            "name": day_name,
            "type": day_type,
            "created": created_ts,
            "rows": rows
        }
        if raw_draws:
            data["raw_draws"] = raw_draws
        _sb.table("event_days").insert(data).execute()
        return day_id
        
    # Local fallback
    event_dir = os.path.join(EVENTS_DIR, event_id)
    days_dir = os.path.join(event_dir, "days")
    os.makedirs(days_dir, exist_ok=True)
    
    day_data = {
        "id": day_id,
        "name": day_name,
        "type": day_type,
        "created": created_ts,
        "rows": rows
    }
    if raw_draws:
        day_data["raw_draws"] = raw_draws
        
    with open(os.path.join(days_dir, f"{day_id}.json"), "w") as f:
        json.dump(day_data, f, indent=2)
        
    return day_id

def get_event_day(event_id, day_id):
    if _sb:
        res = _sb.table("event_days").select("*").eq("id", day_id).eq("event_id", event_id).execute()
        if res.data:
            return res.data[0]
        return None

    # Local fallback
    day_file = os.path.join(EVENTS_DIR, event_id, "days", f"{day_id}.json")
    if os.path.exists(day_file):
        with open(day_file, "r") as f:
            return json.load(f)
    return None

import os
import json
import livef1

def main():
    # 1. Login to F1TV via LiveF1 (uses F1_USERNAME and F1_PASSWORD env vars)
    try:
        livef1.login()
    except Exception as e:
        print(f"Authentication failed: {e}")
        # Proceed to query session structure fallback if offline
    
    # 2. Get the latest or current live session
    try:
        # Fetch data for current active or latest session
        session = livef1.get_session("latest")
        session_info = session.get_data("SessionInfo")
        lap_count = session.get_data("LapCount")
        
        # 3. Parse qualifying stage / race lap details
        session_name = session_info.get("Name", "Unknown")
        path = session_info.get("Path", "")
        
        # Extract Q1/Q2/Q3 if qualifying session
        phase = "N/A"
        if "Qualifying" in session_name or "Shootout" in session_name:
            if "Q3" in path or "Q3" in str(session_info):
                phase = "Q3"
            elif "Q2" in path or "Q2" in str(session_info):
                phase = "Q2"
            else:
                phase = "Q1"
                
        status_payload = {
            "timestamp": livef1.get_timestamp() if hasattr(livef1, "get_timestamp") else "",
            "session_name": session_name,
            "session_type": session_info.get("Type", "Unknown"),
            "phase": phase,
            "lap_info": {
                "current_lap": lap_count.get("CurrentLap", None) if lap_count else None,
                "total_laps": lap_count.get("TotalLaps", None) if lap_count else None
            },
            "status": session_info.get("ArchiveStatus", {}).get("Status", "Live")
        }
    except Exception as err:
        print(f"Error fetching live feed: {err}")
        # Fallback payload structure if session is not active
        status_payload = {
            "session_name": "No Active Session",
            "phase": "N/A",
            "lap_info": {"current_lap": None, "total_laps": None},
            "status": "Inactive"
        }

    # 4. Save to data/race_status.json
    output_path = os.path.join(os.path.dirname(__file__), "../data/race_status.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(status_payload, f, indent=2)
        
    print(f"Successfully wrote payload to data/race_status.json: {status_payload}")

if __name__ == "__main__":
    main()

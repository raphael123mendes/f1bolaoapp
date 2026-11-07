import os
import json
import urllib.request

def fetch_fallback_status():
    """Fallback fetcher using Jolpica F1 API (No Auth / Free / No Cloud Blocking)"""
    url = "https://api.jolpica.net/ergast/f1/current/next.json"
    fallback_payload = {
        "session_name": "No Live Session Active",
        "session_type": "Upcoming Race",
        "phase": "N/A",
        "lap_info": {"current_lap": None, "total_laps": None},
        "status": "Scheduled"
    }

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            if races:
                next_race = races[0]
                fallback_payload["session_name"] = next_race.get("raceName", "Grand Prix")
                fallback_payload["circuit"] = next_race.get("Circuit", {}).get("circuitName")
                fallback_payload["date"] = next_race.get("date")
                fallback_payload["time"] = next_race.get("time")
    except Exception as e:
        print(f"Fallback fetch notice: {e}")

    return fallback_payload


def main():
    email = os.environ.get("F1_USERNAME")
    password = os.environ.get("F1_PASSWORD")

    status_payload = None

    # Try LiveF1 session query if credentials exist
    if email and password:
        try:
            import livef1
            print("Attempting F1TV live stream authentication...")
            livef1.login(force=True)
            
            session = livef1.get_session("latest")
            session_info = session.get_data("SessionInfo")
            lap_count = session.get_data("LapCount")

            if session_info:
                session_name = session_info.get("Name", "Unknown")
                path = str(session_info.get("Path", ""))
                
                phase = "N/A"
                if "Qualifying" in session_name:
                    if "Q3" in path:
                        phase = "Q3"
                    elif "Q2" in path:
                        phase = "Q2"
                    else:
                        phase = "Q1"

                status_payload = {
                    "session_name": session_name,
                    "session_type": session_info.get("Type", "Unknown"),
                    "phase": phase,
                    "lap_info": {
                        "current_lap": lap_count.get("CurrentLap") if lap_count else None,
                        "total_laps": lap_count.get("TotalLaps") if lap_count else None
                    },
                    "status": "Live"
                }
        except Exception as err:
            print(f"LiveF1 attempt did not complete ({err}). Switching to standard schedule status...")

    # Use fallback if LiveF1 failed or off-track
    if not status_payload:
        status_payload = fetch_fallback_status()

    # Write output file
    output_path = os.path.join(os.path.dirname(__file__), "../data/race_status.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(status_payload, f, indent=2)

    print("Successfully written to data/race_status.json")


if __name__ == "__main__":
    main()

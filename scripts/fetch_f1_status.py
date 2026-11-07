import os
import json
import datetime
import urllib.request

def fetch_fallback_status():
    """Fallback fetcher using Ergast/Jolpica F1 API (No Auth / Free)"""
    urls = [
        "https://api.jolpica.org/ergast/f1/current/next.json",
        "https://ergast.com/api/f1/current/next.json"
    ]
    
    fallback_payload = {
        "session_name": "No Active Session",
        "session_type": "Upcoming Race",
        "phase": "N/A",
        "lap_info": {"current_lap": None, "total_laps": None},
        "status": "Scheduled"
    }

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
                if races:
                    next_race = races[0]
                    fallback_payload["session_name"] = next_race.get("raceName", "Grand Prix")
                    fallback_payload["circuit"] = next_race.get("Circuit", {}).get("circuitName")
                    fallback_payload["date"] = next_race.get("date")
                    fallback_payload["time"] = next_race.get("time")
                    break
        except Exception as e:
            print(f"Notice: Fallback URL {url} unreachable ({e}). Trying next...")

    return fallback_payload


def main():
    email = os.environ.get("F1_USERNAME")
    password = os.environ.get("F1_PASSWORD")

    status_payload = None
    current_year = datetime.datetime.now().year

    # Try LiveF1 session query if credentials exist
    if email and password:
        try:
            import livef1
            print("Authenticating with F1TV credentials...")
            livef1.login()

            # Retrieve current season schedule to find active/latest session key
            season_data = livef1.get_season(season=current_year)
            
            # If season object is retrieved, attempt loading recent session
            if hasattr(season_data, 'sessions') and len(season_data.sessions) > 0:
                latest_session_key = season_data.sessions[-1].get('session_key')
                session = livef1.get_session(session_key=latest_session_key)
                
                session_info = session.get_data("Session_Info") or session.get_data("SessionInfo")
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
            print(f"Notice: LiveF1 session lookup did not complete ({err}). Switching to schedule fallback...")

    # Fallback to standard schedule feed if LiveF1 returned no live data or failed
    if not status_payload:
        status_payload = fetch_fallback_status()

    # Always write output JSON file
    output_path = os.path.join(os.path.dirname(__file__), "../data/race_status.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(status_payload, f, indent=2)

    print(f"Successfully updated data/race_status.json: {status_payload}")


if __name__ == "__main__":
    main()

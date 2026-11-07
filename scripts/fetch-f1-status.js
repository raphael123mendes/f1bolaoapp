const fs = require('fs');
const path = require('path');

// Replace with your API endpoint or live stream proxy
const API_URL = 'https://openf1.org/api/v1/sessions?latest=true'; 

async function getRaceStatus() {
  try {
    const response = await fetch(API_URL);
    if (!response.ok) throw new Error(`HTTP Error: ${response.status}`);
    const data = await response.json();
    const session = data[0] || {};

    // Standardized JSON output structure
    const statusPayload = {
      timestamp: new Date().toISOString(),
      session_name: session.session_name || "Unknown", // e.g., Qualifying, Race
      session_type: session.session_type || "Unknown",
      circuit_name: session.circuit_short_name || "Unknown",
      country_name: session.country_name || "Unknown",
      // Phase detection
      phase: session.session_name === 'Qualifying' 
        ? (session.qualifying_phase || 'Q1') // Q1, Q2, or Q3
        : 'N/A',
      // Lap counting (during race sessions)
      lap_info: {
        current_lap: session.current_lap || null,
        total_laps: session.total_laps || null
      },
      is_live: session.is_live || false
    };

    const outputPath = path.join(__dirname, '../data/race_status.json');
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(statusPayload, null, 2));
    
    console.log("Successfully wrote status to data/race_status.json:", statusPayload);
  } catch (error) {
    console.error("Failed to fetch F1 session status:", error.message);
    process.exit(1);
  }
}

getRaceStatus();

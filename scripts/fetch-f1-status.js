const fs = require('fs');
const path = require('path');

// OpenF1 base endpoint for sessions
const API_URL = 'https://api.openf1.org/v1/sessions?session_key=latest';

async function getRaceStatus() {
  try {
    const response = await fetch(API_URL);
    if (!response.ok) {
      throw new Error(`HTTP Error: ${response.status} - ${response.statusText}`);
    }

    const data = await response.json();
    if (!data || data.length === 0) {
      throw new Error("No session data returned from OpenF1 API.");
    }

    // OpenF1 returns an array of matching session objects
    const session = data[0];

    // Standardized payload extraction
    const statusPayload = {
      timestamp: new Date().toISOString(),
      session_name: session.session_name || "Unknown",
      session_type: session.session_type || "Unknown",
      circuit_name: session.circuit_short_name || "Unknown",
      country_name: session.country_name || "Unknown",
      date_start: session.date_start || null,
      date_end: session.date_end || null,
      // Infer phase for Qualifying sessions
      phase: (session.session_name && session.session_name.includes('Qualifying'))
        ? (session.qualifying_phase || 'Q1/Q2/Q3')
        : 'N/A',
      is_live: checkIsLive(session.date_start, session.date_end)
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

// Helper to determine if session is currently within active time frame
function checkIsLive(startStr, endStr) {
  if (!startStr || !endStr) return false;
  const now = new Date();
  const start = new Date(startStr);
  const end = new Date(endStr);
  return now >= start && now <= end;
}

getRaceStatus();

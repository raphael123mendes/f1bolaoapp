"""
f1_config.py — Shared config loader for all F1 Fantasy scripts
===============================================================
Every script does:

    from f1_config import cfg, SCRIPT_DIR

Then reads settings like:

    cfg["league_id"]
    cfg["save_folder"]
    cfg["whatsapp"]          # list of {phone, apikey}
    cfg["textmebot_api_key"]
    etc.

config.json must be in the same folder as this file.
"""

import os
import json

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"\n⚠  config.json not found at {CONFIG_PATH}\n"
            f"   Make sure config.json is in the same folder as your scripts."
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # Resolve relative file paths to absolute (relative to script folder)
    for key in ("google_creds_file",):
        if key in data and not os.path.isabs(data[key]):
            data[key] = os.path.join(SCRIPT_DIR, data[key])

    # Allow GitHub Actions (or any CI) to redirect the save folder via env var
    env_folder = os.environ.get("F1_SAVE_FOLDER")
    if env_folder:
        data["save_folder"] = env_folder

    return data

# Load once at import time — all scripts share the same object
cfg = load_config()

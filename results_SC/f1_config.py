"""
f1_config.py — Shared config loader for all F1 Fantasy scripts
===============================================================
Every script does:

    from f1_config import cfg, SCRIPT_DIR

Then reads settings like:

    cfg["league_id"]
    cfg["save_folder"]
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

    # save_folder is relative to this folder (default: results_SC/data)
    folder = data.get("save_folder", "data")
    if not os.path.isabs(folder):
        data["save_folder"] = os.path.join(SCRIPT_DIR, folder)

    # Allow GitHub Actions (or any CI) to redirect the save folder via env var
    env_folder = os.environ.get("F1_SAVE_FOLDER")
    if env_folder:
        data["save_folder"] = env_folder

    return data

# Load once at import time — all scripts share the same object
cfg = load_config()

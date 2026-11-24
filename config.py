"""
config.py
---------
Configuration management for QueryMate.
Loads environment variables using python-dotenv and provides centralized settings.
Never hardcodes secrets. All sensitive values are fetched from .env at runtime.
"""

import os

try:
    from dotenv import load_dotenv
    # Load key-value pairs from .env into runtime environment (if .env exists)
    load_dotenv()
except ImportError:
    # Fallback gracefully if python-dotenv is not yet installed
    pass

# ==============================================================================
# LLM (Gemini) Settings
# ==============================================================================
# GEMINI_API_KEY: Secret authentication key for the Google Gemini API.
# Strips any accidental surrounding quotes or trailing whitespace
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip("'\"")

# GEMINI_MODEL: Specifies the model version to query (defaults to gemini-3.8-flash)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip().strip("'\"")

# LLM temperature: Set to 0.0 for deterministic, reproducible SQL query generation
TEMPERATURE = 0.0

# ==============================================================================
# Database (MySQL) Settings
# ==============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# DB_NAMES: Comma-separated list of supported databases (defaults to chinook,sakila,world,employees)
_raw_db_names = os.getenv("DB_NAMES", "chinook,sakila,world,employees")
DB_NAMES = [name.strip() for name in _raw_db_names.split(",") if name.strip()]
if not DB_NAMES:
    DB_NAMES = ["chinook", "sakila", "world", "employees"]

# DB_NAME: Primary / default database (defaults to the first in DB_NAMES or 'chinook')
_raw_db_name = os.getenv("DB_NAME", "").strip().strip("'\"")
if _raw_db_name:
    DB_NAME = _raw_db_name
    if DB_NAME not in DB_NAMES:
        DB_NAMES.insert(0, DB_NAME)
else:
    DB_NAME = DB_NAMES[0]

# ==============================================================================
# Safety & Query Limits
# ==============================================================================
# Maximum number of rows returned to prevent UI lag and memory overflow
MAX_ROWS = 500


if __name__ == "__main__":
    # Test script to verify config loading without exposing confidential secrets
    print("=== QueryMate Configuration Loaded ===")
    print(f"Gemini Model : {GEMINI_MODEL}")
    print(f"Gemini API   : {'[Configured]' if GEMINI_API_KEY else '[Not Set - please configure in .env]'}")
    print(f"DB Host      : {DB_HOST}:{DB_PORT}")
    print(f"DB User      : {DB_USER}")
    print(f"Default DB   : {DB_NAME}")
    print(f"All DBs      : {DB_NAMES}")
    print(f"DB Password  : {'[Configured]' if DB_PASSWORD else '[Empty / Not Set]'}")
    print(f"Max Rows     : {MAX_ROWS}")
    print("======================================")

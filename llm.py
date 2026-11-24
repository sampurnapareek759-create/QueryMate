import json
import os
import re
import time

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL

# 15-second timeout (value is in milliseconds) so the app never hangs forever
client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=15000))

# Optional backup model (set in .env). Free-tier quotas are counted per model,
# so a second model gives you a second daily allowance.
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()

# Cache of question -> SQL so repeated questions cost no API quota. Disable with QUERYMATE_CACHE=0
CACHE_ON = os.getenv("QUERYMATE_CACHE", "1") != "0"
CACHE_FILE = ".sql_cache.json"

SYSTEM = """You are an expert MySQL analyst. Convert the user's question into ONE MySQL SELECT query.
Rules:
- Use only the tables and columns in the schema. Never invent names.
- Output only the raw SQL. No explanation, no markdown fences.
- Read-only: never write INSERT/UPDATE/DELETE/DDL.
- Use explicit JOINs with the foreign keys given (or obvious matching key columns), and give columns readable aliases.
- If the question is vague, make the most reasonable assumption and still write the query.
- Output CANNOT_ANSWER ONLY if the question has nothing to do with the data in the schema
  (for example, asking about weather or general knowledge). Never use it for questions the tables can answer."""

# Short per-database hints that help the model join the right tables
HINTS = {
    "employees": (
        "Employee names are employees.first_name/last_name; gender is 'M' or 'F'. "
        "Departments are in departments(dept_no, dept_name) linked to employees through dept_emp. "
        "salaries and titles are HISTORY tables: the current row has to_date = '9999-01-01'. "
        "Managers are in dept_manager."
    ),
    "sakila": (
        "Revenue is payment.amount. Rentals are in rental, linked to inventory and film. "
        "Film categories come from film_category -> category. Actors come from film_actor -> actor."
    ),
    "world": (
        "Population is in country.Population and city.Population. Languages are in countrylanguage "
        "(IsOfficial is 'T' or 'F'). city.CountryCode links to country.Code."
    ),
    "chinook": (
        "Sales are in Invoice and InvoiceLine (revenue = UnitPrice * Quantity). "
        "Genres come from Track.GenreId -> Genre. Artists come from Album.ArtistId -> Artist."
    ),
}


def _guess_db(schema: str) -> str:
    """The pipeline may not pass the database name, so recognise it from the schema text."""
    s = (schema or "").lower()
    if "dept_emp" in s:
        return "employees"
    if "film_actor" in s:
        return "sakila"
    if "countrylanguage" in s:
        return "world"
    if "invoiceline" in s:
        return "chinook"
    return ""


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:sql)?", "", text, flags=re.I).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _is_daily_quota(err: Exception) -> bool:
    s = str(err)
    return "PerDay" in s or "per day" in s.lower()


def _is_temporary(err: Exception) -> bool:
    """503 (overloaded), per-minute 429 and timeouts are worth retrying. Daily quota is not."""
    if _is_daily_quota(err):
        return False
    s = str(err)
    return any(k in s for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "timed out", "Timeout"))


def _call(model: str, prompt: str) -> str:
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0),
    )
    return _clean(resp.text)


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    except Exception:
        pass


def _generate_with_models(prompt: str) -> str:
    """Fail fast: with a backup model set, try main once then backup; otherwise try main twice."""
    has_backup = bool(FALLBACK_MODEL) and FALLBACK_MODEL != GEMINI_MODEL
    tries = 1 if has_backup else 2
    last_err = None
    for attempt in range(tries):
        try:
            return _call(GEMINI_MODEL, prompt)
        except Exception as e:
            last_err = e
            if _is_daily_quota(e) or not _is_temporary(e):
                break
            if attempt + 1 < tries:
                time.sleep(1)

    if has_backup:
        try:
            return _call(FALLBACK_MODEL, prompt)
        except Exception as e:
            last_err = e

    if _is_daily_quota(last_err):
        raise RuntimeError(
            "Daily free-tier quota reached for the Gemini model. Wait for the daily reset, or set "
            "GEMINI_MODEL / GEMINI_FALLBACK_MODEL in .env to another model with free quota left."
        )
    raise last_err


def generate_sql(question, schema, previous_sql=None, error=None, db_name=None, **_ignored) -> str:
    db = (db_name or _guess_db(schema)).lower()

    # Cache only first attempts (retries after a failure always ask Gemini fresh)
    cache_key = f"{db}|{(question or '').strip().lower()}"
    use_cache = CACHE_ON and not (previous_sql and error)
    if use_cache:
        cached = _load_cache().get(cache_key)
        if cached:
            return cached

    prompt = f"SCHEMA:\n{schema}\n"
    if HINTS.get(db):
        prompt += f"\nHINTS: {HINTS[db]}\n"
    prompt += f"\nQUESTION: {question}"
    if previous_sql and error:
        prompt += (
            f"\n\nYour previous query failed.\nPREVIOUS SQL: {previous_sql}\n"
            f"ERROR: {error}\nReturn a corrected query."
        )

    sql = _generate_with_models(prompt)

    if use_cache and sql and sql.strip().upper() != "CANNOT_ANSWER":
        cache = _load_cache()
        cache[cache_key] = sql
        _save_cache(cache)
    return sql

"""
pipeline.py
-----------
End-to-end execution pipeline for QueryMate.
Coordinates the database, LLM, and validator layers:
  1. Retrieves the cached database schema (db.py).
  2. Generates candidate SQL from the user question via Gemini (llm.py).
  3. Validates query safety and enforces row limits (validator.py).
  4. Executes the query and returns a Pandas DataFrame (db.py).
  5. Implements Self-Healing / Auto-Correction: If execution or validation fails,
     retries ONCE by passing the error back to Gemini to generate a fix.
"""

import time
from typing import Any, Dict, Optional
import pandas as pd

from config import DB_NAME
from db import get_schema, run_query
from llm import generate_sql
from validator import validate_sql, UnsafeSQLError


def answer(
    question: str,
    db_name: Optional[str] = None,
    custom_schema: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Processes a natural language question through the complete text-to-SQL pipeline
    for the selected database.
    
    Steps:
      1. Schema Retrieval (specific to target database)
      2. LLM SQL Generation (informed of database name and schema)
      3. Safety Validation
      4. Database Execution (connected to target database)
      5. Single-Attempt Self-Healing Retry on failure
      
    Args:
        question: The user's natural language question.
        db_name: (Optional) Target database name (defaults to DB_NAME).
        custom_schema: (Optional) Pre-cached schema string to skip database schema lookup.
        
    Returns:
        dict with keys:
          - "sql"     : (str) The SQL query executed (or attempted).
          - "df"      : (pd.DataFrame) Results DataFrame (empty on error).
          - "retried" : (bool) True if self-healing retry was triggered, else False.
          - "error"   : (str or None) Error message if failed, None if successful.
          - "timings" : (dict) Breakdown of elapsed seconds for schema, llm, validation, sql, total.
          - "db_name" : (str) Database name queried.
    """
    start_total = time.perf_counter()
    timings = {
        "schema": 0.0,
        "llm": 0.0,
        "validation": 0.0,
        "sql": 0.0,
        "total": 0.0,
    }

    target_db = db_name if db_name else DB_NAME

    # 0. Check for empty question
    if not question or not question.strip():
        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": "",
            "df": pd.DataFrame(),
            "retried": False,
            "error": "Question cannot be empty.",
            "timings": timings,
            "db_name": target_db,
        }

    # 1. Fetch compact database schema for target database
    t0 = time.perf_counter()
    try:
        schema = custom_schema if custom_schema else get_schema(target_db)
        timings["schema"] = round(time.perf_counter() - t0, 3)
    except Exception as exc:
        timings["schema"] = round(time.perf_counter() - t0, 3)
        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": "",
            "df": pd.DataFrame(),
            "retried": False,
            "error": f"Database schema retrieval failed for '{target_db}': {exc}",
            "timings": timings,
            "db_name": target_db,
        }

    # 2. First attempt: Generate SQL from natural language
    t0 = time.perf_counter()
    try:
        raw_sql = generate_sql(question, schema, db_name=target_db)
        timings["llm"] += round(time.perf_counter() - t0, 3)
    except Exception as exc:
        timings["llm"] += round(time.perf_counter() - t0, 3)
        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": "",
            "df": pd.DataFrame(),
            "retried": False,
            "error": f"LLM SQL generation failed: {exc}",
            "timings": timings,
            "db_name": target_db,
        }

    # Handle case where question cannot be answered by the schema
    if raw_sql.strip().upper() == "CANNOT_ANSWER":
        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": "CANNOT_ANSWER",
            "df": pd.DataFrame(),
            "retried": False,
            "error": f"This question cannot be answered using the available schema for '{target_db}'.",
            "timings": timings,
            "db_name": target_db,
        }

    # 3. Attempt validation and execution (First Attempt)
    first_attempt_sql = raw_sql
    try:
        t0 = time.perf_counter()
        validated_sql = validate_sql(first_attempt_sql)
        timings["validation"] += round(time.perf_counter() - t0, 4)

        t0 = time.perf_counter()
        df = run_query(validated_sql, db_name=target_db)
        timings["sql"] += round(time.perf_counter() - t0, 3)

        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": validated_sql,
            "df": df,
            "retried": False,
            "error": None,
            "timings": timings,
            "db_name": target_db,
        }
    except Exception as first_error:
        # Failure detected. Initiate Self-Correction Retry.
        error_message = str(first_error)

    # 4. Second attempt: Self-Healing Retry
    # Send the failed SQL and the exact error back to Gemini for correction
    try:
        t0 = time.perf_counter()
        retry_raw_sql = generate_sql(
            question,
            schema,
            db_name=target_db,
            previous_sql=first_attempt_sql,
            error=error_message,
        )
        timings["llm"] += round(time.perf_counter() - t0, 3)

        if retry_raw_sql.strip().upper() == "CANNOT_ANSWER":
            timings["total"] = round(time.perf_counter() - start_total, 3)
            return {
                "sql": "CANNOT_ANSWER",
                "df": pd.DataFrame(),
                "retried": True,
                "error": "The model determined this question cannot be answered after analyzing the retry error.",
                "timings": timings,
                "db_name": target_db,
            }

        # Validate the corrected query
        t0 = time.perf_counter()
        validated_retry_sql = validate_sql(retry_raw_sql)
        timings["validation"] += round(time.perf_counter() - t0, 4)
        
        # Execute the corrected query on the target database
        t0 = time.perf_counter()
        df = run_query(validated_retry_sql, db_name=target_db)
        timings["sql"] += round(time.perf_counter() - t0, 3)

        timings["total"] = round(time.perf_counter() - start_total, 3)
        return {
            "sql": validated_retry_sql,
            "df": df,
            "retried": True,
            "error": None,
            "timings": timings,
            "db_name": target_db,
        }

    except Exception as second_error:
        timings["total"] = round(time.perf_counter() - start_total, 3)
        # If retry also fails, return the failure details gracefully
        return {
            "sql": retry_raw_sql if "retry_raw_sql" in locals() else first_attempt_sql,
            "df": pd.DataFrame(),
            "retried": True,
            "error": f"Query failed after self-healing retry: {second_error}",
            "timings": timings,
            "db_name": target_db,
        }


if __name__ == "__main__":
    # Self-test block to verify pipeline operation
    print("=== Testing QueryMate Pipeline (pipeline.py) ===")
    
    test_q = "How many customers are there?"
    print(f"\nRunning pipeline for: '{test_q}' (database: chinook)")
    result = answer(test_q, db_name="chinook")

    print("\nPipeline Result Dictionary:")
    print(f"- SQL Generated : {result['sql']}")
    print(f"- Retried       : {result['retried']}")
    print(f"- Error Status  : {result['error']}")
    print(f"- Rows Returned : {len(result['df'])}")
    print("\nDataFrame Preview:")
    print(result["df"])
    print("=" * 48)

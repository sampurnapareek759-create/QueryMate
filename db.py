"""
db.py
-----
Database abstraction layer for QueryMate.
Provides functions to:
  1. get_conn()    : Establish a secure MySQL database connection.
  2. get_schema()  : Inspect information_schema to build a compact schema summary for the LLM.
  3. run_query()   : Execute validated SQL and return results as a Pandas DataFrame, ensuring
                     the connection is always safely closed.
"""

from typing import Optional
import ssl

# ==============================================================================
# Python 3.14+ Compatibility Shim:
# Modern Python removed obsolete TLS constants (PROTOCOL_TLSv1, PROTOCOL_TLSv1_1).
# We provide fallbacks so mysql-connector can initialize without error.
# ==============================================================================
for _proto in ("PROTOCOL_TLSv1", "PROTOCOL_TLSv1_1", "PROTOCOL_TLSv1_2"):
    if not hasattr(ssl, _proto):
        setattr(ssl, _proto, getattr(ssl, "PROTOCOL_TLS", None))

import mysql.connector
from mysql.connector import Error as MySQLError
import pandas as pd

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, DB_NAMES

# In-memory dictionary cache for database schemas keyed by database name
# Format: { "chinook": "...schema...", "sakila": "...schema..." }
_SCHEMA_CACHE: dict[str, str] = {}


def get_conn(db_name: Optional[str] = None) -> mysql.connector.MySQLConnection:
    """
    Establishes and returns a new MySQL connection to the specified database
    (or the default DB_NAME from config if db_name is None).
    
    Args:
        db_name: Name of the database to connect to.
        
    Raises:
        ConnectionError: If connection to the database fails.
    """
    target_db = db_name if db_name else DB_NAME
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=target_db,
            connection_timeout=10,
        )
        return conn
    except MySQLError as err:
        raise ConnectionError(
            f"Failed to connect to MySQL database '{target_db}' at {DB_HOST}:{DB_PORT}. "
            f"Error details: {err}"
        ) from err


def get_schema(db_name: Optional[str] = None, force_refresh: bool = False) -> str:
    """
    Reads table, column, data type, primary key, and foreign key details from
    MySQL's `information_schema` for the specified database and formats them
    into a compact, LLM-friendly string.
    
    Results are cached in memory per database name.
    
    Args:
        db_name: The name of the database to inspect (defaults to DB_NAME).
        force_refresh: If True, bypasses cache and re-queries information_schema.
        
    Returns:
        str: Compact schema representation for LLM prompt injection.
    """
    global _SCHEMA_CACHE
    target_db = db_name if db_name else DB_NAME

    if target_db in _SCHEMA_CACHE and not force_refresh:
        return _SCHEMA_CACHE[target_db]

    conn = get_conn(target_db)
    try:
        cursor = conn.cursor(dictionary=True)

        # 1. Fetch all columns, types, and primary key indicators for the database
        columns_query = """
            SELECT 
                TABLE_NAME, 
                COLUMN_NAME, 
                DATA_TYPE, 
                COLUMN_KEY
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION;
        """
        cursor.execute(columns_query, (target_db,))
        columns_data = cursor.fetchall()

        # 2. Fetch all foreign key relationships for the database
        fk_query = """
            SELECT 
                TABLE_NAME, 
                COLUMN_NAME, 
                REFERENCED_TABLE_NAME, 
                REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s 
              AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_NAME, COLUMN_NAME;
        """
        cursor.execute(fk_query, (target_db,))
        fk_data = cursor.fetchall()
        cursor.close()

        # Build a lookup for foreign keys: (table_name, column_name) -> "ReferencedTable.ReferencedColumn"
        fk_lookup = {
            (row["TABLE_NAME"], row["COLUMN_NAME"]): f"{row['REFERENCED_TABLE_NAME']}.{row['REFERENCED_COLUMN_NAME']}"
            for row in fk_data
        }

        # Group columns by table
        tables_dict = {}
        for row in columns_data:
            table = row["TABLE_NAME"]
            col = row["COLUMN_NAME"]
            dtype = row["DATA_TYPE"]
            col_key = row["COLUMN_KEY"]

            # Mark Primary Keys and Foreign Keys
            tags = []
            if col_key == "PRI":
                tags.append("PK")
            if (table, col) in fk_lookup:
                tags.append(f"FK -> {fk_lookup[(table, col)]}")

            tag_str = f", {', '.join(tags)}" if tags else ""
            col_desc = f"{col} ({dtype}{tag_str})"

            tables_dict.setdefault(table, []).append(col_desc)

        # Build compact text format
        schema_lines = [f"Database: {target_db}"]
        for table_name, cols in sorted(tables_dict.items()):
            cols_str = ", ".join(cols)
            schema_lines.append(f"Table: {table_name}\n  Columns: {cols_str}")

        compact_schema = "\n\n".join(schema_lines)
        _SCHEMA_CACHE[target_db] = compact_schema
        return _SCHEMA_CACHE[target_db]

    finally:
        # Guarantee that database connection is always closed
        conn.close()


def clear_schema_cache(db_name: Optional[str] = None) -> None:
    """Clears the cached schema for a specific database, or all if db_name is None."""
    global _SCHEMA_CACHE
    if db_name:
        _SCHEMA_CACHE.pop(db_name, None)
    else:
        _SCHEMA_CACHE.clear()


def run_query(sql: str, db_name: Optional[str] = None) -> pd.DataFrame:
    """
    Executes a SQL query against MySQL in the specified database and returns
    the results as a Pandas DataFrame.
    Guarantees that the database connection is closed after execution.
    
    Args:
        sql: The SQL query string to execute.
        db_name: The target database name (defaults to DB_NAME).
        
    Returns:
        pd.DataFrame: Query results with matching column names.
        
    Raises:
        Exception: Propagates any SQL execution or connection error.
    """
    target_db = db_name if db_name else DB_NAME
    conn = get_conn(target_db)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)

        # If the query produced rows (e.g. SELECT query)
        if cursor.description:
            columns = [col_desc[0] for col_desc in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)
        else:
            df = pd.DataFrame()

        cursor.close()
        return df

    finally:
        # Guarantee that database connection is always closed
        conn.close()


if __name__ == "__main__":
    # Self-test block to verify multi-database connectivity and schema inspection
    print("Testing QueryMate Database Layer (db.py)...")
    for test_db in DB_NAMES:
        print(f"\n--- Testing Database: '{test_db}' ---")
        try:
            test_conn = get_conn(test_db)
            print(f"   [SUCCESS] Connected to MySQL database '{test_db}'.")
            test_conn.close()

            test_df = run_query("SELECT 1 AS test_col", db_name=test_db)
            print(f"   [SUCCESS] run_query executed. Result: {test_df.iloc[0]['test_col']}")

            schema_output = get_schema(db_name=test_db)
            table_count = schema_output.count("Table: ")
            print(f"   [SUCCESS] Schema extracted. Table count: {table_count}, Character count: {len(schema_output)}")
        except Exception as e:
            print(f"   [ERROR on '{test_db}'] {e}")

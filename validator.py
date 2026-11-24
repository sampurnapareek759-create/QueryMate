"""
validator.py
------------
Safety and security validation layer for QueryMate.
Enforces defense-in-depth against SQL injection and destructive operations:
  1. Strips single-line and multi-line SQL comments.
  2. Disallows multiple statements / query chaining (e.g., semicolon stacking).
  3. Restricts statements to SELECT and WITH (CTEs) queries only.
  4. Blocks dangerous DDL, DML, administrative, and data-exfiltration keywords.
  5. Enforces a maximum result limit by appending LIMIT 500 if omitted.
"""

import re
from config import MAX_ROWS


class UnsafeSQLError(Exception):
    """Raised when a generated SQL query fails safety validation checks."""
    pass


# Forbidden SQL keywords that could modify state, alter schema, or exploit server internals
BLOCKED_KEYWORDS = [
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "replace",
    "grant",
    "revoke",
    "call",
    "exec",
    "load_file",
    "sleep",
    "benchmark",
    "outfile",
    "dumpfile",
]

# Compiled regex to match any of the blocked keywords as whole tokens
# Word boundaries (\b) ensure we don't accidentally flag words like 'updated_at' or 'description'
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE
)


def strip_comments(sql: str) -> str:
    """
    Removes SQL comments (single-line --, # and multi-line /* ... */) from the query string.
    """
    # Remove multi-line comments /* ... */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove single-line comments starting with -- or #
    sql = re.sub(r"(--|#)[^\r\n]*", " ", sql)
    return sql.strip()


def validate_sql(sql: str) -> str:
    """
    Validates a SQL query for safety and appends a row LIMIT if missing.
    
    Args:
        sql: The raw SQL string to validate.
        
    Returns:
        str: Cleaned and validated SQL query guaranteed to be read-only and bounded.
        
    Raises:
        UnsafeSQLError: If any validation rule is violated.
    """
    if not sql or not sql.strip():
        raise UnsafeSQLError("Query is empty. Please provide a valid SQL query.")

    # 1. Strip comments
    cleaned = strip_comments(sql).strip()
    if not cleaned:
        raise UnsafeSQLError("Query contains only comments or whitespace.")

    # 2. Check for multiple statements
    # Remove any trailing semicolon first
    trimmed = cleaned.rstrip(";").strip()
    
    # Temporarily mask string literals so semicolons inside text (e.g. 'rock;pop') aren't treated as statement separators
    masked_strings = re.sub(r"'([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"", "''", trimmed)
    if ";" in masked_strings:
        raise UnsafeSQLError(
            "Multiple SQL statements are not permitted. Only single queries are allowed."
        )

    # 3. Allow only queries starting with SELECT or WITH
    first_token_match = re.match(r"^(SELECT|WITH)\b", trimmed, re.IGNORECASE)
    if not first_token_match:
        raise UnsafeSQLError(
            "Disallowed query type. Only SELECT or WITH (Common Table Expression) queries are permitted."
        )

    # 4. Block prohibited keywords outside of string literals
    match = _BLOCKED_PATTERN.search(masked_strings)
    if match:
        blocked_word = match.group(0)
        raise UnsafeSQLError(
            f"Prohibited keyword detected: '{blocked_word}'. Write operations, DDL, and administrative commands are strictly forbidden."
        )

    # 5. Append LIMIT 500 if no LIMIT clause exists
    if not re.search(r"\bLIMIT\b", trimmed, re.IGNORECASE):
        validated_sql = f"{trimmed} LIMIT {MAX_ROWS}"
    else:
        validated_sql = trimmed

    return validated_sql


if __name__ == "__main__":
    # Self-test suite to verify safety validator behavior
    print("=== Running QueryMate Safety Validator (validator.py) Self-Tests ===")
    
    test_cases = [
        # (Input SQL, Expected result: 'PASS' or 'FAIL', description)
        ("SELECT * FROM Track", "PASS", "Standard SELECT query"),
        ("WITH customer_summary AS (SELECT CustomerId, count(*) FROM Invoice GROUP BY CustomerId) SELECT * FROM customer_summary", "PASS", "Common Table Expression (WITH)"),
        ("SELECT * FROM Track LIMIT 10", "PASS", "SELECT with existing LIMIT"),
        ("SELECT * FROM Track -- trailing comment", "PASS", "SELECT with comment"),
        ("SELECT * FROM Track WHERE Name = 'drop the bass'", "PASS", "Keyword inside string literal (should pass)"),
        ("DROP TABLE Track", "FAIL", "Direct DROP statement"),
        ("INSERT INTO Artist (Name) VALUES ('AC/DC')", "FAIL", "Direct INSERT statement"),
        ("UPDATE Customer SET FirstName = 'Hacker'", "FAIL", "Direct UPDATE statement"),
        ("DELETE FROM Invoice", "FAIL", "Direct DELETE statement"),
        ("SELECT * FROM Customer; DROP TABLE Album;", "FAIL", "Multiple statements / SQL injection attempt"),
        ("SELECT * FROM Track WHERE SLEEP(5) = 0", "FAIL", "Time-based injection (sleep keyword)"),
        ("SELECT * FROM Invoice INTO OUTFILE '/tmp/data.csv'", "FAIL", "Data exfiltration (outfile keyword)"),
        ("ALTER TABLE Customer ADD COLUMN evil VARCHAR(20)", "FAIL", "Schema alteration (alter keyword)"),
    ]

    passed_count = 0
    for query, expected, description in test_cases:
        try:
            result = validate_sql(query)
            if expected == "PASS":
                print(f"[PASS] {description}")
                passed_count += 1
            else:
                print(f"[UNEXPECTED PASS] {description} -> Returned: {result}")
        except UnsafeSQLError as e:
            if expected == "FAIL":
                print(f"[BLOCKED as expected] {description} -> {e}")
                passed_count += 1
            else:
                print(f"[UNEXPECTED FAIL] {description} -> Error: {e}")

    print("=" * 68)
    print(f"Result: {passed_count}/{len(test_cases)} tests passed successfully.")
    print("=" * 68)

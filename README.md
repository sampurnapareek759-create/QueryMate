# QueryMate: AI-Powered Natural-Language-to-SQL Assistant

QueryMate lets anyone ask a question about a MySQL database in plain English. It sends the database
structure and the question to Google Gemini, checks the SQL that comes back, runs it safely and shows
the answer as a table, a chart and the SQL itself.

B.Tech Information Technology, SKIT Jaipur. Author: Sampurna Pareek.

## Features
- Plain English to MySQL, using the Gemini API
- Works on four databases (Chinook, Sakila, World, Employees); pick one from a dropdown
- The schema is read at run time, so a new database needs no code change
- Safety: SELECT-only validator, automatic row limit, and a read-only MySQL user
- Self-healing: if the SQL fails, the error is sent back to Gemini for one corrected retry
- Results table, automatic chart, SQL tab and CSV download
- Answer cache, request timeout and an optional backup model for API limits and overload
- Benchmark script that measures accuracy against hand-written correct SQL

## How it works
```
Question -> read schema -> Gemini writes SQL -> validator (SELECT only?) -> MySQL (read-only user) -> table, chart, SQL
                                                      |                         |
                                                   blocked              error -> one retry via Gemini
```

## Project structure
| File | Purpose |
|---|---|
| `app.py` | Streamlit web interface |
| `pipeline.py` | Runs the steps in order, with the self-healing retry |
| `llm.py` | Talks to Gemini, with timeout, retry, backup model and cache |
| `validator.py` | Blocks anything that is not a safe single SELECT |
| `db.py` | Connects to MySQL, reads the schema, runs queries |
| `config.py` | Reads settings from `.env` |
| `evaluate.py` | Accuracy benchmark |
| `setup_readonly_user.sql` | Creates the read-only MySQL user |
| `.env.example` | Template for your secrets |

## Setup (macOS)
1. Install Python 3.10+ and MySQL Server.
2. Load the sample databases:
   - Sakila and World: https://dev.mysql.com/doc/index-other.html (Example Databases)
   - Chinook: https://github.com/lerocha/chinook-database
   - Employees: https://github.com/datacharmer/test_db (load with `mysql -u root -p < employees.sql` from inside the folder)
3. Create the read-only user: edit the password in `setup_readonly_user.sql`, then run it in MySQL as root.
4. Create a free Gemini API key at https://aistudio.google.com
5. Install and configure:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env      # then fill in your key, DB user and password
   ```
6. Run: `streamlit run app.py`

## Benchmark
```bash
python evaluate.py chinook      # also: sakila, world, employees
```
Prints PASS or FAIL per question, first-attempt and after-retry accuracy, and saves `benchmark_<db>.csv`.

## Safety design
1. The validator allows one SELECT (or WITH) statement, strips comments, blocks DROP, DELETE, INSERT,
   UPDATE and similar keywords, and adds `LIMIT 500` when missing.
2. The app connects with a MySQL user that only has SELECT permission, so even a missed check cannot change data.
3. The API key and passwords live in `.env`, which is never committed.

## Limitations
- Free Gemini API tiers have daily and per-minute limits, and models can be overloaded.
- Vague questions or questions the tables cannot answer can fail or return wrong results.
- No conversation memory: each question is answered on its own.

## Future scope
Follow-up questions with memory, CSV upload and other databases (PostgreSQL, SQL Server), voice input,
user login with roles, and a larger accuracy benchmark.

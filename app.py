"""
app.py
------
Streamlit Web Interface for QueryMate: AI-Powered Multi-Database SQL Assistant.

Features:
  - Multi-database selection (chinook, sakila, world, employees)
  - Sidebar sample questions grouped by category (Customers, Sales, Music catalog, Employees)
  - Multi-step status indicator (Reading schema -> Generating SQL -> Validating -> Running query)
  - Tabbed results display: "Results", "Chart", "SQL", "How it was built"
  - Summary metrics at top of results (Row count, Time taken, Retried Yes/No)
  - Collapsible SQL view with explicit red "Blocked for safety" badge on violations
  - Plotly auto-charts, CSV download, and session history
"""

from datetime import datetime
import streamlit as st
import pandas as pd
import plotly.express as px

from db import get_schema
from pipeline import answer
from config import DB_NAME, DB_NAMES

# ==============================================================================
# Page Configuration
# ==============================================================================
st.set_page_config(
    page_title="QueryMate - AI SQL Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==============================================================================
# Cached Schema Loader
# ==============================================================================
@st.cache_resource(show_spinner=False)
def get_cached_schema(database_name: str) -> str:
    """
    Caches the database schema in Streamlit memory per database name so it
    is only inspected from MySQL once across all sessions and reruns.
    """
    return get_schema(db_name=database_name)


# ==============================================================================
# Session State Initialization
# ==============================================================================
if "history" not in st.session_state:
    st.session_state["history"] = []

if "current_question" not in st.session_state:
    st.session_state["current_question"] = ""

if "latest_result" not in st.session_state:
    st.session_state["latest_result"] = None

if "latest_question" not in st.session_state:
    st.session_state["latest_question"] = ""

if "latest_db" not in st.session_state:
    st.session_state["latest_db"] = ""


# ==============================================================================
# Helper Functions for Auto-Charting
# ==============================================================================
def is_date_like(series: pd.Series, col_name: str) -> bool:
    """Checks if a column name or series data looks like a date/timestamp."""
    col_lower = col_name.lower()
    if any(k in col_lower for k in ("date", "year", "month", "day", "time")):
        return True
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if series.dtype == object and len(series) > 0:
        try:
            pd.to_datetime(series.dropna().head(5))
            return True
        except Exception:
            pass
    return False


def render_auto_chart(df: pd.DataFrame):
    """
    Automatically selects and renders the most appropriate Plotly chart:
      1. Line Chart : If x-axis column is date-like
      2. Pie Chart  : If result has 6 or fewer rows (and > 1 row)
      3. Bar Chart  : Otherwise
    """
    if df.empty or len(df.columns) < 1:
        st.info("No data available to display a chart.")
        return

    # Handle single scalar value (e.g. SELECT count(*))
    if len(df) == 1 and len(df.columns) == 1:
        val = df.iloc[0, 0]
        st.metric(
            label=str(df.columns[0]),
            value=f"{val:,}" if isinstance(val, (int, float)) else str(val)
        )
        return

    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = [c for c in df.columns if c not in num_cols]

    if cat_cols and num_cols:
        x_col = cat_cols[0]
        y_col = num_cols[0]
    elif len(num_cols) >= 2:
        x_col = num_cols[0]
        y_col = num_cols[1]
    elif len(df.columns) >= 2:
        x_col = df.columns[0]
        y_col = df.columns[1]
    else:
        x_col = df.columns[0]
        y_col = None

    if not y_col:
        st.info("Data does not contain numeric values suitable for automatic graphing.")
        return

    # 1. Line chart for date-like x
    if is_date_like(df[x_col], x_col):
        fig = px.line(
            df,
            x=x_col,
            y=y_col,
            title=f"{y_col} Trend over {x_col}",
            markers=True,
            template="plotly_white",
            color_discrete_sequence=["#2563EB"],
        )
        st.plotly_chart(fig, use_container_width=True)

    # 2. Pie chart for 6 or fewer rows (and at least 2 rows)
    elif 1 < len(df) <= 6:
        fig = px.pie(
            df,
            names=x_col,
            values=y_col,
            title=f"Distribution of {y_col} by {x_col}",
            hole=0.3,
            template="plotly_white",
            color_discrete_sequence=px.colors.qualitative.Prism,
        )
        st.plotly_chart(fig, use_container_width=True)

    # 3. Bar chart otherwise
    else:
        fig = px.bar(
            df,
            x=x_col,
            y=y_col,
            title=f"{y_col} by {x_col}",
            template="plotly_white",
            color=y_col if len(num_cols) > 0 else None,
            color_continuous_scale="Blues",
        )
        st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# Pipeline Execution with st.status
# ==============================================================================
def execute_query(question_text: str, database_name: str, schema_data: str):
    """
    Executes the pipeline while providing a visual step-by-step progress status.
    Steps shown:
      1. Reading schema
      2. Generating SQL
      3. Validating
      4. Running query
    """
    with st.status("Executing query pipeline...", expanded=True) as status:
        # Step 1: Schema
        status.write(f"📖 **Step 1/4:** Reading schema for `{database_name}`...")

        # Step 2: Generating SQL
        status.write("🤖 **Step 2/4:** Generating SQL via Gemini...")

        # Step 3 & 4 are handled inside answer()
        res = answer(question_text, db_name=database_name, custom_schema=schema_data)

        status.write("🛡️ **Step 3/4:** Validating SQL safety rules...")
        status.write("⚡ **Step 4/4:** Running query against MySQL...")

        if res.get("error"):
            status.update(label="Query failed or was blocked!", state="error", expanded=False)
        else:
            status.update(label="Query completed successfully!", state="complete", expanded=False)

    # Update session state with results
    st.session_state["latest_result"] = res
    st.session_state["latest_question"] = question_text
    st.session_state["latest_db"] = database_name

    # Save to history (keep newest 10)
    st.session_state["history"].insert(0, {
        "question": question_text,
        "sql": res.get("sql", ""),
        "db_name": database_name,
        "time": datetime.now().strftime("%H:%M:%S"),
        "rows": len(res["df"]) if res.get("df") is not None else 0,
        "error": res.get("error") is not None,
    })
    st.session_state["history"] = st.session_state["history"][:10]


# ==============================================================================
# Categorized Sample Questions (4 questions per category)
# ==============================================================================
DB_SAMPLES = {
    "chinook": {
        "👥 Customers": [
            "How many customers are there?",
            "Top 5 customers by total spending",
            "Which country has the most customers?",
            "List customers who live in USA with their city and state",
        ],
        "💰 Sales": [
            "Total revenue generated across all invoices",
            "Top 5 highest value invoices",
            "Total sales revenue broken down by billing country",
            "Top 5 best-selling tracks by revenue",
        ],
        "🎵 Music catalog": [
            "How many tracks are there in total?",
            "List the top 5 artists with the most albums",
            "Top 5 most popular music genres by track count",
            "Find the top 5 longest tracks with their duration in milliseconds",
        ],
        "👔 Employees": [
            "How many employees are there?",
            "List all employees with their title and hire date",
            "Which employee reports to whom?",
            "How many customers are supported by each employee?",
        ],
    },
    "sakila": {
        "🎬 Films": [
            "Top 5 most rented films",
            "Number of films in each category",
            "Which 5 actors appear in the most films?",
            "How many films are there for each rating?",
        ],
        "👥 Customers & staff": [
            "How many customers are there?",
            "Top 5 customers by total payments",
            "Which country has the most customers?",
            "Number of rentals handled by each staff member",
        ],
        "💰 Revenue": [
            "Total payments per store",
            "Total revenue per month",
            "Which film category earns the most revenue?",
            "Top 5 films by total revenue",
        ],
    },
    "world": {
        "🌍 Countries": [
            "Which 5 countries have the largest population?",
            "Average life expectancy by continent",
            "Top 5 countries by GNP",
            "How many countries are in each continent?",
        ],
        "🏙️ Cities": [
            "Top 10 most populated cities",
            "Which 5 countries have the most cities in the database?",
            "List cities in India with population over 1 million",
            "Average city population per continent",
        ],
        "🗣️ Languages": [
            "Which countries have Spanish as an official language?",
            "Which 5 languages are official in the most countries?",
            "Official languages of Switzerland",
            "Countries where English is spoken by more than 50 percent",
        ],
    },
    "employees": {
        "👔 Workforce": [
            "How many employees are there?",
            "Number of employees hired each year",
            "How many male and female employees are there?",
            "How many current employees are in each department?",
        ],
        "💰 Salaries": [
            "Average current salary by department",
            "Top 5 highest paid current employees",
            "Average current salary by gender",
            "What is the highest salary ever recorded?",
        ],
        "🏷️ Titles & managers": [
            "Most common current job titles",
            "Who are the current managers of each department?",
            "Number of current employees per title",
            "Top 5 longest serving employees",
        ],
    },
}


# ==============================================================================
# Sidebar Components
# ==============================================================================
with st.sidebar:
    st.title("QueryMate")
    st.caption("AI-Powered Natural-Language-to-SQL Assistant")
    st.divider()

    # 1. Database Selector Dropdown
    st.subheader("Select Database")
    default_db_index = DB_NAMES.index(DB_NAME) if DB_NAME in DB_NAMES else 0
    selected_db = st.selectbox(
        "Active Database:",
        options=DB_NAMES,
        index=default_db_index,
        key="selected_database",
        help="Choose the target database for schema inspection and query generation.",
    )

    st.divider()

    # 2. Database Schema Viewer (Defines schema_text first to avoid NameError)
    st.subheader(f"Database Schema: `{selected_db}`")
    schema_text = ""
    try:
        schema_text = get_cached_schema(selected_db)
        with st.expander(f"Explore '{selected_db}' Tables & Columns", expanded=False):
            st.code(schema_text, language="text")
    except Exception as err:
        st.error(f"❌ Failed to load schema for database '{selected_db}': {err}")
        st.info(
            f"Please ensure MySQL is running, user credentials are correct, "
            f"and the database '{selected_db}' exists in MySQL."
        )
        st.stop()

    st.divider()

    # 3. Categorized Sample Questions (Customers, Sales, Music catalog, Employees)
    st.subheader("Sample Questions")
    db_samples = DB_SAMPLES.get(selected_db.lower(), {})
    if not db_samples:
        st.caption("No sample questions for this database yet. Type your own question.")
    for category_name, questions_list in db_samples.items():
        with st.expander(category_name, expanded=(category_name == next(iter(db_samples)))):
            for q_idx, sample_q in enumerate(questions_list):
                btn_key = f"sample_{selected_db}_{category_name}_{q_idx}"
                if st.button(sample_q, key=btn_key, use_container_width=True):
                    st.session_state["current_question"] = sample_q
                    execute_query(sample_q, selected_db, schema_text)
                    st.rerun()

    st.divider()

    # 4. Query History (Last 10 queries across all databases)
    st.subheader("Query History (Last 10)")
    if st.session_state["history"]:
        for h_idx, item in enumerate(st.session_state["history"]):
            status_icon = "❌" if item["error"] else "✅"
            item_db = item.get("db_name", selected_db)
            label = f"{status_icon} [{item_db}] [{item['time']}] {item['question']}"
            with st.expander(label):
                st.write(f"**Database:** `{item_db}`")
                st.write(f"**Rows returned:** {item['rows']}")
                st.code(item["sql"], language="sql")
                if st.button("Re-run Query", key=f"rerun_{h_idx}"):
                    st.session_state["current_question"] = item["question"]
                    rerun_schema = get_cached_schema(item_db)
                    execute_query(item["question"], item_db, rerun_schema)
                    st.rerun()
    else:
        st.write("*No queries executed yet.*")


# ==============================================================================
# Main Content Area
# ==============================================================================
st.title("QueryMate: Natural-Language-to-SQL Assistant")
st.markdown(
    f"Active Database: **`{selected_db}`** — Ask questions about your data in plain English. "
    f"QueryMate automatically writes safe SQL, executes it on MySQL, and renders interactive charts."
)

# Question Input Form
with st.form("query_form", clear_on_submit=False):
    col1, col2 = st.columns([5, 1])
    with col1:
        user_question = st.text_input(
            "Enter your question:",
            value=st.session_state["current_question"],
            placeholder=f"e.g. Which customer spent the most in {selected_db}?",
            label_visibility="collapsed",
        )
    with col2:
        submitted = st.form_submit_button("Ask QueryMate", type="primary", use_container_width=True)

# Handle Form Submission
if submitted:
    if not user_question.strip():
        st.warning("Please type a question or select one of the sample questions from the sidebar.")
    else:
        st.session_state["current_question"] = user_question
        execute_query(user_question, selected_db, schema_text)


# ==============================================================================
# Results Display with Tabs and Metrics
# ==============================================================================
res = st.session_state.get("latest_result")

if res is not None:
    st.divider()

    df = res.get("df")
    row_count = len(df) if df is not None else 0
    timings = res.get("timings", {})
    total_time = timings.get("total", 0.0)
    was_retried = res.get("retried", False)
    error_msg = res.get("error")
    raw_sql = res.get("sql", "")

    # Top Metrics Banner: Row count, Time taken, Retried Yes/No
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    with metric_col1:
        st.metric(label="Rows Returned", value=f"{row_count:,}")
    with metric_col2:
        st.metric(label="Time Taken", value=f"{total_time:.2f}s")
    with metric_col3:
        st.metric(label="Self-Healing Retry", value="Yes (Corrected)" if was_retried else "No (1st Attempt)")
    with metric_col4:
        st.metric(label="Status", value="Error / Blocked" if error_msg else "Success")

    # Informational notice if self-healing retry succeeded
    if was_retried and not error_msg:
        st.info(
            "💡 **Self-Healing Retry Activated:** The initial query generated by Gemini had an issue. "
            "QueryMate diagnosed the error, regenerated a corrected query, and executed it successfully!"
        )

    # Check if this query was blocked by the safety validator
    is_safety_blocked = False
    if error_msg:
        err_lower = error_msg.lower()
        safety_triggers = [
            "prohibited keyword", "unsafe", "disallowed", "write operations",
            "multiple sql statements", "blocked", "strictly forbidden",
            "cannot be answered", "only select or with"
        ]
        if any(w in err_lower for w in safety_triggers):
            is_safety_blocked = True

    # Prominent safety badge if blocked
    if is_safety_blocked:
        st.error(f"🛑 **Blocked for safety:** {error_msg}")
    elif error_msg:
        st.error(f"❌ **Error Encountered:** {error_msg}")

    # Tabs: "Results", "Chart", "SQL", "How it was built"
    tab_results, tab_chart, tab_sql, tab_how = st.tabs([
        "📋 Results",
        "📊 Chart",
        "💻 SQL",
        "🛠️ How it was built",
    ])

    # Tab 1: Results Table & CSV Export
    with tab_results:
        if error_msg:
            st.warning("No data returned because the query encountered an error or was blocked.")
        elif df is not None and row_count > 0:
            st.dataframe(df, use_container_width=True)
            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_data,
                file_name=f"querymate_{selected_db}_results.csv",
                mime="text/csv",
            )
        else:
            st.warning("The query ran successfully, but returned 0 matching rows.")

    # Tab 2: Auto Data Visualization
    with tab_chart:
        if error_msg:
            st.warning("No chart available because the query did not return data.")
        elif df is not None and row_count > 0:
            render_auto_chart(df)
        else:
            st.info("No data available to display a chart.")

    # Tab 3: Collapsible SQL with Safety Annotation
    with tab_sql:
        if is_safety_blocked:
            st.error("🛑 **Blocked for safety:** This query was flagged and stopped by the validator before execution.")

        with st.expander("🔍 View Generated SQL Query", expanded=True):
            if raw_sql:
                st.code(raw_sql, language="sql")
            else:
                st.write("*No SQL generated.*")

    # Tab 4: How it was built (Pipeline explanation & latency breakdown)
    with tab_how:
        st.subheader("Pipeline Execution Breakdown")
        st.markdown(
            """
            QueryMate executes each question through a 4-step pipeline:
            1. **Reading Schema**: Queries MySQL's `information_schema` to inspect tables, primary keys, and foreign keys.
            2. **Generating SQL**: Calls Google Gemini with deterministic temperature (`0.0`) and zero thinking tokens for fast generation.
            3. **Validating SQL**: Enforces single-statement read-only execution (`SELECT` / `WITH`), blocks dangerous keywords (`DROP`, `DELETE`, etc.), and enforces row limits.
            4. **Running Query**: Executes the sanitized SQL against MySQL and loads results into a Pandas DataFrame.
            """
        )

        st.subheader("Step-by-Step Latency")
        gemini_t = timings.get("llm", 0.0)
        sql_t = timings.get("sql", 0.0)
        val_t = timings.get("validation", 0.0)
        schema_t = timings.get("schema", 0.0)

        timing_df = pd.DataFrame([
            {"Pipeline Step": "1. Reading Schema", "Time (seconds)": f"{schema_t:.3f}s"},
            {"Pipeline Step": "2. Gemini LLM Call", "Time (seconds)": f"{gemini_t:.3f}s"},
            {"Pipeline Step": "3. Security Validation", "Time (seconds)": f"{val_t:.4f}s"},
            {"Pipeline Step": "4. MySQL Query Execution", "Time (seconds)": f"{sql_t:.3f}s"},
            {"Pipeline Step": "Total End-to-End Latency", "Time (seconds)": f"{total_time:.3f}s"},
        ])
        st.table(timing_df)

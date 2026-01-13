"""Admin SQL Query Interface.

This page provides a simple interface for executing read-only SQL queries.
Only SELECT queries are allowed for safety.
"""

from typing import Any

import pandas as pd
import requests

import streamlit as st

st_any: Any = st

BACKEND_URL = "http://localhost:8000"


st_any.title("Admin SQL Query")

st_any.markdown(
    """
    **⚠️ Admin Only - Read-Only SQL Execution**

    This interface allows executing SELECT queries only. All other query types are blocked for safety.
    Results are limited to 500 rows maximum.
    """
)

# SQL Query Input
st_any.subheader("SQL Query")

# Default example query
default_query = """SELECT * FROM users LIMIT 10;"""

sql_query = st_any.text_area(
    "Enter your SELECT query:",
    value=default_query,
    height=150,
    help="Only SELECT queries are allowed. Results are limited to 500 rows.",
)

# Execute button
if st_any.button("Execute Query", type="primary"):
    if not sql_query.strip():
        st_any.error("Please enter a SQL query")
    else:
        try:
            # Call the backend API
            response = requests.post(
                f"{BACKEND_URL}/admin/sql/query",
                json={"sql": sql_query},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                columns = data.get("columns", [])
                rows = data.get("rows", [])
                row_count = data.get("row_count", 0)

                if row_count > 0:
                    # Display results as a DataFrame
                    df = pd.DataFrame(rows, columns=columns)
                    st_any.success(f"Query executed successfully. Returned {row_count} row(s).")
                    st_any.dataframe(df, use_container_width=True)

                    # Show row count warning if at limit
                    if row_count >= 500:
                        st_any.warning(
                            "⚠️ Results limited to 500 rows. Add a LIMIT clause or refine your query to see all results."
                        )
                else:
                    st_any.info("Query executed successfully but returned no rows.")
            elif response.status_code == 403:
                st_any.error("❌ Access denied. Admin privileges required.")
            elif response.status_code == 400:
                error_detail = response.json().get("detail", "Unknown error")
                st_any.error(f"❌ Query Error: {error_detail}")
            else:
                st_any.error(f"❌ Unexpected error: {response.status_code}")
                st_any.json(response.json())

        except requests.exceptions.Timeout:
            st_any.error("❌ Request timed out. The query may be too complex or the server is slow.")
        except requests.exceptions.ConnectionError:
            st_any.error(
                f"❌ Connection error. Make sure the backend is running at {BACKEND_URL}"
            )
        except Exception as e:
            st_any.error(f"❌ Unexpected error: {e!s}")

st_any.divider()

# Example queries
with st_any.expander("📚 Example Queries"):
    st_any.markdown(
        """
        **Get recent users:**
        ```sql
        SELECT id, email, created_at, is_active
        FROM users
        ORDER BY created_at DESC
        LIMIT 20;
        ```

        **Get activity count by user:**
        ```sql
        SELECT user_id, COUNT(*) as activity_count
        FROM activities
        GROUP BY user_id
        ORDER BY activity_count DESC;
        ```

        **Get recent activities:**
        ```sql
        SELECT id, user_id, start_time, distance_meters, duration_seconds
        FROM activities
        ORDER BY start_time DESC
        LIMIT 50;
        ```
        """
    )

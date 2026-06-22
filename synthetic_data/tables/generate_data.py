"""
generate_data.py
----------------
Entrypoint for building the entire `maintenance` database.

Run from the project root:

    python synthetic_data/tables/generate_data.py

Flow:
1. Open ONE MySQL connection (from db_connection.py).
2. Build the static / hand-authored tables  (build_sql_tables).
3. Build the Faker-generated tables          (build_faker_tables).
4. Regenerate the data dictionary            (generate_metadata).
5. Close the connection.
"""

from db_connection import get_connection
from create_sql_tables import build_sql_tables
from create_faker_tables import build_faker_tables
from metadata.generate_metadata import generate_metadata


def main() -> None:
    conn = get_connection()
    try:
        # --- Phase 1: static / hand-authored tables ---
        build_sql_tables(conn)

        # --- Phase 2: Faker-generated tables ---
        build_faker_tables(conn)

        # --- Phase 3: data dictionary (introspect DB + enrich -> JSON + MD) ---
        generate_metadata(conn)

        print("✅ Database build complete.")
    finally:
        if conn.is_connected():
            conn.close()


if __name__ == "__main__":
    main()

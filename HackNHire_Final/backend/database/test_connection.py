"""
test_connection.py - check that your Neon database is reachable before uploading anything.

    python database/test_connection.py

It reads DATABASE_URL from the .env file in the backend folder, prints the PostgreSQL
version, switches on PostGIS (needed by the outlines approach) and lists the tables
that are already in the database.
"""

import common


def main() -> None:
    print("Connecting to the database in DATABASE_URL...")
    with common.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_database(), current_user")
            version, database, user = cur.fetchone()
            print(f"  Connected to database '{database}' as '{user}'")
            print(f"  {version.split(',')[0]}")
            try:
                cur.execute(common.schema_sql("postgis"))
                cur.execute("SELECT postgis_lib_version()")
                print(f"  PostGIS {cur.fetchone()[0]} is switched on (polygon_approach.py needs it)")
            except Exception as exc:  # noqa: BLE001 - show any problem in plain words
                conn.rollback()
                print(f"  PostGIS could not be switched on: {exc}")
                print("  stencil_approach.py will still work; polygon_approach.py needs PostGIS.")
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename <> 'spatial_ref_sys' ORDER BY 1")
            tables = [row[0] for row in cur.fetchall()]
            print(f"  Tables already here: {', '.join(tables) if tables else 'none yet'}")
        conn.commit()
    print("All good - next, build a map from your satellite layers and upload it, e.g.:\n"
          "  python pipeline.py --name jambi --data-dir YOUR_LAYERS --bbox 103.0 -2.3 103.5 -1.8 "
          "--test-plots 1000 --verify")


if __name__ == "__main__":
    main()

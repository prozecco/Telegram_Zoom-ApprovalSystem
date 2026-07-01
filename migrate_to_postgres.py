import sqlite3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")

if not DATABASE_URL or "supabase.com" not in DATABASE_URL:
    print("[ERROR] A valid remote DATABASE_URL is not set in your .env file!")
    print("Please configure DATABASE_URL in your .env to point to your Supabase connection string first.")
    exit(1)

print(f"Connecting to local SQLite database: {DATABASE_PATH}...")
lite_conn = sqlite3.connect(DATABASE_PATH)
lite_conn.row_factory = sqlite3.Row
lite_cur = lite_conn.cursor()

print("Connecting to remote PostgreSQL Supabase database...")
try:
    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
except Exception as e:
    print(f"[ERROR] Connection failed: {e}")
    exit(1)

def migrate_table(table_name, columns, conflict_col=None):
    print(f"\nMigrating table '{table_name}'...")
    try:
        lite_cur.execute(f"SELECT * FROM {table_name}")
        rows = lite_cur.fetchall()
    except sqlite3.OperationalError:
        print(f"[INFO] Table '{table_name}' does not exist in local database. Skipping.")
        return
    
    if not rows:
        print(f"[INFO] Table '{table_name}' is empty. Skipping.")
        return

    cols_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    
    insert_query = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"
    if conflict_col:
        insert_query += f" ON CONFLICT ({conflict_col}) DO NOTHING"

    migrated = 0
    for row in rows:
        vals = [row[c] for c in columns]
        try:
            pg_cur.execute(insert_query, vals)
            migrated += 1
        except Exception as e:
            print(f"[WARNING] Failed to migrate row {vals}: {e}")
            pg_conn.rollback()
            
    pg_conn.commit()
    print(f"[SUCCESS] Successfully migrated {migrated}/{len(rows)} rows to table '{table_name}'.")

try:
    # 1. Migrate users
    migrate_table(
        "users", 
        ["registered_email", "telegram_id", "global_status", "behavior_notes", "created_at", "updated_at"], 
        "registered_email"
    )
    
    # 2. Migrate submissions_history
    migrate_table(
        "submissions_history", 
        ["id", "registered_email", "submitted_zoom_name", "submitted_telegram_username", "meeting_id", "action_taken", "action_timestamp"], 
        "id"
    )
    
    # Reset serial sequence for submissions_history id column
    try:
        pg_cur.execute("SELECT setval(pg_get_serial_sequence('submissions_history', 'id'), coalesce(max(id), 1)) FROM submissions_history;")
        pg_conn.commit()
        print("[INFO] Reset auto-increment ID sequence for submissions_history.")
    except Exception as seq_err:
        pg_conn.rollback()
        
    # 3. Migrate settings
    migrate_table("settings", ["key", "value"], "key")
    
    # 4. Migrate admins
    migrate_table("admins", ["telegram_id", "username", "added_at"], "telegram_id")
    
    print("\nMigration completed successfully!")
    
except Exception as e:
    print(f"\n[ERROR] Migration failed: {e}")
finally:
    lite_cur.close()
    lite_conn.close()
    pg_cur.close()
    pg_conn.close()

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from config import DATABASE_PATH, ADMIN_CHAT_ID, DATABASE_URL

# Check if PostgreSQL connection string is set and non-empty
IS_POSTGRES = DATABASE_URL is not None and str(DATABASE_URL).strip() != ""

# Lazy import psycopg2 to avoid importing errors if PostgreSQL is not used
if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras

class DatabaseCursorWrapper:
    """
    A unified wrapper around SQLite and PostgreSQL cursors.
    Enforces identical behavior (e.g. returning self on execute for method chaining).
    Handles dynamic query syntax conversion (? to %s) for PostgreSQL.
    """
    def __init__(self, conn, cursor, is_postgres):
        self.conn = conn
        self.cursor = cursor
        self.is_postgres = is_postgres

    def execute(self, query: str, params: tuple = ()):
        if self.is_postgres:
            query = query.replace("?", "%s")
        self.cursor.execute(query, params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def lastrowid(self):
        if self.is_postgres:
            return getattr(self.cursor, "lastrowid", None)
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

@contextmanager
def get_db():
    """
    Context manager to handle SQLite or PostgreSQL connections and yield a cursor wrapper.
    Enforces dict-like row access for both database types.
    """
    if IS_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        # Use RealDictCursor so psycopg2 rows behave like dictionaries
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        wrapper = DatabaseCursorWrapper(conn, cursor, is_postgres=True)
        try:
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
    else:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()
        wrapper = DatabaseCursorWrapper(conn, cursor, is_postgres=False)
        try:
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

def execute_query(cursor, query: str, params: tuple = ()):
    """
    Executes a query, delegating to the unified cursor wrapper.
    """
    return cursor.execute(query, params)

def init_db():
    """
    Initializes the database by creating tables if they do not exist.
    """
    with get_db() as cursor:
        # 1. Main User Profile Table
        execute_query(cursor, """
            CREATE TABLE IF NOT EXISTS users (
                registered_email TEXT PRIMARY KEY,
                telegram_id BIGINT,
                global_status TEXT DEFAULT 'Pending',
                behavior_notes TEXT DEFAULT '',
                join_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # 2. Form Submissions History (auto-increment syntax difference handled)
        if IS_POSTGRES:
            execute_query(cursor, """
                CREATE TABLE IF NOT EXISTS submissions_history (
                    id SERIAL PRIMARY KEY,
                    registered_email TEXT,
                    submitted_zoom_name TEXT,
                    submitted_telegram_username TEXT,
                    meeting_id TEXT,
                    action_taken TEXT,
                    action_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(registered_email) REFERENCES users(registered_email)
                );
            """)
        else:
            execute_query(cursor, """
                CREATE TABLE IF NOT EXISTS submissions_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    registered_email TEXT,
                    submitted_zoom_name TEXT,
                    submitted_telegram_username TEXT,
                    meeting_id TEXT,
                    action_taken TEXT,
                    action_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(registered_email) REFERENCES users(registered_email)
                );
            """)
        
        # 3. Settings table to persist dynamic variables
        execute_query(cursor, """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        
        # 4. Admins table for secondary administrators rights authorization
        execute_query(cursor, """
            CREATE TABLE IF NOT EXISTS admins (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migration: Safely add join_url column to users table if it doesn't exist
        try:
            execute_query(cursor, "ALTER TABLE users ADD COLUMN join_url TEXT;")
        except Exception:
            pass

def get_setting(key: str, default: str = None) -> str | None:
    """
    Retrieve a setting value by key.
    """
    with get_db() as cursor:
        execute_query(cursor, "SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    """
    Sets or updates a setting value.
    """
    with get_db() as cursor:
        execute_query(
            cursor,
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value).strip())
        )

# ==========================================
# ADMIN AUTHORIZATION METHODS
# ==========================================

def is_admin(telegram_id: int) -> bool:
    """
    Checks if a user is authorized as an administrator.
    Primary Bot Owner (ADMIN_CHAT_ID) is super-admin by default.
    """
    try:
        if int(telegram_id) == int(ADMIN_CHAT_ID):
            return True
    except (ValueError, TypeError):
        pass
        
    with get_db() as cursor:
        execute_query(cursor, "SELECT 1 FROM admins WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone() is not None

def add_admin(telegram_id: int, username: str = None) -> None:
    """
    Adds a new administrator to the secondary admins table.
    """
    with get_db() as cursor:
        execute_query(
            cursor,
            """
            INSERT INTO admins (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO NOTHING
            """,
            (telegram_id, username)
        )

def remove_admin(telegram_id: int) -> None:
    """
    Removes an administrator from the secondary admins table.
    """
    with get_db() as cursor:
        execute_query(cursor, "DELETE FROM admins WHERE telegram_id = ?", (telegram_id,))

def get_admins() -> list[dict]:
    """
    Lists all authorized secondary administrators.
    """
    with get_db() as cursor:
        execute_query(cursor, "SELECT telegram_id, username, added_at FROM admins ORDER BY added_at ASC")
        return [dict(row) for row in cursor.fetchall()]

# ==========================================
# USER OPERATIONS
# ==========================================

def get_user_status(email: str) -> str | None:
    """
    Retrieve the global_status of a user by email (case-insensitive search).
    """
    email = email.strip()
    with get_db() as cursor:
        execute_query(
            cursor,
            "SELECT global_status FROM users WHERE LOWER(registered_email) = LOWER(?)",
            (email,)
        )
        row = cursor.fetchone()
        return row["global_status"] if row else None

def get_user_by_email(email: str) -> dict | None:
    """
    Retrieve the complete user profile.
    """
    email = email.strip()
    with get_db() as cursor:
        execute_query(
            cursor,
            "SELECT * FROM users WHERE LOWER(registered_email) = LOWER(?)",
            (email,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    """
    Retrieve the user profile by Telegram ID.
    """
    with get_db() as cursor:
        execute_query(
            cursor,
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_submissions_by_email(email: str) -> list[dict]:
    """
    Get all submission history records for a specific email.
    """
    email = email.strip()
    with get_db() as cursor:
        execute_query(
            cursor,
            """
            SELECT * FROM submissions_history 
            WHERE LOWER(registered_email) = LOWER(?) 
            ORDER BY action_timestamp DESC
            """,
            (email,)
        )
        return [dict(row) for row in cursor.fetchall()]

def add_submission(email: str, telegram_id: int, zoom_name: str, telegram_username: str, meeting_id: str, action_taken: str = "Pending", join_url: str = None) -> int:
    """
    Records a new submission history log. 
    Inserts a user profile into the 'users' table if they do not exist.
    If the user exists and is NOT blacklisted, their global status is set back to 'Pending'.
    Returns the newly inserted submission ID.
    """
    email = email.strip()
    zoom_name = zoom_name.strip()
    telegram_username = telegram_username.strip()
    meeting_id = meeting_id.strip()

    with get_db() as cursor:
        # Check if user already exists
        execute_query(cursor, "SELECT global_status FROM users WHERE LOWER(registered_email) = LOWER(?)", (email,))
        row = cursor.fetchone()
        
        if row:
            current_status = row["global_status"]
            if action_taken == "NameChangePending":
                new_status = current_status
            else:
                new_status = "Blacklisted" if current_status == "Blacklisted" else action_taken
            
            if join_url:
                execute_query(
                    cursor,
                    """
                    UPDATE users 
                    SET telegram_id = ?, global_status = ?, join_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE LOWER(registered_email) = LOWER(?)
                    """,
                    (telegram_id, new_status, join_url, email)
                )
            else:
                execute_query(
                    cursor,
                    """
                    UPDATE users 
                    SET telegram_id = ?, global_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE LOWER(registered_email) = LOWER(?)
                    """,
                    (telegram_id, new_status, email)
                )
        else:
            execute_query(
                cursor,
                """
                INSERT INTO users (registered_email, telegram_id, global_status, join_url)
                VALUES (?, ?, ?, ?)
                """,
                (email, telegram_id, action_taken, join_url)
            )
            
        # Log to submissions_history
        if IS_POSTGRES:
            execute_query(
                cursor,
                """
                INSERT INTO submissions_history (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (email, zoom_name, telegram_username, meeting_id, action_taken)
            )
            return cursor.fetchone()["id"]
        else:
            execute_query(
                cursor,
                """
                INSERT INTO submissions_history (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email, zoom_name, telegram_username, meeting_id, action_taken)
            )
            return cursor.lastrowid

def update_user_status(email: str, status: str, behavior_notes: str = None) -> bool:
    """
    Updates the global status and behavior notes for a user.
    """
    email = email.strip()
    with get_db() as cursor:
        execute_query(cursor, "SELECT registered_email, behavior_notes FROM users WHERE LOWER(registered_email) = LOWER(?)", (email,))
        row = cursor.fetchone()
        if not row:
            return False
        
        actual_email = row["registered_email"]
        existing_notes = row["behavior_notes"] or ""
        
        if behavior_notes is not None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_note_entry = f"[{timestamp}] {behavior_notes}"
            updated_notes = f"{existing_notes}\n{new_note_entry}".strip() if existing_notes else new_note_entry
        else:
            updated_notes = existing_notes

        execute_query(
            cursor,
            """
            UPDATE users 
            SET global_status = ?, behavior_notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE registered_email = ?
            """,
            (status, updated_notes, actual_email)
        )
        return True

def log_historical_action(email: str, zoom_name: str, telegram_username: str, meeting_id: str, action: str):
    """
    Convenience method to write directly to submissions_history when admin updates a status.
    """
    with get_db() as cursor:
        execute_query(
            cursor,
            """
            INSERT INTO submissions_history (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email.strip(), zoom_name.strip(), telegram_username.strip(), meeting_id.strip(), action)
        )

def get_admin_report_data() -> dict:
    """
    Compiles structured database metrics for the admin /report command.
    """
    with get_db() as cursor:
        # Total users
        execute_query(cursor, "SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()["count"]
        
        # Breakdown by status
        execute_query(cursor, "SELECT global_status, COUNT(*) as count FROM users GROUP BY global_status")
        status_counts = {row["global_status"]: row["count"] for row in cursor.fetchall()}
        for s in ["Pending", "Approved", "Denied", "Blacklisted"]:
            status_counts.setdefault(s, 0)
            
        # Total submissions
        execute_query(cursor, "SELECT COUNT(*) as count FROM submissions_history")
        total_submissions = cursor.fetchone()["count"]
        
        # Blacklisted emails list
        execute_query(cursor, "SELECT registered_email, behavior_notes, updated_at FROM users WHERE global_status = 'Blacklisted'")
        blacklisted = [dict(row) for row in cursor.fetchall()]
        
        # Suspicious activities: Emails with multiple unique zoom names
        if IS_POSTGRES:
            execute_query(
                cursor,
                """
                SELECT registered_email, 
                       COUNT(DISTINCT submitted_zoom_name) as name_count, 
                       string_agg(DISTINCT submitted_zoom_name, ', ') as names
                FROM submissions_history 
                GROUP BY registered_email 
                HAVING COUNT(DISTINCT submitted_zoom_name) > 1
                """
            )
        else:
            execute_query(
                cursor,
                """
                SELECT registered_email, 
                       COUNT(DISTINCT submitted_zoom_name) as name_count, 
                       group_concat(DISTINCT submitted_zoom_name) as names
                FROM submissions_history 
                GROUP BY registered_email 
                HAVING COUNT(DISTINCT submitted_zoom_name) > 1
                """
            )
        suspicious = [dict(row) for row in cursor.fetchall()]
        
        return {
            "total_users": total_users,
            "status_counts": status_counts,
            "total_submissions": total_submissions,
            "blacklisted_emails": blacklisted,
            "suspicious_users": suspicious
        }

def update_user_join_url(email: str, join_url: str) -> bool:
    """
    Updates the join_url for a user.
    """
    email = email.strip()
    with get_db() as cursor:
        execute_query(
            cursor,
            "UPDATE users SET join_url = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
            (join_url, email)
        )
        return cursor.rowcount > 0

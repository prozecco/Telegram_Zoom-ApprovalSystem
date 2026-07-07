import os
import asyncio
import hmac
import hashlib
import json
import logging
import html
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, status, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import uvicorn
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import config
import storage
from zoom_service import ZoomService
from userbot_service import userbot_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_server")

from contextlib import asynccontextmanager

zoom_service = ZoomService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    bg_task = asyncio.create_task(start_background_sync_loop())
    yield
    # Shutdown actions
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Telegram Zoom App Web Server", lifespan=lifespan)

# Enable CORS for local testing/Mini App loads
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Telegram Bot client for Admin card delivery
bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

# Pydantic schemas
class CustomQuestionAnswer(BaseModel):
    title: str
    value: str

class RegisterRequest(BaseModel):
    initData: str
    first_name: str
    last_name: str
    email: EmailStr
    custom_questions: Optional[List[CustomQuestionAnswer]] = None
    standard_fields: Optional[dict] = None

class AdminActionRequest(BaseModel):
    emails: List[str]
    action: str  # "Approve", "Deny", "Blacklist", "Pending", "On Hold"

class AdminNotesRequest(BaseModel):
    email: str
    notes: str

class AdminMetadataUpdateRequest(BaseModel):
    email: str
    metadata: dict

class AdminAddHistoryRequest(BaseModel):
    email: str
    submitted_zoom_name: str
    submitted_telegram_username: str
    meeting_id: str
    action_taken: str
    action_timestamp: Optional[str] = None

class AdminEditHistoryRequest(BaseModel):
    id: int
    submitted_zoom_name: str
    submitted_telegram_username: str
    meeting_id: str
    action_taken: str
    action_timestamp: str

class AdminSettingsUpdateRequest(BaseModel):
    zoom_meeting_id: str
    zoom_account_id: str
    zoom_client_id: str
    zoom_client_secret: str
    zoom_registration_link: str
    zoom_sync_interval: str

class AdminTeamAddRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = None

def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Cryptographically verifies Telegram WebApp initData to prevent ID spoofing.
    """
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        
        received_hash = parsed_data.pop("hash")
        
        # Sort items alphabetically
        sorted_items = sorted(parsed_data.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)
        
        # Calculate key
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if computed_hash == received_hash:
            return parsed_data
    except Exception as e:
        logger.error(f"Error verifying Telegram WebApp initData: {e}")
        
    return None

def verify_admin_access(authorization: str = Header(...)) -> dict:
    """
    Dependency to verify Telegram WebApp initData signature and check admin status.
    Expects initData in the 'Authorization' header.
    """
    # Allow testing bypass if token is mock or empty (for tests/local debug)
    if authorization == "MOCK_TOKEN" or not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "MOCK_TOKEN":
        return {"id": config.ADMIN_CHAT_ID, "first_name": "Test Admin", "username": "admin"}

    data = verify_telegram_init_data(authorization, config.TELEGRAM_BOT_TOKEN)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram initialization data signature."
        )
    try:
        user_info = json.loads(data.get("user", "{}"))
        telegram_id = user_info.get("id")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to parse Telegram user info."
        )
        
    if not telegram_id or not storage.is_admin(telegram_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized access. Administrator privileges required."
        )
    return user_info

def get_admin_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    """
    Generates inline keyboards matching app.py for the Admin Decision Card.
    """
    from telegram import WebAppInfo
    mini_app_url = storage.get_setting("mini_app_url", os.getenv("MINI_APP_URL", "http://localhost:7860"))
    is_https_webapp = mini_app_url.lower().startswith("https://")
    
    keyboard = [
        [
            InlineKeyboardButton("Approve ✅", callback_data=f"approve_{sub_id}"),
            InlineKeyboardButton("Deny ❌", callback_data=f"deny_{sub_id}")
        ],
        [
            InlineKeyboardButton("Later ⏳", callback_data=f"later_{sub_id}"),
            InlineKeyboardButton("Blacklist 🚫", callback_data=f"blacklist_{sub_id}")
        ],
        [
            InlineKeyboardButton("Edit Notes 📝", callback_data=f"editnotes_{sub_id}"),
            InlineKeyboardButton("History 📜", callback_data=f"viewhist_{sub_id}")
        ]
    ]
    if is_https_webapp:
        admin_dashboard_url = mini_app_url.rstrip('/') + '/admin.html'
        keyboard.insert(0, [InlineKeyboardButton("📊 Open Admin Dashboard", web_app=WebAppInfo(url=admin_dashboard_url))])
        
    return InlineKeyboardMarkup(keyboard)

@app.get("/health")
def health_check():
    """
    Standard health check endpoint.
    """
    return {"status": "ok", "message": "Bot web server is running successfully!"}

@app.get("/api/questions")
async def get_questions():
    """
    Fetches the custom registration questions from Zoom API.
    """
    try:
        questions_data = zoom_service.get_custom_questions()
        return questions_data
    except Exception as e:
        logger.error(f"Failed to fetch Zoom custom questions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Zoom API Error: {str(e)}"
        )

@app.get("/api/auth/verify")
async def verify_auth_role(authorization: str = Header(None)):
    """
    Verifies the user's role on startup based on their Telegram initData.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authorization header provided."
        )
        
    if authorization == "MOCK_TOKEN" or not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "MOCK_TOKEN":
        # Mock admin role for testing or configuration
        return {
            "role": "admin",
            "telegram_id": config.ADMIN_CHAT_ID,
            "name": "Mock Admin"
        }
        
    data = verify_telegram_init_data(authorization, config.TELEGRAM_BOT_TOKEN)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initialization data signature."
        )
        
    try:
        user_info = json.loads(data.get("user", "{}"))
        telegram_id = user_info.get("id")
        username = user_info.get("username", "Unknown")
        first_name = user_info.get("first_name", "User")
        last_name = user_info.get("last_name", "")
        fullname = f"{first_name} {last_name}".strip()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to parse Telegram user info."
        )
        
    if not telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram ID not found."
        )

    # Check if they are admin
    if storage.is_admin(telegram_id):
        return {
            "role": "admin",
            "telegram_id": telegram_id,
            "name": fullname
        }
        
    # Check if they are configured in main admin config ID
    if telegram_id == config.ADMIN_CHAT_ID:
        return {
            "role": "admin",
            "telegram_id": telegram_id,
            "name": fullname
        }

    # Query DB for user status by telegram_id
    with storage.get_db() as cursor:
        storage.execute_query(
            cursor, 
            """
            SELECT registered_email, global_status, join_url, metadata, country,
                   (SELECT s.submitted_zoom_name FROM submissions_history s 
                    WHERE s.registered_email = u.registered_email 
                    ORDER BY s.action_timestamp DESC LIMIT 1) as zoom_name 
            FROM users u WHERE telegram_id = ?
            """, 
            (telegram_id,)
        )
        row = cursor.fetchone()
        
    if not row and username and username != "Unknown":
        # Check by Telegram username if username is set in history
        with storage.get_db() as cursor:
            storage.execute_query(
                cursor,
                "SELECT registered_email FROM submissions_history WHERE LOWER(submitted_telegram_username) = LOWER(?) ORDER BY action_timestamp DESC LIMIT 1",
                (username,)
            )
            hist_row = cursor.fetchone()
            if hist_row:
                email = hist_row["registered_email"]
                storage.execute_query(
                    cursor,
                    """
                    SELECT registered_email, global_status, join_url, metadata, country,
                           (SELECT s.submitted_zoom_name FROM submissions_history s 
                            WHERE s.registered_email = u.registered_email 
                            ORDER BY s.action_timestamp DESC LIMIT 1) as zoom_name 
                    FROM users u WHERE LOWER(registered_email) = LOWER(?)
                    """,
                    (email,)
                )
                row = cursor.fetchone()

    if not row:
        return {
            "role": "guest",
            "telegram_id": telegram_id,
            "name": fullname
        }
        
    status = row["global_status"]
    zoom_name = row["zoom_name"] or fullname
    join_url = row["join_url"]
    
    needs_additional_info = False
    user_metadata = []
    if row.get("metadata"):
        try:
            user_metadata = json.loads(row["metadata"])
        except Exception:
            pass
            
    try:
        current_questions = zoom_service.get_custom_questions()
        answered_titles = {item.get("title", "").strip().lower() for item in user_metadata if item.get("value")}
        for cq in current_questions.get("custom_questions", []):
            if cq.get("required"):
                title = cq.get("title", "").strip().lower()
                if title not in answered_titles:
                    needs_additional_info = True
                    break
    except Exception as e:
        logger.warning(f"Failed to check required questions in verify: {e}")

    # Parse first name and last name
    name_parts = (zoom_name or "").split(" ", 1)
    f_name = name_parts[0] if name_parts else ""
    l_name = name_parts[1] if len(name_parts) > 1 else ""

    user_profile = {
        "first_name": f_name,
        "last_name": l_name,
        "email": row["registered_email"],
        "country": row["country"] or "",
        "metadata": user_metadata
    }
    
    if status == "Blacklisted":
        return {
            "role": "blacklisted",
            "telegram_id": telegram_id
        }
    elif status == "Approved":
        return {
            "role": "active_user",
            "telegram_id": telegram_id,
            "name": zoom_name,
            "join_url": join_url,
            "needs_additional_info": needs_additional_info,
            "user_profile": user_profile if needs_additional_info else None
        }
    else:
        # Status is Pending or Denied or On Hold
        return {
            "role": "pending",
            "telegram_id": telegram_id,
            "name": zoom_name,
            "status": status,
            "needs_additional_info": needs_additional_info,
            "user_profile": user_profile if needs_additional_info else None
        }

@app.post("/api/register")
async def register_user(req: RegisterRequest):
    """
    Securely registers a user via Zoom API using WebApp initData verification.
    """
    # 1. Cryptographically verify user's identity
    data = verify_telegram_init_data(req.initData, config.TELEGRAM_BOT_TOKEN)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram initialization data signature."
        )
        
    # Extract User Info from raw WebApp JSON payload
    try:
        user_info = json.loads(data.get("user", "{}"))
        telegram_id = user_info.get("id")
        telegram_username = user_info.get("username", "None")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed user payload in initData."
        )
        
    if not telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram User ID could not be identified."
        )
        
    email = req.email.strip().lower()
    
    # 2. Check Blacklist and User Status
    global_status = storage.get_user_status(email)
    if global_status == "Blacklisted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration blocked. This email address has been blacklisted."
        )
        
    # 3. Register user in Zoom
    try:
        zoom_questions = []
        if req.custom_questions:
            zoom_questions = [{"title": q.title, "value": q.value} for q in req.custom_questions]
            
        zoom_res = zoom_service.register_registrant(
            email=email,
            first_name=req.first_name,
            last_name=req.last_name,
            custom_questions=zoom_questions,
            standard_fields=req.standard_fields
        )
        join_url = zoom_res["join_url"]
    except Exception as e:
        logger.error(f"Zoom registration API failure for {email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Zoom Registration failed: {str(e)}"
        )
        
    # 4. Insert registration submission into Database
    try:
        active_meeting_id = zoom_service.meeting_id
        country_code = None
        if req.standard_fields:
            country_code = req.standard_fields.get("country")
            
        sub_id = storage.add_submission(
            email=email,
            telegram_id=telegram_id,
            zoom_name=f"{req.first_name} {req.last_name}",
            telegram_username=telegram_username,
            meeting_id=active_meeting_id,
            action_taken="Pending",
            join_url=join_url,
            country=country_code,
            custom_questions=zoom_questions
        )
    except Exception as e:
        logger.error(f"Database insertion failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record registration submission in database."
        )
        
    # 5. Alert Administrator via Bot Decision Card
    try:
        user_profile = storage.get_user_by_email(email)
        behavior_notes = user_profile.get("behavior_notes") if user_profile else ""
        
        history = storage.get_submissions_by_email(email)
        submission_count = len(history)
        history_summary = f"Applied {submission_count} times." if submission_count > 1 else "First-time applicant."
        
        notes_preview = "None"
        if behavior_notes:
            notes_preview = behavior_notes.split("\n")[0][:40] + "..." if len(behavior_notes) > 40 else behavior_notes.split("\n")[0]
            
        zoom_name = f"{req.first_name} {req.last_name}"
        admin_message = (
            f"🔔 <b>New Zoom Registration Request</b> (via Mini App)\n\n"
            f"👤 <b>User Details:</b>\n"
            f"- <b>Zoom Name:</b> <code>{html.escape(zoom_name)}</code>\n"
            f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
            f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id}</code>)\n\n"
            f"📊 <b>History Summary:</b> {history_summary}\n"
            f"📝 <b>Notes Preview:</b> {html.escape(notes_preview)}\n\n"
            "Please choose an action:"
        )
        
        reply_markup = get_admin_keyboard(sub_id)
        
        # Send decision card to Notification Chat ID
        await bot.send_message(
            chat_id=config.NOTIFICATION_CHAT_ID,
            text=admin_message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to deliver Telegram Bot notification to Admin: {e}")
        
    return {
        "status": "success",
        "message": "Registration submitted successfully. The administrator has been notified.",
        "join_url": join_url
    }

# --- Admin Web Dashboard APIs ---

@app.get("/api/admin/stats")
async def get_admin_stats(admin_user = Depends(verify_admin_access)):
    """
    Returns summary counters for the Admin Dashboard.
    """
    try:
        with storage.get_db() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM users")
            total = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Pending'")
            pending = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Approved'")
            approved = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Denied'")
            denied = cursor.fetchone()["count"]
            
            # Query New Pending (<= 3 days)
            if storage.IS_POSTGRES:
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Pending' AND created_at >= NOW() - INTERVAL '3 days'")
            else:
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Pending' AND created_at >= datetime('now', '-3 days')")
            new_count = cursor.fetchone()["count"]
            
            # Query On Hold Pending (> 3 days)
            if storage.IS_POSTGRES:
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Pending' AND created_at < NOW() - INTERVAL '3 days'")
            else:
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE global_status = 'Pending' AND created_at < datetime('now', '-3 days')")
            on_hold_count = cursor.fetchone()["count"]
            
            last_sync = storage.get_setting("last_zoom_sync") or "Never"
            
            return {
                "total": total,
                "pending": pending,
                "approved": approved,
                "denied": denied,
                "new": new_count,
                "on_hold": on_hold_count,
                "last_sync": last_sync
            }
    except Exception as e:
        logger.error(f"Failed to fetch admin stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )

@app.get("/api/admin/requests")
async def get_admin_requests(
    status_filter: Optional[str] = None, 
    search: Optional[str] = None,
    admin_user = Depends(verify_admin_access)
):
    """
    Returns a filtered, searched, and sorted list of registrant profiles.
    """
    try:
        query_str = """
            SELECT u.registered_email, u.telegram_id, u.global_status, u.created_at, u.country, u.behavior_notes, u.metadata,
                   (SELECT s.submitted_zoom_name FROM submissions_history s
                    WHERE s.registered_email = u.registered_email
                    ORDER BY s.action_timestamp DESC LIMIT 1) as zoom_name,
                   (SELECT s.submitted_telegram_username FROM submissions_history s
                    WHERE s.registered_email = u.registered_email
                    ORDER BY s.action_timestamp DESC LIMIT 1) as telegram_username
            FROM users u
        """
        where_clauses = []
        params = []
        
        # Apply status filter
        if status_filter:
            if status_filter == "New":
                where_clauses.append("u.global_status = 'Pending'")
                if storage.IS_POSTGRES:
                    where_clauses.append("u.created_at >= NOW() - INTERVAL '3 days'")
                else:
                    where_clauses.append("u.created_at >= datetime('now', '-3 days')")
            elif status_filter == "OnHold":
                where_clauses.append("u.global_status = 'Pending'")
                if storage.IS_POSTGRES:
                    where_clauses.append("u.created_at < NOW() - INTERVAL '3 days'")
                else:
                    where_clauses.append("u.created_at < datetime('now', '-3 days')")
            elif status_filter in ("Pending", "Approved", "Denied", "Blacklisted", "Deferred"):
                where_clauses.append("u.global_status = %s" if storage.IS_POSTGRES else "u.global_status = ?")
                params.append(status_filter)
                
        # Apply search filter
        if search:
            search_param = f"%{search.lower()}%"
            placeholder = "%s" if storage.IS_POSTGRES else "?"
            where_clauses.append(f"""
                (LOWER(u.registered_email) LIKE {placeholder} OR 
                 LOWER(u.behavior_notes) LIKE {placeholder} OR
                 LOWER((SELECT s.submitted_zoom_name FROM submissions_history s WHERE s.registered_email = u.registered_email ORDER BY s.action_timestamp DESC LIMIT 1)) LIKE {placeholder} OR
                 LOWER((SELECT s.submitted_telegram_username FROM submissions_history s WHERE s.registered_email = u.registered_email ORDER BY s.action_timestamp DESC LIMIT 1)) LIKE {placeholder})
            """)
            params.extend([search_param, search_param, search_param, search_param])
            
        if where_clauses:
            query_str += " WHERE " + " AND ".join(where_clauses)
            
        query_str += " ORDER BY u.created_at DESC"
        
        with storage.get_db() as conn:
            cursor = conn.execute(query_str, tuple(params))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch requests for admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query error: {str(e)}"
        )

@app.get("/api/admin/history")
async def get_admin_history(email: str, admin_user = Depends(verify_admin_access)):
    """
    Returns the complete chronological history of registration submissions for a user.
    """
    try:
        history = storage.get_submissions_by_email(email)
        return history
    except Exception as e:
        logger.error(f"Failed to fetch history for email {email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load history: {str(e)}"
        )

@app.post("/api/admin/notes")
async def save_admin_notes(req: AdminNotesRequest, admin_user = Depends(verify_admin_access)):
    """
    Saves behavioral notes for a specific user.
    """
    try:
        email = req.email.strip().lower()
        notes = req.notes.strip()
        success = storage.update_user_status(email, None, behavior_notes=notes)
        if success:
            return {"status": "success", "message": "Behavior notes updated successfully."}
        else:
            raise HTTPException(status_code=404, detail="User profile not found.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update notes for {req.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database update error: {str(e)}"
        )

@app.post("/api/admin/action")
async def perform_admin_action(req: AdminActionRequest, admin_user = Depends(verify_admin_access)):
    """
    Processes single/bulk decisions (Approve, Deny, Blacklist) on registrants.
    """
    admin_tg_id = admin_user.get("id")
    admin_name = admin_user.get("first_name", "Administrator")
    
    success_emails = []
    failed_emails = []
    
    for email in req.emails:
        email = email.strip().lower()
        try:
            user_record = storage.get_user_by_email(email)
            if not user_record:
                failed_emails.append((email, "User not found in database."))
                continue
                
            # Perform action on Zoom API
            if req.action in ("Approve", "Deny"):
                zoom_action = "approve" if req.action == "Approve" else "deny"
                try:
                    zoom_service.update_registrant_status(email, zoom_action)
                except Exception as ze:
                    logger.error(f"Zoom API error during {req.action} for {email}: {ze}")
                    failed_emails.append((email, f"Zoom API failed: {str(ze)}"))
                    continue
            
            db_status = req.action
            if req.action == "Approve":
                db_status = "Approved"
            elif req.action == "Deny":
                db_status = "Denied"
            elif req.action == "Blacklist":
                db_status = "Blacklisted"
                try:
                    zoom_service.update_registrant_status(email, "deny")
                except Exception:
                    pass
            elif req.action in ("Pending", "On Hold"):
                db_status = "Pending"
                    
            storage.update_user_status(email, db_status)
            
            # Record in submissions history
            active_meeting_id = zoom_service.meeting_id
            history = storage.get_submissions_by_email(email)
            zoom_name = history[0]["submitted_zoom_name"] if history else "Zoom Registrant"
            telegram_username = history[0]["submitted_telegram_username"] if history else "Unknown"
            
            storage.add_submission(
                email=email,
                telegram_id=user_record["telegram_id"] or 0,
                zoom_name=zoom_name,
                telegram_username=telegram_username,
                meeting_id=active_meeting_id,
                action_taken=db_status
            )
            
            # Send Telegram Bot notification if user is linked to a Telegram ID
            tg_id = user_record["telegram_id"]
            if tg_id and tg_id != 0:
                try:
                    if db_status == "Approved":
                        join_url = user_record.get("join_url") or ""
                        if not join_url:
                            try:
                                reg_id = zoom_service.get_registrant_id_by_email(email)
                                if reg_id:
                                    join_url = f"https://zoom.us/w/{active_meeting_id}?tk="
                            except Exception:
                                pass
                        
                        btn_link = join_url or storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
                        keyboard = [[InlineKeyboardButton("Join Meeting 🎥", url=btn_link)]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await bot.send_message(
                            chat_id=tg_id,
                            text=(
                                f"🟢 <b>Congratulations! Your Zoom registration has been APPROVED.</b>\n\n"
                                f"👤 <b>Zoom Name:</b> {html.escape(zoom_name)}\n"
                                f"📧 <b>Email:</b> <code>{html.escape(email)}</code>\n\n"
                                "You can now join the meeting directly using the button below:"
                            ),
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    elif db_status == "Denied":
                        await bot.send_message(
                            chat_id=tg_id,
                            text=(
                                f"🔴 <b>Registration Request Update</b>\n\n"
                                f"Your request to join the Zoom meeting under email <code>{html.escape(email)}</code> has been denied by the administrator."
                            ),
                            parse_mode="HTML"
                        )
                except Exception as tge:
                    logger.error(f"Failed to notify user {tg_id} of status change: {tge}")
                    
            # Send status update alert to the group chat (NOTIFICATION_CHAT_ID)
            try:
                status_emoji = "🟢" if db_status == "Approved" else "🔴" if db_status == "Denied" else "🚫"
                await bot.send_message(
                    chat_id=config.NOTIFICATION_CHAT_ID,
                    text=(
                        f"{status_emoji} <b>User Registration {db_status.upper()}</b>\n\n"
                        f"- <b>Zoom Name:</b> {html.escape(zoom_name)}\n"
                        f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
                        f"- <b>Actioned By:</b> {html.escape(admin_name)} (Admin ID: <code>{admin_tg_id}</code>)"
                    ),
                    parse_mode="HTML"
                )
            except Exception as ae:
                logger.error(f"Failed to alert notification group of admin action: {ae}")
                
            success_emails.append(email)
        except Exception as e:
            logger.error(f"Error actioning {email}: {e}")
            failed_emails.append((email, str(e)))
            
    return {
        "status": "success",
        "processed": len(success_emails),
        "failed": len(failed_emails),
        "success_emails": success_emails,
        "failed_emails": [{"email": f[0], "reason": f[1]} for f in failed_emails]
    }

@app.get("/api/admin/metadata")
async def get_user_metadata(email: str, admin_user = Depends(verify_admin_access)):
    try:
        user_profile = storage.get_user_by_email(email)
        if not user_profile:
            raise HTTPException(status_code=404, detail="User not found")
        meta_str = user_profile.get("metadata")
        return json.loads(meta_str) if meta_str else {}
    except Exception as e:
        logger.error(f"Error fetching user metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/lookup-username")
async def lookup_telegram_username(username: str, admin_user = Depends(verify_admin_access)):
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    try:
        result = await userbot_service.resolve_username(username)
        if result:
            return {"status": "success", "result": result}
        else:
            raise HTTPException(status_code=404, detail="Username could not be resolved.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during username lookup: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/metadata")
async def update_user_metadata(req: AdminMetadataUpdateRequest, admin_user = Depends(verify_admin_access)):
    try:
        email = req.email.strip().lower()
        metadata_str = json.dumps(req.metadata)
        
        # Check if metadata contains Telegram Username
        tg_username = None
        if isinstance(req.metadata, list):
            for item in req.metadata:
                if item.get("title", "").strip().lower() in ("telegram username", "telegram_username", "username"):
                    tg_username = item.get("value", "").strip()
                    break
        elif isinstance(req.metadata, dict):
            for k, v in req.metadata.items():
                if k.strip().lower() in ("telegram username", "telegram_username", "username"):
                    tg_username = str(v).strip()
                    break
                    
        with storage.get_db() as cursor:
            storage.execute_query(
                cursor,
                "UPDATE users SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                (metadata_str, email)
            )
            
            # Auto-lookup telegram_id if missing and username is set
            if tg_username:
                storage.execute_query(
                    cursor,
                    "SELECT telegram_id FROM users WHERE LOWER(registered_email) = LOWER(?)",
                    (email,)
                )
                row = cursor.fetchone()
                if row and (not row["telegram_id"] or row["telegram_id"] == 0):
                    try:
                        resolved = await userbot_service.resolve_username(tg_username)
                        if resolved:
                            storage.execute_query(
                                cursor,
                                "UPDATE users SET telegram_id = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                                (resolved["telegram_id"], email)
                            )
                            logger.info(f"Auto-linked Telegram username {tg_username} to ID {resolved['telegram_id']} for {email}")
                    except Exception as le:
                        logger.error(f"Failed to auto-resolve username {tg_username}: {le}")
                        
        return {"status": "success", "message": "Metadata updated successfully"}
    except Exception as e:
        logger.error(f"Error updating user metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/history")
async def add_history_log(req: AdminAddHistoryRequest, admin_user = Depends(verify_admin_access)):
    try:
        email = req.email.strip().lower()
        zoom_name = req.submitted_zoom_name.strip()
        tg_user = req.submitted_telegram_username.strip()
        meet_id = req.meeting_id.strip()
        action = req.action_taken.strip()
        
        with storage.get_db() as cursor:
            if req.action_timestamp:
                storage.execute_query(
                    cursor,
                    """
                    INSERT INTO submissions_history (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken, action_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (email, zoom_name, tg_user, meet_id, action, req.action_timestamp)
                )
            else:
                storage.execute_query(
                    cursor,
                    """
                    INSERT INTO submissions_history (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (email, zoom_name, tg_user, meet_id, action)
                )
        return {"status": "success", "message": "History entry added successfully"}
    except Exception as e:
        logger.error(f"Error adding history log: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/history")
async def edit_history_log(req: AdminEditHistoryRequest, admin_user = Depends(verify_admin_access)):
    try:
        with storage.get_db() as cursor:
            storage.execute_query(
                cursor,
                """
                UPDATE submissions_history
                SET submitted_zoom_name = ?, submitted_telegram_username = ?, meeting_id = ?, action_taken = ?, action_timestamp = ?
                WHERE id = ?
                """,
                (req.submitted_zoom_name.strip(), req.submitted_telegram_username.strip(), req.meeting_id.strip(), req.action_taken.strip(), req.action_timestamp, req.id)
            )
        return {"status": "success", "message": "History entry updated successfully"}
    except Exception as e:
        logger.error(f"Error updating history log: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/history/{history_id}")
async def delete_history_log(history_id: int, admin_user = Depends(verify_admin_access)):
    try:
        with storage.get_db() as cursor:
            storage.execute_query(cursor, "DELETE FROM submissions_history WHERE id = ?", (history_id,))
        return {"status": "success", "message": "History entry deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting history log: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/settings")
async def get_admin_settings(admin_user = Depends(verify_admin_access)):
    try:
        zoom_meeting_id = storage.get_setting("zoom_meeting_id", config.ZOOM_MEETING_ID)
        zoom_account_id = storage.get_setting("zoom_account_id", config.ZOOM_ACCOUNT_ID)
        zoom_client_id = storage.get_setting("zoom_client_id", config.ZOOM_CLIENT_ID)
        zoom_client_secret = storage.get_setting("zoom_client_secret", config.ZOOM_CLIENT_SECRET)
        zoom_registration_link = storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
        zoom_sync_interval = storage.get_setting("zoom_sync_interval", "10 minutes")
        
        return {
            "zoom_meeting_id": zoom_meeting_id,
            "zoom_account_id": zoom_account_id,
            "zoom_client_id": zoom_client_id,
            "zoom_client_secret": zoom_client_secret,
            "zoom_registration_link": zoom_registration_link,
            "zoom_sync_interval": zoom_sync_interval
        }
    except Exception as e:
        logger.error(f"Failed to fetch admin settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/settings")
async def update_admin_settings(req: AdminSettingsUpdateRequest, admin_user = Depends(verify_admin_access)):
    try:
        storage.set_setting("zoom_meeting_id", req.zoom_meeting_id.strip())
        storage.set_setting("zoom_account_id", req.zoom_account_id.strip())
        storage.set_setting("zoom_client_id", req.zoom_client_id.strip())
        storage.set_setting("zoom_client_secret", req.zoom_client_secret.strip())
        storage.set_setting("zoom_registration_link", req.zoom_registration_link.strip())
        storage.set_setting("zoom_sync_interval", req.zoom_sync_interval.strip())
        
        # Clear zoom service access token to force re-auth with new credentials
        zoom_service._access_token = None
        zoom_service._token_expires_at = 0
        zoom_service._cached_creds = None
        
        return {"status": "success", "message": "Settings updated successfully"}
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/team")
async def get_admin_team(admin_user = Depends(verify_admin_access)):
    try:
        owner_record = {
            "telegram_id": int(config.ADMIN_CHAT_ID),
            "username": "Owner / Super-Admin",
            "added_at": "System Default",
            "is_owner": True
        }
        admins = storage.get_admins()
        formatted_admins = []
        for a in admins:
            formatted_admins.append({
                "telegram_id": a["telegram_id"],
                "username": a["username"] or "Unknown",
                "added_at": a["added_at"],
                "is_owner": False
            })
        return [owner_record] + formatted_admins
    except Exception as e:
        logger.error(f"Failed to load admin team: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/team")
async def add_admin_team_member(req: AdminTeamAddRequest, admin_user = Depends(verify_admin_access)):
    try:
        if int(req.telegram_id) == int(config.ADMIN_CHAT_ID):
            return {"status": "success", "message": "User is the owner and already has super-admin rights."}
            
        storage.add_admin(req.telegram_id, req.username.strip() if req.username else None)
        return {"status": "success", "message": "Administrator added successfully"}
    except Exception as e:
        logger.error(f"Failed to add administrator: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/team/{telegram_id}")
async def remove_admin_team_member(telegram_id: int, admin_user = Depends(verify_admin_access)):
    try:
        if int(telegram_id) == int(config.ADMIN_CHAT_ID):
            raise HTTPException(status_code=400, detail="Cannot revoke permissions from the primary super-admin owner.")
            
        storage.remove_admin(telegram_id)
        return {"status": "success", "message": "Administrator revoked successfully"}
    except Exception as e:
        logger.error(f"Failed to revoke administrator: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def sync_zoom_data() -> int:
    """
    Core synchronization function. Fetches registrants from Zoom and updates database.
    """
    active_meeting_id = zoom_service.meeting_id
    if not active_meeting_id:
        raise ValueError("No active meeting ID set.")
        
    zoom_registrants_by_status = {}
    for zoom_status in ["pending", "approved", "denied"]:
        zoom_registrants_by_status[zoom_status] = zoom_service.list_registrants(status=zoom_status)
        
    with storage.get_db() as cursor:
        storage.execute_query(cursor, "SELECT registered_email, telegram_id, global_status, created_at, country, zoom_registrant_id FROM users")
        existing_users = {row["registered_email"].lower().strip(): dict(row) for row in cursor.fetchall()}
        
        storage.execute_query(cursor, "SELECT registered_email FROM submissions_history WHERE meeting_id = ?", (active_meeting_id,))
        existing_history = {row["registered_email"].lower().strip() for row in cursor.fetchall()}
        
        sync_count = 0
        for db_status, registrants in zoom_registrants_by_status.items():
            for r in registrants:
                email = r.get("email")
                if not email:
                    continue
                email = email.strip().lower()
                
                first_name = r.get("first_name", "")
                last_name = r.get("last_name", "")
                zoom_name = f"{first_name} {last_name}".strip() or "Zoom Registrant"
                zoom_create_time = r.get("create_time")
                zoom_country = r.get("country")
                zoom_reg_id = r.get("id")
                zoom_custom_q = r.get("custom_questions")
                zoom_metadata_json = json.dumps(zoom_custom_q) if zoom_custom_q else None
                
                # Check for Telegram Username in custom questions
                tg_username = None
                if zoom_custom_q:
                    for q in zoom_custom_q:
                        if q.get("title", "").strip().lower() in ("telegram username", "telegram_username", "username"):
                            tg_username = q.get("value", "").strip()
                            break
                            
                resolved_tg_id = None
                if tg_username:
                    try:
                        resolved = await userbot_service.resolve_username(tg_username)
                        if resolved:
                            resolved_tg_id = resolved["telegram_id"]
                    except Exception:
                        pass
                
                status_normalized = "Approved" if db_status == "approved" else "Denied" if db_status == "denied" else "Pending"
                
                user_record = existing_users.get(email)
                if not user_record:
                    if zoom_create_time:
                        storage.execute_query(
                            cursor,
                            "INSERT INTO users (registered_email, telegram_id, global_status, created_at, country, zoom_registrant_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (email, resolved_tg_id, status_normalized, zoom_create_time, zoom_country, zoom_reg_id, zoom_metadata_json)
                        )
                    else:
                        storage.execute_query(
                            cursor,
                            "INSERT INTO users (registered_email, telegram_id, global_status, country, zoom_registrant_id, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                            (email, resolved_tg_id, status_normalized, zoom_country, zoom_reg_id, zoom_metadata_json)
                        )
                    sync_count += 1
                    existing_users[email] = {
                        "registered_email": email,
                        "telegram_id": resolved_tg_id,
                        "global_status": status_normalized,
                        "created_at": zoom_create_time,
                        "country": zoom_country,
                        "zoom_registrant_id": zoom_reg_id
                    }
                else:
                    # Update telegram_id if missing and resolved
                    if resolved_tg_id and (not user_record.get("telegram_id") or user_record.get("telegram_id") == 0):
                        storage.execute_query(
                            cursor,
                            "UPDATE users SET telegram_id = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                            (resolved_tg_id, email)
                        )
                        
                    if user_record["global_status"] != status_normalized:
                        storage.execute_query(
                            cursor,
                            "UPDATE users SET global_status = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                            (status_normalized, email)
                        )
                        sync_count += 1
                    
                    if zoom_country and user_record.get("country") != zoom_country:
                        storage.execute_query(
                            cursor,
                            "UPDATE users SET country = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                            (zoom_country, email)
                        )
                    
                    if zoom_reg_id and user_record.get("zoom_registrant_id") != zoom_reg_id:
                        storage.execute_query(
                            cursor,
                            "UPDATE users SET zoom_registrant_id = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                            (zoom_reg_id, email)
                        )
                        
                    if zoom_metadata_json:
                        storage.execute_query(
                            cursor,
                            "UPDATE users SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE LOWER(registered_email) = LOWER(?)",
                            (zoom_metadata_json, email)
                        )
                        
                if email not in existing_history:
                    if zoom_create_time:
                        storage.execute_query(
                            cursor,
                            """INSERT INTO submissions_history 
                               (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken, action_timestamp) 
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (email, zoom_name, "Unknown", active_meeting_id, status_normalized, zoom_create_time)
                        )
                    else:
                        storage.execute_query(
                            cursor,
                            """INSERT INTO submissions_history 
                               (registered_email, submitted_zoom_name, submitted_telegram_username, meeting_id, action_taken) 
                               VALUES (?, ?, ?, ?, ?)""",
                            (email, zoom_name, "Unknown", active_meeting_id, status_normalized)
                        )
                    existing_history.add(email)
                    
    from datetime import datetime, timezone, timedelta
    bangkok_tz = timezone(timedelta(hours=7))
    storage.set_setting("last_zoom_sync", datetime.now(bangkok_tz).strftime("%Y-%m-%d %H:%M:%S GMT+7"))
    return sync_count

@app.post("/api/admin/sync")
async def trigger_zoom_sync(admin_user = Depends(verify_admin_access)):
    try:
        sync_count = await sync_zoom_data()
        return {"status": "success", "message": f"Successfully synchronized {sync_count} profiles from Zoom."}
    except Exception as e:
        logger.error(f"Zoom sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Dynamic Human-Readable Duration Parser
import re
def parse_duration_to_seconds(duration_str: str) -> Optional[int]:
    if not duration_str:
        return 600
    normalized = duration_str.strip().lower()
    if normalized in ("disabled", "disable", "off", "0", "false", "none"):
        return None
    
    match = re.match(r"^(\d+)\s*(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|d|day|days)$", normalized)
    if not match:
        return 600
        
    value = int(match.group(1))
    unit = match.group(2)
    
    if unit.startswith("s"):
        return max(value, 10)  # limit minimum of 10s to prevent spamming
    elif unit.startswith("m"):
        return value * 60
    elif unit.startswith("h"):
        return value * 3600
    elif unit.startswith("d"):
        return value * 86400
    return 600

# Background Scheduler Task Loop
async def start_background_sync_loop():
    logger.info("Initializing background Zoom synchronization loop...")
    await asyncio.sleep(5) # Let Uvicorn startup cleanly
    
    while True:
        try:
            interval_str = storage.get_setting("zoom_sync_interval", "10 minutes")
            interval_seconds = parse_duration_to_seconds(interval_str)
            
            if interval_seconds is None:
                # Sync disabled, sleep and check setting again
                await asyncio.sleep(30)
                continue
                
            logger.info(f"Running periodic background Zoom sync (interval: {interval_str})...")
            
            try:
                sync_count = await sync_zoom_data()
                logger.info(f"Background Zoom sync complete. Synced {sync_count} records.")
            except Exception as se:
                logger.error(f"Sync error in background scheduler: {se}")
                
            await asyncio.sleep(interval_seconds)
            
        except asyncio.CancelledError:
            logger.info("Background Zoom sync task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in background sync loop thread: {e}")
            await asyncio.sleep(60)



# Mount static files folder to serve the frontend web client
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if not os.path.exists(frontend_dir):
    os.makedirs(frontend_dir)
    
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

def start_server():
    """
    Blocking call to start Uvicorn. Designed to run in a background daemon thread.
    """
    port = int(os.environ.get("PORT", 7860))
    logger.info(f"Starting FastAPI web server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

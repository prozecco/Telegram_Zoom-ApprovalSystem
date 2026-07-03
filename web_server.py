import os
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_server")

zoom_service = ZoomService()
app = FastAPI(title="Telegram Zoom App Web Server")

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
    action: str  # "Approve", "Deny", "Blacklist"

class AdminNotesRequest(BaseModel):
    email: str
    notes: str

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
            country=country_code
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
            
            return {
                "total": total,
                "pending": pending,
                "approved": approved,
                "denied": denied
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
            SELECT u.registered_email, u.telegram_id, u.global_status, u.created_at, u.country, u.behavior_notes,
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

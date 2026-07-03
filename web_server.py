import os
import hmac
import hashlib
import json
import logging
import html
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, status
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
        
        # Send decision card to Admin Chat ID
        await bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
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

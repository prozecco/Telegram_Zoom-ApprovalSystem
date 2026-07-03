import logging
import re
import html
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
import storage
from zoom_service import ZoomService

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Zoom Service
zoom_service = ZoomService()

# Conversation states for User Flow
AWAIT_ZOOM_NAME, AWAIT_EMAIL, AWAIT_CONFIRMATION = range(3)

# Conversation states for Configuration Flow
AWAIT_CONFIG_CHOICE, AWAIT_MID_INPUT, AWAIT_LINK_INPUT, AWAIT_CLIENT_ID_INPUT, AWAIT_CLIENT_SECRET_INPUT, AWAIT_ACCOUNT_ID_INPUT = range(3, 9)

# Conversation states for Admin Rights Management Flow
AWAIT_ADMIN_MANAGE_CHOICE, AWAIT_ADD_ADMIN_INPUT, AWAIT_REMOVE_ADMIN_CHOICE = range(9, 12)

# Conversation states for User Name Change Flow
AWAIT_NEW_NAME_INPUT = 12

# Conversation states for User Search Flow
AWAIT_SEARCH_INPUT = 13

# ==========================================
# HEALTH CHECK HTTP SERVER FOR HUGGING FACE
# ==========================================

class HealthCheckHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

def start_health_check_server():
    """
    Exposes an HTTP server on port 7860 to conform to Hugging Face Spaces requirements.
    Gracefully catches and logs errors if port is restricted or in use locally.
    """
    port = int(os.environ.get("PORT", 7860))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info("Health check server starting on port %s...", port)
        server.serve_forever()
    except Exception as e:
        logger.warning(
            "Could not start health check HTTP server on port %s: %s. "
            "This is normal if running locally on Windows or if the port is already in use.",
            port, e
        )

# Helper function to validate email format
def is_valid_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email.strip()))

# Helper to reply safely to both text commands and callback queries
async def reply_helper(update: Update, text: str, reply_markup=None) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

# Helper to generate the main Admin Control Panel inline keyboard markup
def get_admin_panel_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📋 View Requests List", callback_data="admin_requests"),
            InlineKeyboardButton("✏️ View Name Changes", callback_data="admin_name_changes")
        ],
        [
            InlineKeyboardButton("🔍 Search User", callback_data="admin_search"),
            InlineKeyboardButton("⚙️ Configure Zoom", callback_data="admin_config")
        ],
        [
            InlineKeyboardButton("📊 System Report", callback_data="admin_report"),
            InlineKeyboardButton("👤 Manage Admins", callback_data="admin_manage")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Helper to generate the main User Menu inline keyboard markup
def get_user_menu_markup(user_id: int) -> InlineKeyboardMarkup:
    mini_app_url = storage.get_setting("mini_app_url", os.getenv("MINI_APP_URL", "http://localhost:7860"))
    is_https_webapp = mini_app_url.lower().startswith("https://")
    registration_link = storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
    
    first_row = []
    if is_https_webapp:
        first_row.append(InlineKeyboardButton("🔗 Register via Mini App", web_app=WebAppInfo(url=mini_app_url)))
    else:
        first_row.append(InlineKeyboardButton("🔗 Register on Zoom", url=registration_link))
        
    first_row.append(InlineKeyboardButton("📝 Request Approval", callback_data="user_register"))
    
    keyboard = [first_row]
    
    user_record = storage.get_user_by_telegram_id(user_id)
    if user_record and user_record["global_status"] == "Approved":
        keyboard.append([InlineKeyboardButton("✏️ Request Name Change", callback_data="user_name_change")])
        
    keyboard.append([InlineKeyboardButton("ℹ️ How It Works", callback_data="user_help")])
    return InlineKeyboardMarkup(keyboard)

# Helper to generate the administrative inline keyboard (standard registration)
def get_admin_keyboard(sub_id: int) -> InlineKeyboardMarkup:
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
        ],
        [
            InlineKeyboardButton("Back to List 📋", callback_data="admin_requests")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

_zoom_health_cache = None
_zoom_health_cache_time = 0.0

def get_zoom_health() -> tuple[str, str]:
    global _zoom_health_cache, _zoom_health_cache_time
    import time
    if _zoom_health_cache and (time.time() - _zoom_health_cache_time < 30):
        return _zoom_health_cache
        
    try:
        # 1. Test token acquisition
        token = zoom_service._get_access_token()
        api_status = "Healthy 🟢"
    except Exception as e:
        err_msg = str(e)
        if "invalid_client" in err_msg or "Invalid client_id" in err_msg:
            api_status = "Error 🔴 (Invalid Credentials)"
        else:
            api_status = "Error 🔴 (Connection Failed)"
        _zoom_health_cache = (api_status, "Broken 🔴 (API Auth Failed)")
        _zoom_health_cache_time = time.time()
        return _zoom_health_cache
        
    try:
        # 2. Test meeting lookup
        meeting_id = zoom_service.meeting_id
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/registrants"
        headers = {
            "Authorization": f"Bearer {token}"
        }
        import requests
        res = requests.get(url, headers=headers, params={"page_size": 1}, timeout=5)
        if res.status_code in (200, 201):
            meeting_status = "Active 🟢"
        else:
            logger.warning("Zoom meeting health check failed: status=%s, response=%s", res.status_code, res.text)
            if res.status_code == 404:
                meeting_status = "Broken 🔴 (Meeting Not Found)"
            elif res.status_code == 400:
                meeting_status = "Broken 🔴 (Invalid Meeting ID)"
            else:
                meeting_status = f"Broken 🔴 (Status {res.status_code})"
    except Exception as e:
        logger.exception("Zoom meeting health check exception")
        meeting_status = "Error 🔴"
        
    _zoom_health_cache = (api_status, meeting_status)
    _zoom_health_cache_time = time.time()
    return _zoom_health_cache

def get_admin_panel_text(user_first_name: str, bot_hosting: str, db_type: str) -> str:
    api_status, meeting_status = get_zoom_health()
    return (
        f"🛡️ <b>Admin Control Panel</b>\n\n"
        f"🌐 <b>Bot Hosting:</b> <code>{bot_hosting}</code>\n"
        f"🗄️ <b>Database:</b> <code>{db_type}</code>\n"
        f"🔑 <b>Zoom API Status:</b> <code>{api_status}</code>\n"
        f"📅 <b>Zoom Meeting:</b> <code>{meeting_status}</code>\n\n"
        f"Welcome back, <b>{html.escape(user_first_name)}</b>!\n"
        "Please select a management task from the menu below:"
    )

def get_admin_panel_back_text(bot_hosting: str, db_type: str) -> str:
    api_status, meeting_status = get_zoom_health()
    return (
        f"🛡️ <b>Admin Control Panel</b>\n\n"
        f"🌐 <b>Bot Hosting:</b> <code>{bot_hosting}</code>\n"
        f"🗄️ <b>Database:</b> <code>{db_type}</code>\n"
        f"🔑 <b>Zoom API Status:</b> <code>{api_status}</code>\n"
        f"📅 <b>Zoom Meeting:</b> <code>{meeting_status}</code>\n\n"
        "Welcome back. Please select a task from the menu below:"
    )

# ==========================================
# USER & ADMIN MAIN MENUS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Greets the user and presents either the Admin Control Panel or the User Menu.
    """
    user = update.effective_user
    logger.info("User %s (%s) started the conversation.", user.first_name, user.id)
    
    is_user_admin = storage.is_admin(user.id)
    
    if is_user_admin:
        # Determine environment details for display
        db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
        bot_hosting = "Local Machine"
        if "PORT" in os.environ:
            if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
                bot_hosting = "Cloud (Hugging Face Spaces)"
            else:
                bot_hosting = "Cloud (Render/PaaS)"
        elif "RENDER" in os.environ:
            bot_hosting = "Cloud (Render)"

        reply_markup = get_admin_panel_markup()
        
        admin_text = get_admin_panel_text(user.first_name, bot_hosting, db_type)
        await update.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        # Regular User Main Menu
        # Check if the user already has a registration record in database
        user_record = storage.get_user_by_telegram_id(user.id)
        status_message = ""
        if user_record:
            status = user_record["global_status"]
            email = user_record["registered_email"]
            history = storage.get_submissions_by_email(email)
            zoom_name = history[0]["submitted_zoom_name"] if history else "Zoom Applicant"
            
            status_emojis = {
                "Pending": "🟡 Pending review",
                "Approved": "🟢 Approved",
                "Denied": "🔴 Denied",
                "Blacklisted": "🚫 Blacklisted"
            }
            status_text = status_emojis.get(status, status)
            status_message = (
                f"📋 <b>Your Current Status:</b>\n"
                f"- <b>Zoom Name:</b> {html.escape(zoom_name)}\n"
                f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
                f"- <b>Status:</b> {status_text}\n\n"
            )
            
        reply_markup = get_user_menu_markup(user.id)
        
        await update.message.reply_text(
            f"Welcome <b>{html.escape(user.first_name)}</b> to the Telegram & Zoom Automated Approval System! 🚀\n\n"
            f"{status_message}"
            "Please select an option from the menu below to get started:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    
    return ConversationHandler.END

async def user_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes User menu selection callback buttons.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "user_link":
        registration_link = storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
        keyboard = [[InlineKeyboardButton("Back to Menu ⬅️", callback_data="back_to_user_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"🔗 <b>Zoom Registration Link:</b>\n"
            f"<a href=\"{registration_link}\">Click here to register on Zoom first</a>\n\n"
            "Once you have registered, return here and tap <b>Request Approval</b> from the menu.",
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    elif query.data == "user_help":
        keyboard = [[InlineKeyboardButton("Back to Menu ⬅️", callback_data="back_to_user_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            "ℹ️ <b>How it works:</b>\n\n"
            "1️⃣ Click the <b>Register on Zoom</b> link and complete the form.\n"
            "2️⃣ Tap <b>Request Approval</b> on this bot.\n"
            "3️⃣ Submit your Zoom Name and Registered Email.\n"
            "4️⃣ The administrator will review and approve your registration automatically!",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    elif query.data == "back_to_user_menu":
        user_id = query.from_user.id
        user_record = storage.get_user_by_telegram_id(user_id)
        status_message = ""
        if user_record:
            status = user_record["global_status"]
            email = user_record["registered_email"]
            history = storage.get_submissions_by_email(email)
            zoom_name = history[0]["submitted_zoom_name"] if history else "Zoom Applicant"
            
            status_emojis = {
                "Pending": "🟡 Pending review",
                "Approved": "🟢 Approved",
                "Denied": "🔴 Denied",
                "Blacklisted": "🚫 Blacklisted"
            }
            status_text = status_emojis.get(status, status)
            status_message = (
                f"📋 <b>Your Current Status:</b>\n"
                f"- <b>Zoom Name:</b> {html.escape(zoom_name)}\n"
                f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
                f"- <b>Status:</b> {status_text}\n\n"
            )
            
        reply_markup = get_user_menu_markup(user_id)
        
        await query.message.reply_text(
            f"{status_message}"
            "Please select an option from the menu below to get started:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

# ==========================================
# USER CONVERSATION FLOW (NEW REGISTRATIONS)
# ==========================================

async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point to user registration conversation flow.
    """
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "Great! Let's request approval for your registration.\n\n"
        "✍️ Please type your <b>Zoom Display Name</b> exactly as it appears on Zoom:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_ZOOM_NAME

async def zoom_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the Zoom Name and prompts for the email.
    """
    zoom_name = update.message.text.strip()
    context.user_data["zoom_name"] = zoom_name
    
    keyboard = [
        [
            InlineKeyboardButton("Back ⬅️", callback_data="back_to_name"),
            InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Got it: <b>{html.escape(zoom_name)}</b>.\n\n"
        "📧 Now, please type the <b>Email Address</b> you used to register on Zoom:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_EMAIL

async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Validates email format, verifies it is registered on Zoom, then prompts for confirmation.
    """
    email = update.message.text.strip().lower()
    
    if not is_valid_email(email):
        keyboard = [
            [
                InlineKeyboardButton("Back ⬅️", callback_data="back_to_name"),
                InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ That doesn't look like a valid email address. Please type it again:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return AWAIT_EMAIL

    # Check if registered on Zoom before accepting
    await update.message.reply_text("🔍 Verifying your email against the Zoom meeting registration list...")
    is_registered = zoom_service.is_email_registered_on_zoom(email)
    
    if not is_registered:
        registration_link = storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
        keyboard = [
            [
                InlineKeyboardButton("Register on Zoom 🔗", url=registration_link),
                InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ <b>Email Not Registered on Zoom</b>\n\n"
            f"We could not find a Zoom registration for the email <code>{html.escape(email)}</code> under the active meeting.\n\n"
            "Please make sure to complete your Zoom registration using the link below first, and then type your email here again:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return AWAIT_EMAIL
        
    context.user_data["zoom_email"] = email
    zoom_name = context.user_data["zoom_name"]
    telegram_username = update.effective_user.username or "None"
    
    # Prompt for confirmation
    keyboard = [
        [
            InlineKeyboardButton("Submit Details ✅", callback_data="submit_reg"),
            InlineKeyboardButton("Start Over 🔄", callback_data="start_over")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Please confirm your registration details:\n\n"
        f"👤 <b>Zoom Name:</b> {html.escape(zoom_name)}\n"
        f"📧 <b>Email Address:</b> {html.escape(email)}\n"
        f"💬 <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{update.effective_user.id}</code>)\n\n"
        "Are these details correct?",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_CONFIRMATION

async def start_over(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Restarts the user conversation flow.
    """
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "Let's start over.\n\n"
        "✍️ Please type your <b>Zoom Display Name</b> exactly as it appears on Zoom:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_ZOOM_NAME

async def submit_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Submits user details to the database and notifies the administrator with a compact card.
    """
    query = update.callback_query
    await query.answer()
    
    email = context.user_data["zoom_email"]
    zoom_name = context.user_data["zoom_name"]
    telegram_id = update.effective_user.id
    telegram_username = update.effective_user.username or "None"
    
    # Check current status in DB
    user_record = storage.get_user_by_email(email)
    is_blacklisted = False
    behavior_notes = ""
    
    if user_record:
        is_blacklisted = (user_record.get("global_status") == "Blacklisted")
        behavior_notes = user_record.get("behavior_notes") or ""
        
    # Check if this Telegram ID has other associated email profiles
    other_profiles = []
    with storage.get_db() as conn:
        cursor = conn.execute(
            "SELECT registered_email, global_status, behavior_notes FROM users WHERE telegram_id = ? AND LOWER(registered_email) != LOWER(?)",
            (telegram_id, email)
        )
        other_profiles = [dict(row) for row in cursor.fetchall()]
        
    tg_warning = ""
    tg_history_line = ""
    if other_profiles:
        profile_states = []
        for p in other_profiles:
            profile_states.append(f"{p['registered_email']} ({p['global_status']})")
            if p['global_status'] == "Blacklisted":
                is_blacklisted = True
                tg_warning += f"🚨 <b>WARNING: Linked Telegram ID has a BLACKLISTED email: <code>{p['registered_email']}</code>!</b>\n\n"
            elif p['global_status'] == "Denied":
                tg_warning += f"⚠️ <b>WARNING: Linked Telegram ID was previously DENIED under email: <code>{p['registered_email']}</code>!</b>\n\n"
            
            # Carry over behavior notes if any
            if p['behavior_notes'] and not behavior_notes:
                behavior_notes = f"[From linked profile {p['registered_email']}] {p['behavior_notes']}"
                
        tg_history_line = f"- <b>Linked Telegram Profiles:</b> {', '.join(profile_states)}\n"
        
    active_meeting_id = storage.get_setting("zoom_meeting_id", config.ZOOM_MEETING_ID)
        
    # Log submission to history
    sub_id = storage.add_submission(
        email=email,
        telegram_id=telegram_id,
        zoom_name=zoom_name,
        telegram_username=telegram_username,
        meeting_id=active_meeting_id,
        action_taken="Pending"
    )
    
    # Respond to user
    await query.message.reply_text(
        "Thank you! Your registration details have been submitted. "
        "The administrator will review your registration shortly."
    )
    
    # Compile compact history metrics
    history = storage.get_submissions_by_email(email)
    submission_count = len(history)
    history_summary = f"Applied {submission_count} times." if submission_count > 1 else "First-time applicant."
    
    # Check if there are behavior notes
    notes_preview = "None"
    if behavior_notes:
        notes_preview = behavior_notes.split("\n")[0][:40] + "..." if len(behavior_notes) > 40 else behavior_notes.split("\n")[0]
        
    # Build blacklist warning block if applicable
    blacklist_warning = ""
    if is_blacklisted:
        blacklist_warning = "🚨 <b>WARNING: THIS USER IS BLACKLISTED!</b>\n\n"
        
    # Build Admin Decision Card message (Compact Format)
    admin_message = (
        f"🔔 <b>New Zoom Registration Request</b>\n\n"
        f"{blacklist_warning}"
        f"{tg_warning}"
        f"👤 <b>User Details:</b>\n"
        f"- <b>Zoom Name:</b> <code>{html.escape(zoom_name)}</code>\n"
        f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
        f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id}</code>)\n"
        f"{tg_history_line}\n"
        f"📊 <b>History Summary:</b> {history_summary}\n"
        f"📝 <b>Notes Preview:</b> {html.escape(notes_preview)}\n\n"
        "Please choose an action:"
    )
    
    reply_markup = get_admin_keyboard(sub_id)
    
    # Send decision card to Notification Chat ID
    await context.bot.send_message(
        chat_id=config.NOTIFICATION_CHAT_ID,
        text=admin_message,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    # End conversation state machine for this user
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels the user conversation.
    """
    await update.message.reply_text("Registration cancelled. You can type /start to try again.")
    context.user_data.clear()
    return ConversationHandler.END

# Callback query button handlers for Back and Cancel actions during form filling

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels the conversation via callback query button and redirects to User menu.
    """
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    reply_markup = get_user_menu_markup(query.from_user.id)
    await query.message.reply_text(
        "❌ Registration cancelled.\n\n"
        "Please select an option from the menu below to get started:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def back_to_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Goes back to the Zoom Name prompt.
    """
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Cancel ❌", callback_data="cancel_conv")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "✍️ Please type your <b>Zoom Display Name</b> exactly as it appears on Zoom:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_ZOOM_NAME

# ==========================================
# USER CONVERSATION FLOW (NAME CHANGES)
# ==========================================

async def start_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the Zoom Name Change conversation flow.
    """
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_record = storage.get_user_by_telegram_id(user_id)
    if not user_record:
        await query.message.reply_text("⚠️ You must have an approved registration before requesting a name change.")
        return ConversationHandler.END
        
    context.user_data["registered_email"] = user_record["registered_email"]
    
    keyboard = [[InlineKeyboardButton("Cancel ❌", callback_data="cancel_name_change")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "✏️ <b>Zoom Name Change Request</b>\n\n"
        "✍️ Please type your <b>new Zoom Display Name</b> exactly as it appears on Zoom:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_NEW_NAME_INPUT

async def name_change_input_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Prompts user to submit or cancel their requested new display name.
    """
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("⚠️ Name cannot be empty. Please type again:")
        return AWAIT_NEW_NAME_INPUT
        
    context.user_data["new_name"] = new_name
    email = context.user_data["registered_email"]
    
    keyboard = [
        [
            InlineKeyboardButton("Submit Request ✅", callback_data="submit_name_change"),
            InlineKeyboardButton("Cancel ❌", callback_data="cancel_name_change")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Please confirm your name change request details:\n\n"
        f"📧 <b>Registered Email:</b> {html.escape(email)}\n"
        f"✏️ <b>New Zoom Name:</b> {html.escape(new_name)}\n\n"
        "Do you want to submit this request?",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_NEW_NAME_INPUT

async def submit_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Logs the name change request to submissions_history as NameChangePending and notifies admins.
    """
    query = update.callback_query
    await query.answer()
    
    email = context.user_data["registered_email"]
    new_name = context.user_data["new_name"]
    telegram_id = query.from_user.id
    telegram_username = query.from_user.username or "None"
    active_meeting_id = storage.get_setting("zoom_meeting_id", config.ZOOM_MEETING_ID)
    
    # Fetch previous name details and name changes count
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT submitted_zoom_name FROM submissions_history
            WHERE registered_email = ? AND action_taken IN ('Approved', 'ApprovedNameChange')
            ORDER BY action_timestamp DESC LIMIT 1
            """,
            (email,)
        )
        prev = cursor.fetchone()
        prev_name = prev["submitted_zoom_name"] if prev else "None"
        
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM submissions_history WHERE registered_email = ? AND action_taken = 'ApprovedNameChange'",
            (email,)
        )
        name_change_count = cursor.fetchone()["count"]

    # Log to submissions_history (leaves global_status in users unchanged as 'Approved')
    sub_id = storage.add_submission(
        email=email,
        telegram_id=telegram_id,
        zoom_name=new_name,
        telegram_username=telegram_username,
        meeting_id=active_meeting_id,
        action_taken="NameChangePending"
    )
    
    # Notify Admin (Compact Format with Old Name and Name Change metrics)
    admin_message = (
        f"✏️ <b>Zoom Name Change Request</b>\n\n"
        f"👤 <b>User Details:</b>\n"
        f"- <b>Email:</b> <code>{html.escape(email)}</code>\n"
        f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id}</code>)\n\n"
        f"⏪ <b>Current Approved Name:</b> <code>{html.escape(prev_name)}</code>\n"
        f"⏩ <b>Requested New Name:</b> <code>{html.escape(new_name)}</code>\n\n"
        f"📊 <b>History:</b> Approved name changes: {name_change_count} times.\n\n"
        "Please choose an action:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Approve Name Change ✅", callback_data=f"apprname_{sub_id}"),
            InlineKeyboardButton("Deny Name Change ❌", callback_data=f"denyname_{sub_id}")
        ],
        [
            InlineKeyboardButton("History 📜", callback_data=f"viewhist_{sub_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=config.NOTIFICATION_CHAT_ID,
        text=admin_message,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    # Respond to user
    await query.message.reply_text("✅ Your name change request has been submitted for administrator review.")
    context.user_data.clear()
    
    # Back to user menu
    reply_markup_user = get_user_menu_markup(query.from_user.id)
    await query.message.reply_text(
        "Return to main menu:",
        reply_markup=reply_markup_user
    )
    return ConversationHandler.END

async def cancel_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels the name change conversation and returns to user menu.
    """
    query = update.callback_query
    if query:
        await query.answer()
        context.user_data.clear()
        
        reply_markup = get_user_menu_markup(query.from_user.id)
        await query.message.reply_text(
            "❌ Name change request cancelled.\n\n"
            "Return to main menu:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text("Name change request cancelled.")
        context.user_data.clear()
    return ConversationHandler.END

# ==========================================
# ADMIN DASHBOARD DECISION CALLBACKS
# ==========================================

async def admin_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes the administrator action buttons from decision cards.
    """
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Enforce Admin security check
    if not storage.is_admin(user_id):
        await query.answer("Unauthorized access.", show_alert=True)
        return
        
    data = query.data
    match = re.match(r"^(approve|deny|later|blacklist|editnotes|reviewreq|reviewname|apprname|denyname|viewhist)_(\d+)$", data)
    if not match:
        await query.answer("Invalid callback data.")
        return
        
    action, sub_id_str = match.groups()
    sub_id = int(sub_id_str)
    
    # Fetch submission details
    with storage.get_db() as conn:
        cursor = conn.execute("SELECT * FROM submissions_history WHERE id = ?", (sub_id,))
        submission = cursor.fetchone()
        
    if not submission:
        await query.answer("Error: Submission log not found.", show_alert=True)
        return
        
    email = submission["registered_email"]
    zoom_name = submission["submitted_zoom_name"]
    telegram_username = submission["submitted_telegram_username"]
    telegram_id = None
    
    # Get telegram_id from users table
    user_profile = storage.get_user_by_email(email)
    if user_profile:
        telegram_id = user_profile["telegram_id"]

    original_text = query.message.text_html
    
    # Extract the base text of the card, removing any status indicators
    clean_text = original_text.split("Please choose an action:")[0]
    for indicator in [
        "\n\n🟢 <b>Current Status:</b>", 
        "\n\n🔴 <b>Current Status:</b>", 
        "\n\n🟡 <b>Current Status:</b>", 
        "\n\n🚫 <b>Current Status:</b>",
        "\n\n⏳ <b>Status Update:</b>",
        "\n\n✅ <b>Action Taken:</b>",
        "\n\n❌ <b>Action Taken:</b>",
        "\n\n🚫 <b>Action Taken:</b>",
        "\n\n🟢 <b>Name Change Approved</b>",
        "\n\n🔴 <b>Name Change Denied</b>"
    ]:
        clean_text = clean_text.split(indicator)[0]
    clean_text = clean_text.strip()
    
    # Helper to notify user
    async def try_notify_user(msg: str):
        if telegram_id:
            try:
                await context.bot.send_message(chat_id=telegram_id, text=msg, parse_mode="HTML")
            except Exception as e:
                logger.warning("Could not notify user %s: %s", telegram_id, e)

    try:
        if action == "approve":
            await query.answer("Processing Approval...")
            zoom_service.update_registrant_status(email, "approve")
            storage.update_user_status(email, "Approved")
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'Approved' WHERE id = ?", (sub_id,))
                
            user_profile = storage.get_user_by_email(email)
            join_url = user_profile.get("join_url") if user_profile else None
            
            msg = (
                "🎉 <b>Congratulations!</b>\n"
                "Your registration request for the Zoom meeting has been <b>Approved</b>.\n"
            )
            if join_url:
                msg += f"\nHere is your unique link: <a href=\"{join_url}\">Join Meeting</a>"
            else:
                msg += "\nYou will receive a confirmation email from Zoom containing your joining link."
                
            await try_notify_user(msg)
            await query.edit_message_text(
                text=f"{clean_text}\n\n🟢 <b>Current Status:</b> Approved by admin.\n\nPlease choose an action:",
                reply_markup=query.message.reply_markup,
                parse_mode="HTML"
            )
            
        elif action == "deny":
            await query.answer("Processing Denial...")
            zoom_service.update_registrant_status(email, "deny")
            storage.update_user_status(email, "Denied")
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'Denied' WHERE id = ?", (sub_id,))
                
            await try_notify_user(
                "❌ <b>Registration Denied</b>\n"
                "Your Zoom registration request has been denied by the administrator."
            )
            await query.edit_message_text(
                text=f"{clean_text}\n\n🔴 <b>Current Status:</b> Denied by admin.\n\nPlease choose an action:",
                reply_markup=query.message.reply_markup,
                parse_mode="HTML"
            )
            
        elif action == "later":
            await query.answer("Putting request on hold...")
            storage.update_user_status(email, "Pending")
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'Deferred' WHERE id = ?", (sub_id,))
                
            await try_notify_user(
                "⏳ <b>Registration On Hold</b>\n"
                "Your Zoom registration request has been put on hold for review."
            )
            await query.edit_message_text(
                text=f"{clean_text}\n\n🟡 <b>Current Status:</b> Deferred (Review Later).\n\nPlease choose an action:",
                reply_markup=query.message.reply_markup,
                parse_mode="HTML"
            )
            
        elif action == "blacklist":
            await query.answer("Blacklisting Identity...")
            try:
                zoom_service.update_registrant_status(email, "deny")
            except Exception as e:
                logger.info("Zoom deny failed during blacklist: %s", e)
                
            storage.update_user_status(email, "Blacklisted", behavior_notes="Blacklisted via Admin decision.")
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'Blacklisted' WHERE id = ?", (sub_id,))
                
            await try_notify_user(
                "❌ <b>Registration Denied</b>\n"
                "Your Zoom registration request has been denied by the administrator."
            )
            await query.edit_message_text(
                text=f"{clean_text}\n\n🚫 <b>Current Status:</b> Blacklisted.\n\nPlease choose an action:",
                reply_markup=query.message.reply_markup,
                parse_mode="HTML"
            )
            
        elif action == "editnotes":
            await query.answer()
            await context.bot.send_message(
                chat_id=config.NOTIFICATION_CHAT_ID,
                text=(
                    f"📝 <b>Edit Notes for {html.escape(email)}</b>\n\n"
                    "Tap the command below to copy it, add your notes, and send:\n"
                    f"<code>/notes {html.escape(email)} </code>"
                ),
                parse_mode="HTML"
            )
            
        elif action == "reviewreq":
            await query.answer("Loading details...")
            
            global_status = user_profile["global_status"] if user_profile else "Pending"
            behavior_notes = user_profile["behavior_notes"] if user_profile else ""
            
            history = storage.get_submissions_by_email(email)
            submission_count = len(history)
            history_summary = f"Applied {submission_count} times." if submission_count > 1 else "First-time applicant."
            
            notes_preview = "None"
            if behavior_notes:
                notes_preview = behavior_notes.split("\n")[0][:40] + "..." if len(behavior_notes) > 40 else behavior_notes.split("\n")[0]
                
            blacklist_warning = ""
            if global_status == "Blacklisted":
                blacklist_warning = "🚨 <b>WARNING: THIS USER IS BLACKLISTED!</b>\n\n"
                
            status_emojis = {
                "Pending": "🟡 Pending review.",
                "Approved": "🟢 Approved by admin.",
                "Denied": "🔴 Denied by admin.",
                "Blacklisted": "🚫 Blacklisted.",
                "Deferred": "⏳ Deferred (Review Later)."
            }
            status_text = status_emojis.get(global_status, f"{global_status}")
            
            admin_message = (
                f"🔔 <b>Zoom Registration Request Panel</b>\n\n"
                f"{blacklist_warning}"
                f"👤 <b>User Details:</b>\n"
                f"- <b>Zoom Name:</b> <code>{html.escape(zoom_name)}</code>\n"
                f"- <b>Registered Email:</b> <code>{html.escape(email)}</code>\n"
                f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id}</code>)\n\n"
                f"📊 <b>History Summary:</b> {history_summary}\n"
                f"📝 <b>Notes Preview:</b> {html.escape(notes_preview)}\n\n"
                f"🟡 <b>Current Status:</b> {status_text}\n\n"
                "Please choose an action:"
            )
            
            reply_markup = get_admin_keyboard(sub_id)
            await context.bot.send_message(
                chat_id=user_id,
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            
        elif action == "reviewname":
            await query.answer("Loading details...")
            await review_name_change_card(update, context, sub_id)
            
        elif action == "apprname":
            await query.answer("Approving Name Change...")
            zoom_service.update_registrant_name(email, zoom_name)
            
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'ApprovedNameChange' WHERE id = ?", (sub_id,))
                
            await try_notify_user(
                "✅ <b>Name Change Approved!</b>\n"
                f"Your request to update your Zoom Display Name to <b>{html.escape(zoom_name)}</b> has been approved."
            )
            await query.edit_message_text(
                text=f"{clean_text}\n\n🟢 <b>Name Change Approved</b>",
                parse_mode="HTML"
            )
            
        elif action == "denyname":
            await query.answer("Denying Name Change...")
            with storage.get_db() as conn:
                conn.execute("UPDATE submissions_history SET action_taken = 'DeniedNameChange' WHERE id = ?", (sub_id,))
                
            await try_notify_user(
                "❌ <b>Name Change Denied</b>\n"
                f"Your request to update your Zoom Display Name to <b>{html.escape(zoom_name)}</b> was denied."
            )
            await query.edit_message_text(
                text=f"{clean_text}\n\n🔴 <b>Name Change Denied</b>",
                parse_mode="HTML"
            )
            
        elif action == "viewhist":
            await view_full_history(update, context)
            
    except Exception as e:
        logger.error("Error performing admin action %s: %s", action, e)
        await query.answer(f"⚠️ Error: {str(e)}", show_alert=True)
        await query.edit_message_text(
            text=f"⚠️ <b>Error:</b> {html.escape(str(e))}\n\n{clean_text}\n\nPlease choose an action:",
            reply_markup=query.message.reply_markup,
            parse_mode="HTML"
        )

# ==========================================
# HISTORY AUDIT LOG EXPANSION VIEWER
# ==========================================

async def view_full_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends the detailed chronological timeline audit log for a user's registration.
    """
    query = update.callback_query
    sub_id = int(query.data.split("_")[1])
    
    with storage.get_db() as conn:
        cursor = conn.execute("SELECT * FROM submissions_history WHERE id = ?", (sub_id,))
        sub = cursor.fetchone()
        
    if not sub:
        await query.message.reply_text("⚠️ Submission record not found.")
        return
        
    email = sub["registered_email"]
    user_record = storage.get_user_by_email(email)
    history = storage.get_submissions_by_email(email)
    
    # Build detailed text
    details = f"📜 <b>Detailed Audit Log & History</b>\n"
    details += f"📧 <b>Email:</b> <code>{html.escape(email)}</code>\n\n"
    
    if user_record:
        notes = user_record["behavior_notes"] or "<i>No behavior logs.</i>"
        details += f"📝 <b>Administrative Behavior Notes:</b>\n{html.escape(notes)}\n\n"
        
    details += "📊 <b>Historical Submission Timeline:</b>\n"
    for i, h in enumerate(reversed(history), 1):
        ts = h["action_timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        details += f"{i}. [{ts_str}] Name: <b>{html.escape(h['submitted_zoom_name'])}</b> | Status: <b>{h['action_taken']}</b> (Meeting ID: {h['meeting_id']})\n"
        
    await query.message.reply_text(details, parse_mode="HTML")

# ==========================================
# USER SEARCH FLOW (ADMIN ONLY)
# ==========================================

async def start_search_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point to admin user search flow.
    """
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("Cancel ❌", callback_data="back_to_admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "🔍 <b>User Search Panel</b>\n\n"
        "Please type the user's <b>Telegram ID</b>, <b>Username</b> (starting with @), or <b>Registered Email</b> to search:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    return AWAIT_SEARCH_INPUT

async def search_input_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Processes the search input, looking up database records or live profile info.
    """
    query_text = update.message.text.strip()
    
    if not query_text:
        await update.message.reply_text("⚠️ Search query cannot be empty. Please type again:")
        return AWAIT_SEARCH_INPUT
        
    user_record = None
    email_query = query_text.lower()
    
    # 1. Search by Telegram ID (if numeric)
    if query_text.isdigit():
        tg_id = int(query_text)
        user_record = storage.get_user_by_telegram_id(tg_id)
    
    # 2. Search by Email
    if not user_record and "@" in query_text and "." in query_text:
        user_record = storage.get_user_by_email(email_query)
        
    # 3. Search by Username
    if not user_record:
        username_clean = query_text.lstrip("@").lower()
        with storage.get_db() as conn:
            cursor = conn.execute(
                """
                SELECT registered_email FROM submissions_history 
                WHERE LOWER(submitted_telegram_username) = ? 
                LIMIT 1
                """,
                (username_clean,)
            )
            row = cursor.fetchone()
            if row:
                user_record = storage.get_user_by_email(row["registered_email"])
                
    # 4. Search by Zoom Name
    if not user_record:
        zoom_clean = query_text.lower()
        with storage.get_db() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT registered_email FROM submissions_history 
                WHERE LOWER(submitted_zoom_name) = ? OR LOWER(submitted_zoom_name) LIKE ?
                LIMIT 5
                """,
                (zoom_clean, f"%{zoom_clean}%")
            )
            rows = cursor.fetchall()
            
        if len(rows) > 1:
            message = f"🔍 <b>Multiple matches found for Zoom Name:</b> <code>{html.escape(query_text)}</code>\n\n"
            keyboard = []
            for row in rows:
                email = row["registered_email"]
                history = storage.get_submissions_by_email(email)
                zoom_name = history[0]["submitted_zoom_name"] if history else "Unknown"
                sub_id = history[0]["id"] if history else 0
                
                message += f"- {html.escape(zoom_name)} (<code>{html.escape(email)}</code>)\n"
                keyboard.append([InlineKeyboardButton(f"Review: {zoom_name}", callback_data=f"reviewreq_{sub_id}")])
                
            keyboard.append([InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
            return ConversationHandler.END
        elif len(rows) == 1:
            user_record = storage.get_user_by_email(rows[0]["registered_email"])
                
    if not user_record:
        # Live Telegram ID lookup (if query is numeric ID)
        if query_text.isdigit():
            tg_id = int(query_text)
            try:
                chat = await context.bot.get_chat(tg_id)
                first_name = chat.first_name
                last_name = chat.last_name
                username = chat.username
                full_name = f"{first_name or ''} {last_name or ''}".strip()
                display_name = f"{full_name} (@{username})" if username else full_name
                
                keyboard = [[InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"🔍 <b>Telegram Profile Found (Not in DB):</b>\n\n"
                    f"👤 <b>Name:</b> {html.escape(display_name)}\n"
                    f"🆔 <b>ID:</b> <code>{tg_id}</code>\n"
                    f"🔗 <b>Chat Link:</b> <a href=\"tg://user?id={tg_id}\">Click to open chat 💬</a>\n\n"
                    "<i>This user has never submitted an approval request.</i>",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return ConversationHandler.END
            except Exception:
                pass
                
        keyboard = [
            [
                InlineKeyboardButton("Try Again 🔄", callback_data="admin_search"),
                InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"❌ <b>No records found</b> for <code>{html.escape(query_text)}</code>.",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Profile found in database
    email = user_record["registered_email"]
    telegram_id = user_record["telegram_id"]
    global_status = user_record["global_status"]
    behavior_notes = user_record["behavior_notes"] or ""
    
    history = storage.get_submissions_by_email(email)
    if history:
        latest = history[0]
        sub_id = latest["id"]
        zoom_name = latest["submitted_zoom_name"]
        telegram_username = latest["submitted_telegram_username"]
        submission_count = len(history)
        history_summary = f"Applied {submission_count} times." if submission_count > 1 else "First-time applicant."
    else:
        sub_id = 0
        zoom_name = "Manual User Profile"
        telegram_username = "None"
        history_summary = "No submissions logged."
        
    notes_preview = "None"
    if behavior_notes:
        notes_preview = behavior_notes.split("\n")[0][:40] + "..." if len(behavior_notes) > 40 else behavior_notes.split("\n")[0]
        
    blacklist_warning = ""
    if global_status == "Blacklisted":
        blacklist_warning = "🚨 <b>WARNING: THIS USER IS BLACKLISTED!</b>\n\n"
        
    status_emojis = {
        "Pending": "🟡 Pending review.",
        "Approved": "🟢 Approved by admin.",
        "Denied": "🔴 Denied by admin.",
        "Blacklisted": "🚫 Blacklisted.",
        "Deferred": "⏳ Deferred (Review Later)."
    }
    status_text = status_emojis.get(global_status, f"{global_status}")
    
    tg_link = f'<a href="tg://user?id={telegram_id}">Open Telegram Chat 💬</a>' if telegram_id else "<i>No Telegram ID linked.</i>"
    
    card = (
        f"🔍 <b>Search Result: User Profile Found</b>\n\n"
        f"{blacklist_warning}"
        f"👤 <b>User Details:</b>\n"
        f"- <b>Zoom Name:</b> <code>{html.escape(zoom_name)}</code>\n"
        f"- <b>Registered Email:</b> <code>{html.escape(email)}</code>\n"
        f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id}</code>)\n\n"
        f"📊 <b>History Summary:</b> {history_summary}\n"
        f"📝 <b>Notes Preview:</b> {html.escape(notes_preview)}\n"
        f"🟡 <b>Current Status:</b> {status_text}\n"
        f"🔗 <b>Direct Link:</b> {tg_link}\n\n"
        "Please choose an action:"
    )
    
    reply_markup = get_admin_keyboard(sub_id if sub_id > 0 else 0)
    await update.message.reply_text(card, reply_markup=reply_markup, parse_mode="HTML")
    return ConversationHandler.END

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels search flow and returns to main Admin Menu.
    """
    # Redirect to Admin Panel start
    query = update.callback_query
    if query:
        await query.answer()
        db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
        bot_hosting = "Local Machine"
        if "PORT" in os.environ:
            if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
                bot_hosting = "Cloud (Hugging Face Spaces)"
            else:
                bot_hosting = "Cloud (Render/PaaS)"
        elif "RENDER" in os.environ:
            bot_hosting = "Cloud (Render)"

        reply_markup = get_admin_panel_markup()
        admin_text = get_admin_panel_back_text(bot_hosting, db_type)
        await query.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("Search cancelled.")
    return ConversationHandler.END

# ==========================================
# ADMIN CONFIGURATION FLOW
# ==========================================

async def show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Displays current configuration settings.
    """
    meeting_id = storage.get_setting("zoom_meeting_id", config.ZOOM_MEETING_ID)
    registration_link = storage.get_setting("zoom_registration_link", config.ZOOM_REGISTRATION_LINK)
    
    # Zoom S2S Credentials
    client_id = storage.get_setting("zoom_client_id", config.ZOOM_CLIENT_ID)
    client_secret = storage.get_setting("zoom_client_secret", config.ZOOM_CLIENT_SECRET)
    account_id = storage.get_setting("zoom_account_id", config.ZOOM_ACCOUNT_ID)
    
    keyboard = [
        [
            InlineKeyboardButton("Change Meeting ID 🆔", callback_data="set_mid"),
            InlineKeyboardButton("Change Registration Link 🔗", callback_data="set_link")
        ],
        [
            InlineKeyboardButton("Set Client ID 🔑", callback_data="set_cid"),
            InlineKeyboardButton("Set Client Secret 🔒", callback_data="set_secret")
        ],
        [
            InlineKeyboardButton("Set Account ID 👤", callback_data="set_aid"),
            InlineKeyboardButton("📘 Recovery Manual", callback_data="zoom_recovery_manual")
        ],
        [
            InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    def mask_val(val: str) -> str:
        if not val:
            return "Not Set ❌"
        val = str(val).strip()
        if len(val) <= 6:
            return "***"
        return f"{val[:3]}***{val[-3:]}"

    message = (
        "⚙️ <b>Zoom Configuration & API Settings:</b>\n\n"
        f"🆔 <b>Meeting ID:</b> <code>{html.escape(meeting_id)}</code>\n"
        f"🔗 <b>Registration Link:</b> <code>{html.escape(registration_link)}</code>\n\n"
        f"🔑 <b>Zoom S2S Credentials:</b>\n"
        f"- <b>Client ID:</b> <code>{html.escape(mask_val(client_id))}</code>\n"
        f"- <b>Client Secret:</b> <code>{html.escape(mask_val(client_secret))}</code>\n"
        f"- <b>Account ID:</b> <code>{html.escape(mask_val(account_id))}</code>\n\n"
        "Select an option below to update them:"
    )
    
    await reply_helper(update, message, reply_markup=reply_markup)
    return AWAIT_CONFIG_CHOICE

async def config_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point to configuration conversation flow.
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return ConversationHandler.END
    return await show_config_menu(update, context)

async def config_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Processes configuration selection callback buttons.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "set_mid":
        await query.message.reply_text(
            "✍️ Please type the new <b>Zoom Meeting ID</b> (spaces will be stripped automatically):",
            parse_mode="HTML"
        )
        return AWAIT_MID_INPUT
    elif query.data == "set_link":
        await query.message.reply_text(
            "✍️ Please type the new <b>Zoom Registration Link</b>:",
            parse_mode="HTML"
        )
        return AWAIT_LINK_INPUT
    elif query.data == "set_cid":
        await query.message.reply_text(
            "✍️ Please type the new <b>Zoom Client ID</b>:",
            parse_mode="HTML"
        )
        return AWAIT_CLIENT_ID_INPUT
    elif query.data == "set_secret":
        await query.message.reply_text(
            "✍️ Please type the new <b>Zoom Client Secret</b>:",
            parse_mode="HTML"
        )
        return AWAIT_CLIENT_SECRET_INPUT
    elif query.data == "set_aid":
        await query.message.reply_text(
            "✍️ Please type the new <b>Zoom Account ID</b>:",
            parse_mode="HTML"
        )
        return AWAIT_ACCOUNT_ID_INPUT
    elif query.data == "zoom_recovery_manual":
        recovery_text = (
            "📘 <b>Zoom OAuth App Recovery Guide</b>\n\n"
            "To restore the bot's Zoom integration after account suspension, you can view the complete, detailed step-by-step recovery guide with screenshots on Telegra.ph (loads instantly in Telegram):\n\n"
            "🔗 <a href=\"https://telegra.ph/Zoom-OAuth-App-Recovery-Guide-07-02-2\">Open Illustrated Recovery Manual</a>\n\n"
            "💡 <b>Granular Scope Checklist (Make sure to select all 10 scopes in the Meetings section):</b>\n"
            "1️⃣ <code>meeting:write:registrant:admin</code>\n"
            "2️⃣ <code>meeting:write:batch_registrants:admin</code>\n"
            "3️⃣ <code>meeting:write:invite_links:admin</code>\n"
            "4️⃣ <code>meeting:delete:registrant:admin</code>\n"
            "5️⃣ <code>meeting:update:registrant_status:admin</code>\n"
            "6️⃣ <code>meeting:update:registration_question:admin</code>\n"
            "7️⃣ <code>meeting:read:participant:admin</code>\n"
            "8️⃣ <code>meeting:read:registrant:admin</code>\n"
            "9️⃣ <code>meeting:read:list_registrants:admin</code>\n"
            "🔟 <code>meeting:read:list_registration_questions:admin</code>\n\n"
            "Activate the app in Zoom Marketplace after choosing these scopes, then apply the new Client ID, Client Secret, and Account ID here!"
        )
        keyboard = [[InlineKeyboardButton("Back to Settings ⬅️", callback_data="back_to_config")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            recovery_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return AWAIT_CONFIG_CHOICE
    elif query.data == "back_to_config":
        return await show_config_menu(update, context)
    elif query.data == "back_to_admin_panel":
        # Exit configuration flow and return to main admin control panel
        db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
        bot_hosting = "Local Machine"
        if "PORT" in os.environ:
            if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
                bot_hosting = "Cloud (Hugging Face Spaces)"
            else:
                bot_hosting = "Cloud (Render/PaaS)"
        elif "RENDER" in os.environ:
            bot_hosting = "Cloud (Render)"

        reply_markup = get_admin_panel_markup()
        admin_text = get_admin_panel_back_text(bot_hosting, db_type)
        await query.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return ConversationHandler.END
    return AWAIT_CONFIG_CHOICE

async def meeting_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new meeting ID setting.
    """
    new_id = update.message.text.strip().replace(" ", "")
    if not new_id:
        await update.message.reply_text("⚠️ Meeting ID cannot be empty. Please type again:")
        return AWAIT_MID_INPUT
        
    storage.set_setting("zoom_meeting_id", new_id)
    await update.message.reply_text("✅ Zoom Meeting ID updated successfully!")
    return await show_config_menu(update, context)

async def registration_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new registration link setting.
    """
    new_link = update.message.text.strip()
    if not new_link:
        await update.message.reply_text("⚠️ Link cannot be empty. Please type again:")
        return AWAIT_LINK_INPUT
        
    storage.set_setting("zoom_registration_link", new_link)
    await update.message.reply_text("✅ Zoom Registration Link updated successfully!")
    return await show_config_menu(update, context)

async def client_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new Zoom Client ID setting.
    """
    new_cid = update.message.text.strip()
    if not new_cid:
        await update.message.reply_text("⚠️ Client ID cannot be empty. Please type again:")
        return AWAIT_CLIENT_ID_INPUT
        
    storage.set_setting("zoom_client_id", new_cid)
    await update.message.reply_text("✅ Zoom Client ID updated successfully!")
    return await show_config_menu(update, context)

async def client_secret_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new Zoom Client Secret setting.
    """
    new_secret = update.message.text.strip()
    if not new_secret:
        await update.message.reply_text("⚠️ Client Secret cannot be empty. Please type again:")
        return AWAIT_CLIENT_SECRET_INPUT
        
    storage.set_setting("zoom_client_secret", new_secret)
    await update.message.reply_text("✅ Zoom Client Secret updated successfully!")
    return await show_config_menu(update, context)

async def account_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new Zoom Account ID setting.
    """
    new_aid = update.message.text.strip()
    if not new_aid:
        await update.message.reply_text("⚠️ Account ID cannot be empty. Please type again:")
        return AWAIT_ACCOUNT_ID_INPUT
        
    storage.set_setting("zoom_account_id", new_aid)
    await update.message.reply_text("✅ Zoom Account ID updated successfully!")
    return await show_config_menu(update, context)

async def cancel_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels configuration flow.
    """
    await update.message.reply_text("Configuration cancelled.")
    return ConversationHandler.END

# ==========================================
# ADMIN RIGHTS MANAGEMENT FLOW
# ==========================================

async def admin_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the Admin Rights Management conversation.
    """
    user_id = update.effective_user.id
    if not storage.is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("Unauthorized.", show_alert=True)
        else:
            await update.message.reply_text("Unauthorized access.")
        return ConversationHandler.END
        
    if update.callback_query:
        await update.callback_query.answer()
        
    admins = storage.get_admins()
    
    admin_list_text = f"- Owner / Super-Admin (ID: <code>{config.ADMIN_CHAT_ID}</code>)\n"
    for a in admins:
        admin_list_text += f"- @{html.escape(a['username'] or 'User')} (ID: <code>{a['telegram_id']}</code>)\n"
        
    message = (
        "👤 <b>Admin Rights Management Panel</b>\n\n"
        "Here are the currently authorized administrators:\n"
        f"{admin_list_text}\n"
        "Choose an action below to manage admin access:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Add Admin ➕", callback_data="add_admin_prompt"),
            InlineKeyboardButton("Remove Admin ➖", callback_data="remove_admin_list")
        ],
        [
            InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await reply_helper(update, message, reply_markup=reply_markup)
    return AWAIT_ADMIN_MANAGE_CHOICE

async def admin_manage_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles choices on the Admin rights panel.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_admin_prompt":
        keyboard = [[InlineKeyboardButton("Cancel ❌", callback_data="back_to_manage")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            "✍️ Please type the <b>numeric Telegram ID</b> of the user you want to authorize as an administrator:\n\n"
            "<i>(Or tap Cancel below to go back. Users can find their numeric ID using @userinfobot)</i>",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return AWAIT_ADD_ADMIN_INPUT
        
    elif query.data == "remove_admin_list":
        admins = storage.get_admins()
        if not admins:
            await query.message.reply_text(
                "⚠️ There are no other secondary administrators registered. Only the main Bot Owner is active."
            )
            return await admin_manage_start(update, context)
            
        keyboard = []
        for a in admins:
            keyboard.append([
                InlineKeyboardButton(
                    f"❌ Remove: {a['username'] or a['telegram_id']}", 
                    callback_data=f"remadmin_{a['telegram_id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("Cancel ⬅️", callback_data="back_to_manage")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "👤 Select an administrator from the list below to revoke their admin privileges:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return AWAIT_REMOVE_ADMIN_CHOICE
        
    elif query.data == "back_to_admin_panel":
        # Send main Admin panel
        db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
        bot_hosting = "Local Machine"
        if "PORT" in os.environ:
            if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
                bot_hosting = "Cloud (Hugging Face Spaces)"
            else:
                bot_hosting = "Cloud (Render/PaaS)"
        elif "RENDER" in os.environ:
            bot_hosting = "Cloud (Render)"

        reply_markup = get_admin_panel_markup()
        admin_text = get_admin_panel_back_text(bot_hosting, db_type)
        await query.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return ConversationHandler.END

    return AWAIT_ADMIN_MANAGE_CHOICE

async def add_admin_input_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Saves the new administrator ID to the database.
    """
    text = update.message.text.strip()
    try:
        new_admin_id = int(text)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid ID format. Telegram ID must be a numeric integer. Please try again:")
        return AWAIT_ADD_ADMIN_INPUT
        
    await update.message.reply_text("🔍 Looking up Telegram profile...")
    try:
        chat = await context.bot.get_chat(new_admin_id)
        username_val = chat.username
        first_name = chat.first_name
        last_name = chat.last_name
        full_name = f"{first_name or ''} {last_name or ''}".strip()
        display_name = f"{full_name} (@{username_val})" if username_val else full_name
        if not display_name:
            display_name = f"User {new_admin_id}"
    except Exception as e:
        display_name = f"User_{new_admin_id}"
        
    storage.add_admin(new_admin_id, username=display_name)
    
    await update.message.reply_text(
        f"✅ Successfully authorized <b>{html.escape(display_name)}</b> (ID: <code>{new_admin_id}</code>) as an administrator!", 
        parse_mode="HTML"
    )
    return await admin_manage_start(update, context)

async def remove_admin_choice_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Revokes administrative privileges.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_manage":
        return await admin_manage_start(update, context)
        
    match = re.match(r"^remadmin_(\d+)$", query.data)
    if not match:
        return await admin_manage_start(update, context)
        
    target_id = int(match.group(1))
    storage.remove_admin(target_id)
    
    await query.message.reply_text(f"✅ Successfully revoked administrative rights for ID <code>{target_id}</code>.", parse_mode="HTML")
    return await admin_manage_start(update, context)

async def cancel_admin_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels admin rights flow and redirects to Admin Panel.
    """
    db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
    bot_hosting = "Local Machine"
    if "PORT" in os.environ:
        if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
            bot_hosting = "Cloud (Hugging Face Spaces)"
        else:
            bot_hosting = "Cloud (Render/PaaS)"
    elif "RENDER" in os.environ:
        bot_hosting = "Cloud (Render)"

    reply_markup = get_admin_panel_markup()
    admin_text = get_admin_panel_back_text(bot_hosting, db_type)
    await reply_helper(
        update, 
        admin_text, 
        reply_markup=reply_markup
    )
    return ConversationHandler.END

# ==========================================
# DYNAMIC NAVIGATION BUTTONS TRIGGERS
# ==========================================

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Routes callback queries from the Admin Control Panel.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_requests":
        await requests_command(update, context)
    elif query.data.startswith("reqpage_"):
        page = int(query.data.split("_")[1])
        await requests_command(update, context, page=page)
    elif query.data == "admin_name_changes":
        await admin_name_changes_command(update, context)
    elif query.data == "admin_config":
        await show_config_menu(update, context)
    elif query.data == "admin_report":
        await report_command(update, context)
    elif query.data == "back_to_admin_panel":
        db_type = "Cloud PostgreSQL (Supabase)" if storage.IS_POSTGRES else "Local SQLite (database.db)"
        bot_hosting = "Local Machine"
        if "PORT" in os.environ:
            if "SPACE_ID" in os.environ or "SPACE_OWNER" in os.environ:
                bot_hosting = "Cloud (Hugging Face Spaces)"
            else:
                bot_hosting = "Cloud (Render/PaaS)"
        elif "RENDER" in os.environ:
            bot_hosting = "Cloud (Render)"

        reply_markup = get_admin_panel_markup()
        admin_text = get_admin_panel_back_text(bot_hosting, db_type)
        await query.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

# ==========================================
# ADMINISTRATIVE COMMANDS
# ==========================================

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Generates a system report for the admin.
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    data = storage.get_admin_report_data()
    
    status_str = ""
    for status, count in data["status_counts"].items():
        status_str += f"- <b>{status}:</b> {count}\n"
        
    suspicious_str = ""
    if data["suspicious_users"]:
        for user in data["suspicious_users"]:
            suspicious_str += f"- <code>{html.escape(user['registered_email'])}</code> used {user['name_count']} names: {html.escape(user['names'])}\n"
    else:
        suspicious_str = "<i>None detected.</i>"
        
    report = (
        "📊 <b>Telegram & Zoom Auto-Approval Report</b>\n\n"
        f"👥 <b>Total Tracked Profiles:</b> {data['total_users']}\n"
        f"{status_str}\n"
        f"📝 <b>Total Submissions Logged:</b> {data['total_submissions']}\n\n"
        f"🚨 <b>Suspicious Activities (Duplicate Emails):</b>\n{suspicious_str}"
    )
    
    keyboard = [[InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_helper(update, report, reply_markup=reply_markup)

def resolve_email_from_param(param: str) -> str | None:
    """
    Resolves a registered Zoom email from a command parameter which might be:
    - A direct Zoom email (e.g. alice@example.com)
    - A numeric Telegram ID (e.g. 7905968402)
    - A Telegram Username (e.g. @izax_x or izax_x)
    - A Zoom Display Name (partial or exact)
    Returns the resolved email if found, or None.
    """
    param = param.strip()
    if not param:
        return None
        
    # Case 1: Already an email format
    if "@" in param and "." in param and not param.startswith("@"):
        return param.lower()
        
    # Case 2: Numeric Telegram ID
    if param.isdigit():
        tg_id = int(param)
        user_record = storage.get_user_by_telegram_id(tg_id)
        if user_record:
            return user_record["registered_email"]
            
    # Case 3: Telegram Username (with or without @)
    username_clean = param.lstrip("@").lower()
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT registered_email FROM submissions_history 
            WHERE LOWER(submitted_telegram_username) = ? 
            LIMIT 1
            """,
            (username_clean,)
        )
        row = cursor.fetchone()
        if row:
            return row["registered_email"]
            
    # Case 4: Zoom Display Name search
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT DISTINCT registered_email FROM submissions_history 
            WHERE LOWER(submitted_zoom_name) = ? OR LOWER(submitted_zoom_name) LIKE ?
            LIMIT 1
            """,
            (param.lower(), f"%{param.lower()}%")
        )
        row = cursor.fetchone()
        if row:
            return row["registered_email"]
            
    return None

async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Allows admin to manually blacklist an email.
    Usage: /blacklist <email/id/username/zoom_name> [notes]
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: <code>/blacklist &lt;email/id/username/name&gt; [optional_notes]</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    notes = " ".join(context.args[1:]).strip() if len(context.args) > 1 else "Manually blacklisted via command."
    
    email = resolve_email_from_param(target)
    if not email:
        if "@" in target and "." in target:
            email = target.lower()
        else:
            await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
            return
        
    try:
        zoom_service.update_registrant_status(email, "deny")
    except Exception as e:
        logger.info("Zoom deny failed on blacklist command: %s", e)
        
    user_exists = storage.get_user_by_email(email) is not None
    if user_exists:
        storage.update_user_status(email, "Blacklisted", behavior_notes=notes)
    else:
        with storage.get_db() as conn:
            conn.execute(
                "INSERT INTO users (registered_email, global_status, behavior_notes) VALUES (?, ?, ?)",
                (email, "Blacklisted", f"[Manual] {notes}")
            )
            
    await update.message.reply_text(f"🚫 Email <code>{html.escape(email)}</code> has been successfully blacklisted.", parse_mode="HTML")

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Allows admin to attach custom notes to a user profile.
    Usage: /notes <email/id/username/zoom_name> <notes_text>
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if len(context.args) < 2:
        await update.message.reply_text("Usage: <code>/notes &lt;email/id/username/name&gt; &lt;notes_text&gt;</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    notes_text = " ".join(context.args[1:]).strip()
    
    email = resolve_email_from_param(target)
    if not email:
        await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
        return
        
    user_record = storage.get_user_by_email(email)
    if not user_record:
        await update.message.reply_text(f"⚠️ User profile for <code>{html.escape(email)}</code> does not exist in the database.", parse_mode="HTML")
        return
        
    storage.update_user_status(email, user_record["global_status"], behavior_notes=notes_text)
    await update.message.reply_text(f"📝 Notes updated for user <code>{html.escape(email)}</code>.", parse_mode="HTML")

async def requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    """
    Lists all registration requests in the database with page-by-page pagination (10 per page).
    """
    chat_id = update.effective_chat.id
    if not storage.is_admin(chat_id):
        await reply_helper(update, "Unauthorized access.")
        return
        
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT u.registered_email, u.global_status, 
                   (SELECT s.submitted_zoom_name FROM submissions_history s 
                    WHERE s.registered_email = u.registered_email 
                    ORDER BY s.action_timestamp DESC LIMIT 1) as zoom_name
            FROM users u
            ORDER BY u.updated_at DESC
            """
        )
        rows = cursor.fetchall()
        
    if not rows:
        keyboard = [[InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await reply_helper(update, "📋 <b>Registration Request List:</b>\n\n<i>No profiles found in the database.</i>", reply_markup=reply_markup)
        return

    PAGE_SIZE = 10
    total_items = len(rows)
    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
    
    # Clamp page index
    if page < 0:
        page = 0
    elif page >= total_pages:
        page = total_pages - 1
        
    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_items)
    page_rows = rows[start_idx:end_idx]
    
    message = f"📋 <b>Registration Request List (Page {page + 1}/{total_pages}):</b>\n"
    message += f"Showing profiles {start_idx + 1} to {end_idx} of {total_items} total.\n\n"
    
    keyboard = []
    
    for row in page_rows:
        email = row["registered_email"]
        status = row["global_status"]
        name = row["zoom_name"] or "Unknown (Manual Profile)"
        
        # Get latest submission ID for this email
        with storage.get_db() as conn2:
            c2 = conn2.execute(
                "SELECT id FROM submissions_history WHERE registered_email = ? ORDER BY action_timestamp DESC LIMIT 1", 
                (email,)
            )
            latest = c2.fetchone()
            sub_id = latest["id"] if latest else 0
            
        status_emojis = {
            "Pending": "🟡",
            "Approved": "🟢",
            "Denied": "🔴",
            "Blacklisted": "🚫",
            "Deferred": "⏳"
        }
        emoji = status_emojis.get(status, "⚪")
        
        message += f"- {emoji} {html.escape(name)} (<code>{html.escape(email)}</code>) [<i>{status}</i>]\n"
        keyboard.append([InlineKeyboardButton(f"{emoji} Review: {name}", callback_data=f"reviewreq_{sub_id}")])
        
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"reqpage_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"reqpage_{page + 1}"))
        
    if nav_row:
        keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query = update.callback_query
    if query:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")

async def admin_name_changes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lists all pending Zoom name change requests.
    """
    if not storage.is_admin(update.effective_chat.id):
        await reply_helper(update, "Unauthorized access.")
        return
        
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT s.id, s.registered_email, s.submitted_zoom_name
            FROM submissions_history s
            WHERE s.action_taken = 'NameChangePending'
            ORDER BY s.action_timestamp DESC
            """
        )
        rows = cursor.fetchall()
        
    message = "✏️ <b>Pending Zoom Name Change Requests:</b>\n\n"
    keyboard = []
    
    if rows:
        for row in rows:
            sub_id = row["id"]
            email = row["registered_email"]
            new_name = row["submitted_zoom_name"]
            
            message += f"- ✏️ {html.escape(new_name)} (<code>{html.escape(email)}</code>)\n"
            keyboard.append([InlineKeyboardButton(f"✏️ Review: {new_name}", callback_data=f"reviewname_{sub_id}")])
    else:
        message += "<i>No pending name change requests found.</i>"
        
    keyboard.append([InlineKeyboardButton("Back to panel 🛡️", callback_data="back_to_admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_helper(update, message, reply_markup=reply_markup)

async def review_name_change_card(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int) -> None:
    """
    Builds and sends the compact name change review card to the administrator.
    """
    user_id = update.effective_user.id
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT s.*, u.behavior_notes 
            FROM submissions_history s
            JOIN users u ON s.registered_email = u.registered_email
            WHERE s.id = ?
            """,
            (sub_id,)
        )
        submission = cursor.fetchone()
        
    if not submission:
        await context.bot.send_message(chat_id=user_id, text="⚠️ Error: Submission record not found.")
        return
        
    email = submission["registered_email"]
    new_name = submission["submitted_zoom_name"]
    telegram_username = submission["submitted_telegram_username"]
    
    # Get previous name (latest approved name change or initial registration)
    with storage.get_db() as conn:
        cursor = conn.execute(
            """
            SELECT submitted_zoom_name 
            FROM submissions_history 
            WHERE registered_email = ? AND action_taken IN ('Approved', 'ApprovedNameChange')
            ORDER BY action_timestamp DESC LIMIT 1
            """,
            (email,)
        )
        prev = cursor.fetchone()
        prev_name = prev["submitted_zoom_name"] if prev else "Unknown (Approved)"
        
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM submissions_history WHERE registered_email = ? AND action_taken = 'ApprovedNameChange'",
            (email,)
        )
        name_change_count = cursor.fetchone()["count"]
        
    card = (
        f"✏️ <b>Zoom Name Change Review Card</b>\n\n"
        f"📧 <b>User Email:</b> <code>{html.escape(email)}</code>\n"
        f"💬 <b>Telegram:</b> @{html.escape(telegram_username)}\n\n"
        f"⏪ <b>Current Approved Name:</b> <code>{html.escape(prev_name)}</code>\n"
        f"⏩ <b>Requested New Name:</b> <code>{html.escape(new_name)}</code>\n\n"
        f"📊 <b>History:</b> Approved name changes: {name_change_count} times.\n\n"
        f"Please choose an action:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Approve Name Change ✅", callback_data=f"apprname_{sub_id}"),
            InlineKeyboardButton("Deny Name Change ❌", callback_data=f"denyname_{sub_id}")
        ],
        [
            InlineKeyboardButton("History 📜", callback_data=f"viewhist_{sub_id}"),
            InlineKeyboardButton("Back to List 📋", callback_data="admin_name_changes")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=user_id,
        text=card,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Pulls up the full interactive admin decision card for any email.
    Usage: /review <email/id/username/zoom_name>
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: <code>/review &lt;email/id/username/name&gt;</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    
    email = resolve_email_from_param(target)
    if not email:
        await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
        return
        
    user_record = storage.get_user_by_email(email)
    if not user_record:
        await update.message.reply_text(f"⚠️ No profile found for <code>{html.escape(email)}</code>.", parse_mode="HTML")
        return
        
    history = storage.get_submissions_by_email(email)
    if not history:
        latest = {
            "id": 0,
            "submitted_zoom_name": "No submissions logged",
            "submitted_telegram_username": "None"
        }
        sub_id = 0
        zoom_name = latest["submitted_zoom_name"]
        telegram_username = latest["submitted_telegram_username"]
        history_summary = "First-time applicant."
    else:
        latest = history[0]
        sub_id = latest["id"]
        zoom_name = latest["submitted_zoom_name"]
        telegram_username = latest["submitted_telegram_username"]
        submission_count = len(history)
        history_summary = f"Applied {submission_count} times." if submission_count > 1 else "First-time applicant."
        
    telegram_id = user_record["telegram_id"]
    global_status = user_record["global_status"]
    behavior_notes = user_record["behavior_notes"] or ""
    
    # Check if this Telegram ID has other associated email profiles
    other_profiles = []
    if telegram_id:
        with storage.get_db() as conn:
            cursor = conn.execute(
                "SELECT registered_email, global_status, behavior_notes FROM users WHERE telegram_id = ? AND LOWER(registered_email) != LOWER(?)",
                (telegram_id, email)
            )
            other_profiles = [dict(row) for row in cursor.fetchall()]
            
    tg_warning = ""
    tg_history_line = ""
    if other_profiles:
        profile_states = []
        for p in other_profiles:
            profile_states.append(f"{p['registered_email']} ({p['global_status']})")
            if p['global_status'] == "Blacklisted":
                tg_warning += f"🚨 <b>WARNING: Linked Telegram ID has a BLACKLISTED email: <code>{p['registered_email']}</code>!</b>\n\n"
            elif p['global_status'] == "Denied":
                tg_warning += f"⚠️ <b>WARNING: Linked Telegram ID was previously DENIED under email: <code>{p['registered_email']}</code>!</b>\n\n"
            
            # Carry over behavior notes if any
            if p['behavior_notes'] and not behavior_notes:
                behavior_notes = f"[From linked profile {p['registered_email']}] {p['behavior_notes']}"
                
        tg_history_line = f"- <b>Linked Telegram Profiles:</b> {', '.join(profile_states)}\n"
        
    notes_preview = "None"
    if behavior_notes:
        notes_preview = behavior_notes.split("\n")[0][:40] + "..." if len(behavior_notes) > 40 else behavior_notes.split("\n")[0]
        
    blacklist_warning = ""
    if global_status == "Blacklisted":
        blacklist_warning = "🚨 <b>WARNING: THIS USER IS BLACKLISTED!</b>\n\n"
        
    status_emojis = {
        "Pending": "🟡 Pending review.",
        "Approved": "🟢 Approved by admin.",
        "Denied": "🔴 Denied by admin.",
        "Blacklisted": "🚫 Blacklisted.",
        "Deferred": "⏳ Deferred (Review Later)."
    }
    
    status_text = status_emojis.get(global_status, f"{global_status}")
    
    admin_message = (
        f"🔔 <b>Zoom Registration Request Panel</b>\n\n"
        f"{blacklist_warning}"
        f"{tg_warning}"
        f"👤 <b>User Details:</b>\n"
        f"- <b>Zoom Name:</b> <code>{html.escape(zoom_name)}</code>\n"
        f"- <b>Registered Email:</b> <code>{html.escape(email)}</code>\n"
        f"- <b>Telegram:</b> @{html.escape(telegram_username)} (ID: <code>{telegram_id or 'None'}</code>)\n"
        f"{tg_history_line}\n"
        f"📊 <b>History Summary:</b> {history_summary}\n"
        f"📝 <b>Notes Preview:</b> {html.escape(notes_preview)}\n\n"
        f"🟡 <b>Current Status:</b> {status_text}\n\n"
        "Please choose an action:"
    )
    
    reply_markup = get_admin_keyboard(sub_id if sub_id > 0 else 0)
    await update.message.reply_text(admin_message, reply_markup=reply_markup, parse_mode="HTML")

async def deleteuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Deletes the user profile and their submission logs entirely from the database.
    Usage: /deleteuser <email/id/username/zoom_name>
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: <code>/deleteuser &lt;email/id/username/name&gt;</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    email = resolve_email_from_param(target)
    if not email:
        await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
        return
        
    with storage.get_db() as conn:
        conn.execute("DELETE FROM submissions_history WHERE LOWER(registered_email) = LOWER(?)", (email,))
        cursor = conn.execute("DELETE FROM users WHERE LOWER(registered_email) = LOWER(?)", (email,))
        deleted_count = cursor.rowcount
        
    if deleted_count > 0:
        await update.message.reply_text(f"🗑️ Deleted user profile and submission history for <code>{html.escape(email)}</code>.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ User <code>{html.escape(email)}</code> not found in database.", parse_mode="HTML")

async def clearhistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Deletes submission history records for a user, resetting application count to 0.
    Usage: /clearhistory <email/id/username/zoom_name>
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: <code>/clearhistory &lt;email/id/username/name&gt;</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    email = resolve_email_from_param(target)
    if not email:
        await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
        return
        
    with storage.get_db() as conn:
        cursor = conn.execute("DELETE FROM submissions_history WHERE LOWER(registered_email) = LOWER(?)", (email,))
        deleted_history = cursor.rowcount
        
    if deleted_history > 0:
        await update.message.reply_text(f"🔄 Cleared submission history ({deleted_history} records) for <code>{html.escape(email)}</code>. Submission count reset to zero.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ No history records found for <code>{html.escape(email)}</code>.", parse_mode="HTML")

async def clearnotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clears the behavior notes of a user profile.
    Usage: /clearnotes <email/id/username/zoom_name>
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: <code>/clearnotes &lt;email/id/username/name&gt;</code>", parse_mode="HTML")
        return
        
    target = context.args[0].strip()
    email = resolve_email_from_param(target)
    if not email:
        await update.message.reply_text(f"⚠️ Could not resolve user or email from query: <code>{html.escape(target)}</code>", parse_mode="HTML")
        return
        
    with storage.get_db() as conn:
        cursor = conn.execute("UPDATE users SET behavior_notes = '' WHERE LOWER(registered_email) = LOWER(?)", (email,))
        updated_count = cursor.rowcount
        
    if updated_count > 0:
        await update.message.reply_text(f"🧹 Cleared behavior notes on user profile <code>{html.escape(email)}</code>.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ User <code>{html.escape(email)}</code> not found in database.", parse_mode="HTML")

async def synczoom_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Retrieves all registrants for the active meeting from Zoom and synchronizes them.
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized access.")
        return
        
    await reply_helper(update, "🔄 Fetching registrants from Zoom and syncing database (this may take a moment)...")
    
    sync_count = 0
    active_meeting_id = storage.get_setting("zoom_meeting_id", config.ZOOM_MEETING_ID)
    
    try:
        for zoom_status, db_status in [
            ("pending", "Pending"), 
            ("approved", "Approved"), 
            ("denied", "Denied")
        ]:
            registrants = zoom_service.list_registrants(zoom_status)
            for r in registrants:
                email = r.get("email")
                first_name = r.get("first_name", "")
                last_name = r.get("last_name", "")
                zoom_name = f"{first_name} {last_name}".strip() or "Zoom Registrant"
                
                if not email:
                    continue
                    
                existing = storage.get_user_by_email(email)
                if not existing:
                    with storage.get_db() as cursor:
                        storage.execute_query(
                            cursor,
                            "INSERT INTO users (registered_email, telegram_id, global_status) VALUES (?, ?, ?)",
                            (email.lower(), None, db_status)
                        )
                    sync_count += 1
                else:
                    if existing["global_status"] != db_status:
                        storage.update_user_status(email, db_status)
                        sync_count += 1
                        
                history = storage.get_submissions_by_email(email)
                if not history:
                    storage.add_submission(
                        email=email,
                        telegram_id=0,
                        zoom_name=zoom_name,
                        telegram_username="Unknown",
                        meeting_id=active_meeting_id,
                        action_taken=db_status
                    )
                    
        await reply_helper(update, f"✅ Sync completed! Synchronized <b>{sync_count}</b> registrant profiles from Zoom.")
    except Exception as e:
        logger.error("Sync error: %s", e)
        await reply_helper(update, f"⚠️ Error synchronizing from Zoom: <code>{html.escape(str(e))}</code>")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays help instructions for the admin.
    """
    if not storage.is_admin(update.effective_chat.id):
        await update.message.reply_text(
            "Welcome! Please select an action from the bot main menu. Type /start to open the menu."
        )
        return
        
    help_text = (
        "🛠 <b>Administrator Command Help:</b>\n\n"
        "📋 <code>/requests</code> - Lists all requests grouped by status (Read/Unread) with tap-to-review buttons.\n"
        "⚙️ <code>/config</code> - Interactive menu to view/change Zoom Meeting ID & Link.\n"
        "🔄 <code>/synczoom</code> - Pulls existing registrants from Zoom and syncs database.\n"
        "🔍 <code>/review &lt;email&gt;</code> - Pulls up the interactive decision card for any profile.\n"
        "🚫 <code>/blacklist &lt;email&gt; [notes]</code> - Blacklists an email profile.\n"
        "📝 <code>/notes &lt;email&gt; &lt;text&gt;</code> - Adds/appends behavior notes.\n"
        "📊 <code>/report</code> - Generates a database statistics summary.\n\n"
        "🧹 <b>Management / Reset Controls:</b>\n"
        "🗑️ <code>/deleteuser &lt;email&gt;</code> - Deletes user profile & history completely.\n"
        "🔄 <code>/clearhistory &lt;email&gt;</code> - Clears user submissions (resets count to 0).\n"
        "🧹 <code>/clearnotes &lt;email&gt;</code> - Clears behavior notes on a profile."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


# ==========================================
# MAIN APPLICATION INITIALIZATION
# ==========================================

def main() -> None:
    """
    Starts the bot and registers handlers.
    """
    # 1. Initialize Database Tables
    storage.init_db()
    logger.info("Database initialized successfully.")

    # 1.5 Start health check server & FastAPI web server for Telegram Mini App
    import web_server
    threading.Thread(target=web_server.start_server, daemon=True).start()

    # 2. Build the Application with custom HTTPX timeouts
    from telegram.request import HTTPXRequest
    request_config = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).request(request_config).build()

    # 3. Add Conversational Handler for User Registrations
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start_register, pattern="^user_register$")
        ],
        states={
            AWAIT_ZOOM_NAME: [
                CallbackQueryHandler(cancel_callback, pattern="^cancel_conv$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, zoom_name_received)
            ],
            AWAIT_EMAIL: [
                CallbackQueryHandler(back_to_name_callback, pattern="^back_to_name$"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel_conv$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, email_received)
            ],
            AWAIT_CONFIRMATION: [
                CallbackQueryHandler(submit_registration, pattern="^submit_reg$"),
                CallbackQueryHandler(start_over, pattern="^start_over$")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel_callback, pattern="^cancel_conv$")
        ],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # 3.4 Add Conversational Handler for User Name Change requests
    name_change_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_name_change, pattern="^user_name_change$")
        ],
        states={
            AWAIT_NEW_NAME_INPUT: [
                CallbackQueryHandler(submit_name_change, pattern="^submit_name_change$"),
                CallbackQueryHandler(cancel_name_change, pattern="^cancel_name_change$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, name_change_input_received)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_name_change),
            CallbackQueryHandler(cancel_name_change, pattern="^cancel_name_change$")
        ],
        allow_reentry=True
    )
    application.add_handler(name_change_conv_handler)

    # 3.5 Add Conversational Handler for Admin Variable Configurations
    config_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("config", config_start),
            CallbackQueryHandler(config_start, pattern="^admin_config$")
        ],
        states={
            AWAIT_CONFIG_CHOICE: [
                CallbackQueryHandler(config_choice, pattern="^(set_mid|set_link|set_cid|set_secret|set_aid|back_to_admin_panel|zoom_recovery_manual|back_to_config)$")
            ],
            AWAIT_MID_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, meeting_id_received)
            ],
            AWAIT_LINK_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, registration_link_received)
            ],
            AWAIT_CLIENT_ID_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, client_id_received)
            ],
            AWAIT_CLIENT_SECRET_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, client_secret_received)
            ],
            AWAIT_ACCOUNT_ID_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, account_id_received)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_config)],
        allow_reentry=True
    )
    application.add_handler(config_conv_handler)

    # 3.6 Add Conversational Handler for Admin Rights Authorization Management
    admin_manage_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_manage_start, pattern="^admin_manage$")
        ],
        states={
            AWAIT_ADMIN_MANAGE_CHOICE: [
                CallbackQueryHandler(admin_manage_choice, pattern="^(add_admin_prompt|remove_admin_list|back_to_admin_panel)$")
            ],
            AWAIT_ADD_ADMIN_INPUT: [
                CallbackQueryHandler(cancel_admin_manage, pattern="^back_to_manage$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_input_received)
            ],
            AWAIT_REMOVE_ADMIN_CHOICE: [
                CallbackQueryHandler(remove_admin_choice_received, pattern="^(remadmin_\\d+|back_to_manage)$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin_manage),
            CallbackQueryHandler(cancel_admin_manage, pattern="^back_to_admin_panel$")
        ],
        allow_reentry=True
    )
    application.add_handler(admin_manage_conv_handler)

    # 3.7 Add Conversational Handler for User Search
    search_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_search_flow, pattern="^admin_search$")
        ],
        states={
            AWAIT_SEARCH_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_input_received)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_search),
            CallbackQueryHandler(cancel_search, pattern="^back_to_admin_panel$")
        ],
        allow_reentry=True
    )
    application.add_handler(search_conv_handler)

    # 4. Add Admin Dashboard Callbacks
    application.add_handler(
        CallbackQueryHandler(admin_decision_callback, pattern="^(approve|deny|later|blacklist|editnotes|reviewreq|reviewname|apprname|denyname|viewhist)_\\d+$")
    )
    
    # 4.5 Add Main Menu Navigation Callbacks
    application.add_handler(
        CallbackQueryHandler(admin_menu_callback, pattern="^(admin_requests|admin_name_changes|admin_config|admin_report|back_to_admin_panel|reqpage_\\d+|admin_search)$")
    )
    application.add_handler(
        CallbackQueryHandler(user_menu_callback, pattern="^(user_link|user_help|back_to_user_menu)$")
    )

    # 5. Add Administrative Commands
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("blacklist", blacklist_command))
    application.add_handler(CommandHandler("notes", notes_command))
    application.add_handler(CommandHandler("requests", requests_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("deleteuser", deleteuser_command))
    application.add_handler(CommandHandler("clearhistory", clearhistory_command))
    application.add_handler(CommandHandler("clearnotes", clearnotes_command))
    application.add_handler(CommandHandler("synczoom", synczoom_command))

    # 6. Start Polling
    logger.info("Telegram Bot starts polling...")
    application.run_polling()

if __name__ == "__main__":
    main()

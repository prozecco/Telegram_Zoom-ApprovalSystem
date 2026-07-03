import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Required Telegram configurations
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID")

# Required Zoom configurations
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_MEETING_ID_RAW = os.getenv("ZOOM_MEETING_ID")

# Optional configurations
ZOOM_REGISTRATION_LINK = os.getenv("ZOOM_REGISTRATION_LINK")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")
DATABASE_URL = os.getenv("DATABASE_URL")

# Validation list
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "ADMIN_CHAT_ID": ADMIN_CHAT_ID_RAW,
    "ZOOM_ACCOUNT_ID": ZOOM_ACCOUNT_ID,
    "ZOOM_CLIENT_ID": ZOOM_CLIENT_ID,
    "ZOOM_CLIENT_SECRET": ZOOM_CLIENT_SECRET,
    "ZOOM_MEETING_ID": ZOOM_MEETING_ID_RAW,
}

missing = [name for name, val in required_vars.items() if not val]
if missing:
    raise ValueError(f"Missing required environment variables in .env: {', '.join(missing)}")

# Validate and parse ADMIN_CHAT_ID
try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError:
    raise ValueError(f"ADMIN_CHAT_ID must be a numeric integer. Got: '{ADMIN_CHAT_ID_RAW}'")

# Validate and parse optional NOTIFICATION_CHAT_ID (falls back to ADMIN_CHAT_ID)
NOTIFICATION_CHAT_ID_RAW = os.getenv("NOTIFICATION_CHAT_ID")
if NOTIFICATION_CHAT_ID_RAW:
    try:
        NOTIFICATION_CHAT_ID = int(NOTIFICATION_CHAT_ID_RAW)
    except ValueError:
        raise ValueError(f"NOTIFICATION_CHAT_ID must be a numeric integer. Got: '{NOTIFICATION_CHAT_ID_RAW}'")
else:
    NOTIFICATION_CHAT_ID = ADMIN_CHAT_ID

# Process Zoom Meeting ID to strip spaces
ZOOM_MEETING_ID = str(ZOOM_MEETING_ID_RAW).strip().replace(" ", "")

# Generate Zoom registration link fallback if not provided
if not ZOOM_REGISTRATION_LINK:
    # Standard registration URL pattern. 
    # Note: For some Zoom meetings, the registration URL contains a hash,
    # so setting the actual ZOOM_REGISTRATION_LINK in .env is highly recommended.
    ZOOM_REGISTRATION_LINK = f"https://zoom.us/meeting/register/{ZOOM_MEETING_ID}"
else:
    ZOOM_REGISTRATION_LINK = ZOOM_REGISTRATION_LINK.strip()

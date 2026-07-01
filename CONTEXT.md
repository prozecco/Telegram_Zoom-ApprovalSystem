# Antigravity Context: Telegram & Zoom Automated Approval System

## 1. Project Overview & Scope
The objective is to build an interactive, agentic workflow that replaces a manual Zoom registration approval process. Instead of an administrator opening the Zoom Web Portal to search for registrants and manually click approve, this system uses a Telegram Bot as the frontend form collector and the Zoom Server-to-Server OAuth API to automate operations.

### Key Behaviors:
- **User Interface:** A multi-step inline-keyboard conversation flow that guides users to register via a Zoom link and then gather verification metrics (Zoom Name, Registered Email, Telegram Username).
- **Admin Dashboard & Live Auditing:** A human-in-the-loop interactive alert system sent to a specific Telegram Admin Chat ID containing complete user behavioral profiles retrieved from the database upon matching the registered email.
- **Dynamic Feedback Loop:** Admins must be able to approve, deny, defer, or blacklist a user, as well as add or append behavioral notes to a user's record dynamically during the decision phase or at a later time.

---

## 2. Technical Stack & Environment Variables
- **Language:** Python 3.10+ (Async execution loops via `python-telegram-bot` v20+)
- **Database Engine:** SQLite3 (Standard relational operations)
- **API Interfaces:** Telegram Bot API & Zoom Server-to-Server OAuth

### Required Secret Variables (`.env` setup target):
```text
TELEGRAM_BOT_TOKEN="<token_from_botfather>"
ADMIN_CHAT_ID=<numeric_telegram_id>
ZOOM_ACCOUNT_ID="<zoom_developer_account_id>"
ZOOM_CLIENT_ID="<zoom_developer_client_id>"
ZOOM_CLIENT_SECRET="<zoom_developer_client_secret>"
ZOOM_MEETING_ID="<target_zoom_meeting_numeric_id>"

---

## 3. Storage Architecture (SQLite Schema)
Initialize the database with the following relational structures to capture tracking logs and persistent behavior notes:

SQL
-- Main identity tracker utilizing email as the primary key index
CREATE TABLE IF NOT EXISTS users (
    registered_email TEXT PRIMARY KEY,
    telegram_id INTEGER,
    global_status TEXT DEFAULT 'Pending', -- Pending, Approved, Denied, Blacklisted
    behavior_notes TEXT DEFAULT '',       -- Persistent admin logging for behavioral tracking
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Submission history logging multiple display names tied to a single email address
CREATE TABLE IF NOT EXISTS submissions_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registered_email TEXT,
    submitted_zoom_name TEXT,
    submitted_telegram_username TEXT,
    meeting_id TEXT,
    action_taken TEXT,                    -- Approved, Denied, Deferred
    action_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(registered_email) REFERENCES users(registered_email)
);

---

## 4. Key Benefits & Functional Advantages
On-Demand Behavioral Intelligence: The moment a user submits a form, the system matches the email against the users table and instantly extracts their historical profiles. The Admin Decision Card dynamically displays their lifetime approvals, previously used Zoom names, and existing behavioral records before any action is taken.

Dynamic Note Appending: Admins can click a [📝 Edit Notes] inline button at any point (during active registration review or retroactively). The bot will issue a ForceReply prompt, intercept the admin's text reply, and update the behavior_notes field in the SQLite database instantly.

Automated Blacklisting: Once the admin flags a user as "Blacklisted," the system blocks that identity permanently. Any subsequent application using that email is auto-rejected instantly before reaching the admin queue.

Deferred Review ("Later" Option): If the admin is away or busy, the "Later" state defers the decision while triggering an automatic holding response to the user. This ensures the user receives instant feedback without feeling ignored.

---

## 5. Core Operational Logic & Modules Map
The project must be strictly decoupled across four primary modules:

config.py: Handles environment initialization, variable extraction, and token safety.

storage.py: Implements database initialization, history retrieval, duplicate matching, and dynamic behavior note updating functions based on the SQLite schema.

zoom_service.py: Encapsulates OAuth token generation (https://zoom.us/oauth/token) and handles meeting registrant updates (PUT /v2/meetings/{meetingId}/registrants/status).

app.py: Coordinates the main Telegram user workflow, the interactive decision state machine, and the administrative note-editing state machine.

Admin Card Layout & Interaction Logic:
When a submission is parsed, the admin receives the following layout:

Plaintext
🔔 NEW REGISTRATION REQUEST

📧 Email: user@example.com [🚨 NEW USER / ⚠️ RETURNING USER]
👤 Current Zoom Name: John Doe
💬 Telegram: @johndoe123

📜 Historical Profiles Found:
- Past Names: [Johnny D, J. Doe]
- Total Meetings Attended: 3

📝 Behavior Notes:
"User is highly interactive. Shared incomplete credentials last week."

[ ✅ Approve ]   [ ❌ Deny ]
[ ⏳ Later ]     [ 🚫 Blacklist ]
[ 📝 Edit Notes ]
Operational Flow for Note Editing: Clicking [ 📝 Edit Notes ] pauses the approval state, prompts the admin with a reply interface, updates behavior_notes in the database, and refreshes the Admin Card text in real-time to show the newly updated notes alongside the original approval controls.
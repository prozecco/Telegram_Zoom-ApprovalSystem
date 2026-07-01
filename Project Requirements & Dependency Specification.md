

# **Project Requirements & Dependency Specification**

## **1\. Credentials & External APIs**

To bridge Telegram and Zoom, you will need access tokens and identifiers from both developer portals.

### **A. Telegram Bot API**

* **What is needed:** A Bot API Key (TELEGRAM\_BOT\_TOKEN) and your target Admin Chat ID.  
* **Where to get it:**  
  * Open Telegram, message [@BotFather](https://t.me/BotFather), and use the /newbot command to generate your token.  
  * To find your target Admin Chat ID (or Group ID), add your admin account/group to [@userinfobot](https://t.me/userinfobot) to fetch the numeric ID.

### **B. Zoom Developer Credentials**

* **What is needed:** Account ID, Client ID, Client Secret, and the specific Meeting ID.  
* **Where to get it:**  
  1. Log into the [Zoom App Marketplace](https://marketplace.zoom.us/).  
  2. Click **Develop** $\\rightarrow$ **Build App** and create a **Server-to-Server OAuth** app.  
  3. Under the **App Credentials** tab, copy the Account ID, Client ID, and Client Secret.  
  4. Under the **Scopes** tab, explicitly add meeting:write:admin (Required to update registrant approval statuses).

## **2\. Environment & Software Stack**

Ensure your secondary drive or current Antigravity shadow workspace environment satisfies these runtime conditions.

### **A. Runtime Environment**

* **Python 3.10 or higher** (Required for stable async execution loops).  
* **SQLite** (Built into standard Python libraries; no extra engine setup needed).

### **B. Target Package Dependencies**

Add these libraries to your virtual environment or requirements.txt:

Plaintext  
python-telegram-bot\>=20.0  
requests\>=2.31.0

## **3\. Architecture & Relational Database Schema**

When structuring the project inside Antigravity, separate your system into modular layers. The database tracking utilizes a one-to-many relationship indexed by the user's email.

├── app.py                 \# Core application entry point & interaction logic  
├── config.py              \# Environment variables and API keys  
├── zoom\_service.py        \# Zoom OAuth token management & approval requests  
└── storage.py             \# SQLite helper methods for tracking user profiles & history

### **Relational Schema Blueprint (SQLite)**

SQL  
\-- 1\. Main User Profile Table (Indexed by Email)  
CREATE TABLE IF NOT EXISTS users (  
    registered\_email TEXT PRIMARY KEY,  
    telegram\_id INTEGER,  
    global\_status TEXT DEFAULT 'Pending', \-- Pending, Approved, Denied, Blacklisted  
    behavior\_notes TEXT DEFAULT '',       \-- Admin notes for behavioral tracking  
    created\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP,  
    updated\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP  
);

\-- 2\. Form Submissions History (To handle multiple names used by the same email)  
CREATE TABLE IF NOT EXISTS submissions\_history (  
    id INTEGER PRIMARY KEY AUTOINCREMENT,  
    registered\_email TEXT,  
    submitted\_zoom\_name TEXT,  
    submitted\_telegram\_username TEXT,  
    meeting\_id TEXT,  
    action\_taken TEXT,                    \-- Approved, Denied, Deferred  
    action\_timestamp TIMESTAMP DEFAULT CURRENT\_TIMESTAMP,  
    FOREIGN KEY(registered\_email) REFERENCES users(registered\_email)  
);

## **4\. Key Benefits of This Database Upgrade**

* **Duplicate Email Management:** If a user attempts to reuse an email address but alters their Zoom display name, the system detects it instantly via the primary key index. You will see a comprehensive historical log of how many times that email applied and with which names.  
* **Automated Blacklisting:** Once the admin flags a user as "Blacklisted," the system blocks that identity permanently. Any subsequent application using that email is auto-rejected without reaching the admin queue.  
* **Deferred Review ("Later" Option):** If you are away or busy, the "Later" state defers the decision while triggering an automatic holding response to the user. This ensures the user receives instant feedback without being ignored.  
* **Behavioral Auditing & Reports:** The custom /report admin command allows you to pull structured summaries—tracking banned identities, submission frequencies, and flagging suspicious activities.

## **5\. Core Operational Logic & Expected Flow**

* **Pre-check Guard:** Before presenting a decision card to the admin, the bot checks the users table. If the email is flagged as Blacklisted, it triggers an immediate rejection via the Zoom API silently, saving admin review time.  
* **Admin Dashboard Integration:** When an active request comes in, the bot queries the submissions\_history to compile a complete history map (e.g., *"This email previously applied under 2 different names"*), giving the admin data-driven context before clicking an action button.  
* **State Machine Synchronicity:** Every decision made via the inline buttons (Approve, Deny, Later) instantly updates both the local SQLite database state and the remote Zoom Server-to-Server OAuth registry concurrently.

## **Next Steps for Antigravity Setup**

1. Create a clean project workspace directory.  
2. Initialize your virtual environment: python \-m venv venv && source venv/bin/activate.  
3. Save this entire block into a file named CONTEXT.md in your project root, then tell your Antigravity agent: *"Read CONTEXT.md and scaffold the module files based on this structural layout."*
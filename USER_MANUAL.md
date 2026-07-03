# 📘 Telegram & Zoom Approval System User Manual

Welcome to the operations and maintenance manual for the **Telegram & Zoom Automated Approval System**. This document provides copy-pasteable commands and step-by-step guides for version control, database maintenance, running the bot, and troubleshooting.

---

## 🚀 1. Bot Operations

### Starting the Bot
To launch the bot locally, open your terminal (Command Prompt, PowerShell, or VS Code terminal) in the project directory and run:
```bash
python app.py
```

### Stopping the Bot
To terminate the bot process, press **`Ctrl + C`** inside the terminal window where the bot is running.

### Running System Tests
To run the automated integration tests to ensure that code refactoring didn't introduce compile or logic issues:
```bash
python test_system.py
```

### Viewing Logs
The bot writes real-time logs to the console and logs connection statuses. Look out for the following warnings:
*   `WARNING - Zoom meeting health check failed: status=...` — Indicates credential or meeting configuration problems.
*   `INFO - User XXX started the conversation` — Logs user interactions.

---

## 🗄️ 2. Database Management

The bot supports both local development databases and cloud hosting databases.

### Switching Databases
All configurations are managed in your `.env` file (or Render environment variables):
*   **SQLite (Local)**: Ensure the `DATABASE_URL` variable is empty or commented out. The bot will automatically fallback to SQLite and write to `database.db`.
*   **PostgreSQL (Cloud / Supabase)**: Set `DATABASE_URL` to your Supabase connection string. For example:
    ```env
    DATABASE_URL="postgresql://postgres:password@db.supabase.co:5432/postgres"
    ```

---

## 🐙 3. Git & GitHub Version Control Guide

Use these commands to compare, save, and sync your code changes with your GitHub repository.

### Comparing Local Code with GitHub
Before pushing updates, check what has changed locally:
```bash
# 1. See which files have been modified or added
git status

# 2. View a line-by-line comparison of your changes
git diff

# 3. View a simple summary of modified lines
git diff --stat
```

### Saving & Pushing Changes to GitHub
To upload your local updates (like the new health checks or keyboard fixes) to GitHub:
```bash
# 1. Stage all modified and new files for saving
git add .

# 2. Commit the changes with a clear message
git commit -m "feat: add Zoom health checks, Telegram ID warnings, and Telegraph manual"

# 3. Push the commits to your remote GitHub repository
git push origin master
```

### Fetching Updates from GitHub
If you made changes on GitHub directly (or on another computer) and need to sync them to this machine:
```bash
git pull origin master
```

### Undoing Mistakes
If you made changes that broke the bot and want to restore the code back to the latest GitHub version:
```bash
# Revert modifications in a specific file
git restore app.py

# Revert modifications in all files
git restore .
```

---

## 🔑 4. Zoom API Recovery Procedures

If the Zoom API or Zoom Meeting health checks in the Admin panel turn **`Broken 🔴`**, follow these steps:

### Check live warnings:
If the API status is `Error`, look at the bot console log to find the Zoom API status code:
*   **Status 401 (Unauthorized)**: Access token expired or invalid client credentials.
*   **Status 400/404 (Meeting/Registrants Error)**: The meeting ID you configured does not exist, has registration disabled, or belongs to a different Zoom account.

### How to configure new S2S OAuth app credentials:
If your host email is suspended, you must create a new Server-to-Server OAuth app.
1. Follow the **[Telegraph Zoom App Recovery Guide](https://telegra.ph/Zoom-OAuth-App-Recovery-Guide-07-02-2)** to create a new app and activate it.
2. In the Telegram Bot, go to **`⚙️ Configure Zoom`**.
3. Update the credentials with the new values:
   * **`Set Client ID`**
   * **`Set Client Secret`**
   * **`Set Account ID`**
4. Update the **Meeting ID** and **Registration Link** with a newly created meeting.
5. Tap **`Back to panel 🛡️`** to refresh and confirm the health indicators are green!

---

## 🛠️ 5. Zoom Sync & Legacy Approvals

If you have legacy registrants who signed up directly through your Zoom registration link but did not submit their details via the Telegram bot, use these administrative commands in the admin chat to synchronize or bulk-approve them:

### `/synczoom` (Sync Registrants from Zoom)
*   **What it does**: Fetches the list of all registrants (`pending`, `approved`, and `denied`) directly from the Zoom meeting API and writes them into the bot database.
*   **Use case**: Use this if you want to import existing registrants into the bot's tracking database.

### `/approveall` (Bulk Approve on Zoom)
*   **What it does**: Automatically fetches all currently pending registrants from Zoom, bulk-approves them directly on the Zoom API, and registers their profiles in your database as Approved.
*   **Use case**: Use this when switching meeting IDs or if users registered on the web link and need immediate approval without requesting via the Telegram bot first.


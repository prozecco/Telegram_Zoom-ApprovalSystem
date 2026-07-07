---
name: system-review
description: Comprehensive system review checklist and methodology for auditing the Telegram & Zoom Automated Approval System. Use this when asked to review, audit, or check the system for issues.
---

# System Review Skill

## When to Use
Trigger this skill when the user asks to:
- Review the system / codebase
- Audit for bugs, security issues, or performance problems
- Do a health check or quality assessment
- Prepare for production deployment

## Review Methodology

### Phase 1: Architecture & Configuration Audit
1. **Environment Variables**: Compare `.env` / `.env.example` against all `os.getenv()` calls in `config.py`. Flag any missing variables.
2. **Dependencies**: Compare `requirements.txt` against all `import` statements. Flag missing packages.
3. **Deployment Config**: Check `Dockerfile`, `Procfile`, or `render.yaml` for correctness.
4. **Secrets Hygiene**: Verify `.env` is in `.gitignore` and NOT tracked by git. Check if any secrets are committed in history.

### Phase 2: Backend Security Audit
1. **Authentication Bypass**: Search for hardcoded tokens, mock auth, or debug backdoors in `verify_admin_access()` and all `Depends()` calls.
2. **CORS Configuration**: Check `CORSMiddleware` — `allow_origins=["*"]` with `allow_credentials=True` is an anti-pattern.
3. **Secret Exposure**: Ensure API credentials (Zoom secrets, bot tokens) are never returned to frontend in plaintext. Mask sensitive values.
4. **SQL Injection**: Verify all database queries use parameterized queries. Check for string concatenation in SQL.
5. **Input Validation**: Check all user-facing endpoints for proper input validation.

### Phase 3: Database Integrity Audit
1. **Schema Migrations**: Check `init_db()` for ALTER TABLE statements. Ensure they run in isolated transactions (PostgreSQL aborts entire transaction block on failed ALTER).
2. **Parameter Format**: Ensure all queries go through `execute_query()` wrapper for `?` to `%s` conversion on PostgreSQL. Direct `cursor.execute()` with `?` breaks PostgreSQL.
3. **Status Corruption**: Verify all `update_user_status()` calls pass the correct status — passing `None` will corrupt the status column to NULL.
4. **Indexes**: Check if frequently-queried columns have indexes (especially `telegram_id`, `registered_email`).
5. **N+1 Queries**: Look for loops that execute individual queries per row.

### Phase 4: API Integration Audit
1. **Telegram Bot API**: 
   - `file.file_path` is a RELATIVE path — must prepend `https://api.telegram.org/file/bot{TOKEN}/` for full URLs
   - `getUserProfilePhotos` returns photo sizes array — use `[-1]` for highest resolution
2. **Zoom API**:
   - Check OAuth token caching and refresh logic
   - Verify `list_registrants` handles pagination correctly
   - Check error handling — silent `except: pass` hides failures
3. **Userbot (Pyrogram)**:
   - Requires 3 env vars: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`
   - Session string is sensitive — never log or expose
   - `resolve_entity()` must be guarded with a `client is not None` check before attempting resolution

### Phase 5: Frontend Audit
1. **DOM Reference Integrity**: For every `document.getElementById('X')` in JS, verify a matching `id="X"` exists in the HTML.
2. **XSS Prevention**: Check for `innerHTML` assignments with user data — use `escapeHtml()` or `textContent`.
3. **Navigation**: Links with `target="_blank"` will open in external browser when inside Telegram WebApp. Use inline navigation instead.
4. **Error States**: Verify all API calls have error handling and show user-friendly messages.

### Phase 6: Performance Audit
1. **Duplicate Service Instances**: Check if `ZoomService()`, `Bot()`, etc. are instantiated multiple times unnecessarily.
2. **API Call Frequency**: Check if questions/settings are fetched from Zoom API on every page load — should be cached in database.
3. **Background Sync**: Verify sync interval parsing and jitter to prevent API rate limiting.

## Common Bug Patterns in This Codebase

| Pattern | Example | Fix |
|---------|---------|-----|
| PostgreSQL transaction abort | `ALTER TABLE` inside `init_db()` `get_db()` block | Use separate `get_db()` context for migrations |
| Status NULL corruption | `update_user_status(email, None, notes=...)` | Read current status first, or use dedicated notes function |
| Broken Telegram file URLs | Storing `file.file_path` directly | Prepend `https://api.telegram.org/file/bot{token}/` |
| `?` placeholder on PostgreSQL | Direct `cursor.execute("... ? ...", params)` | Always use `execute_query()` wrapper |
| External browser in Telegram | `<a target="_blank">` in Mini App | Remove `target="_blank"` for inline navigation |

## Output Format
After completing a review, produce:
1. A markdown report artifact (`system_review_report.md`) with findings grouped by severity
2. Immediate fixes for all CRITICAL and HIGH issues
3. Recommendations list for MEDIUM/LOW issues
4. Updated environment variables checklist

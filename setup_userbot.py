import os
import asyncio

async def generate_session():
    print("\n=== Telegram Userbot Session Generator ===")
    print("Please retrieve your API ID and API Hash from https://my.telegram.org")
    
    api_id_str = input("Enter API ID: ").strip()
    api_hash = input("Enter API Hash: ").strip()
    
    if not api_id_str or not api_hash:
        print("Error: API ID and API Hash are required.")
        return
        
    try:
        api_id = int(api_id_str)
    except ValueError:
        print("Error: API ID must be an integer.")
        return

    from pyrogram import Client
    # Initialize Pyrogram client in memory (no file created)
    async with Client(":memory:", api_id=api_id, api_hash=api_hash) as app:
        session_str = await app.export_session_string()
        print("\n" + "="*50)
        print("✅ SUCCESS! HERE IS YOUR TELEGRAM_SESSION_STRING:")
        print("="*50)
        print(session_str)
        print("="*50)
        print("Copy the entire string above and set it as your environment variable:")
        print("TELEGRAM_SESSION_STRING")
        print("Also set TELEGRAM_API_ID and TELEGRAM_API_HASH.")
        print("="*50 + "\n")

if __name__ == "__main__":
    try:
        import pyrogram
    except ImportError:
        print("Pyrogram is not installed. Installing it now...")
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyrogram", "tgcrypto"])
        print("Pyrogram installed successfully!\n")
    
    asyncio.run(generate_session())

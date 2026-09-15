#!/usr/bin/env python3
"""
Interactive String Session Generator for TeleForge userbot.
Generates an exportable Pyrogram session string for seamless deployment.
"""
import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Patch Pyrogram 2.0.x 32-bit channel ID limitation to support modern 64-bit channel IDs
try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1000000000000000000
    pyrogram.utils.MAX_USER_ID = 1000000000000000000
except Exception:
    pass

import os
import sys
from pathlib import Path
from pyrogram import Client

def load_existing_env():
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_file)
        except ImportError:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

async def main():
    load_existing_env()

    print("\nTeleForge · String Session Generator")
    print("Credentials can be retrieved from https://my.telegram.org\n")

    api_id_env = os.getenv("API_ID", "").strip()
    api_hash_env = os.getenv("API_HASH", "").strip()

    if api_id_env:
        print(f"Detected API_ID in .env: {api_id_env}")
        api_id_input = input("Use this API_ID? (Y/n): ").strip().lower()
        api_id = int(api_id_env) if api_id_input in ["", "y", "yes"] else int(input("Enter API_ID: ").strip())
    else:
        api_id = int(input("Enter API_ID: ").strip())

    if api_hash_env:
        print(f"Detected API_HASH in .env: {api_hash_env[:6]}...")
        api_hash_input = input("Use this API_HASH? (Y/n): ").strip().lower()
        api_hash = api_hash_env if api_hash_input in ["", "y", "yes"] else input("Enter API_HASH: ").strip()
    else:
        api_hash = input("Enter API_HASH: ").strip()

    print("\nConnecting to Telegram servers...")
    client = Client(
        name=":memory:",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    )

    async with client:
        session_string = await client.export_session_string()
        user = await client.get_me()
        name = user.first_name + (f" {user.last_name}" if user.last_name else "")

        print(f"\nAuthenticated as: {name} (@{user.username or user.id})\n")
        print("Pyrogram String Session (keep this private):\n")
        print(session_string)
        print("")

        # Offer to write into .env
        save_env = input("\nSave credentials into .env automatically? (Y/n): ").strip().lower()
        if save_env in ["", "y", "yes"]:
            env_file = Path(__file__).resolve().parent / ".env"
            env_vars = {}
            if env_file.is_file():
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_vars[k.strip()] = v.strip()

            env_vars["API_ID"] = str(api_id)
            env_vars["API_HASH"] = str(api_hash)
            env_vars["SESSION_STRING"] = session_string
            if "TRIGGER" not in env_vars:
                env_vars["TRIGGER"] = "."

            out_lines = [
                f"API_ID={env_vars['API_ID']}",
                f"API_HASH={env_vars['API_HASH']}",
                f"TRIGGER={env_vars.get('TRIGGER', '.')}",
                f"SESSION_STRING={env_vars['SESSION_STRING']}",
            ]
            for k, v in env_vars.items():
                if k not in ["API_ID", "API_HASH", "TRIGGER", "SESSION_STRING"]:
                    out_lines.append(f"{k}={v}")

            with open(env_file, "w", encoding="utf-8") as f:
                f.write("\n".join(out_lines) + "\n")
            print("Updated .env with API_ID, API_HASH, and SESSION_STRING.")

    print("\nSetup complete. You can now launch TeleForge using: ./pygramx.sh start")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.")

import os
import sys
from pathlib import Path

# Load .env file if available
env_path = Path(__file__).resolve().parent / ".env"
if env_path.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path)
    except ImportError:
        # Fallback simple parser if python-dotenv is not installed
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

API_ID_RAW = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
TRIGGER_DEFAULT = os.getenv("TRIGGER", ".").strip() or "."
BOT_TRIGGER_DEFAULT = os.getenv("BOT_TRIGGER", "/").strip() or "/"
SESSION_STRING = os.getenv("SESSION_STRING", "").strip() or None
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip() or None
LOG_CHAT_RAW = os.getenv("LOG_CHAT", "").strip() or None
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip() or None
LOG_LEVEL_RAW = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

def get_trigger() -> str:
    """
    Retrieve active command trigger prefix for userbot.
    Checks dynamic local database override first, then environment variable.
    """
    try:
        from pygramx.database import db
        db_trigger = db.get("TRIGGER")
        if db_trigger and str(db_trigger).strip():
            return str(db_trigger).strip()
    except Exception:
        pass
    return TRIGGER_DEFAULT

def get_bot_trigger() -> str:
    """
    Retrieve active command trigger prefix for companion bot.
    Checks dynamic local database override first, then environment variable.
    """
    try:
        from pygramx.database import db
        db_trigger = db.get("BOT_TRIGGER")
        if db_trigger and str(db_trigger).strip():
            return str(db_trigger).strip()
    except Exception:
        pass
    return BOT_TRIGGER_DEFAULT

def __getattr__(name: str):
    if name == "TRIGGER":
        return get_trigger()
    if name == "BOT_TRIGGER":
        return get_bot_trigger()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def get_log_level():
    """Retrieve configured logging level (INFO, DEBUG, WARNING, ERROR)."""
    import logging
    return getattr(logging, LOG_LEVEL_RAW, logging.INFO)

def get_battery_threshold() -> float:
    """Retrieve temperature threshold in Celsius for thermal alerts."""
    try:
        from pygramx.database import db
        db_val = db.get("BATTERY_THRESHOLD")
        if db_val is not None:
            return float(db_val)
    except Exception:
        pass
    try:
        return float(os.getenv("BATTERY_THRESHOLD", "42.0"))
    except ValueError:
        return 42.0

def is_watchdog_enabled() -> bool:
    """Check if battery thermal watchdog is active."""
    try:
        from pygramx.database import db
        db_val = db.get("WATCHDOG_ENABLED")
        if db_val is not None:
            return bool(db_val)
    except Exception:
        pass
    val = os.getenv("WATCHDOG_ENABLED", "true").strip().lower()
    return val not in ["false", "0", "no", "off"]

def get_owner_id():
    """
    Retrieve configured owner user ID.
    Checks dynamic local database override first, then environment variable.
    """
    try:
        from pygramx.database import db
        db_owner = db.get("OWNER_ID")
        if db_owner:
            return int(db_owner)
    except Exception:
        pass

    if OWNER_ID_RAW:
        try:
            return int(OWNER_ID_RAW)
        except ValueError:
            pass
    return None

def get_log_chat():
    """
    Retrieve configured log/alert chat target.
    Checks dynamic local database override first, then environment variable.
    """
    try:
        from pygramx.database import db
        db_target = db.get("LOG_CHAT")
        if db_target is not None and str(db_target).strip():
            try:
                return int(db_target)
            except (ValueError, TypeError):
                return str(db_target)
    except Exception:
        pass

    if not LOG_CHAT_RAW:
        return None

    try:
        return int(LOG_CHAT_RAW)
    except ValueError:
        return str(LOG_CHAT_RAW)

def validate():
    if not API_ID_RAW or not API_HASH:
        sys.exit(
            "\n[TeleForge Configuration Error]\n"
            "Missing API_ID or API_HASH in your configuration.\n"
            "1. Obtain credentials from https://my.telegram.org\n"
            "2. Run './pygramx.sh session' to configure your account interactively,\n"
            "   or populate API_ID and API_HASH inside .env\n"
        )
    try:
        int(API_ID_RAW)
    except ValueError:
        sys.exit("\n[TeleForge Configuration Error]\nAPI_ID must be a valid integer.\n")

    if not SESSION_STRING and not os.path.isfile("pygramx.session"):
        sys.exit(
            "\n[TeleForge Authentication Error]\n"
            "No Telegram session found in .env and no pygramx.session file exists.\n"
            "Please authenticate your userbot account first by running:\n"
            "  ./pygramx.sh session\n"
        )

def get_api_id() -> int:
    return int(API_ID_RAW)

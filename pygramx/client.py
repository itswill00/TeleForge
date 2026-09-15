import asyncio
import logging
import os
import shutil
import sys
import time
from typing import Dict, Any, List, Optional
from pyrogram import Client
import config

logger = logging.getLogger("pygramx")

class PyGramClient(Client):
    """
    Custom Pyrogram client subclass for TeleForge userbot.
    Maintains centralized command metadata and bot lifecycle.
    """
    COMMANDS: Dict[str, Dict[str, Any]] = {}
    START_TIME: float = time.time()
    OWNER_ID: Optional[int] = None
    BOT_ID: Optional[int] = None
    USERBOT_CLIENT: Optional[Any] = None
    BOT_CLIENT: Optional[Any] = None
    MODULE_HANDLERS: Dict[str, List[Any]] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_time = time.time()
        logger.info("Initializing PyGramClient instance")

    @classmethod
    def register_command(
        cls,
        name: str,
        desc: str = "",
        usage: str = "",
        aliases: Optional[List[str]] = None,
        category: str = "general",
        module: Optional[str] = None,
    ) -> None:
        cls.COMMANDS[name] = {
            "desc": desc,
            "usage": usage,
            "aliases": aliases or [],
            "is_alias": False,
            "category": category,
            "module": module,
        }
        if aliases:
            for alias in aliases:
                cls.COMMANDS[alias] = {
                    "desc": f"Alias for {name}",
                    "usage": usage,
                    "aliases": [],
                    "is_alias": True,
                    "primary": name,
                    "category": category,
                    "module": module,
                }

    @classmethod
    def unregister_command(cls, name: str) -> None:
        meta = cls.COMMANDS.pop(name, None)
        if meta and not meta.get("is_alias"):
            for alias in meta.get("aliases", []):
                cls.COMMANDS.pop(alias, None)

    @classmethod
    def load_module_handlers(cls, module: Any) -> int:
        """Dynamically attach all handlers declared in a module to running clients."""
        from pyrogram.handlers.handler import Handler
        count = 0
        mod_name = getattr(module, "__name__", "")
        tracked = cls.MODULE_HANDLERS.setdefault(mod_name, [])

        for attr_name in dir(module):
            try:
                item = getattr(module, attr_name)
                handlers = getattr(item, "handlers", None)
                if isinstance(handlers, list):
                    for handler, group in handlers:
                        if isinstance(handler, Handler) and isinstance(group, int):
                            if handler not in tracked:
                                tracked.append(handler)
                            for cl in [cls.USERBOT_CLIENT, cls.BOT_CLIENT]:
                                if cl and hasattr(cl, "add_handler") and getattr(cl, "is_connected", False):
                                    try:
                                        cl.add_handler(handler, group)
                                    except Exception as e:
                                        logger.debug("Failed adding dynamic handler: %s", e)
                            count += 1
            except Exception:
                pass
        return count

    @classmethod
    def unload_module(cls, mod_name: str) -> list[str]:
        """Unregister handlers and remove commands associated with a dynamic module."""
        handlers = cls.MODULE_HANDLERS.pop(mod_name, [])
        for handler in handlers:
            for cl in [cls.USERBOT_CLIENT, cls.BOT_CLIENT]:
                if cl and hasattr(cl, "remove_handler"):
                    try:
                        cl.remove_handler(handler)
                    except Exception:
                        pass

        # Sweep dispatcher groups directly for any handlers defined in this module
        for cl in [cls.USERBOT_CLIENT, cls.BOT_CLIENT]:
            if cl and hasattr(cl, "dispatcher") and hasattr(cl.dispatcher, "groups"):
                for group_id, handler_list in list(cl.dispatcher.groups.items()):
                    to_remove = [
                        h for h in handler_list
                        if getattr(getattr(h, "callback", None), "__module__", "") == mod_name
                    ]
                    for h in to_remove:
                        try:
                            handler_list.remove(h)
                        except Exception:
                            pass

        removed_cmds = []
        for cmd_name, meta in list(cls.COMMANDS.items()):
            if not meta.get("is_alias") and meta.get("module") == mod_name:
                cls.unregister_command(cmd_name)
                removed_cmds.append(cmd_name)

        sys.modules.pop(mod_name, None)
        return removed_cmds

    @property
    def uptime(self) -> float:
        return time.time() - self.start_time

    async def start(self):
        result = await super().start()
        user = await self.get_me()

        if not user.is_bot:
            PyGramClient.USERBOT_CLIENT = self
            PyGramClient.OWNER_ID = user.id
            try:
                from pygramx.database import db
                db.set("OWNER_ID", user.id)
            except Exception:
                pass

            # Handle post-restart notification edit if restart was triggered
            try:
                from pygramx.database import db
                restart_data = db.get("RESTART_NOTICE")
                if restart_data:
                    db.delete("RESTART_NOTICE")
                    elapsed = time.time() - restart_data.get("time", time.time())
                    header = restart_data.get("header", "TeleForge restarted successfully!")
                    extra = restart_data.get("extra")
                    lines = [f"**{header}**", f"• **Downtime:** `{elapsed:.2f}s`"]
                    if extra:
                        lines.append(extra)
                    try:
                        await self.edit_message_text(
                            chat_id=restart_data["chat_id"],
                            message_id=restart_data["message_id"],
                            text="\n".join(lines),
                        )
                    except Exception:
                        await self.send_message(
                            chat_id=restart_data["chat_id"],
                            text="\n".join(lines),
                        )
            except Exception as e:
                logger.debug("Post-restart notice check skipped: %s", e)

            # Spawn background battery thermal watchdog on userbot
            asyncio.create_task(self._battery_watchdog())

            # Spawn background ISS pass alert watchdog
            try:
                from modules.astronomy import start_iss_watcher
                start_iss_watcher(self)
            except Exception as e:
                logger.debug("ISS watcher defer on start: %s", e)

            # Pre-cache help banner photo so first .help is instant
            try:
                from modules.system import _background_cache_pfp
                asyncio.create_task(_background_cache_pfp())
            except Exception as e:
                logger.debug("Help photo pre-cache defer on start: %s", e)
        else:
            PyGramClient.BOT_CLIENT = self
            PyGramClient.BOT_ID = user.id
            if getattr(user, "username", None):
                try:
                    from pygramx.database import db
                    db.set("BOT_USERNAME", user.username)
                except Exception:
                    pass
            logger.info("Companion bot active as @%s (%d)", user.username, user.id)
            try:
                from modules.system import _background_cache_pfp
                asyncio.create_task(_background_cache_pfp())
            except Exception as e:
                logger.debug("Help photo pre-cache defer on bot start: %s", e)

        return result

    async def _battery_watchdog(self):
        """
        Adaptive background watchdog:
        - Auto-prunes temporary files in downloads/ older than 24 hours.
        - On Termux/Android: monitors battery thermal condition.
        - On Linux Server/VPS: monitors disk storage and RAM exhaustion.
        Dispatches alerts to configured LOG_CHAT when thresholds are breached.
        """
        CHECK_INTERVAL_SECONDS = 300  # 5 minutes
        COOLDOWN_SECONDS = 1800  # 30 minutes between repeated alerts
        last_alert_time = 0.0
        last_alert_level = ""

        # Delay initial check to allow connection stabilization
        await asyncio.sleep(20)

        while True:
            # 1. Periodic cleanup of stale downloads (>24h)
            try:
                now_ts = time.time()
                if os.path.isdir("downloads"):
                    for entry in os.scandir("downloads"):
                        if entry.is_file() and (now_ts - entry.stat().st_mtime) > 86400:
                            try:
                                os.remove(entry.path)
                            except OSError:
                                pass
            except Exception:
                pass

            # 2. Watchdog metrics check
            try:
                if not config.is_watchdog_enabled():
                    await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                log_chat_raw = config.get_log_chat()
                log_chat = int(log_chat_raw) if log_chat_raw and str(log_chat_raw).lstrip("-").isdigit() else log_chat_raw

                from modules.system import _get_battery_stats
                batt = _get_battery_stats()
                now = time.time()

                if batt and batt.get("temp") and batt["temp"] != "Normal":
                    # Android / Termux Battery Watchdog
                    alert_threshold_c = config.get_battery_threshold()
                    critical_threshold_c = alert_threshold_c + 3.0
                    temp_str = batt["temp"].replace("°C", "").strip()
                    try:
                        temp_c = float(temp_str)
                    except ValueError:
                        temp_c = 0.0

                    if temp_c >= alert_threshold_c and log_chat:
                        severity = "CRITICAL" if temp_c >= critical_threshold_c else "WARNING"
                        if (now - last_alert_time >= COOLDOWN_SECONDS) or (
                            severity == "CRITICAL" and last_alert_level != "CRITICAL"
                        ):
                            last_alert_time = now
                            last_alert_level = severity
                            from pygramx.utils import get_device_model, send_auto_notification
                            dev_name = get_device_model()
                            alert_msg = (
                                f"**Thermal Alert · {severity}**\n"
                                f"• **Device:** {dev_name}\n"
                                f"• **Temperature:** `{batt['temp']}`\n"
                                f"• **Battery Level:** `{batt['level']}` ({batt.get('status', 'Unknown')})\n"
                                f"• **Battery Condition:** {batt.get('health', 'Normal')}\n\n"
                                f"The battery temperature has exceeded the safety threshold of {alert_threshold_c}°C. Please check the charger or improve airflow."
                            )
                            try:
                                await send_auto_notification(alert_msg)
                            except Exception as err:
                                logger.warning("Failed to deliver battery alert: %s", err)

                    elif temp_c < (alert_threshold_c - 3.0) and last_alert_level != "":
                        from pygramx.utils import send_auto_notification
                        try:
                            await send_auto_notification(
                                f"**Temperature Restored**\n"
                                f"• **Temperature:** `{batt['temp']}`\n"
                                f"• **Battery Level:** `{batt['level']}`\n\n"
                                f"The battery temperature has returned to safe operational levels."
                            )
                        except Exception:
                            pass
                        last_alert_level = ""
                else:
                    # Cloud / VPS Server Watchdog (Storage & Memory)
                    if (now - last_alert_time >= COOLDOWN_SECONDS):
                        from pygramx.utils import format_bytes, send_auto_notification

                        # Check Disk Storage
                        try:
                            total, used, free = shutil.disk_usage(".")
                            disk_percent = (used / total) * 100 if total else 0.0
                            if disk_percent >= 90.0:
                                last_alert_time = now
                                alert_msg = (
                                    f"**Server Storage Alert**\n"
                                    f"• **Disk Usage:** `{disk_percent:.1f}%`\n"
                                    f"• **Used:** `{format_bytes(used)}` / `{format_bytes(total)}`\n"
                                    f"• **Free:** `{format_bytes(free)}`\n\n"
                                    f"Disk usage has exceeded 90%. Please clean logs or downloads to prevent database locks."
                                )
                                try:
                                    await send_auto_notification(alert_msg)
                                except Exception as err:
                                    logger.warning("Failed to deliver disk alert: %s", err)
                        except Exception:
                            pass

                        # Check RAM Usage
                        from modules.system import _get_memory_usage
                        try:
                            mem = _get_memory_usage()
                            if mem and mem.get("percent", 0.0) >= 92.0:
                                last_alert_time = now
                                alert_msg = (
                                    f"**Server Memory Alert**\n"
                                    f"• **RAM Usage:** `{mem['percent']:.1f}%`\n"
                                    f"• **Used:** `{format_bytes(mem['used'])}` / `{format_bytes(mem['total'])}`\n"
                                    f"• **Free:** `{format_bytes(mem.get('free', 0))}`\n\n"
                                    f"System RAM is critically high (>92%). Bot may be killed by Linux OOM killer."
                                )
                                try:
                                    await send_auto_notification(alert_msg)
                                except Exception as err:
                                    logger.warning("Failed to deliver memory alert: %s", err)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("Adaptive watchdog error: %s", e)

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

# Backward compatibility alias
AltClient = PyGramClient

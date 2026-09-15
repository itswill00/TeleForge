# TeleForge Developer Guide

<p align="center">
  <img src="https://img.shields.io/badge/Guide-Architecture%20%26%20Extending-8A2BE2?style=flat-square" alt="Developer Guide">
  <img src="https://img.shields.io/badge/Pattern-Modular%20Plugins-3776AB?style=flat-square" alt="Modular Plugins">
  <img src="https://img.shields.io/badge/Storage-LocalDB-003B57?style=flat-square" alt="Storage">
</p>

Comprehensive architectural manual for developing, debugging, and extending TeleForge.

## Architectural Fundamentals

TeleForge operates on an asynchronous dual-client foundation designed for high-availability background execution on Android Termux and Linux servers, incorporating vital modernization patches over upstream Pyrogram 2.0.x:

- **Dual-Client Architecture & Delegation**: Runs both the Userbot (authenticated via MTProto string session) and an optional Companion Bot (authenticated via Bot API token) simultaneously using `pyrogram.compose`. MTProto-restricted actions (such as `messages.GetHistory` and user reactions) are delegated to the userbot client seamlessly.
- **Prefix Isolation**: The Userbot handles the dynamic trigger (`.` by default, configurable via `.settrigger`), while the Companion Bot responds strictly to `/` inside authorized groups or private DMs to eliminate command collision.
- **Event Loop Stability**: Modern Python 3.12+ / 3.14+ deprecates implicit event loop creation. TeleForge guarantees active event loop initialization across all module files and subshell spawns via `asyncio.set_event_loop()`.
- **64-bit Peer ID Patch**: Upstream Pyrogram hardcodes `MIN_CHANNEL_ID = -1002147483647` (32-bit limit). TeleForge monkeypatches `pyrogram.utils.MIN_CHANNEL_ID = -1000000000000000000` and `pyrogram.utils.MAX_USER_ID = 1000000000000000000` across all entrypoints and patches `pyrogram/utils.py` during installation to support modern 64-bit Telegram channels.
- **Bot API 9.4 Styled Buttons Bridge**: Native MTProto layer lacks styled buttons (`primary`, `success`, `danger`). TeleForge integrates an atomic HTTP REST bridge for styled button delivery without visual render lag.
- **Boolean Type Safety**: Explicitly evaluates `isinstance(res, bool)` before `isinstance(res, int)` to guard against Python's boolean inheritance trap on deletion counting.

## Module Development

Modules reside in the `modules/` directory and are automatically loaded during startup.

### 1. Basic Command Structure

Every command function must be decorated with `@on_cmd`:

```python
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

@on_cmd("hello", desc="Send greeting message", usage="[name]")
async def hello_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    target = args[1].strip() if len(args) > 1 else "world"
    await edit_or_reply(message, f"Hello, {target}!")
```

### 2. Command Aliases

Provide a list of strings to register multiple aliases for a single command handler:

```python
@on_cmd(["stats", "sys", "hardware"], desc="Display hardware statistics")
async def stats_cmd(client: Client, message: Message):
    await edit_or_reply(message, "Hardware statistics...")
```

The first element (`stats`) serves as the primary command name in the registry, while remaining elements are registered as dynamic aliases.

### 3. Client Context & Dynamic Prefixes

Commands may be triggered either from the userbot or the companion bot. To format syntax guides accurately, resolve the active client prefix using `get_client_prefix(client)`:

```python
@on_cmd("sample", desc="Example command", usage="<value>")
async def sample_cmd(client: Client, message: Message):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"`Usage: {prefix}sample <value>`")
        return
    await edit_or_reply(message, f"Value: `{args[1]}`")
```

### 4. Output Formatting & Safe Delivery

Telegram limits individual messages to 4096 characters. TeleForge provides utilities to handle text safely without crashing or truncating abruptly:

- `edit_or_reply(message, text)`: Edits outgoing self-messages or replies to incoming messages. Catches `FloodWait` exceptions and repairs unclosed markdown codeblocks automatically.
- `send_large_output(message, content, caption, filename)`: Delivers output inline if under 3900 characters. If output exceeds limits, it uploads the content as a document file attachment (e.g. `.txt` or `.py`).

Example:

```python
from pygramx.utils import send_large_output

@on_cmd("fetch", desc="Fetch large data dump")
async def fetch_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Fetching data...`")
    raw_data = "Large payload content..."
    await send_large_output(
        status_msg,
        raw_data,
        caption="**Data Dump Result:**",
        filename="dump.txt",
    )
```

## State & Local Database

TeleForge provides `LocalDB`, a SQLite-backed persistent key-value store with an in-memory cache for O(1) reads:

```python
from pygramx import db

# Storing data (automatically JSON serialized)
db.set("MY_KEY", {"enabled": True, "count": 42})

# Retrieving data
data = db.get("MY_KEY", default={})
enabled = data.get("enabled", False)

# Checking key existence
if db.has("MY_KEY"):
    pass

# Deleting keys
db.delete("MY_KEY")

# Inspecting stored keys
all_keys = db.keys()
```

### Database Best Practices
- Never write credentials, auth tokens, or session strings to `LocalDB`. Keep secrets in `.env`.
- Database operations are synchronous but run in-process with minimal SQLite WAL overhead. Keep payloads concise.
- Use uppercase key conventions (e.g., `LOG_CHAT`, `AFK_DATA`, `TRIGGER`).

## Background Workers & Watchdogs

Long-running workers (such as hardware health or thermal monitors) should be registered inside `PyGramClient.start()` in `pygramx/client.py`:

```python
async def _custom_worker(self):
    await asyncio.sleep(15)  # Wait for connection handshake
    while True:
        try:
            # Perform periodic maintenance or status polling
            await do_work()
        except Exception as e:
            logger.debug("Worker error: %s", e)
        await asyncio.sleep(300)
```

Workers should gracefully handle exceptions to ensure background tasks do not exit silently.

## Verification & Testing

Before committing changes, execute the full diagnostic self-check:

```bash
# 1. Compile all files to detect syntax errors
python3 -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True)]"

# 2. Run architecture diagnostics
./setup.sh

# 3. Check service runtime status
./pygramx.sh status
```

## Git & Commit Conventions

TeleForge adheres to Conventional Commits:

```bash
git add <modified-files>
git commit -s -m "feat(scope): brief summary" -m "Detailed explanation of rationale and design decisions."
git push origin main
```

- Every commit must include `-s` (Signed-off-by) and Gerrit `Change-Id`.
- No decorative ASCII dividers are permitted in commit messages, documentation, or code comments.

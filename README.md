# TeleForge

<p align="center">
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.10%20%7C%203.14-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="https://github.com/pyrogram/pyrogram"><img src="https://img.shields.io/badge/Pyrogram-v2.0.106-2CA5E0?style=flat-square&logo=telegram&logoColor=white" alt="Pyrogram"></a>
  <img src="https://img.shields.io/badge/Protocol-MTProto%20v2.0-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram MTProto">
  <img src="https://img.shields.io/badge/Architecture-Dual--Client%20(User%20%2B%20Bot)-8A2BE2?style=flat-square" alt="Dual-Client Architecture">
  <br>
  <img src="https://img.shields.io/badge/Platform-Termux%20%7C%20Linux-000000?style=flat-square&logo=android&logoColor=3DDC84" alt="Platform">
  <img src="https://img.shields.io/badge/Runner-tmux%20%7C%20Daemon%20%7C%20Boot-1BB954?style=flat-square&logo=tmux&logoColor=white" alt="Runner">
  <img src="https://img.shields.io/badge/Storage-SQLite%20(WAL)-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="Database">
  <img src="https://img.shields.io/badge/System-Battery%20%26%20Thermal-FF5722?style=flat-square" alt="System Health">
  <img src="https://img.shields.io/badge/AsyncIO-Non--Blocking-00599C?style=flat-square" alt="AsyncIO">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License"></a>
</p>

Minimalist, modular Telegram userbot designed for persistent background deployment on Android and Linux environments.

TeleForge is built around Python and Pyrogram, prioritizing low memory footprint, zero background idling overhead, and resilient connection recovery. It features a centralized decorator architecture, SQLite-backed state persistence, and native shell execution capabilities.

## Architecture

- **Event loop resilience**: Automatically handles Python 3.12+ and 3.14+ event loop lifecycles on Android Termux, preventing main-thread runtime aborts.
- **Connection supervisor**: Built-in auto-recovery loop with backoff, ensuring uninterrupted 24/7 background operation across network handovers.
- **Decoupled module registry**: Automatic command indexing with syntax metadata, self-filtering (`filters.me`), and isolated error boundaries preventing client crashes.
- **SQLite local database**: Zero-dependency key-value persistence with in-memory caching and JSON serialization for instant read/write state access.
- **Intelligent document delivery**: Text outputs exceeding Telegram's 4096-character limit are automatically delivered as attached text documents.
- **Universal process manager**: Unified `./pygramx.sh` script providing background execution via tmux or nohup, paired with Android `termux-wake-lock` integration.

## Commands

| Domain | Commands |
| :--- | :--- |
| System | `.ping` `.alive` `.setalive` `.resetalive` `.setalivepic` `.help` `.helppic` `.settings` `.restart` `.update` `.sys` `.backup` `.tail` `.plugins` `.install` `.uninstall` |
| Battery & Hardware | `.watchdog` `.setlog` `.getlog` `.hw` `.kver` `.bootinfo` `.elfinfo` `.lpmeta` `.zipmod` `.modcheck` |
| Administration | `.ban` `.unban` `.kick` `.mute` `.unmute` `.warn` `.warns` `.resetwarns` `.promote` `.demote` `.pin` `.unpin` `.del` `.purge` `.purgeme` `.cleanservice` `.zombies` `.tagall` `.canceltag` `.lock` `.unlock` `.slowmode` `.title` |
| Security & Sessions | `.pmpermit` `.a` `.da` `.block` `.unblock` `.listapproved` `.sessions` `.terminate` |
| Network & Tools | `.speedtest` `.scan` `.unshort` `.dcinfo` `.pingdc` `.netstat` `.eval` `.sh` `.stopsh` `.setvar` `.getvar` `.delvar` `.raw` `.wspr` |
| Media & Audio FX | `.tovn` `.round` `.trim` `.togif` `.frame` `.mutevid` `.tts` `.bass` `.fast` `.slow` `.echo` `.reverse` |
| Visual & Stickers | `.q` `.carbon` `.paste` `.kang` `.packinfo` `.catbox` `.litterbox` `.dl` `.up` |
| Football & Soccer | `.bola` `.epl` `.ucl` |
| Weather | `.weather` |
| Finance | `.crypto` `.kurs` |
| Utility & Status | `.c` `.afk` `.id` `.info` `.chatinfo` `.triggers` `.addtrigger` `.deltrigger` `.settrigger` `.setalias` `.delalias` `.aliases` `.sd` `.ls` `.cat` |

*Syntax details and aliases are inspectable directly in Telegram via `.help <command>` (e.g. `.help sys`).*

## Repository Layout

```text
TeleForge/
├── pygramx.sh            # Process manager (start, stop, logs, status)
├── setup.sh              # Environment installer and verification
├── main.py               # Entrypoint and connection supervisor
├── config.py             # Configuration parser and validator
├── requirements.txt      # Python dependencies
├── pygramx/              # Core framework
│   ├── client.py         # PyGramClient class and command registry
│   ├── data/             # Bundled offline datasets (motorsport calendars)
│   ├── database.py       # SQLite key-value store (LocalDB)
│   ├── decorators.py     # Command decorators
│   └── utils/            # Formatters and message helpers
└── modules/              # Modular command plugins
    ├── admin.py          # Chat moderation and member management
    ├── afk.py            # AFK status and auto-replies
    ├── finance.py        # Crypto prices and fiat currency conversion
    ├── football.py       # Football fixtures, live scores, and standings
    ├── info.py           # User and chat identity lookup
    ├── inline.py         # Control center toggles and companion inline hub
    ├── media.py          # Video notes, voice notes, and audio filters
    ├── network.py        # MTProto latency, speed test, and link unshortener
    ├── pmpermit.py       # Direct message protection, remote moderation, and anti-spam
    ├── pray.py           # Prayer times and next-prayer countdown
    ├── purge.py          # Message bulk deletion
    ├── stickers.py       # Sticker kang and pack utilities
    ├── system.py         # System health, ping, help, restart, and update
    ├── tools.py          # Python evaluation, shell execution, and variable storage
    ├── transfer.py       # Local file upload, download, and self-destructing messages
    ├── tts.py            # Text-to-speech voice generation
    ├── uploader.py       # Catbox and Litterbox file hosting
    ├── visual.py         # Quote stickers, carbon code images, and pastebin
    └── weather.py        # Current weather and multi-day forecasts
```

## Quick Start

### 1. Installation

Clone the repository and run the setup script:

```bash
git clone https://github.com/itswill00/TeleForge.git
cd TeleForge
./setup.sh
```

### 2. Configuration & Session

Generate an exportable string session from [my.telegram.org](https://my.telegram.org) credentials:

```bash
./pygramx.sh session
```

Alternatively, copy `.env.example` to `.env` and configure manually:

```env
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef
TRIGGER=.
```

### 3. Execution

Manage the background service:

```bash
./pygramx.sh start                 # Launch runner (auto-detects tmux or daemon mode)
./pygramx.sh start --no-tmux       # Force background daemon mode (nohup with PID tracking)
./pygramx.sh start --tmux          # Force tmux session mode
./pygramx.sh status                # Inspect runner mode (tmux or daemon), PID, and wake-lock
./pygramx.sh logs                  # Attach to live tmux session (or stream daemon log)
./pygramx.sh logs --tail           # Stream log output directly with tail -f
./pygramx.sh stop                  # Terminate background instance cleanly
./pygramx.sh boot                  # Install Termux:Boot autostart launcher
```

## Upstream Pyrogram Modernization & Patches

Because upstream Pyrogram 2.0.x is no longer actively maintained, TeleForge incorporates essential architectural patches and runtime guards to ensure seamless compatibility with modern Telegram infrastructure and Python 3.12+ / 3.14+:

- **64-bit Peer ID Resolution**:
  - Upstream Pyrogram hardcodes `MIN_CHANNEL_ID = -1002147483647` (32-bit limit). Modern Telegram channels and supergroups created with 64-bit IDs (`-1002200000000+`) cause `ValueError: Peer id invalid`.
  - TeleForge dynamically overrides `MIN_CHANNEL_ID = -1000000000000000000` and `MAX_USER_ID = 1000000000000000000` across all entrypoints and automatically patches `pyrogram/utils.py` during `./setup.sh` installation.
- **Python 3.12 / 3.14 Event Loop Persistence**:
  - Modern Python releases deprecate implicit event loop creation in `asyncio.get_event_loop()`, throwing `RuntimeError` on cold imports.
  - Every module and entrypoint incorporates an event loop guard that checks for active loops and initializes one prior to loading Pyrogram.
- **Bot API 9.4 Styled Inline Buttons**:
  - Upstream Pyrogram MTProto layer lacks native support for modern styled inline buttons (`style="primary"`, `"success"`, `"danger"`).
  - TeleForge implements a direct REST bridge delivering styled buttons in a single atomic HTTP call, eliminating visual render flicker.
- **Dual-Client MTProto Delegation**:
  - Telegram MTProto forbids Bot Tokens from invoking `messages.GetHistory` and user message reactions.
  - TeleForge transparently delegates chat history reading and member scraping to the userbot client while maintaining companion bot command responsiveness.
- **Type Safety & Boolean Inheritance**:
  - Guards against Python's `isinstance(True, int) == True` inheritance trap across Telegram API responses to ensure accurate message purge accounting.

## Creating Custom Modules

Modules are automatically discovered and loaded from the `modules/` directory. Creating a custom command requires minimal boilerplate:

```python
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

@on_cmd("hello", desc="Send greeting message", usage="[name]")
async def hello_command(client: Client, message: Message):
    args = message.text.split(maxsplit=1)
    target = args[1] if len(args) > 1 else "world"
    await edit_or_reply(message, f"Hello, {target}!")
```

For advanced architectural patterns, LocalDB key-value state management, large document delivery, and background worker hooks, refer to the [Developer Guide](DEVELOPMENT.md) and [Contributing Guidelines](CONTRIBUTING.md).
#!/bin/sh
# TeleForge · Fast, Resilient Environment Setup & Diagnostic Verifier
# Author: @itswill00

set -eu

# Resolve base directory portably
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  TeleForge · Environment Setup"
echo ""

# Step 1: Detect missing system binaries
MISSING_BINS=""
for bin in python3 git tmux ffmpeg; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        MISSING_BINS="$MISSING_BINS $bin"
    fi
done

if [ -n "$MISSING_BINS" ]; then
    echo "[1/4] Missing packages detected:$MISSING_BINS. Installing..."
    if command -v pkg >/dev/null 2>&1; then
        pkg install -y $MISSING_BINS clang libjpeg-turbo python-pillow
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -y
        sudo apt-get install -y $MISSING_BINS python3-pip build-essential
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y $MISSING_BINS python3-pip gcc
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm $MISSING_BINS python-pip base-devel
    elif command -v apk >/dev/null 2>&1; then
        sudo apk add $MISSING_BINS py3-pip build-base
    else
        echo "Please install missing packages manually:$MISSING_BINS"
    fi
else
    echo "[1/4] System dependencies: OK (all required binaries present)"
fi

# Step 2: Check Python package dependencies
echo "[2/4] Verifying Python runtime packages..."
if python3 -c "import pygramx, dotenv" >/dev/null 2>&1; then
    echo "      Python dependencies: OK (Pyrogram and python-dotenv active)"
else
    echo "      Installing required Python packages..."
    PIP_FLAGS="--quiet"
    if python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        PIP_FLAGS="$PIP_FLAGS --break-system-packages"
    fi
    python3 -m pip install -r requirements.txt $PIP_FLAGS
fi
python3 -c "
import pyrogram.utils
p = pyrogram.utils.__file__
try:
    with open(p, 'r') as f: c = f.read()
    if 'MIN_CHANNEL_ID = -1002147483647' in c:
        with open(p, 'w') as f: f.write(c.replace('MIN_CHANNEL_ID = -1002147483647', 'MIN_CHANNEL_ID = -1000000000000000000'))
except Exception: pass
" 2>/dev/null || true

# Step 3: Check configuration & install credential safeguards
echo "[3/4] Checking configuration (.env) and git safeguards..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "      Created .env template. Please configure your API credentials."
else
    echo "      Existing .env detected."
fi

# Install anti-leak git hook if repository is managed under git
if [ -d ".git/hooks" ]; then
    cat << 'EOF' > .git/hooks/pre-commit
#!/bin/sh
# TeleForge · Guard against accidental secret leaks
LEAKED_FILES=$(git diff --cached --name-only | grep -E '(\.env|\.session|\.sqlite|\.db|pygramx\.session)' || true)
if [ -n "$LEAKED_FILES" ]; then
    echo ""
    echo "  [SECURITY ERROR] TeleForge Pre-Commit Guard:"
    echo "  Attempting to commit sensitive credential or database files:"
    echo "  $LEAKED_FILES"
    echo ""
    echo "  Commit rejected to prevent leaking credentials to public git."
    echo ""
    exit 1
fi
EOF
    chmod +x .git/hooks/pre-commit
fi

# Step 4: Run diagnostic self-check
echo "[4/4] Running diagnostic verification..."
python3 -c "
import config
from pygramx import PyGramClient, db
import modules.system
import modules.tools
import modules.afk
import modules.purge
import modules.info
import modules.pmpermit
import modules.session
import modules.admin
import modules.uploader
import modules.android
import modules.transfer
import modules.stickers
import modules.tts
import modules.media
import modules.network
import modules.visual

assert len(PyGramClient.COMMANDS) >= 40, f'Expected >=40 commands, got {len(PyGramClient.COMMANDS)}'
print('      Architecture self-check: OK (' + str(len(PyGramClient.COMMANDS)) + ' commands registered across 16 modules)')
"

echo ""
echo "Deployment foundation ready."
echo "  ./pygramx.sh session   Generate string session"
echo "  ./pygramx.sh start     Start background service"
echo "  ./pygramx.sh status    Check service status"
echo "  ./pygramx.sh logs      Stream service logs"
echo ""

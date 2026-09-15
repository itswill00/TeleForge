#!/bin/sh
# TeleForge · Universal Process & Service Controller
# Usage: ./pygramx.sh [start|stop|restart|status|logs|session|boot]
# Author: @itswill00

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/pygramx.pid"
LOG_FILE="$SCRIPT_DIR/pygramx.log"
SESSION_NAME="pygramx"

# Default configuration
USE_TMUX="auto"
USE_WAKELOCK="true"
LOG_MAX_MB=10

# Load .env variables if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            \#*|"") continue ;;
            USE_TMUX=*)
                val="${line#*=}"
                USE_TMUX="$(echo "$val" | tr -d ' "'\''')"
                ;;
            USE_WAKELOCK=*)
                val="${line#*=}"
                USE_WAKELOCK="$(echo "$val" | tr -d ' "'\''')"
                ;;
            LOG_MAX_MB=*)
                val="${line#*=}"
                LOG_MAX_MB="$(echo "$val" | tr -d ' "'\''')"
                ;;
        esac
    done < "$SCRIPT_DIR/.env"
fi

acquire_wake_lock() {
    case "$USE_WAKELOCK" in
        false|0|no|off)
            return 0
            ;;
    esac
    if command -v termux-wake-lock >/dev/null 2>&1; then
        termux-wake-lock || true
    fi
}

release_wake_lock() {
    if command -v termux-wake-unlock >/dev/null 2>&1; then
        termux-wake-unlock || true
    fi
}

is_running() {
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        return 0
    fi
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

rotate_logs() {
    if [ -f "$LOG_FILE" ]; then
        log_size=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
        max_bytes=$((LOG_MAX_MB * 1048576))
        if [ "$log_size" -ge "$max_bytes" ]; then
            mv "$LOG_FILE" "${LOG_FILE}.1"
        fi
    fi
}

start_bot() {
    opt_mode="${1:-}"
    if is_running; then
        echo "TeleForge is already running."
        exit 0
    fi

    # CLI flag overrides
    case "$opt_mode" in
        --no-tmux) USE_TMUX="false" ;;
        --tmux) USE_TMUX="true" ;;
    esac

    rotate_logs
    echo "Starting TeleForge userbot..."
    acquire_wake_lock

    # Resolve runner mode
    use_tmux_resolved=0
    case "$USE_TMUX" in
        false|0|no|off)
            use_tmux_resolved=0
            ;;
        true|1|yes|on)
            if command -v tmux >/dev/null 2>&1; then
                use_tmux_resolved=1
            else
                echo "Warning: tmux requested but not installed. Falling back to daemon mode."
                use_tmux_resolved=0
            fi
            ;;
        *)
            if command -v tmux >/dev/null 2>&1; then
                use_tmux_resolved=1
            else
                use_tmux_resolved=0
            fi
            ;;
    esac

    if [ "$use_tmux_resolved" -eq 1 ]; then
        tmux new-session -d -s "$SESSION_NAME" -c "$SCRIPT_DIR" "python3 main.py 2>&1 | tee -a '$LOG_FILE'"
        echo "TeleForge started via tmux session '$SESSION_NAME'."
        echo "View live logs: ./pygramx.sh logs (or ./pygramx.sh logs --tail)"
    else
        nohup python3 main.py >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "TeleForge started as background daemon (PID: $(cat "$PID_FILE"))."
        echo "View live logs: ./pygramx.sh logs"
    fi
}

stop_bot() {
    echo "Stopping TeleForge userbot..."
    stopped=0

    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
        stopped=1
    fi

    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            stopped=1
        fi
        rm -f "$PID_FILE"
    fi

    release_wake_lock

    if [ "$stopped" -eq 1 ]; then
        echo "TeleForge stopped successfully."
    else
        echo "TeleForge was not running."
    fi
}

status_bot() {
    if is_running; then
        echo "Status: RUNNING"
        if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
            echo "Runner: tmux session ($SESSION_NAME)"
        fi
        if [ -f "$PID_FILE" ]; then
            echo "Runner: background daemon (PID: $(cat "$PID_FILE"))"
        fi
        case "$USE_WAKELOCK" in
            false|0|no|off) echo "Wake  : disabled" ;;
            *) echo "Wake  : enabled" ;;
        esac
    else
        echo "Status: STOPPED"
    fi
}

logs_bot() {
    opt="${1:-}"
    is_tmux=0
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        is_tmux=1
    fi

    case "$opt" in
        --tail|-f|--follow)
            if [ -f "$LOG_FILE" ]; then
                tail -f -n 50 "$LOG_FILE"
            else
                echo "No log file found at $LOG_FILE"
            fi
            return 0
            ;;
    esac

    if [ "$is_tmux" -eq 1 ]; then
        echo "Attaching to live tmux session (Press Ctrl+B then D to detach)..."
        tmux attach -t "$SESSION_NAME"
    else
        if [ -f "$LOG_FILE" ]; then
            tail -f -n 50 "$LOG_FILE"
        else
            echo "No log file found at $LOG_FILE"
        fi
    fi
}

session_gen() {
    python3 generate_session.py
}

boot_install() {
    if [ -d "/data/data/com.termux" ] || command -v termux-wake-lock >/dev/null 2>&1; then
        base_home="/data/data/com.termux/files/home"
        if [ ! -d "$base_home" ]; then
            base_home="${HOME:-/data/data/com.termux/files/home}"
        fi
        termux_dir="$base_home/.termux/boot"
        mkdir -p "$termux_dir"
        target="$termux_dir/start-pygramx.sh"
        cat <<EOF > "$target"
#!/bin/sh
# TeleForge Termux:Boot autostart
termux-wake-lock 2>/dev/null || true
sleep 5
cd "$SCRIPT_DIR" && ./pygramx.sh start
EOF
        chmod +x "$target"
        echo "Termux:Boot autostart installed: $target"
        echo "TeleForge will launch automatically when Termux:Boot runs."
    else
        echo "Linux system detected."
        echo "To configure automatic startup on Linux boot, create a systemd service:"
        echo ""
        echo "  sudo nano /etc/systemd/system/pygramx.service"
        echo ""
        echo "Paste the following configuration:"
        echo "[Unit]"
        echo "Description=TeleForge Userbot Service"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "User=$USER"
        echo "WorkingDirectory=$SCRIPT_DIR"
        echo "ExecStart=/bin/sh $SCRIPT_DIR/pygramx.sh start --no-tmux"
        echo "ExecStop=/bin/sh $SCRIPT_DIR/pygramx.sh stop"
        echo "Restart=always"
        echo "RestartSec=5"
        echo ""
        echo "[Install]"
        echo "WantedBy=multi-user.target"
        echo ""
        echo "Then enable and start it:"
        echo "  sudo systemctl daemon-reload"
        echo "  sudo systemctl enable --now pygramx"
    fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
    start)
        start_bot "${1:-}"
        ;;
    stop)
        stop_bot
        ;;
    restart)
        stop_bot
        sleep 1
        start_bot "${1:-}"
        ;;
    status)
        status_bot
        ;;
    logs)
        logs_bot "${1:-}"
        ;;
    session)
        session_gen
        ;;
    boot)
        boot_install
        ;;
    *)
        echo "Usage: ./pygramx.sh {start|stop|restart|status|logs|session|boot} [options]"
        echo "  start [--tmux | --no-tmux]"
        echo "  logs  [--tail]"
        exit 1
        ;;
esac

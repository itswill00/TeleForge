import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import io
import os
import signal
import sys
import time
import traceback
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd, db
from pygramx.utils import (
    edit_or_reply,
    send_large_output,
    get_client_prefix,
    get_portable_home,
    format_bytes,
)

async def aexec(code: str, client: Client, message: Message):
    """
    Asynchronously execute dynamic Python code with injected context variables.
    Supports top-level 'await' expressions and variable returns.
    """
    import config
    exec_scope = {
        "client": client,
        "app": client,
        "c": client,
        "message": message,
        "msg": message,
        "m": message,
        "r": message.reply_to_message,
        "reply": message.reply_to_message,
        "chat": message.chat,
        "user": message.from_user,
        "db": db,
        "config": config,
        "edit_or_reply": edit_or_reply,
        "send_large_output": send_large_output,
        "asyncio": asyncio,
        "sys": sys,
        "os": os,
        "time": time,
    }

    # Parse code into AST and transform trailing expression into a return statement
    import ast
    code_clean = code.strip()
    compiled = False
    try:
        tree = ast.parse(code_clean)
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            tree.body[-1] = ast.Return(value=tree.body[-1].value)
            ast.fix_missing_locations(tree)
        func_ast = ast.AsyncFunctionDef(
            name="__aexec",
            args=ast.arguments(
                posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]
            ),
            body=tree.body,
            decorator_list=[],
        )
        mod = ast.Module(body=[func_ast], type_ignores=[])
        ast.fix_missing_locations(mod)
        exec(compile(mod, "<eval>", "exec"), exec_scope)
        compiled = True
    except Exception:
        pass

    if not compiled:
        lines = code_clean.splitlines()
        indented_code = "\n".join(f"    {line}" for line in lines)
        function_def = f"async def __aexec():\n{indented_code}"
        exec(function_def, exec_scope)

    redirected_output = io.StringIO()

    def _scoped_print(*args, sep=" ", end="\n", file=None, flush=False):
        target = redirected_output if file in (None, sys.stdout) else file
        target.write(sep.join(str(a) for a in args) + end)

    class _ScopedSys:
        def __init__(self, real_sys, out_stream):
            self._real_sys = real_sys
            self.stdout = out_stream
            self.stderr = out_stream

        def __getattr__(self, name):
            return getattr(self._real_sys, name)

    exec_scope["print"] = _scoped_print
    exec_scope["sys"] = _ScopedSys(sys, redirected_output)

    return_value = None
    exc = None

    try:
        coro = exec_scope["__aexec"]()
        return_value = await asyncio.wait_for(coro, timeout=30.0)
    except asyncio.TimeoutError:
        exc = "Execution timed out after 30 seconds."
    except Exception:
        exc = traceback.format_exc()

    stdout_val = redirected_output.getvalue().strip()

    if exc:
        return exc
    if stdout_val and return_value is not None:
        return f"{stdout_val}\n\nResult: {repr(return_value)}"
    if stdout_val:
        return stdout_val
    if return_value is not None:
        return repr(return_value)
    return "Completed successfully with no output."

@on_cmd("eval", desc="Evaluate a Python expression or code block", usage="<code>")
async def eval_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    cmd = text.split(maxsplit=1)
    if len(cmd) < 2:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"`Usage: {prefix}eval <python_code>`")
        return

    code = cmd[1].strip()
    status_msg = await edit_or_reply(message, "`Evaluating code...`")

    output = await aexec(code, client, message)
    await send_large_output(
        status_msg,
        output,
        caption=f"**Input:**\n```python\n{code}\n```\n\n**Output:**",
        filename="eval_output.py",
    )

_ACTIVE_SHELL_TASKS: dict[int, asyncio.subprocess.Process] = {}

@on_cmd(["sh", "exec"], desc="Execute a system shell command with live streaming output", usage="<command>")
async def shell_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    cmd = text.split(maxsplit=1)
    prefix = get_client_prefix(client)
    if len(cmd) < 2:
        await edit_or_reply(message, f"`Usage: {prefix}sh <command>`")
        return

    chat_id = message.chat.id
    if chat_id in _ACTIVE_SHELL_TASKS and _ACTIVE_SHELL_TASKS[chat_id].returncode is None:
        await edit_or_reply(
            message,
            f"A command is already running in this chat. Use `{prefix}stopsh` to abort it.",
        )
        return

    command = cmd[1].strip()
    status_msg = await edit_or_reply(message, f"`Starting: {command}...`")

    env = dict(os.environ)
    portable_home = get_portable_home()
    env["HOME"] = portable_home

    process = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=portable_home,
        env=env,
        start_new_session=True,
    )
    _ACTIVE_SHELL_TASKS[chat_id] = process

    output_chunks: list[str] = []
    output_lock = asyncio.Lock()

    async def _read_stream():
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                async with output_lock:
                    output_chunks.append(line.decode("utf-8", errors="replace"))
        except Exception:
            pass

    reader_task = asyncio.create_task(_read_stream())

    start_time = time.time()
    last_edit_time = start_time
    last_seen_len = 0
    TIMEOUT = 120.0

    while process.returncode is None:
        await asyncio.sleep(1.2)
        now = time.time()
        if now - start_time > TIMEOUT:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            break

        async with output_lock:
            current_output = "".join(output_chunks)

        if len(current_output) != last_seen_len and (now - last_edit_time >= 1.5):
            last_seen_len = len(current_output)
            last_edit_time = now
            tail_lines = current_output.splitlines()[-10:]
            preview = "\n".join(tail_lines)
            elapsed = now - start_time
            stream_text = (
                f"**Command:**\n```bash\n{command}\n```\n\n"
                f"**Running... ({elapsed:.1f}s)**\n```\n{preview}\n```\n"
                f"• Cancel with `{prefix}stopsh`"
            )
            try:
                await edit_or_reply(status_msg, stream_text)
            except Exception:
                pass

    try:
        await asyncio.wait_for(reader_task, timeout=2.0)
    except Exception:
        pass
    try:
        await process.wait()
    except Exception:
        pass
    _ACTIVE_SHELL_TASKS.pop(chat_id, None)

    full_output = "".join(output_chunks).strip()
    exit_code = process.returncode
    exit_str = f"\n\nExit code: `{exit_code}`" if exit_code != 0 else ""
    final_output = (full_output or "Process completed with no output.") + exit_str

    await send_large_output(
        status_msg,
        final_output,
        caption=f"**Command:**\n```bash\n{command}\n```\n\n**Output:**",
        filename="shell_output.txt",
    )

@on_cmd(["stopsh", "killsh"], desc="Terminate running shell process in current chat")
async def stopsh_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    proc = _ACTIVE_SHELL_TASKS.get(chat_id)
    if proc and proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _ACTIVE_SHELL_TASKS.pop(chat_id, None)
        await edit_or_reply(message, "Active shell process has been terminated.")
    else:
        await edit_or_reply(message, "No active shell process running in this chat.")

@on_cmd("ls", desc="List files and directories", usage="[path]")
async def ls_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    target = args[1].strip() if len(args) > 1 else "."
    target_path = os.path.abspath(os.path.expanduser(target))

    if not os.path.exists(target_path):
        return await edit_or_reply(message, f"Path not found: `{target_path}`")

    if os.path.isfile(target_path):
        st = os.stat(target_path)
        size_str = format_bytes(st.st_size)
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        mode_str = oct(st.st_mode)[-3:]
        card = (
            f"**File Details**\n"
            f"• **Path:** `{target_path}`\n"
            f"• **Size:** `{size_str}`\n"
            f"• **Permissions:** `{mode_str}`\n"
            f"• **Modified:** `{mtime}`"
        )
        return await edit_or_reply(message, card)

    try:
        entries = list(os.scandir(target_path))
    except PermissionError:
        return await edit_or_reply(message, f"Permission denied: `{target_path}`")
    except Exception as e:
        return await edit_or_reply(message, f"Error reading directory: `{e}`")

    dirs = sorted([e for e in entries if e.is_dir()], key=lambda x: x.name.lower())
    files = sorted([e for e in entries if not e.is_dir()], key=lambda x: x.name.lower())

    lines = []
    MAX_ITEMS = 40
    count = 0

    for d in dirs:
        if count >= MAX_ITEMS:
            break
        lines.append(f"📁 `{d.name}/`")
        count += 1

    for f in files:
        if count >= MAX_ITEMS:
            break
        try:
            f_size = format_bytes(f.stat().st_size)
        except OSError:
            f_size = "?"
        lines.append(f"📄 `{f.name}` ({f_size})")
        count += 1

    total_items = len(dirs) + len(files)
    remaining = total_items - count

    header = (
        f"**Directory:** `{target_path}`\n"
        f"• **Total:** {len(dirs)} folders, {len(files)} files\n\n"
    )
    body = "\n".join(lines) if lines else "_Directory is empty._"
    if remaining > 0:
        body += f"\n\n_... and {remaining} more items._"

    await send_large_output(
        message,
        body,
        caption=header,
        filename="dir_listing.txt",
    )

@on_cmd("cat", desc="View text file contents", usage="<path>")
async def cat_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}cat <path>`")

    target_path = os.path.abspath(os.path.expanduser(args[1].strip()))
    if not os.path.exists(target_path):
        return await edit_or_reply(message, f"File not found: `{target_path}`")
    if not os.path.isfile(target_path):
        return await edit_or_reply(message, f"Path is not a regular file: `{target_path}`")

    st = os.stat(target_path)
    if st.st_size > 5 * 1024 * 1024:
        return await edit_or_reply(
            message,
            f"File too large to display ({format_bytes(st.st_size)}). Use `{prefix}up {args[1]}` to upload it as document.",
        )

    try:
        with open(target_path, "rb") as f:
            sample = f.read(1024)
            if b"\x00" in sample:
                return await edit_or_reply(
                    message,
                    f"Target file appears to be a binary file. Use `{prefix}up {args[1]}` to upload it.",
                )
    except Exception as e:
        return await edit_or_reply(message, f"Failed to read file: `{e}`")

    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return await edit_or_reply(message, f"Failed to read file: `{e}`")

    filename = os.path.basename(target_path)
    header = f"**File:** `{target_path}` ({format_bytes(st.st_size)})"
    await send_large_output(
        message,
        content,
        caption=header,
        filename=filename if filename.endswith((".txt", ".py", ".sh", ".json", ".md", ".env", ".conf", ".yaml", ".yml")) else f"{filename}.txt",
    )

@on_cmd("setvar", desc="Save a key-value pair to the database", usage="<key> <value>")
async def setvar_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=2)
    if len(args) < 3:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"`Usage: {prefix}setvar <key> <value>`")
        return
    key, val = args[1], args[2]
    db.set(key, val)
    await edit_or_reply(message, f"**Saved to Database:**\n• `{key}` = `{val}`")

@on_cmd("getvar", desc="Retrieve a key from the database", usage="[key]")
async def getvar_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        keys = db.keys()
        if not keys:
            await edit_or_reply(message, "`The database is currently empty.`")
            return
        preview = "\n".join(f"• `{k}`: `{db.get(k)}`" for k in sorted(keys)[:30])
        await edit_or_reply(message, f"**Stored Database Keys ({len(keys)}):**\n{preview}")
        return
    key = args[1]
    val = db.get(key)
    if val is None:
        await edit_or_reply(message, f"Key `{key}` was not found in the database.")
        return
    await edit_or_reply(message, f"**Database Entry:**\n• `{key}` = `{val}`")

@on_cmd("delvar", desc="Delete a key from the database", usage="<key>")
async def delvar_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"`Usage: {prefix}delvar <key>`")
        return
    key = args[1]
    if db.delete(key):
        await edit_or_reply(message, f"Key `{key}` has been deleted from the database.")
    else:
        await edit_or_reply(message, f"Key `{key}` does not exist in the database.")

@on_cmd(["raw", "json"], desc="View raw Telegram message data", usage="")
async def raw_cmd(client: Client, message: Message):
    target = message.reply_to_message or message
    status_msg = await edit_or_reply(message, "`Fetching message data...`")
    raw_str = str(target)
    await send_large_output(
        status_msg,
        raw_str,
        caption=f"**Raw Message Data** (ID: `{target.id}`)",
        filename=f"raw_message_{target.id}.json",
    )


@on_cmd(["setalias", "addalias"], desc="Create a custom shortcut alias for any command", usage="<alias> <target_command>")
async def setalias_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split()
    prefix = get_client_prefix(client)
    if len(args) < 3:
        return await edit_or_reply(message, f"Usage: `{prefix}setalias <alias> <target_command>`\nExample: `{prefix}setalias s settings`")

    alias = args[1].lower().strip().lstrip(prefix).lstrip(".")
    target = args[2].lower().strip().lstrip(prefix).lstrip(".")

    primary_cmds = {k.lower(): v for k, v in PyGramClient.COMMANDS.items()}
    if target not in primary_cmds:
        return await edit_or_reply(message, f"Target command `{prefix}{target}` does not exist in TeleForge.")

    aliases = db.get("CUSTOM_ALIASES", {})
    aliases[alias] = target
    db.set("CUSTOM_ALIASES", aliases)
    await edit_or_reply(message, f"**Custom Alias Configured:**\n• Shortcut: `{prefix}{alias}` -> `{prefix}{target}`")


@on_cmd(["delalias", "remalias"], desc="Remove a custom shortcut alias", usage="<alias>")
async def delalias_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split()
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}delalias <alias>`")

    alias = args[1].lower().strip().lstrip(prefix).lstrip(".")
    aliases = db.get("CUSTOM_ALIASES", {})
    if alias in aliases:
        target = aliases.pop(alias)
        db.set("CUSTOM_ALIASES", aliases)
        await edit_or_reply(message, f"Alias `{prefix}{alias}` (pointing to `{prefix}{target}`) removed.")
    else:
        await edit_or_reply(message, f"Alias `{prefix}{alias}` not found.")


@on_cmd("aliases", desc="List all configured custom command shortcuts")
async def aliases_cmd(client: Client, message: Message):
    prefix = get_client_prefix(client)
    aliases = db.get("CUSTOM_ALIASES", {})
    if not aliases:
        return await edit_or_reply(message, f"**Custom Aliases:** `None`\nCreate shortcuts via `{prefix}setalias <shortcut> <command>`.")

    lines = [f"**Custom Command Aliases ({len(aliases)}):**"]
    for a, t in sorted(aliases.items()):
        lines.append(f"• `{prefix}{a}` -> `{prefix}{t}`")
    await edit_or_reply(message, "\n".join(lines))


@on_cmd(["triggers", "prefixes"], desc="List active command triggers")
async def triggers_cmd(client: Client, message: Message):
    custom = db.get("CUSTOM_TRIGGERS")
    current = custom if (custom and isinstance(custom, list)) else [config.get_trigger()]
    trig_str = " ".join(f"`{t}`" for t in current)
    prefix = get_client_prefix(client)
    await edit_or_reply(
        message,
        f"**Active Triggers ({len(current)}):** {trig_str}\n"
        f"• Add trigger: `{prefix}addtrigger <symbol>`\n"
        f"• Remove trigger: `{prefix}deltrigger <symbol>`"
    )


@on_cmd("addtrigger", desc="Add a new custom command trigger symbol", usage="<symbol>")
async def addtrigger_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split()
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}addtrigger <symbol>`\nExample: `{prefix}addtrigger ,`")

    sym = args[1].strip()
    custom = db.get("CUSTOM_TRIGGERS")
    current = list(custom) if (custom and isinstance(custom, list)) else [config.get_trigger()]

    if sym in current:
        return await edit_or_reply(message, f"Trigger `{sym}` is already active.")

    current.append(sym)
    db.set("CUSTOM_TRIGGERS", current)
    await edit_or_reply(message, f"Added trigger `{sym}`. Active triggers: {' '.join(f'`{t}`' for t in current)}")


@on_cmd("deltrigger", desc="Remove a custom command trigger symbol", usage="<symbol>")
async def deltrigger_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split()
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}deltrigger <symbol>`")

    sym = args[1].strip()
    custom = db.get("CUSTOM_TRIGGERS")
    current = list(custom) if (custom and isinstance(custom, list)) else [config.get_trigger()]

    if len(current) <= 1:
        return await edit_or_reply(message, "Cannot delete the last remaining trigger.")

    if sym in current:
        current.remove(sym)
        db.set("CUSTOM_TRIGGERS", current)
        await edit_or_reply(message, f"Removed trigger `{sym}`. Active triggers: {' '.join(f'`{t}`' for t in current)}")
    else:
        await edit_or_reply(message, f"Trigger `{sym}` is not in active triggers.")


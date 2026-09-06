import os
import json
import time
import hmac
import hashlib
import asyncio
import threading
from secrets import randbits
from urllib.parse import parse_qsl

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

from telethon import TelegramClient, functions, types, events

from groups import TG_GROUPS


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from .env")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "dekanat_session",
    API_ID,
    API_HASH
)

telegram_loop = None
telegram_ready = threading.Event()
telegram_account_id = None


# ============================================================
# CONFIG FILE
#
# Groups are stored in this format:
#
# {
#   "message_type": "reqdocs",
#   "copy_message": true,
#   "groups": {
#       "1": [
#           "Economics|qq",
#           "Economics|ru"
#       ]
#   }
# }
# ============================================================

CONFIG_FILE = "config.json"

ALLOWED_MESSAGE_TYPES = {
    "xabar",
    "reqdocs",
    "regulations",
    "links",
    "timetable",
    "faq",
}


def default_config():
    return {
        "message_type": "reqdocs",
        "copy_message": True,
        "groups": {},
    }


def normalize_groups(groups):
    """
    Return the canonical flat group-selection format.

    Canonical:
        {
            "1": ["Economics|qq", "Economics|ru"]
        }

    Also accepts the older nested format so an existing config.json
    can be migrated automatically.
    """

    if not isinstance(groups, dict):
        return {}

    normalized = {}

    for course, selections in groups.items():

        if course not in TG_GROUPS:
            continue

        valid = []

        # New format:
        # "1": ["Economics|qq", "Economics|ru"]
        if isinstance(selections, list):

            for selection in selections:

                if not isinstance(selection, str):
                    continue

                parts = selection.split("|", 1)

                if len(parts) != 2:
                    continue

                faculty, language = parts

                if (
                    faculty in TG_GROUPS[course]
                    and language in TG_GROUPS[course][faculty]
                ):
                    key = f"{faculty}|{language}"

                    if key not in valid:
                        valid.append(key)

        # Legacy format:
        # "1": {
        #     "Economics": ["qq", "ru"]
        # }
        elif isinstance(selections, dict):

            for faculty, languages in selections.items():

                if faculty not in TG_GROUPS[course]:
                    continue

                if not isinstance(languages, list):
                    continue

                for language in languages:

                    if language in TG_GROUPS[course][faculty]:

                        key = f"{faculty}|{language}"

                        if key not in valid:
                            valid.append(key)

        if valid:
            normalized[course] = valid

    return normalized


def load_config():

    if not os.path.exists(CONFIG_FILE):
        return default_config()

    try:

        with open(
            CONFIG_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        config = default_config()

        if not isinstance(data, dict):
            return config

        message_type = data.get("message_type")

        if message_type in ALLOWED_MESSAGE_TYPES:
            config["message_type"] = message_type

        config["copy_message"] = bool(
            data.get("copy_message", True)
        )

        config["groups"] = normalize_groups(
            data.get("groups", {})
        )

        return config

    except Exception as error:

        print("Could not load config:", error)

        return default_config()


def save_config(config):

    config_to_save = {
        "message_type": config.get(
            "message_type",
            "reqdocs",
        ),
        "copy_message": bool(
            config.get("copy_message", True)
        ),
        "groups": normalize_groups(
            config.get("groups", {})
        ),
    }

    with open(
        CONFIG_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            config_to_save,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def configured_destination_chat_ids():

    config = load_config()

    result = set()

    for course, selections in config["groups"].items():

        if course not in TG_GROUPS:
            continue

        for selection in selections:

            try:
                faculty, language = selection.split("|", 1)

                group_data = TG_GROUPS[course][faculty][language]

                result.add(int(group_data["chatid"]))

            except (
                KeyError,
                ValueError,
                TypeError,
            ):
                continue

    return result


async def distribute_message(message):

    config = load_config()

    message_type = config["message_type"]
    copy_message = config["copy_message"]
    selected_groups = config["groups"]

    if not selected_groups:
        print("Distribution skipped: no groups selected.")
        return

    print(
        f"Distributing message {message.id} "
        f"(type={message_type}, copy={copy_message})"
    )

    # The original message's source chat.
    #
    # This is needed for ForwardMessagesRequest.
    try:
        source_peer = await client.get_input_entity(
            message.peer_id
        )
    except Exception as error:
        print(
            f"Could not resolve source peer for message "
            f"{message.id}: {error}"
        )
        return

    for course, selections in selected_groups.items():

        if course not in TG_GROUPS:
            continue

        for selection in selections:

            try:

                faculty, language = selection.split("|", 1)

                group_data = TG_GROUPS[
                    course
                ][
                    faculty
                ][
                    language
                ]

                chat_id = int(
                    group_data["chatid"]
                )

                topic_id = group_data.get(
                    message_type
                )

                if not topic_id:
                    print(
                        f"Skipping {course}|{faculty}|{language}: "
                        f"no topic for {message_type}"
                    )
                    continue

                topic_id = int(topic_id)

                entity = await client.get_entity(
                    chat_id
                )

                if copy_message:

                    # Copy the message without the
                    # "Forwarded from..." header.
                    #
                    # reply_to is the topic root message ID.
                    await client.send_message(
                        entity,
                        message,
                        reply_to=topic_id,
                    )

                else:

                    # Telethon's high-level forward_messages()
                    # does not expose forum topic selection.
                    #
                    # Telegram's raw messages.forwardMessages
                    # does support top_msg_id, which is the
                    # destination forum topic.
                    await client(
                        functions.messages.ForwardMessagesRequest(
                            from_peer=source_peer,
                            id=[message.id],
                            random_id=[randbits(64)],
                            to_peer=entity,
                            top_msg_id=topic_id,
                        )
                    )

                print(
                    f"Distributed -> "
                    f"{course}|{faculty}|{language}"
                )

            except Exception as error:

                print(
                    f"Distribution error -> "
                    f"{course}|{faculty}|{language}: "
                    f"{error}"
                )


# ============================================================
# AUTOMATIC OUTGOING MESSAGE DISTRIBUTION
# ============================================================

@client.on(events.NewMessage(outgoing=True))
async def outgoing_message_handler(event):

    # The Telegram session must belong to the configured admin.
    if telegram_account_id != ADMIN_ID:
        return

    # Do not redistribute messages which the distributor itself
    # has just sent to destination groups.
    destination_ids = configured_destination_chat_ids()

    if event.chat_id in destination_ids:
        return

    print(
        f"Outgoing message detected: "
        f"chat_id={event.chat_id}, "
        f"message_id={event.message.id}"
    )

    await distribute_message(
        event.message
    )


# ============================================================
# TELEGRAM MINI APP AUTHENTICATION
# ============================================================

def validate_init_data(init_data):

    if not init_data:
        return None

    try:

        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = parsed.pop(
            "hash",
            None,
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed)
        )

        secret_key = hmac.new(
            key=b"WebAppData",
            msg=BOT_TOKEN.encode(),
            digestmod=hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            return None

        auth_date = int(
            parsed.get(
                "auth_date",
                "0",
            )
        )

        # Reject very old Mini App authentication data.
        if time.time() - auth_date > 86400:
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user = json.loads(user_json)

        return user

    except Exception as error:

        print(
            "Mini App auth error:",
            error,
        )

        return None


def get_authenticated_user():

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        "",
    )

    return validate_init_data(
        init_data
    )


def require_admin():

    user = get_authenticated_user()

    if not user:
        return None

    if int(user.get("id", 0)) != ADMIN_ID:
        return None

    return user


# ============================================================
# TELEGRAM LOOP BRIDGE
#
# Flask runs in its own thread.
# Telethon owns one asyncio event loop in another thread.
# All Telethon work from Flask is submitted to that same loop.
# ============================================================

def run_on_telegram(coroutine, timeout=120):

    if not telegram_ready.wait(timeout=30):
        raise RuntimeError(
            "Telegram client is not ready."
        )

    if telegram_loop is None:
        raise RuntimeError(
            "Telegram event loop is not available."
        )

    future = asyncio.run_coroutine_threadsafe(
        coroutine,
        telegram_loop,
    )

    return future.result(
        timeout=timeout
    )


# ============================================================
# AUTH ENDPOINT
# ============================================================

@app.get("/auth/me")
def auth_me():

    user = get_authenticated_user()

    if not user:

        return jsonify({
            "authenticated": False,
            "is_admin": False,
        })

    return jsonify({
        "authenticated": True,
        "is_admin": (
            int(user.get("id", 0))
            == ADMIN_ID
        ),
        "user_id": user.get("id"),
    })


# ============================================================
# MAIN PAGE
# ============================================================

@app.get("/")
def index():

    config = load_config()

    return render_template(
        "index.html",
        groups=TG_GROUPS,
        config=config,
    )


# ============================================================
# SAVE CONFIGURATION
# ============================================================

@app.post("/save")
def save():

    user = require_admin()

    if not user:

        return jsonify({
            "success": False,
            "error": "Administrator access required.",
        }), 403

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "error": "Invalid request.",
        }), 400

    message_type = data.get(
        "message_type"
    )

    if message_type not in ALLOWED_MESSAGE_TYPES:

        return jsonify({
            "success": False,
            "error": "Invalid message type.",
        }), 400

    groups = data.get(
        "groups",
        {},
    )

    normalized_groups = normalize_groups(
        groups
    )

    config = {
        "message_type": message_type,
        "copy_message": bool(
            data.get(
                "copy_message",
                True,
            )
        ),
        "groups": normalized_groups,
    }

    save_config(config)

    print(
        "Configuration saved:",
        config,
    )

    return jsonify({
        "success": True,
        "config": config,
    })


# ============================================================
# CONTENT-SAVING STATUS
# ============================================================

async def get_group_content_saving_status(
    chat_id
):

    entity = await client.get_entity(
        int(chat_id)
    )

    # --------------------------------------------------------
    # CHANNEL / SUPERGROUP
    # --------------------------------------------------------

    if isinstance(entity, types.Channel):

        result = await client(
            functions.channels.GetFullChannelRequest(
                channel=entity
            )
        )

        for chat in result.chats:

            if chat.id == entity.id:

                return bool(
                    getattr(
                        chat,
                        "noforwards",
                        False,
                    )
                )

        return False

    # --------------------------------------------------------
    # NORMAL GROUP
    # --------------------------------------------------------

    if isinstance(entity, types.Chat):

        result = await client(
            functions.messages.GetFullChatRequest(
                chat_id=entity.id
            )
        )

        for chat in result.chats:

            if chat.id == entity.id:

                return bool(
                    getattr(
                        chat,
                        "noforwards",
                        False,
                    )
                )

        return False

    return False


async def read_all_content_saving_status():

    result = {}

    for course, faculties in TG_GROUPS.items():

        for faculty, languages in faculties.items():

            for language, data in languages.items():

                chat_id = data["chatid"]

                key = (
                    f"{course}|"
                    f"{faculty}|"
                    f"{language}"
                )

                try:

                    result[key] = (
                        await get_group_content_saving_status(
                            chat_id
                        )
                    )

                except Exception as error:

                    print(
                        "Status error:",
                        course,
                        faculty,
                        language,
                        error,
                    )

                    result[key] = False

    return result


@app.get("/content-saving-status")
def content_saving_status():

    user = require_admin()

    if not user:

        return jsonify({
            "success": False,
            "error": "Administrator access required.",
        }), 403

    try:

        status = run_on_telegram(
            read_all_content_saving_status()
        )

        return jsonify({
            "success": True,
            "status": status,
        })

    except Exception as error:

        print(
            "Content-saving status error:",
            error,
        )

        return jsonify({
            "success": False,
            "error": str(error),
        }), 500


# ============================================================
# APPLY CONTENT-SAVING
# ============================================================

async def apply_content_saving_changes(
    enabled,
    selected_groups,
):

    results = {}

    normalized_groups = normalize_groups(
        selected_groups
    )

    for course, selections in normalized_groups.items():

        for selection in selections:

            try:

                faculty, language = selection.split(
                    "|",
                    1,
                )

                group_data = TG_GROUPS[
                    course
                ][
                    faculty
                ][
                    language
                ]

                chat_id = int(
                    group_data["chatid"]
                )

                key = (
                    f"{course}|"
                    f"{faculty}|"
                    f"{language}"
                )

                entity = await client.get_entity(
                    chat_id
                )

                # Read the current Telegram state.
                current_state = (
                    await get_group_content_saving_status(
                        chat_id
                    )
                )

                print(
                    f"Content saving state: "
                    f"{key} -> current={current_state}, "
                    f"requested={enabled}"
                )

                # Nothing to change.
                if current_state == enabled:

                    print(
                        f"Already in requested state: "
                        f"{key}"
                    )

                    results[key] = {
                        "success": True,
                        "changed": False,
                        "enabled": current_state,
                    }

                    continue

                # Change the Telegram setting.
                await client(
                    functions.messages.ToggleNoForwardsRequest(
                        peer=entity,
                        enabled=enabled,
                    )
                )

                print(
                    f"Toggle request sent: "
                    f"{key} -> enabled={enabled}"
                )

                # Verify the actual state after changing it.
                final_state = (
                    await get_group_content_saving_status(
                        chat_id
                    )
                )

                print(
                    f"Content saving state after apply: "
                    f"{key} -> {final_state}"
                )

                if final_state != enabled:

                    print(
                        f"Verification failed: "
                        f"{key} -> expected={enabled}, "
                        f"actual={final_state}"
                    )

                    results[key] = {
                        "success": False,
                        "changed": False,
                        "enabled": final_state,
                    }

                    continue

                results[key] = {
                    "success": True,
                    "changed": True,
                    "enabled": final_state,
                }

            except Exception as error:

                key = (
                    f"{course}|"
                    f"{selection}"
                )

                print(
                    "Apply error:",
                    key,
                    error,
                )

                results[key] = {
                    "success": False,
                    "changed": False,
                    "error": str(error),
                }

    return results

@app.post("/apply-content-saving")
def apply_content_saving():

    user = require_admin()

    if not user:

        return jsonify({
            "success": False,
            "error": "Administrator access required.",
        }), 403

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "error": "Invalid request.",
        }), 400

    enabled = bool(
        data.get(
            "enabled",
            False,
        )
    )

    selected_groups = data.get(
        "groups",
        {},
    )

    if not isinstance(
        selected_groups,
        dict,
    ):

        return jsonify({
            "success": False,
            "error": "Invalid groups.",
        }), 400

    try:

        results = run_on_telegram(
            apply_content_saving_changes(
                enabled,
                selected_groups,
            )
        )

        return jsonify({
            "success": True,
            "results": results,
        })

    except Exception as error:

        print(
            "Apply content-saving error:",
            error,
        )

        return jsonify({
            "success": False,
            "error": str(error),
        }), 500


# ============================================================
# TELEGRAM CLIENT STARTUP
# ============================================================

def start_telegram():

    global telegram_loop
    global telegram_account_id

    async def runner():

        global telegram_loop
        global telegram_account_id

        telegram_loop = asyncio.get_running_loop()

        await client.connect()

        if not await client.is_user_authorized():

            print(
                "Telegram session is not authorized."
            )

            return

        me = await client.get_me()

        telegram_account_id = me.id

        print("Telegram client connected:")

        print(
            f"  Name: "
            f"{me.first_name or ''} "
            f"{me.last_name or ''}"
        )

        print(
            f"  Username: "
            f"@{me.username}"
            if me.username
            else "  Username: none"
        )

        print(
            f"  ID: {me.id}"
        )

        if me.id != ADMIN_ID:

            print(
                "ERROR: Telegram session account does not "
                "match ADMIN_ID."
            )

            print(
                f"Session account: {me.id}"
            )

            print(
                f"Configured ADMIN_ID: {ADMIN_ID}"
            )

            return

        telegram_ready.set()

        print(
            "Telegram event loop started."
        )

        await client.run_until_disconnected()

    try:

        asyncio.run(
            runner()
        )

    except Exception as error:

        print(
            "Telegram client error:",
            error,
        )

    finally:

        telegram_ready.clear()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True,
    )

    telegram_thread.start()

    print(
        "Starting Flask on 0.0.0.0:8000"
    )

    app.run(
        host="0.0.0.0",
        port=8000,
        debug=False,
        use_reloader=False,
    )

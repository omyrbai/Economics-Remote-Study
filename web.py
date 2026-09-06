import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
from urllib.parse import parse_qsl
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template, request

from telethon import TelegramClient, functions, types

from groups import TG_GROUPS


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

SESSION_FILE = "dekanat_session.session"
CONFIG_FILE = "config.json"

HOST = "0.0.0.0"
PORT = 8000


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# TELEGRAM CLIENT
# ============================================================

telegram_client = None
telegram_loop = None
telegram_thread = None


# ============================================================
# CONFIG FILE
# ============================================================

DEFAULT_CONFIG = {
    "message_type": "xabar",
    "copy_message": True,
    "groups": {}
}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG.copy())

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

        if not isinstance(config, dict):
            return DEFAULT_CONFIG.copy()

        config.setdefault("message_type", "xabar")
        config.setdefault("copy_message", True)
        config.setdefault("groups", {})

        return config

    except Exception as e:
        print(f"Error loading config.json: {e}")
        return DEFAULT_CONFIG.copy()


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            config,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# TELEGRAM MINI APP AUTHENTICATION
# ============================================================

def validate_telegram_init_data(init_data):
    """
    Validate Telegram Mini App initData.

    Returns:
        user dictionary if valid
        None if invalid
    """

    if not init_data:
        return None

    try:
        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            key=b"WebAppData",
            msg=BOT_TOKEN.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        auth_date = int(
            parsed.get("auth_date", "0")
        )

        # 24-hour validity window
        if auth_date <= 0:
            return None

        if time.time() - auth_date > 86400:
            return None

        user_data = parsed.get("user")

        if not user_data:
            return None

        user = json.loads(user_data)

        if not isinstance(user, dict):
            return None

        if "id" not in user:
            return None

        return user

    except Exception as e:
        print(f"Mini App authentication error: {e}")
        return None


def get_authenticated_user():
    init_data = request.headers.get(
        "X-Telegram-Init-Data"
    )

    return validate_telegram_init_data(init_data)


def require_admin():
    """
    Returns Telegram user if the request
    belongs to the configured admin.

    Returns None otherwise.
    """

    user = get_authenticated_user()

    if not user:
        return None

    try:
        user_id = int(user["id"])
        admin_id = int(ADMIN_ID)
    except (TypeError, ValueError):
        return None

    if user_id != admin_id:
        return None

    return user


# ============================================================
# GROUP HELPERS
# ============================================================

def get_all_groups():
    """
    Return a flat list of all configured Telegram groups.
    """

    result = []

    for course, faculties in TG_GROUPS.items():

        for faculty, languages in faculties.items():

            for language, group_data in languages.items():

                result.append({
                    "course": course,
                    "faculty": faculty,
                    "language": language,
                    "display_language": (
                        "kk"
                        if language == "qq"
                        else language
                    ),
                    "chatid": str(group_data["chatid"]),
                    "topics": {
                        key: str(value)
                        for key, value in group_data.items()
                        if key != "chatid"
                    }
                })

    return result


def get_group(chat_id):
    chat_id = str(chat_id)

    for course, faculties in TG_GROUPS.items():
        for faculty, languages in faculties.items():
            for language, group_data in languages.items():

                if str(group_data["chatid"]) == chat_id:
                    return {
                        "course": course,
                        "faculty": faculty,
                        "language": language,
                        "display_language": (
                            "kk"
                            if language == "qq"
                            else language
                        ),
                        "chatid": chat_id,
                        "topics": {
                            key: str(value)
                            for key, value in group_data.items()
                            if key != "chatid"
                        }
                    }

    return None


def get_selected_chat_ids():
    config = load_config()

    selected = []

    for course, groups in config.get("groups", {}).items():

        for group_key in groups:

            try:
                faculty, language = group_key.split("|", 1)
            except ValueError:
                continue

            course_data = TG_GROUPS.get(course, {})
            faculty_data = course_data.get(faculty, {})
            group_data = faculty_data.get(language)

            if group_data:
                selected.append(
                    str(group_data["chatid"])
                )

    return selected


# ============================================================
# TELEGRAM STATUS
# ============================================================

async def get_group_content_saving_status(chat_id):
    """
    Get the real Telegram noforwards state.
    """

    entity = await telegram_client.get_entity(
        int(chat_id)
    )

    # Channels / supergroups
    if isinstance(entity, types.Channel):

        result = await telegram_client(
            functions.channels.GetFullChannelRequest(
                channel=entity
            )
        )

        # Find the refreshed channel object.
        for chat in result.chats:
            if getattr(chat, "id", None) == entity.id:
                return bool(
                    getattr(
                        chat,
                        "noforwards",
                        False
                    )
                )

        return bool(
            getattr(
                entity,
                "noforwards",
                False
            )
        )

    # Normal groups
    if isinstance(entity, types.Chat):

        result = await telegram_client(
            functions.messages.GetFullChatRequest(
                chat_id=entity.id
            )
        )

        return bool(
            getattr(
                result.full_chat,
                "noforwards",
                False
            )
        )

    return False


async def get_all_content_saving_status():
    result = {}

    for group in get_all_groups():

        chat_id = group["chatid"]

        try:
            status = await get_group_content_saving_status(
                chat_id
            )

            result.setdefault(
                group["course"],
                {}
            )

            result[group["course"]].setdefault(
                group["faculty"],
                {}
            )

            result[group["course"]][group["faculty"]][
                group["language"]
            ] = status

        except Exception as e:

            print(
                f"Could not get status for "
                f"{chat_id}: {e}"
            )

            result.setdefault(
                group["course"],
                {}
            )

            result[group["course"]].setdefault(
                group["faculty"],
                {}
            )

            result[group["course"]][group["faculty"]][
                group["language"]
            ] = False

    return result


async def apply_content_saving_to_groups(
    selected_groups,
    enabled
):
    results = []

    for item in selected_groups:

        course = str(item.get("course"))
        faculty = str(item.get("faculty"))
        language = str(item.get("language"))

        group_data = (
            TG_GROUPS
            .get(course, {})
            .get(faculty, {})
            .get(language)
        )

        if not group_data:
            results.append({
                "course": course,
                "faculty": faculty,
                "language": language,
                "success": False,
                "error": "Group not found"
            })
            continue

        chat_id = str(group_data["chatid"])

        try:

            entity = await telegram_client.get_entity(
                int(chat_id)
            )

            await telegram_client(
                functions.messages.ToggleNoForwardsRequest(
                    peer=entity,
                    enabled=bool(enabled)
                )
            )

            results.append({
                "course": course,
                "faculty": faculty,
                "language": language,
                "chatid": chat_id,
                "success": True
            })

        except Exception as e:

            print(
                f"Could not change content saving "
                f"for {chat_id}: {e}"
            )

            results.append({
                "course": course,
                "faculty": faculty,
                "language": language,
                "chatid": chat_id,
                "success": False,
                "error": str(e)
            })

    return results


# ============================================================
# TELEGRAM LOOP BRIDGE
# ============================================================

def run_telegram_coroutine(coro):
    """
    Run a coroutine on the persistent Telegram event loop.
    """

    if telegram_loop is None:
        raise RuntimeError(
            "Telegram event loop is not running"
        )

    future = asyncio.run_coroutine_threadsafe(
        coro,
        telegram_loop
    )

    return future.result()


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template(
        "index.html",
        groups=TG_GROUPS
    )


@app.route("/auth/me")
def auth_me():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "authenticated": False,
            "is_admin": False
        }), 401

    user_id = int(user["id"])

    return jsonify({
        "authenticated": True,
        "is_admin": user_id == int(ADMIN_ID),
        "user": {
            "id": user_id,
            "first_name": user.get(
                "first_name",
                ""
            ),
            "last_name": user.get(
                "last_name",
                ""
            ),
            "username": user.get(
                "username",
                ""
            )
        }
    })


# ============================================================
# CONFIGURATION
# ============================================================

@app.route("/config")
def get_config():

    config = load_config()

    return jsonify(config)


@app.route("/save", methods=["POST"])
def save():

    # SERVER-SIDE ADMIN CHECK
    if require_admin() is None:

        return jsonify({
            "success": False,
            "error": "Access denied"
        }), 403

    try:

        data = request.get_json()

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "Invalid request"
            }), 400

        message_type = data.get(
            "message_type",
            "xabar"
        )

        allowed_message_types = {
            "xabar",
            "reqdocs",
            "regulations",
            "links",
            "timetable",
            "faq"
        }

        if message_type not in allowed_message_types:
            return jsonify({
                "success": False,
                "error": "Invalid message type"
            }), 400

        copy_message = bool(
            data.get(
                "copy_message",
                True
            )
        )

        groups = data.get(
            "groups",
            {}
        )

        if not isinstance(groups, dict):
            return jsonify({
                "success": False,
                "error": "Invalid groups"
            }), 400

        # Clean the groups before saving.
        cleaned_groups = {}

        for course, selected in groups.items():

            if course not in TG_GROUPS:
                continue

            if not isinstance(selected, list):
                continue

            cleaned = []

            for group_key in selected:

                if not isinstance(
                    group_key,
                    str
                ):
                    continue

                try:
                    faculty, language = group_key.split(
                        "|",
                        1
                    )
                except ValueError:
                    continue

                if (
                    faculty in TG_GROUPS[course]
                    and language in TG_GROUPS[
                        course
                    ][faculty]
                ):
                    cleaned.append(
                        f"{faculty}|{language}"
                    )

            if cleaned:
                cleaned_groups[course] = cleaned

        new_config = {
            "message_type": message_type,
            "copy_message": copy_message,
            "groups": cleaned_groups
        }

        save_config(new_config)

        return jsonify({
            "success": True,
            "config": new_config
        })

    except Exception as e:

        print(f"Save error: {e}")

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# CONTENT SAVING STATUS
# ============================================================

@app.route("/content-saving-status")
def content_saving_status():

    # IMPORTANT:
    # Non-admin users are not allowed to request
    # Telegram group status at all.

    if require_admin() is None:

        return jsonify({
            "success": False,
            "error": "Access denied"
        }), 403

    try:

        result = run_telegram_coroutine(
            get_all_content_saving_status()
        )

        return jsonify({
            "success": True,
            "status": result
        })

    except Exception as e:

        print(
            f"Content saving status error: {e}"
        )

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# APPLY CONTENT SAVING
# ============================================================

@app.route(
    "/apply-content-saving",
    methods=["POST"]
)
def apply_content_saving():

    # SERVER-SIDE ADMIN CHECK
    if require_admin() is None:

        return jsonify({
            "success": False,
            "error": "Access denied"
        }), 403

    try:

        data = request.get_json()

        selected_groups = data.get(
            "groups",
            []
        )

        enabled = bool(
            data.get(
                "enabled",
                False
            )
        )

        if not isinstance(
            selected_groups,
            list
        ):
            return jsonify({
                "success": False,
                "error": "Invalid groups"
            }), 400

        result = run_telegram_coroutine(
            apply_content_saving_to_groups(
                selected_groups,
                enabled
            )
        )

        return jsonify({
            "success": True,
            "results": result
        })

    except Exception as e:

        print(
            f"Apply content saving error: {e}"
        )

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# TELEGRAM CLIENT STARTUP
# ============================================================

def telegram_worker():
    global telegram_client
    global telegram_loop

    telegram_loop = asyncio.new_event_loop()

    asyncio.set_event_loop(
        telegram_loop
    )

    telegram_client = TelegramClient(
        SESSION_FILE,
        API_ID,
        API_HASH
    )

    async def start_client():

        await telegram_client.connect()

        if not await telegram_client.is_user_authorized():

            print(
                "Telegram session is not authorized."
            )

            print(
                "Please authorize "
                f"{SESSION_FILE} first."
            )

            return False

        me = await telegram_client.get_me()

        print(
            "Telegram client connected:"
        )

        print(
            f"  Name: {me.first_name}"
        )

        print(
            f"  Username: @{me.username}"
        )

        print(
            f"  ID: {me.id}"
        )

        return True

    authorized = telegram_loop.run_until_complete(
        start_client()
    )

    if not authorized:

        telegram_loop.run_until_complete(
            telegram_client.disconnect()
        )

        telegram_loop.close()

        return

    print(
        "Telegram event loop started."
    )

    try:

        telegram_loop.run_until_complete(
            telegram_client.run_until_disconnected()
        )

    except Exception as e:

        print(
            f"Telegram loop error: {e}"
        )

    finally:

        telegram_loop.run_until_complete(
            telegram_client.disconnect()
        )

        telegram_loop.close()

        print(
            "Telegram client disconnected."
        )


def start_telegram():

    global telegram_thread

    telegram_thread = threading.Thread(
        target=telegram_worker,
        daemon=True
    )

    telegram_thread.start()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "Starting Telegram client..."
    )

    start_telegram()

    print(
        f"Starting Flask on "
        f"{HOST}:{PORT}"
    )

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False
    )
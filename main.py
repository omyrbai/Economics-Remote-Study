from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


API_ID = 32862187
API_HASH = "a1c263b78db3fef59dd90490e9891bd1"
PHONE = "+998900292892"

# Completely new session
SESSION_NAME = "dekanat_session"


client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH
)


async def login():

    print("A: connecting...", flush=True)

    await client.connect()

    print("B: connected", flush=True)

    authorized = await client.is_user_authorized()

    print(f"C: authorized = {authorized}", flush=True)

    if authorized:
        print("Already logged in.", flush=True)
        return

    print("D: sending code...", flush=True)

    await client.send_code_request(PHONE)

    print("E: code request completed", flush=True)

    code = input(
        "F: ENTER TELEGRAM CODE: "
    ).strip()

    print("G: code entered", flush=True)

    try:

        await client.sign_in(
            phone=PHONE,
            code=code
        )

        print("H: LOGIN CODE ACCEPTED", flush=True)

    except SessionPasswordNeededError:

        print("I: 2FA PASSWORD REQUIRED", flush=True)

        password = input(
            "J: ENTER 2FA PASSWORD: "
        )

        print("K: signing in with 2FA...", flush=True)

        await client.sign_in(
            password=password
        )

        print("L: 2FA ACCEPTED", flush=True)

    me = await client.get_me()

    print()
    print("=" * 60)
    print("LOGIN SUCCESSFUL")
    print("=" * 60)
    print(f"Name     : {me.first_name}")
    print(f"Username : @{me.username}" if me.username else "Username : None")
    print(f"User ID  : {me.id}")
    print("=" * 60)


async def main():

    print("===== NEW TELEGRAM SESSION TEST =====", flush=True)

    await login()


if __name__ == "__main__":
    client.loop.run_until_complete(main())

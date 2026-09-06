import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")


async def main():
    client = TelegramClient(
        "dekanat_session",
        API_ID,
        API_HASH
    )

    print("===== CREATE NEW TELEGRAM SESSION =====")
    print("Connecting...")

    await client.connect()

    if not await client.is_user_authorized():
        phone = input("Please enter your phone: ")

        await client.send_code_request(phone)

        code = input("Please enter the code you received: ")

        try:
            await client.sign_in(
                phone=phone,
                code=code
            )

        except Exception as e:
            from telethon.errors import SessionPasswordNeededError

            if isinstance(e, SessionPasswordNeededError):
                password = input("Please enter your 2FA password: ")
                await client.sign_in(password=password)
            else:
                raise

    me = await client.get_me()

    print()
    print("Successfully logged in!")
    print(f"Name: {me.first_name}")
    print(f"Username: @{me.username}")
    print(f"Telegram ID: {me.id}")
    print()
    print("Session file: dekanat_session.session")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
import os
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

load_dotenv()

async def send_log(category: str, message: str):
    encoded_message = quote_plus(message)

    topic = ""
    match category:
        case "ping":
            topic = os.getenv('TELEGRAM_PING_CHAT_ID')
        case "tracert":
            topic = os.getenv('TELEGRAM_TRACERT_CHAT_ID')
        case "request":
            topic = os.getenv('TELEGRAM_REQUESTS_CHAT_ID')
        case "connect":
            topic = os.getenv('TELEGRAM_CONNECTS_CHAT_ID')

    # Делим сообщение если оно слишком длинное
    max_length = 4000
    for i in range(0, len(encoded_message), max_length):
        chunk = encoded_message[i:i + max_length]
        telegram_url = f'https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/sendMessage?chat_id={os.getenv('TELEGRAM_GROUP_ID')}&message_thread_id={topic}&parse_mode=HTML&text={chunk}'
        requests.get(telegram_url)
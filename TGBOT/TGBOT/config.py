import os
from dotenv import load_dotenv

load_dotenv()

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "8067436187:AAHWaXohg-Cbh3K25oUqC6wufr7EUzRKa_4")
PROXY_USER = os.getenv("PROXY_USER", "MsEVeK")
PROXY_PASS = os.getenv("PROXY_PASS", "X235LV")
PROXY_HOST = "168.80.203.180"
PROXY_PORT = "8000"

PROXY_URL = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"


# Настройки базы данных
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///meetings_bot.db")

# Настройки логирования
LOG_DIR = "logs"
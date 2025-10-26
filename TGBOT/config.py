import os
from dotenv import load_dotenv

load_dotenv()

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "8067436187:AAFiv3PWNwecdNNu6exg8p93hmLUT6SLRY0")

# Настройки базы данных
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///meetings_bot.db")

# Настройки логирования
LOG_DIR = "logs"
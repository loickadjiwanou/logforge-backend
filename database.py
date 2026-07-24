from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus
import os

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

user = quote_plus(os.getenv("MONGO_USER"))
password = quote_plus(os.getenv("MONGO_PASSWORD"))
cluster = os.getenv("MONGO_URL")
db_name = os.getenv("DB_NAME")

mongo_url = (
    f"mongodb+srv://{user}:{password}@{cluster}/"
    f"{db_name}?retryWrites=true&w=majority"
)

client = AsyncIOMotorClient(mongo_url)
db = client[db_name]
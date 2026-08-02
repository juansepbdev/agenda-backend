from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = os.getenv("DJANGO_ENV", "local")

if env == "production":
    load_dotenv(BASE_DIR / ".env.production")
else:
    load_dotenv(BASE_DIR / ".env.local")
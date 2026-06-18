import os
import time
from dotenv import load_dotenv

# Env yükle
load_dotenv()

HOST = os.getenv("HYBRIDP2P_HOST", "0.0.0.0")
PORT = int(os.getenv("HYBRIDP2P_PORT", "8000"))
DB_PATH = os.getenv("HYBRIDP2P_DB_PATH", "relay_server.db")
MAX_FILE_SIZE = int(os.getenv("HYBRIDP2P_MAX_FILE_SIZE", "10485760")) # default 10MB

cors_origins_raw = os.getenv("HYBRIDP2P_CORS_ORIGINS", "*")
CORS_ORIGINS = [orig.strip() for orig in cors_origins_raw.split(",")] if cors_origins_raw else ["*"]

allowed_hosts_raw = os.getenv("HYBRIDP2P_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_raw.split(",")] if allowed_hosts_raw else ["*"]

START_TIME = time.time()

# VoIP TURN sunucu ayarları
TURN_HOST       = os.getenv("TURN_HOST", "")
TURN_USERNAME   = os.getenv("TURN_USERNAME", "")
TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "")
TURN_SECRET     = os.getenv("TURN_SECRET", "")

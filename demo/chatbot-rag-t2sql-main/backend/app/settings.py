import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Keys
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Database
DATABASE_URI = os.getenv('DATABASE_URI')

# File Search Store
FILE_SEARCH_STORE_NAME = os.getenv('FILE_SEARCH_STORE_NAME')

# LLM
LLM_MODEL = os.getenv('LLM_MODEL', 'gemini-1.5-pro')
LLM_TEMPERATURE = float(os.getenv('LLM_TEMPERATURE', '0.7'))

# Checkpointer
CHECKPOINT_DB_PATH = os.getenv('CHECKPOINT_DB_PATH', 'data/checkpoints.db')

# Flask
FLASK_ENV = os.getenv('FLASK_ENV', 'development')
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = FLASK_ENV == 'development'

# JWT Authentication
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', SECRET_KEY)
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))

# --- Validation ---
if not GOOGLE_API_KEY:
    raise ValueError("Missing required environment variable: GOOGLE_API_KEY")
if not DATABASE_URI:
    raise ValueError("Missing required environment variable: DATABASE_URI")
if not FILE_SEARCH_STORE_NAME:
    raise ValueError("Missing required environment variable: FILE_SEARCH_STORE_NAME. "
                     "Please create a File Search Store and set FILE_SEARCH_STORE_NAME in your .env file.")

if not 0.0 <= LLM_TEMPERATURE <= 1.0:
    raise ValueError(f"LLM_TEMPERATURE must be between 0.0 and 1.0, got {LLM_TEMPERATURE}")

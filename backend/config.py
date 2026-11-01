"""Central config. Loaded from .env, with safe fallbacks so the app
starts even if some optional keys are missing."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# --- Optional services ---
BHASHINI_API_KEY = os.getenv("BHASHINI_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")

# --- HTTP ---
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "15"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SOURCE_RATE_LIMIT_SEC = float(os.getenv("SOURCE_RATE_LIMIT_SEC", "1.0"))

# --- Voice ---
TTS_BACKEND = os.getenv("TTS_BACKEND", "edge")
DEFAULT_OUTPUT_LANG = os.getenv("DEFAULT_OUTPUT_LANG", "hi")

# --- Endpoints ---
ENDPOINT_TIMEOUT_SEC = 600  # 10 minutes — your Gemini calls take ~3 min each
CACHE_TTL_SEC = 600  # 10-min in-memory fetch cache per spec section 3

# --- RAG ---
EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DEVICE = "cpu"  # free tier, no GPU
CHUNK_SIZE_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50
RETRIEVE_TOP_K = 6

# --- LLM model ---
# Default to gemini-flash-latest — this alias always points to the newest
# available Flash model on your account, so it survives Google deprecations.
# Override via env if you want a specific pinned version.
GEMINI_MODEL_VISION = os.getenv("GEMINI_MODEL_VISION", "gemini-flash-latest")
GEMINI_MODEL_TEXT = os.getenv("GEMINI_MODEL_TEXT", "gemini-flash-latest")
# Models to try in order if the first one 404s. Lets us survive Google
# deprecating a model without code changes.
GEMINI_MODEL_FALLBACKS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"]

# --- Sources (fed only; spec section 3) ---
# Set True for sources the app should call by default. Toggle off if a
# source is broken on demo day (update KNOWN_BROKEN in fetch/sources.py).
ENABLED_SOURCES = {
    "pib": True,
    "factcheck": True,
    "myscheme": True,
    "datagov": True,  # requires DATA_GOV_API_KEY — see datagov.py
}

# Data.gov.in requires a free API key from https://api.data.gov.in/
DATA_GOV_API_KEY = os.getenv("DATA_GOV_API_KEY", "")

# --- Allowed official domains for citation validation ---
# Used by guardrails.strip_unverified_urls to kill hallucinated links.
OFFICIAL_DOMAINS = {
    "pib.gov.in",
    "factcheck.pib.gov.in",
    "myscheme.gov.in",
    "data.gov.in",
    "indiacode.nic.in",
    "egazette.gov.in",
    "india.gov.in",
    "nic.in",
    "gov.in",
}

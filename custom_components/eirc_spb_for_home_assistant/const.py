"""Version: 0.0.1. Constants for the EIRC SPB integration."""

DOMAIN = "eirc_spb_for_home_assistant"

CONF_ACCOUNT_IDS = "account_ids"
CONF_ACCOUNT_NAMES = "account_names"
CONF_AUTH_TYPE = "auth_type"
CONF_AUTH = "auth"
CONF_ACCESS = "access"
CONF_EMAIL = "email"
CONF_CHALLENGE_TYPE = "challenge_type"
CONF_CODE = "code"
CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_PHONE = "phone"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SESSION_COOKIE = "session_cookie"
CONF_USER_ID = "user_id"
CONF_VERIFIED = "verified"

AUTH_TYPE_EMAIL = "EMAIL"
AUTH_TYPE_PHONE = "PHONE"
AUTH_TYPE_FLASHCALL = "FLASHCALL"

DEFAULT_SCAN_INTERVAL_HOURS = 12
RETRY_504_INTERVAL_MINUTES = 30
RETRY_504_MAX_ATTEMPTS = 3
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_cache"
ISSUE_ID_API_UNAVAILABLE = "api_unavailable"

API_BASE_URL = "https://ikus.pesc.ru"
API_AUTH_PATH = "/api/v8/users/auth"
API_CUSTOMER = "ikus-spb"
API_ACCOUNT_GROUPS_PATH = "/api/v6/accounts/groups"
API_ACCOUNT_DETAILS_PATH = "/api/v7/accounts/{account_id}/details"
API_CURRENT_USER_PATH = "/api/v6/users/current"

import os

# Isolate tests from local .env keys and external APIs
os.environ.setdefault("USE_DEMO_DATA", "true")
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_KEY"] = ""
os.environ["SHARPAPI_KEY"] = ""

import os
from dotenv import load_dotenv
from supabase import create_client

# This loads your .env file
load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

print(f"Connecting to: {url}")

try:
    supabase = create_client(url, key)
    # This tries to 'ping' your database
    response = supabase.table("lockers").select("count", count="exact").execute()
    print("✅ Connection Successful!")
    print(f"Total lockers found in database: {response.count}")
except Exception as e:
    print(f"Connection Failed: {e}")
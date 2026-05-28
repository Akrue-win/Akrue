from supabase import create_client, Client
from akrue.env import SUPABASE_URL, SUPABASE_SECRET_KEY

def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

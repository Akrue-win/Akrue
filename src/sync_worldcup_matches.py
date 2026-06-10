#!/usr/bin/env python3
"""
Sync World Cup matches from football-data.org to Supabase.
Run this once to populate pending_matches with World Cup data.
Usage: python src/sync_worldcup_matches.py
"""

import sys
from src.worldcup_adapter import WorldCupAdapter
from akrue.supabase_client import get_client


def sync_worldcup_matches():
    """Fetch World Cup matches and insert into pending_matches table."""
    adapter = WorldCupAdapter()

    print("[WorldCup] Fetching SCHEDULED matches...")
    matches = adapter.get_matches(status="SCHEDULED")

    if not matches:
        print("[WorldCup] No matches found. Check API key and connection.")
        return False

    print(f"[WorldCup] Found {len(matches)} matches. Syncing to Supabase...")

    sb = get_client()

    for match in matches:
        match_id = f"worldcup_{match['id']}"

        try:
            # Upsert to avoid duplicates
            result = sb.table("pending_matches").upsert({
                "match_id": match_id,
                "sport": "worldcup",
                "team_id": int(match["id"]),  # Use match ID as team_id placeholder
                "team_name": match["home"],
                "opponent": match["away"],
                "kickoff_utc": match["kickoff_utc"],
                "settled": False,
            }).execute()

            if result.data:
                print(f"  ✓ {match['home']} vs {match['away']} ({match['kickoff_utc']})")
        except Exception as e:
            print(f"  ✗ Error syncing {match['home']} vs {match['away']}: {e}")
            return False

    print(f"\n[WorldCup] ✓ Synced {len(matches)} matches!")
    return True


if __name__ == "__main__":
    success = sync_worldcup_matches()
    sys.exit(0 if success else 1)

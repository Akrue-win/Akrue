"""
World Cup 2026 match fetcher.
Uses football-data.org API to fetch World Cup matches.
Isolated for easy deletion post-tournament.
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
WORLDCUP_COMPETITION_ID = 2000  # World Cup 2026 in football-data.org

class WorldCupAdapter:
    """Fetch World Cup matches from football-data.org."""

    def __init__(self, api_key: str = FOOTBALL_DATA_API_KEY):
        self.api_key = api_key
        self.base_url = "https://api.football-data.org/v4"
        self.headers = {"X-Auth-Token": api_key}

    def get_matches(self, status: str = "SCHEDULED") -> List[Dict[str, Any]]:
        """
        Fetch World Cup matches.
        status: 'SCHEDULED', 'LIVE', 'FINISHED'
        Returns list of match dicts with: id, home, away, kickoff_utc, status, score
        """
        url = f"{self.base_url}/competitions/{WORLDCUP_COMPETITION_ID}/matches"
        params = {"status": status}

        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            matches = []
            for match in data.get("matches", []):
                matches.append({
                    "id": str(match["id"]),
                    "home": match["homeTeam"]["name"],
                    "away": match["awayTeam"]["name"],
                    "kickoff_utc": match["utcDate"],
                    "status": match["status"],
                    "home_score": match["score"]["fullTime"]["home"],
                    "away_score": match["score"]["fullTime"]["away"],
                    "draw_emoji": "🟡" if match["score"]["fullTime"]["home"] == match["score"]["fullTime"]["away"] else None,
                })
            return matches
        except Exception as e:
            print(f"[WorldCup] Error fetching matches: {e}")
            return []

    def get_match_details(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single match by ID."""
        url = f"{self.base_url}/matches/{match_id}"

        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            match = resp.json()

            return {
                "id": str(match["id"]),
                "home": match["homeTeam"]["name"],
                "away": match["awayTeam"]["name"],
                "kickoff_utc": match["utcDate"],
                "status": match["status"],
                "home_score": match["score"]["fullTime"]["home"],
                "away_score": match["score"]["fullTime"]["away"],
            }
        except Exception as e:
            print(f"[WorldCup] Error fetching match {match_id}: {e}")
            return None

    @staticmethod
    def get_result(home_score: Optional[int], away_score: Optional[int],
                   user_prediction: str) -> bool:
        """
        Determine if user prediction is correct.
        user_prediction: 'win' (home wins), 'draw', 'loss' (away wins)
        """
        if home_score is None or away_score is None:
            return False

        if home_score > away_score:
            return user_prediction == "win"
        elif home_score == away_score:
            return user_prediction == "draw"
        else:
            return user_prediction == "loss"

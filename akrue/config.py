SPORT_CONFIG = {
    "epl": {
        "name":             "Premier League",
        "emoji":            "⚽",
        "allows_draw":      True,
        "options":          ["WIN", "DRAW", "LOSS"],
        "user_field":       "epl_team",
        "match_id_prefix":  "epl_",
        "win_emoji":        "🟢",
        "draw_emoji":       "🟡",
        "loss_emoji":       "🔴",
        "start_label":      "kicks off",
    },
    "mlb": {
        "name":             "MLB",
        "emoji":            "⚾",
        "allows_draw":      False,
        "options":          ["WIN", "LOSS"],
        "user_field":       "mlb_team",
        "match_id_prefix":  "mlb_",
        "win_emoji":        "⚾",
        "draw_emoji":       None,
        "loss_emoji":       "😬",
        "start_label":      "first pitch",
    },
    "worldcup": {
        "name":             "World Cup 2026",
        "emoji":            "🌍",
        "allows_draw":      True,
        "options":          ["WIN", "DRAW", "LOSS"],
        "user_field":       None,
        "match_id_prefix":  "worldcup_",
        "win_emoji":        "🟢",
        "draw_emoji":       "🟡",
        "loss_emoji":       "🔴",
        "start_label":      "kicks off",
    },
}

SPORT_TEAM_IDS = {
    "epl": {
        "Arsenal": 57, "Aston Villa": 58, "Bournemouth": 1044,
        "Brentford": 402, "Brighton": 397, "Burnley": 328,
        "Chelsea": 61, "Crystal Palace": 354, "Everton": 62,
        "Fulham": 63, "Leeds United": 341, "Liverpool": 64,
        "Manchester City": 65, "Manchester United": 66, "Newcastle United": 67,
        "Nottingham Forest": 351, "Sunderland": 356, "Tottenham Hotspur": 73,
        "West Ham United": 563, "Wolverhampton": 76,
    },
    "mlb": {
        "Baltimore Orioles": 110, "Boston Red Sox": 111, "New York Yankees": 147,
        "Tampa Bay Rays": 139, "Toronto Blue Jays": 141, "Chicago White Sox": 145,
        "Cleveland Guardians": 114, "Detroit Tigers": 116, "Kansas City Royals": 118,
        "Minnesota Twins": 142, "Houston Astros": 117, "Los Angeles Angels": 108,
        "Oakland Athletics": 133, "Seattle Mariners": 136, "Texas Rangers": 140,
        "Atlanta Braves": 144, "Miami Marlins": 146, "New York Mets": 121,
        "Philadelphia Phillies": 143, "Washington Nationals": 120, "Chicago Cubs": 112,
        "Cincinnati Reds": 113, "Milwaukee Brewers": 158, "Pittsburgh Pirates": 134,
        "St. Louis Cardinals": 138, "Arizona Diamondbacks": 109, "Colorado Rockies": 115,
        "Los Angeles Dodgers": 119, "San Diego Padres": 135, "San Francisco Giants": 137,
    },
}

PROMPT_WINDOW_MIN = 5
PROMPT_WINDOW_MAX = 45
MLB_PRE_GAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup", "Preview"}
MLB_FINAL_STATUSES    = {"Final", "Game Over", "Completed Early"}
CAP_WARNING_THRESHOLD  = 0.75
DEFAULT_CAP_MULTIPLIER = 1.25
MAX_CAP_MULTIPLIER     = 2.0

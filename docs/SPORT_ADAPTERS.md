# Adding a New Sport

Akrue is sport-agnostic. Adding a new sport requires changes in four places.

## 1. Add an entry to `SPORT_CONFIG` in `akrue/config.py`

```python
"nfl": {
    "name":             "NFL",
    "emoji":            "🏈",
    "allows_draw":      False,
    "options":          ["WIN", "LOSS"],
    "user_field":       "nfl_team",        # column name in users table
    "match_id_prefix":  "nfl_",
    "win_emoji":        "🏈",
    "draw_emoji":       None,
    "loss_emoji":       "😬",
    "start_label":      "kickoff",
}
```

## 2. Add team IDs to `SPORT_TEAM_IDS` in `akrue/config.py`

```python
"nfl": {
    "Dallas Cowboys": 6,
    "New England Patriots": 17,
    # ...
}
```

## 3. Add API fetch functions to `src/nudge.py`

You need six functions:

| Function | Returns | Notes |
|---|---|---|
| `get_nfl_upcoming(team_id)` | list of game dicts | Only pre-game statuses |
| `get_nfl_recent(team_id)` | list of game dicts | Only finished games |
| `nfl_result_for_team(game, team_id)` | `"win"` or `"loss"` | |
| `nfl_teams_from_game(game, team_id)` | `(home, away, opponent)` tuple | |
| `nfl_score_str(game)` | score string e.g. `"24-17"` | |
| `nfl_kickoff_utc(game)` | `datetime` or `None` | Timezone-naive UTC |
| `nfl_match_key(game)` | `"nfl_{game_id}"` | No team suffix |
| `nfl_finished_dict(team_id)` | `{match_key: game}` dict | Used in settlement |

## 4. Register in `SPORT_API_HANDLERS` in `src/nudge.py`

```python
"nfl": {
    "get_upcoming":  get_nfl_upcoming,
    "get_finished":  nfl_finished_dict,
    "result_fn":     nfl_result_for_team,
    "teams_fn":      nfl_teams_from_game,
    "score_fn":      nfl_score_str,
    "kickoff_fn":    nfl_kickoff_utc,
    "match_key_fn":  nfl_match_key,
}
```

That's it — the generic `check_pre_match`, `check_post_match`, and `check_reminders` loops will automatically pick up the new sport.

## Also add to the users table

Add a `nfl_team` column to Supabase `users` table. The `user_field` in SPORT_CONFIG tells the system which column to check.

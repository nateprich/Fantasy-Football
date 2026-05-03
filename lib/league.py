"""League constants from the constitution (theleague.us).

These are stable per-rule facts; update if the constitution changes.
"""

LEAGUE_ID = "13522"
LEAGUE_NAME = "The League"
NUM_TEAMS = 16
SALARY_CAP = 45_000_000
LEAGUE_MIN_SALARY = 425_000
ANNUAL_ESCALATION = 0.10  # 10% Feb 15 raise for everyone under contract
MAX_CONTRACT_YEARS = 5
ACTIVE_ROSTER_MAX = 22
PRACTICE_SQUAD_MAX = 3
PS_CAP_PCT = 0.50

# Starting lineup (per constitution): QB 1, RB 1-4, WR 1-4, TE 1-4, PK 1, Def 1.
# Total starters = 9 (1 QB + 6 flex from RB/WR/TE + 1 PK + 1 Def).
STARTING_QB = 1
STARTING_PK = 1
STARTING_DEF = 1
FLEX_TOTAL = 6  # RB+WR+TE combined, each 1-4
SCORING_POSITIONS = ["QB", "RB", "WR", "TE", "PK", "Def"]

# Excluded MFL position codes (team-aggregate / IDP we don't use)
EXCLUDED_POSITIONS = {
    "TMWR", "TMRB", "TMDL", "TMTE", "TMQB", "TMPK", "TMPN", "TMLB", "TMDB",
    "ST", "Off", "HB", "CB", "DB", "DL", "LB", "S", "DE", "DT", "FB",
}

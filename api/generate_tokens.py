"""Generate the three test tokens required by the assessment.

    python -m api.generate_tokens

Writes api/test_tokens.json and prints ready-to-paste curl commands.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import auth

TEST_USERS = [
    {"sub": "coo@pinewood.example", "role": "corporate_admin",
     "region": None, "community_id": None,
     "note": "Corporate admin — sees all 14 communities"},
    {"sub": "rd.pnw@pinewood.example", "role": "regional_director",
     "region": "Pacific Northwest", "community_id": None,
     "note": "Regional Director, Pacific Northwest — sees C001-C005 (Oregon)"},
    {"sub": "ed.c007@pinewood.example", "role": "executive_director",
     "region": None, "community_id": "C007",
     "note": "Executive Director of C007 (Pinewood Saguaro) — sees only C007"},
]


def main() -> None:
    out = []
    for u in TEST_USERS:
        token = auth.create_token(u["sub"], u["role"], u["region"], u["community_id"])
        out.append({**u, "token": token})
        print(f"\n# {u['note']}\nexport TOKEN='{token}'")
        print('curl -s -H "Authorization: Bearer $TOKEN" '
              '"http://127.0.0.1:8000/occupancy" | head -c 400')

    path = Path(__file__).parent / "test_tokens.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\n\nTokens written to {path}")


if __name__ == "__main__":
    main()

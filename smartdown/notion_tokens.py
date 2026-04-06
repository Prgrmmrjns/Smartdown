"""Notion integration token normalization (no FastAPI imports)."""


def normalize_notion_integration_token(raw: str) -> str:
    """Strip paste junk: BOM, ZWSP, newlines, optional Bearer/quote wrappers."""
    t = (raw or "").replace("\ufeff", "").strip()
    for z in ("\u200b", "\u200c", "\u200d", "\u2060"):
        t = t.replace(z, "")
    t = t.strip().replace("\r", "").replace("\n", "").strip()
    low = t.lower()
    if low.startswith("bearer "):
        t = t[7:].strip()
    if len(t) >= 2:
        if (t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'"):
            t = t[1:-1].strip()
    return t

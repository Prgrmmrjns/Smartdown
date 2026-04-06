"""Detect summary-style asks → fragment-only model output (no full-file echo)."""
import re

_FULL_DOC_HINT = re.compile(
    r"\b(rewrite|reformat|whole\s+(?:document|file)|full\s+document|beautif\w*|restructure|"
    r"convert\s+all|replace\s+the\s+entire|edit\s+the\s+whole|apply\s+to\s+the\s+whole|"
    r"regenerat\w*\s+(?:the\s+)?(?:whole|entire))\b",
    re.I,
)

_FRAGMENT_HINT = re.compile(
    r"\b(summar(y|ize)|bullet|outline|tl;?dr|synopsis|abstract|executive\s+summary|"
    r"key\s+points?|takeaways?)\b",
    re.I,
)


def wants_fragment_only_apply(last_user: str, prefix: str, *, force_full: bool) -> bool:
    if force_full:
        return False
    t = f"{prefix}\n{last_user}"
    if _FULL_DOC_HINT.search(t):
        return False
    return bool(_FRAGMENT_HINT.search(t))


def prepend_fragment_to_document(fragment: str, original_md: str) -> str:
    frag = (fragment or "").strip()
    if not frag:
        return original_md
    body = (original_md or "").strip()
    if not body:
        return frag
    return f"{frag}\n\n---\n\n{body}"

"""Page language: ``<html lang>`` if present, else a cheap Arabic-script check."""

from __future__ import annotations

import re

ARABIC_SHARE = 0.30
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def detect_lang(html_lang: str | None, text: str) -> str:
    """Return a lowercase primary language subtag (``"en"``, ``"ar"``), or ``"und"``.

    ``<html lang="ar-TN">`` is reduced to ``"ar"``.
    """
    declared = (html_lang or "").strip().lower().replace("_", "-").split("-")[0]
    if declared:
        return declared
    letters = [c for c in text if c.isalpha()]
    if letters and len(_ARABIC.findall(text)) / len(letters) > ARABIC_SHARE:
        return "ar"
    return "und"

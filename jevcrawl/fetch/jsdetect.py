"""Detect pages that need a JavaScript browser to show their content."""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

MIN_VISIBLE_CHARS = 200
APP_ROOT_SELECTORS = ("#root", "#__next", "#app")
_ENABLE_JS = re.compile(r"(enable|turn on|requires?)\W+(\w+\W+){0,3}javascript", re.IGNORECASE)


@dataclass(frozen=True)
class JsSignals:
    """Evidence that must be read from the raw tree, before ``noscript`` etc. are stripped."""

    empty_app_root: bool = False
    noscript_wants_js: bool = False


def js_signals(tree: LexborHTMLParser) -> JsSignals:
    """Collect the signals that disappear once the segmenter strips tags."""
    empty_root = any(
        (node := tree.css_first(selector)) is not None and not node.text(strip=True)
        for selector in APP_ROOT_SELECTORS
    )
    wants_js = any(_ENABLE_JS.search(n.text()) for n in tree.css("noscript"))
    return JsSignals(empty_root, wants_js)


def needs_js(signals: JsSignals, visible_text: str) -> bool:
    """True when the page is probably an unrendered JavaScript app."""
    return (
        len(visible_text) < MIN_VISIBLE_CHARS or signals.empty_app_root or signals.noscript_wants_js
    )

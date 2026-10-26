"""Segment an HTML page into verbatim blocks with character offsets, plus its links."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Final

from selectolax.lexbor import LexborHTMLParser, LexborNode

from jevcrawl.fetch.jsdetect import js_signals, needs_js
from jevcrawl.models import Block, Link, Page, estimate_tokens
from jevcrawl.segment.lang import detect_lang
from jevcrawl.segment.links import resolve_link

ALWAYS_REMOVED: Final = ["script", "style", "noscript", "template", "svg", "iframe", "canvas"]
LAYOUT_TAGS: Final = ["nav", "footer", "aside"]
HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Tags that start and end a unit of text. Everything else is inline and flows into the open run.
BLOCK_TAGS: Final = frozenset(
    [
        "p",
        "li",
        "pre",
        "blockquote",
        "dt",
        "dd",
        "div",
        "section",
        "article",
        "main",
        "header",
        "ul",
        "ol",
        "dl",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "caption",
        "form",
        "figure",
        "figcaption",
        "details",
        "summary",
        "address",
        "fieldset",
        "legend",
        "hgroup",
        "menu",
        "body",
        "center",
        "hr",
        "nav",
        "footer",
        "aside",
        "dialog",
    ]
)
# Containers that start a new section and whose headings do not leak out of them, so a cookie
# banner after <main> never joins the last paragraph of the article.
SCOPE_TAGS: Final = frozenset(
    ["main", "article", "section", "aside", "nav", "footer", "form", "dialog"]
)
SCOPE_ROLES: Final = frozenset({"dialog", "alertdialog", "banner", "contentinfo", "complementary"})
_NOISE_ATTR = re.compile(r"cookie|consent|gdpr|modal|popup", re.IGNORECASE)
# Interface chrome rather than content.
IGNORED_TAGS: Final = frozenset(
    {"select", "option", "optgroup", "datalist", "textarea", "button", "input"}
)
MIN_BLOCK_TOKENS: Final = 20
MAX_LINK_DENSITY: Final = 0.5  # blocks above this are dropped by the free filters
CONTEXT_CHARS: Final = 200
MAX_TITLE_CHARS: Final = 300  # the title rides in every Jev request of the page
# Sentence enders: ASCII, CJK and fullwidth variants, and the Arabic question mark.
_SENTENCE_END = re.compile("(?<=[.!?\\u3002\\uff01\\uff1f\\u061f])\\s+")
_DISPLAY_NONE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


def _is_link_dense(link_chars: int, text: str) -> bool:
    return bool(text) and link_chars / len(text) > MAX_LINK_DENSITY


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _normalize_pre(text: str) -> str:
    return "\n".join(line for line in (_normalize(raw) for raw in text.splitlines()) if line)


@dataclass
class _Unit:
    """One paragraph, list item, table row or loose text run."""

    section: int
    heading_path: str
    text: str
    link_chars: int


@dataclass
class _RawLink:
    url: str
    text: str
    unit: int | None = None


@dataclass
class _Draft:
    """A block under construction."""

    heading_path: str
    text: str
    link_chars: int
    section: int
    units: list[int] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    @property
    def link_dense(self) -> bool:
        return _is_link_dense(self.link_chars, self.text)

    def absorb(self, other: _Draft, *, before: bool = False) -> None:
        """Merge ``other`` into this draft, placing its text before or after ours."""
        first, second = (other, self) if before else (self, other)
        self.text = f"{first.text}\n{second.text}"
        self.units = first.units + second.units
        self.link_chars += other.link_chars


class _Walker:
    """Single in-order walk over the DOM that produces units and links."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.units: list[_Unit] = []
        self.links: list[_RawLink] = []
        self._run: list[tuple[str, bool]] = []
        self._pending: list[_RawLink] = []
        self._stack: list[tuple[int, str]] = []
        self._section = 0
        self._link_depth = 0
        self._inline_only = 0  # >0 inside table cells: block tags do not split the run
        self._preserve_lines = False

    @property
    def heading_path(self) -> str:
        return " > ".join(text for _, text in self._stack)

    def walk(self, node: LexborNode) -> None:
        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                self._run.append((child.text_content or "", self._link_depth > 0))
            elif tag == "-comment" or tag in IGNORED_TAGS or self._hidden(child):
                continue
            elif self._inline_only:
                self._inline(child)
            elif tag in HEADINGS:
                self._heading(child)
            elif tag == "tr":
                self._row(child)
            elif tag == "pre":
                self.flush()
                self._preserve_lines = True
                self.walk(child)
                self.flush()
                self._preserve_lines = False
            elif tag in BLOCK_TAGS:
                self._block(child)
            else:
                self._inline(child)

    def _block(self, node: LexborNode) -> None:
        """Walk a block element. Scoped containers also close their headings and section."""
        scoped = self._is_scope(node)
        split = scoped or node.tag == "div"  # a <div> only splits blocks; its headings may leak
        saved = list(self._stack)
        self.flush()
        if split:
            self._section += 1
        self.walk(node)
        self.flush()
        if scoped:
            self._stack = saved  # headings inside the container do not leak out of it
        if split:
            self._section += 1

    @staticmethod
    def _is_scope(node: LexborNode) -> bool:
        attrs = node.attributes
        if node.tag in SCOPE_TAGS or (attrs.get("role") or "") in SCOPE_ROLES:
            return True
        return bool(_NOISE_ATTR.search(f"{attrs.get('class') or ''} {attrs.get('id') or ''}"))

    def _inline(self, node: LexborNode) -> None:
        if node.tag == "br":
            self._run.append((" ", False))
            return
        if node.tag == "a":
            self._open_link(node)
            self._link_depth += 1
            self.walk(node)
            self._link_depth -= 1
            return
        self.walk(node)

    def _open_link(self, node: LexborNode) -> None:
        url = resolve_link(node.attributes.get("href") or "", self.base_url)
        if url is None:
            return
        text = _normalize(node.text(separator=" "))
        if not text:
            text = _normalize(
                node.attributes.get("title") or node.attributes.get("aria-label") or ""
            )
        link = _RawLink(url, text)
        self.links.append(link)
        self._pending.append(link)

    def _heading(self, node: LexborNode) -> None:
        self.flush()
        # Docs sites append a permalink sign to headings ("Assignment expressions ¶").
        text = _normalize(node.text(separator=" ")).rstrip(" ¶#§").strip()
        if not text:
            return
        level = int((node.tag or "h1")[1])
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, text))
        self._section += 1

    def _row(self, row: LexborNode) -> None:
        self.flush()
        cells: list[str] = []
        link_chars = 0
        self._inline_only += 1
        for cell in row.iter():
            if cell.tag not in {"td", "th"}:
                continue
            self._run = []
            self.walk(cell)
            text, links = self._take_run()
            if text:
                cells.append(text)
                link_chars += links
        self._inline_only -= 1
        self._emit(" | ".join(cells), link_chars)

    def _take_run(self) -> tuple[str, int]:
        pieces, self._run = self._run, []
        joined = "".join(text for text, _ in pieces)
        text = _normalize_pre(joined) if self._preserve_lines else _normalize(joined)
        link_chars = sum(len(_normalize(t)) for t, in_link in pieces if in_link)
        return text, min(link_chars, len(text))

    def flush(self) -> None:
        """Turn the open text run into a unit, if it holds any text."""
        text, link_chars = self._take_run()
        self._emit(text, link_chars)

    def _emit(self, text: str, link_chars: int) -> None:
        if not text:
            return
        self.units.append(_Unit(self._section, self.heading_path, text, link_chars))
        for link in self._pending:
            link.unit = len(self.units) - 1
        self._pending.clear()

    @staticmethod
    def _hidden(node: LexborNode) -> bool:
        attrs = node.attributes
        if "hidden" in attrs or attrs.get("aria-hidden") == "true":
            return True
        return bool(_DISPLAY_NONE.search(attrs.get("style") or ""))


def _split_sentences(text: str, max_tokens: int) -> list[str]:
    """Split at sentence boundaries; break any sentence still too long at word boundaries."""
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if estimate_tokens(sentence) <= max_tokens:
            pieces.append(sentence)
            continue
        current, used = "", 0
        for word in sentence.split(" "):
            cost = estimate_tokens(word) + 1  # the word plus a space
            if current and used + cost > max_tokens:
                pieces.append(current)
                current, used = word, cost
            else:
                current = f"{current} {word}" if current else word
                used += cost
        pieces.append(current)
    return [p for p in pieces if p]


def _pack_section(units: list[tuple[int, _Unit]], block_tokens: int) -> list[_Draft]:
    """Greedy packing of one section's units into drafts of at most ``block_tokens``.

    Link-heavy units (menus, "related links" lists) never share a block with prose, so one
    link list cannot drag a paragraph past the link-density filter, or the other way round.
    """
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for index, unit in units:
        if current and current.link_dense != _is_link_dense(unit.link_chars, unit.text):
            drafts.append(current)
            current = None
        if estimate_tokens(unit.text) <= block_tokens:
            parts = [(unit.text, unit.link_chars)]
        else:
            pieces = _split_sentences(unit.text, block_tokens)
            total = sum(len(p) for p in pieces) or 1
            parts = [(p, round(unit.link_chars * len(p) / total)) for p in pieces]
        for position, (text, link_chars) in enumerate(parts):
            sep = "\n" if position == 0 else " "
            if current and estimate_tokens(current.text + sep + text) > block_tokens:
                drafts.append(current)
                current = None
            if current is None:
                current = _Draft(unit.heading_path, text, link_chars, unit.section, [index])
            else:
                current.text += sep + text
                current.link_chars += link_chars
                if index not in current.units:
                    current.units.append(index)
    if current:
        drafts.append(current)
    return drafts


def _merge_tiny(drafts: list[_Draft]) -> list[_Draft]:
    """Merge blocks under MIN_BLOCK_TOKENS into a neighbour with the same heading path.

    The neighbour must be in the same section (so a footer div never fuses with the paragraph
    before it) and on the same side of the link-density line.
    """

    def mergeable(small: _Draft, other: _Draft) -> bool:
        return (
            small.tokens < MIN_BLOCK_TOKENS
            and small.heading_path == other.heading_path
            and small.section == other.section
            and small.link_dense == other.link_dense
        )

    merged: list[_Draft] = []
    for draft in drafts:
        if merged and mergeable(draft, merged[-1]):
            merged[-1].absorb(draft)
        else:
            merged.append(draft)
    result: list[_Draft] = []
    for i, draft in enumerate(merged):
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if nxt and mergeable(draft, nxt):
            nxt.absorb(draft, before=True)
        else:
            result.append(draft)
    return result


def _merge_tiny_runs(drafts: list[_Draft], block_tokens: int) -> list[_Draft]:
    """Join runs of consecutive tiny blocks under one heading, even across sections.

    Card grids and people lists put each item in its own div: a name, then a year, each a
    block of three tokens that means nothing alone ("Brad Sorenson", "2024"). Only tiny blocks
    join, so a real paragraph never fuses with its neighbours, and a run stops at
    ``block_tokens``.
    """
    merged: list[_Draft] = []
    runs: set[int] = set()  # ids of drafts built only from tiny blocks
    for draft in drafts:
        last = merged[-1] if merged else None
        if (
            last is not None
            and draft.tokens < MIN_BLOCK_TOKENS
            and (last.tokens < MIN_BLOCK_TOKENS or id(last) in runs)
            and last.heading_path == draft.heading_path
            and last.link_dense == draft.link_dense
            and last.tokens + draft.tokens <= block_tokens
        ):
            last.absorb(draft)
            runs.add(id(last))
        else:
            merged.append(draft)
    return merged


def _group_sections(units: list[_Unit]) -> list[list[tuple[int, _Unit]]]:
    sections: list[list[tuple[int, _Unit]]] = []
    for index, unit in enumerate(units):
        if sections and sections[-1][0][1].section == unit.section:
            sections[-1].append((index, unit))
        else:
            sections.append([(index, unit)])
    return sections


def _page_title(tree: LexborHTMLParser) -> str:
    node = tree.css_first("title") or tree.css_first("h1")
    return _normalize(node.text(separator=" "))[:MAX_TITLE_CHARS] if node else ""


def segment_html(
    html: str,
    url: str,
    *,
    final_url: str | None = None,
    status: int = 200,
    keep_layout_tags: bool = False,
    block_tokens: int = 300,
) -> Page:
    """Parse ``html`` into a :class:`Page` of blocks and links.

    ``Page.text`` is the block texts in DOM order joined by a blank line, and each block's
    ``char_start``/``char_end`` index into it, so ``page.text[start:end] == block.text``.
    Block ids (``b0``, ``b1``, ...) number the page's blocks before any filtering.
    """
    final_url = final_url or url
    tree = LexborHTMLParser(html)
    title = _page_title(tree)
    root = tree.css_first("html")
    html_lang = root.attributes.get("lang") if root else None
    signals = js_signals(tree)
    nav_links = [] if keep_layout_tags else _layout_links(tree, final_url)

    tree.strip_tags(ALWAYS_REMOVED + ([] if keep_layout_tags else LAYOUT_TAGS), recursive=True)
    walker = _Walker(final_url)
    if tree.body is not None:
        walker.walk(tree.body)
    walker.flush()

    drafts: list[_Draft] = []
    for section in _group_sections(walker.units):
        drafts.extend(_pack_section(section, block_tokens))
    drafts = _merge_tiny_runs(_merge_tiny(drafts), block_tokens)

    blocks: list[Block] = []
    unit_block: dict[int, int] = {}
    offset = 0
    for i, draft in enumerate(drafts):
        start = offset
        offset += len(draft.text) + 2  # "\n\n" separator
        blocks.append(
            Block(
                f"b{i}", final_url, draft.heading_path, draft.text, start, start + len(draft.text),
                draft.tokens, min(draft.link_chars, len(draft.text)),
            )
        )  # fmt: skip
        for unit_index in draft.units:
            unit_block.setdefault(unit_index, i)
    text = "\n\n".join(b.text for b in blocks)

    links = _build_links(walker, unit_block, blocks) + _number_links(nav_links, len(walker.links))
    return Page(
        url=url,
        final_url=final_url,
        status=status,
        title=title,
        lang=detect_lang(html_lang, text),
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        blocks=blocks,
        links=links,
        needs_js=needs_js(signals, text),
    )


# <header> is not stripped, so its links are collected with the content.
LAYOUT_LINK_SELECTOR = ", ".join(f"{tag} a[href]" for tag in LAYOUT_TAGS)
_LABEL_TAGS = ("a", "button", "summary", "span")


def _layout_links(tree: LexborHTMLParser, base_url: str) -> list[tuple[str, str, str]]:
    """Links from navigation, sidebars and footers as ``(url, text, context)``.

    Their text is not content and never becomes a block, but on documentation sites the sidebar
    menu is often the only way to the right section, so the links stay candidates. They come after
    every in-content link and have no block, so the per-page cap prefers in-content links.
    Must run before the layout tags are stripped.
    """
    found: list[tuple[str, str, str]] = []
    for node in tree.css(LAYOUT_LINK_SELECTOR):
        url = resolve_link(node.attributes.get("href") or "", base_url)
        if url is None:
            continue
        text = _normalize(node.text(separator=" "))
        if not text:
            text = _normalize(
                node.attributes.get("title") or node.attributes.get("aria-label") or ""
            )
        found.append((url, text, _menu_context(node, text)))
    return found


def _menu_context(node: LexborNode, own_text: str) -> str:
    """The menu path of a link, e.g. ``Authentication > Security`` (the labels of its groups)."""
    labels: list[str] = []
    ancestor = node.parent
    for _ in range(12):
        if ancestor is None:
            break
        if ancestor.tag == "li":
            for child in ancestor.iter():
                if child.tag in _LABEL_TAGS and child is not node:
                    label = _normalize(child.text(separator=" "))
                    if label and label != own_text:
                        labels.append(label[:60])
                    break
        ancestor = ancestor.parent
    labels.reverse()
    path = " > ".join(dict.fromkeys(labels))  # outermost group first, no repeats
    return f"Site navigation: {path}" if path else "Site navigation"


def _number_links(found: list[tuple[str, str, str]], offset: int) -> list[Link]:
    return [
        Link(f"L{offset + i}", url, text, context[: CONTEXT_CHARS + 20], offset + i)
        for i, (url, text, context) in enumerate(found)
    ]


def _build_links(walker: _Walker, unit_block: dict[int, int], blocks: list[Block]) -> list[Link]:
    links = []
    for position, raw in enumerate(walker.links):
        block_id = None
        context = ""
        if raw.unit is not None:
            unit = walker.units[raw.unit]
            block_id = blocks[unit_block[raw.unit]].id
            body = unit.text[:CONTEXT_CHARS]
            context = f"{unit.heading_path}: {body}" if unit.heading_path else body
        links.append(Link(f"L{position}", raw.url, raw.text, context, position, block_id))
    return links

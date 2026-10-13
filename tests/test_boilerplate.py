"""Required test 2: boilerplate memory, plus the link-density filter."""

from jevcrawl.segment.blocks import segment_html
from jevcrawl.segment.boilerplate import BoilerplateStore, block_hash, free_filter_blocks

FOOTER = "<div id='f'>Copyright 2026 Example Shop. Contact us: support@example.com</div>"


TOPICS = {1: "returns", 2: "shipping", 3: "warranty"}


def page_with_footer(n: int):
    # distinct words, not distinct numbers: digits are normalized away in block hashes
    unique = f"Unique content about {TOPICS[n]} for this page, quite specific."
    body = f"<h2>Topic {n}</h2><p>{unique}</p>{FOOTER}"
    return segment_html(f"<html lang='en'><body>{body}</body></html>", f"https://ex.com/p{n}")


def texts(blocks):
    return [b.text for b in blocks]


def has_footer(blocks):
    return any("Copyright" in t for t in texts(blocks))


def test_footer_repeated_on_three_pages_is_dropped_from_the_third():
    store = BoilerplateStore()
    kept = [free_filter_blocks(page_with_footer(n), store) for n in (1, 2, 3)]
    assert has_footer(kept[0]) and has_footer(kept[1])
    assert not has_footer(kept[2])
    assert all(any("Unique content" in t for t in texts(k)) for k in kept)


def test_same_page_recrawled_does_not_count_twice():
    store = BoilerplateStore()
    for _ in range(5):
        kept = free_filter_blocks(page_with_footer(1), store)
    assert has_footer(kept)


def test_domains_are_counted_separately():
    store = BoilerplateStore()
    for host in ("a.com", "b.com", "c.com"):
        page = segment_html(f"<html><body>{FOOTER}</body></html>", f"https://{host}/x")
        assert free_filter_blocks(page, store)  # never 3 pages on one domain


def test_hash_ignores_case_whitespace_and_digits():
    assert block_hash("Updated 12 May  2026") == block_hash("updated 3 may 1999")
    assert block_hash("abc") != block_hash("abd")


def test_link_dense_blocks_are_dropped_without_a_store():
    links = "".join(f"<li><a href='/{i}'>Topic number {i}</a></li>" for i in range(5))
    prose = "Real prose about the topic that is long enough."
    page = segment_html(
        f"<html><body><ul>{links}</ul><p>{prose}</p></body></html>", "https://ex.com/"
    )
    assert [b.text for b in free_filter_blocks(page, None)] == [prose]

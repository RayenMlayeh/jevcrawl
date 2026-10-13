"""Required test 1: the segmenter."""

import hashlib

from jevcrawl.models import estimate_tokens
from jevcrawl.segment.blocks import segment_html
from tests.fixtures.fake_site import BASE, PAGES, read

URL = "https://t.example/page"


def seg(body: str, **kw):
    return segment_html(f"<html lang='en'><body>{body}</body></html>", URL, **kw)


def test_headings_form_heading_path():
    page = seg(
        "<h1>Help</h1><h2>Returns</h2><h3>Refunds</h3>"
        "<p>Refunds are issued within 14 days of receiving the returned item.</p>"
        "<h2>Shipping</h2><p>Standard delivery takes 3 to 5 working days, express takes less.</p>"
    )
    assert [b.heading_path for b in page.blocks] == ["Help > Returns > Refunds", "Help > Shipping"]
    assert (
        page.blocks[0].text == "Refunds are issued within 14 days of receiving the returned item."
    )


def test_offsets_index_into_page_text_for_every_fixture_page():
    for path, filename in PAGES.items():
        page = segment_html(read(filename), BASE + path)
        assert page.text == "\n\n".join(b.text for b in page.blocks)
        for block in page.blocks:
            assert page.text[block.char_start : block.char_end] == block.text, (path, block.id)


def test_text_sha256_matches_page_text():
    page = segment_html(read("help/returns.html"), BASE + "/help/returns")
    assert page.text_sha256 == hashlib.sha256(page.text.encode()).hexdigest()


def test_large_block_splits_at_sentence_boundaries():
    sentences = [f"This is sentence number {i} of a very long paragraph." for i in range(120)]
    page = seg("<h2>Long</h2><p>" + " ".join(sentences) + "</p>", block_tokens=300)
    assert len(page.blocks) > 1
    for block in page.blocks:
        assert block.tokens_est <= 300
        assert block.text.endswith(".")  # never cut mid-sentence
        assert block.heading_path == "Long"
    assert " ".join(b.text for b in page.blocks) == " ".join(sentences)


def test_tiny_trailing_fragment_merges_into_previous_block():
    sentences = [f"This is sentence number {i} of a very long paragraph." for i in range(40)]
    page = seg("<h2>Long</h2><p>" + " ".join(sentences) + " End.</p>", block_tokens=300)
    assert page.blocks[-1].text.endswith("End.")
    assert all(b.tokens_est >= 20 for b in page.blocks)


def test_short_paragraphs_under_one_heading_form_one_block():
    page = seg("<h2>A</h2><p>short one.</p><p>short two.</p>")
    assert [b.text for b in page.blocks] == ["short one.\nshort two."]


def test_tiny_blocks_join_across_sections_but_never_fuse_with_a_paragraph():
    # A card grid (real: poms.org award winners): name and year are separate divs and mean
    # nothing apart.
    cards = seg("<h2>Past recipients</h2><div>Brad Sorenson</div><div>2024</div><div>Di Wu</div>")
    assert [b.text for b in cards.blocks] == ["Brad Sorenson\n2024\nDi Wu"]
    # A real paragraph never absorbs a tiny div from another section (e.g. a footer line).
    para = "Refunds are issued within fourteen days of receiving the returned item in good shape."
    page = seg(f"<h2>A</h2><p>{para}</p><div>short two.</div>")
    assert [b.text for b in page.blocks] == [para, "short two."]


def test_tiny_block_with_different_heading_path_stays():
    page = seg("<h2>A</h2><p>short.</p><h2>B</h2><p>also short.</p>")
    assert len(page.blocks) == 2


def test_layout_tags_removed_unless_kept():
    html = (
        "<nav>Menu item</nav><p>Body text here that is long enough to matter.</p>"
        "<footer>Foot</footer>"
    )
    assert "Menu" not in seg(html).text and "Foot" not in seg(html).text
    kept = seg(html, keep_layout_tags=True).text
    assert "Menu item" in kept and "Foot" in kept


def test_scripts_styles_and_hidden_content_are_dropped():
    page = seg(
        "<script>var x = 1;</script><style>p{}</style><noscript>enable it</noscript>"
        "<div hidden>secret</div><p style='display:none'>also secret</p><p>Visible paragraph.</p>"
    )
    assert page.text == "Visible paragraph."


def test_table_rows_join_cells_with_pipe_and_lists_become_lines():
    page = seg(
        "<table><tr><th>Plan</th><th>Price</th></tr><tr><td>Basic</td><td>$5</td></tr></table>"
        "<ul><li>one</li><li>two <ul><li>nested</li></ul></li></ul>"
    )
    assert "Plan | Price\nBasic | $5\none\ntwo\nnested" in page.text


def test_pre_keeps_line_breaks():
    assert "line 1\nline 2" in seg("<pre>line 1\n   line 2</pre>").text


def test_link_chars_counts_anchor_text():
    page = seg("<p><a href='/a'>click here</a> and some plain words</p>")
    assert page.blocks[0].link_chars == len("click here")


def test_cookie_banner_does_not_join_the_last_section():
    page = segment_html(read("help/returns.html"), BASE + "/help/returns")
    last = page.blocks[-1]
    assert last.text.startswith("We use cookies") and last.heading_path == ""
    assert "cookies" not in page.blocks[-2].text


def test_links_resolved_canonical_and_attached_to_blocks():
    page = segment_html(read("index.html"), BASE + "/")
    urls = {link.text: link.url for link in page.links}
    assert urls["Returns & refunds"] == BASE + "/help/returns"
    assert all("#" not in link.url for link in page.links)
    assert [link.position for link in page.links] == list(range(len(page.links)))
    link = next(link for link in page.links if link.text == "Shipping")
    assert link.context.startswith("Help Center > Popular topics: Shipping")
    assert link.block_id in {b.id for b in page.blocks}


def test_block_token_estimate():
    page = seg("<p>" + "x" * 70 + "</p>")
    assert page.blocks[0].tokens_est == estimate_tokens("x" * 70) == 34  # 70 / 2.1


NAV_PAGE = """<html lang="en"><body>
<nav><ul>
  <li><a href="/login">Log in</a></li>
  <li><a href="/docs/auth">Authentication</a>
    <ul><li><a href="/docs/auth/tokens">Personal access tokens</a></li></ul></li>
  <li><a href="/docs/billing">Billing</a></li>
</ul></nav>
<main><h1>Docs</h1>
<p>Welcome to the docs. Pick a topic in the menu or read <a href="/docs/intro">the intro</a>.</p>
</main>
<footer><a href="/privacy">Privacy</a></footer>
</body></html>"""


def test_menu_links_are_candidates_but_never_text_blocks():
    page = segment_html(NAV_PAGE, "https://t.example/docs")
    assert "Personal access tokens" not in page.text and "Billing" not in page.text
    by_text = {link.text: link for link in page.links}
    assert page.links[0].text == "the intro"  # in-content links come first
    assert by_text["the intro"].block_id is not None
    tokens = by_text["Personal access tokens"]
    assert tokens.url == "https://t.example/docs/auth/tokens" and tokens.block_id is None
    assert tokens.context == "Site navigation: Authentication"  # the menu path, not page text
    assert by_text["Billing"].context == "Site navigation"
    assert {"Log in", "Privacy"} <= set(by_text)  # footer and menu entries are candidates too
    assert [link.position for link in page.links] == list(range(len(page.links)))
    assert len({link.id for link in page.links}) == len(page.links)


def test_menu_links_rank_behind_in_content_links_under_the_cap():
    from jevcrawl.segment.links import cap_links

    page = segment_html(NAV_PAGE, "https://t.example/docs")
    surviving = {b.id for b in page.blocks}
    kept = cap_links(page.links, 1, surviving)
    assert [link.text for link in kept] == ["the intro"]


def test_keep_layout_tags_does_not_duplicate_menu_links():
    page = segment_html(NAV_PAGE, "https://t.example/docs", keep_layout_tags=True)
    urls = [link.url for link in page.links]
    assert len(urls) == len(set(urls))
    assert "Billing" in page.text  # the menu is content when layout tags are kept


def test_heading_permalink_signs_are_dropped():
    page = seg("<h2>Assignment expressions <a class='headerlink' href='#x'>¶</a></h2>"
               "<p>There is new syntax := that assigns values to variables.</p>")  # fmt: skip
    assert page.blocks[0].heading_path == "Assignment expressions"

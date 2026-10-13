"""JS detection and language detection."""

from jevcrawl.segment.blocks import segment_html
from jevcrawl.segment.lang import detect_lang
from tests.fixtures.fake_site import BASE, read

LONG = "<p>" + "A reasonably long sentence about nothing in particular. " * 6 + "</p>"


def page(body: str):
    return segment_html(f"<html><body>{body}</body></html>", "https://x.example/")


def test_js_shell_fixture_needs_js():
    assert segment_html(read("app.html"), BASE + "/app").needs_js


def test_content_page_does_not_need_js():
    assert not segment_html(read("help/returns.html"), BASE + "/help/returns").needs_js
    assert not page(LONG).needs_js


def test_short_text_needs_js():
    assert page("<p>Hello</p>").needs_js


def test_empty_app_root_needs_js_even_with_other_text():
    for root in ("root", "__next", "app"):
        assert page(f"<div id='{root}'></div>{LONG}").needs_js


def test_filled_app_root_is_fine():
    assert not page(f"<div id='root'>{LONG}</div>").needs_js


def test_noscript_enable_javascript_needs_js():
    assert page(f"{LONG}<noscript>Please enable JavaScript to continue.</noscript>").needs_js


def test_lang_from_html_attribute_reduced_to_primary_subtag():
    assert detect_lang("ar-TN", "hello") == "ar"
    assert detect_lang("EN_us", "") == "en"


def test_lang_arabic_by_script_share():
    assert detect_lang(None, "مرحبا بكم في مركز المساعدة لدينا") == "ar"
    assert detect_lang(None, "hello world and more مرحبا") == "und"  # about 23% Arabic
    assert detect_lang(None, "") == "und"
    assert detect_lang("", "plain english text") == "und"

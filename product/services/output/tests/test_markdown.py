"""Markdown renderer tests. Primary security property: HTML is escaped FIRST, so
model output can never inject markup (no XSS). Plus the supported formatting."""

from __future__ import annotations

import pytest

from app.markdown import render_markdown

# --------------------------------------------------------------------------- #
# Security: no XSS. The renderer must never emit an executable/live tag from
# user/model content — everything is escaped before our fixed tags are added.
# --------------------------------------------------------------------------- #


def test_script_tag_is_escaped_not_emitted():
    html = render_markdown("<script>alert('xss')</script>")
    assert "<script>" not in html.lower()
    assert "&lt;script&gt;" in html
    assert "&lt;/script&gt;" in html


def test_img_onerror_is_neutralized():
    html = render_markdown("look: <img src=x onerror=alert(1)>")
    assert "<img" not in html.lower()
    assert "&lt;img" in html


def test_event_handler_and_quotes_escaped():
    html = render_markdown('<a href="javascript:alert(1)" onclick=\'x\'>hi</a>')
    assert "<a " not in html.lower()
    assert "&lt;a" in html
    assert "&quot;" in html
    assert "&#39;" in html
    # No raw attribute-bearing anchor survived.
    assert "href=" not in html or "&quot;" in html


def test_html_inside_bold_is_escaped_but_still_bolded():
    html = render_markdown("**<b>not bold html</b>**")
    assert "<strong>" in html
    assert "&lt;b&gt;" in html
    assert "<b>" not in html.lower().replace("<strong>", "")


def test_html_inside_inline_code_is_escaped():
    html = render_markdown("use `<script>evil</script>` carefully")
    assert "<code>" in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html
    assert "<script>" not in html.lower()


def test_html_inside_code_block_is_escaped():
    md = "```\n<script>alert(1)</script>\n```"
    html = render_markdown(md)
    assert "<pre><code>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html.lower()


def test_ampersand_escaped_once():
    html = render_markdown("Tom & Jerry & Co")
    assert html.count("&amp;") == 2
    assert "&amp;amp;" not in html


# --------------------------------------------------------------------------- #
# Formatting: the supported markdown subset renders as expected.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_headings(level):
    html = render_markdown("#" * level + " Heading")
    assert f"<h{level}>Heading</h{level}>" in html


def test_bold_and_italic():
    html = render_markdown("this is **bold** and *italic* and _also italic_")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<em>also italic</em>" in html


def test_bold_underscore_form():
    html = render_markdown("this is __very bold__ text")
    assert "<strong>very bold</strong>" in html


def test_snake_case_not_italicized():
    html = render_markdown("call get_user_id and set_turn_id now")
    assert "<em>" not in html
    assert "get_user_id" in html
    assert "set_turn_id" in html


def test_inline_code():
    html = render_markdown("run `pytest -q` please")
    assert "<code>pytest -q</code>" in html


def test_code_span_protects_markers():
    # Asterisks inside inline code must not become emphasis.
    html = render_markdown("literal `a*b*c` stays")
    assert "<code>a*b*c</code>" in html
    assert "<em>" not in html


def test_unordered_list():
    html = render_markdown("- alpha\n- beta\n- gamma")
    assert html.count("<li>") == 3
    assert "<ul>" in html and "</ul>" in html
    assert "<li>alpha</li>" in html


def test_ordered_list():
    html = render_markdown("1. first\n2. second")
    assert "<ol>" in html and "</ol>" in html
    assert "<li>first</li>" in html
    assert "<li>second</li>" in html


def test_paragraphs_separated_by_blank_line():
    html = render_markdown("para one\nstill one\n\npara two")
    assert html.count("<p>") == 2
    assert "<p>para one still one</p>" in html
    assert "<p>para two</p>" in html


def test_code_block_preserves_newlines_and_content():
    md = "```python\nx = 1\ny = 2\n```"
    html = render_markdown(md)
    assert "<pre><code>x = 1\ny = 2</code></pre>" in html


def test_mixed_document_blocks():
    md = "# Title\n\nintro text\n\n- item `code`\n- **bold item**\n\n```\nraw\n```"
    html = render_markdown(md)
    assert "<h1>Title</h1>" in html
    assert "<p>intro text</p>" in html
    assert "<ul>" in html
    assert "<code>code</code>" in html
    assert "<strong>bold item</strong>" in html
    assert "<pre><code>raw</code></pre>" in html


def test_empty_input():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""

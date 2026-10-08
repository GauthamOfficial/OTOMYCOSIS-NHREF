"""Validation report rendering. Runs without the model."""

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import report  # noqa: E402

EVALUATION = os.path.join(ROOT, "artifacts", "evaluation_report.json")


def test_markdown_subset_converts():
    html = report.markdown_to_html(
        "# Title\n\nSome **bold** and `code` <script>.\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        "- one\n- two\n  continued\n\n"
        "1. first\n2. second\n\n"
        "```\nraw <b>\n```\n"
    )
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    assert "&lt;script&gt;" in html, "text must be escaped"
    assert "<table>" in html and "<td>1</td>" in html
    assert "<ul><li>one</li><li>two continued</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert "raw &lt;b&gt;" in html


@pytest.mark.skipif(not os.path.isfile(EVALUATION), reason="run scripts/calibrate.py first")
def test_generated_report_is_complete_and_dash_free():
    with open(EVALUATION, encoding="utf-8") as fh:
        data = json.load(fh)
    markdown = report.render_report(data)
    for heading in ("## Summary", "## Intended use", "## Per condition performance",
                    "## Calibration", "## Known limitations"):
        assert heading in markdown
    em_dash, en_dash = chr(0x2014), chr(0x2013)
    assert em_dash not in markdown and en_dash not in markdown
    page = report.render_html_page(markdown)
    assert page.startswith("<!DOCTYPE html>") and "<table>" in page

"""Pure-logic tests for Anthropic content-block text extraction.

Regression coverage for the Opus 5 crash: newer models return a leading
'thinking' block, which older SDK versions mis-parse as a TextBlock with
text=None. Naively reading content[0].text returned None and crashed on
`.strip()` downstream. `_text_from_anthropic_content` must skip non-text
blocks and return the real answer text.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.claude import _text_from_anthropic_content


def _block(**kwargs):
    return SimpleNamespace(**kwargs)


def test_plain_text_block():
    content = [_block(type="text", text="hello")]
    assert _text_from_anthropic_content(content) == "hello"


def test_skips_leading_thinking_block_opus5_shape():
    # Exact shape old SDK 0.40.0 produces for an Opus 5 thinking block.
    content = [
        _block(type="thinking", text=None, thinking="", signature="abc"),
        _block(type="text", text='{"type":"question","message":"hi"}'),
    ]
    assert _text_from_anthropic_content(content) == '{"type":"question","message":"hi"}'


def test_joins_multiple_text_blocks():
    content = [
        _block(type="text", text="part one "),
        _block(type="text", text="part two"),
    ]
    assert _text_from_anthropic_content(content) == "part one part two"


def test_only_thinking_returns_empty_string():
    # Empty result signals the caller to raise a clean AIServiceError.
    content = [_block(type="thinking", text=None, thinking="reasoning")]
    assert _text_from_anthropic_content(content) == ""


def test_ignores_empty_text_blocks():
    content = [
        _block(type="text", text=""),
        _block(type="text", text="real"),
    ]
    assert _text_from_anthropic_content(content) == "real"

"""Tests for rag._chunk_text."""

import rag


def test_chunk_text_terminates_and_covers_text():
    for n in (1, 99, 100, 500, 501, 1200, 5000):
        text = "x" * n
        chunks = rag._chunk_text(text, chunk_size=500, overlap=100)
        assert chunks, n
        assert len(chunks) <= n // 400 + 1, n
        assert text.endswith(chunks[-1]), n


def test_chunk_text_empty():
    assert rag._chunk_text("") == []

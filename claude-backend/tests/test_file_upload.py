"""Tests for file_upload original-file retention."""

import file_upload


def test_upload_keeps_original_and_lists_path(tmp_path, monkeypatch):
    monkeypatch.setattr(file_upload, "STORAGE_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(file_upload, "ORIGINALS_DIR", str(tmp_path / "orig"))
    doc_id = file_upload.process_uploaded_file(b"hello plan", "plan.txt")
    path = file_upload.list_documents()[0]["original_path"]
    assert path == str(tmp_path / "orig" / f"{doc_id[:8]}-plan.txt")
    assert open(path, "rb").read() == b"hello plan"
    assert f"Original file: {path}" in file_upload.get_document_context()
    file_upload.delete_document(doc_id)
    assert open(path, "rb").read() == b"hello plan"

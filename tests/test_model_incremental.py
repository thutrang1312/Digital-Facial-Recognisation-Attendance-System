import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import model


def make_fake_emb(dim=128, val=0.1):
    return np.full((dim,), float(val), dtype=float)


def test_add_new_student_creates_db(tmp_path, monkeypatch):
    # Redirect file paths to tmp
    monkeypatch.setattr(model, 'EMBED_DB', str(tmp_path / 'emb.npz'))
    monkeypatch.setattr(model, 'MODEL_PATH', str(tmp_path / 'face.pkl'))
    monkeypatch.setattr(model, 'CACHE_PATH', str(tmp_path / 'cache.pkl'))

    # Patch extract_embedding to return a stable embedding
    emb = make_fake_emb(128, 0.12)
    monkeypatch.setattr(model, 'extract_embedding', lambda img: emb)

    ok, msg = model.add_embeddings_for_student(1234, [None])
    assert ok is True, f"Expected success, got: {msg}"

    X, y = model.load_embeddings_db()
    assert X is not None and y is not None
    assert any(str(1234) == label for label in y)


def test_reject_embedding_too_similar_to_other(tmp_path, monkeypatch):
    # Prepare existing DB with one student (id 1)
    X_old = np.vstack([make_fake_emb(128, 0.2)])
    y_old = np.array(["1"]) 

    db_path = tmp_path / 'emb.npz'
    np.savez(db_path, X=X_old, y=y_old)

    # Redirect paths
    monkeypatch.setattr(model, 'EMBED_DB', str(db_path))
    monkeypatch.setattr(model, 'MODEL_PATH', str(tmp_path / 'face.pkl'))
    monkeypatch.setattr(model, 'CACHE_PATH', str(tmp_path / 'cache.pkl'))

    # New embedding is very close to existing student 1 -> should be rejected for new student 2
    close_emb = make_fake_emb(128, 0.205)
    monkeypatch.setattr(model, 'extract_embedding', lambda img: close_emb)

    ok, msg = model.add_embeddings_for_student(2, [None])
    assert ok is False, "Expected rejection due to similarity to other student"
    assert isinstance(msg, str) and len(msg) > 0

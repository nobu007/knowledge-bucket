"""Tests for core module: ULID generation, shard path, directory structure."""

import os
import tempfile

from kb.core import (
    CONCEPT_DIR,
    CONFIG_DIR,
    DOC_DIR,
    RECORDS_DIR,
    ensure_dirs,
    generate_ulid,
    kb_root,
    shard_path,
)


class TestUlid:
    def test_length(self):
        assert len(generate_ulid()) == 26

    def test_sortable(self):
        ids = [generate_ulid() for _ in range(100)]
        assert ids == sorted(ids)

    def test_unique(self):
        ids = {generate_ulid() for _ in range(100)}
        assert len(ids) == 100

    def test_charset(self):
        valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        ulid = generate_ulid()
        assert all(c in valid for c in ulid)


class TestShardPath:
    def test_format(self):
        path = shard_path("01KTG21J7Q2CV8KVSCBHTB17BX")
        parts = path.split(os.sep)
        assert len(parts) == 3  # ab/cd/ulid.md
        assert len(parts[0]) == 2
        assert len(parts[1]) == 2
        assert parts[2] == "01KTG21J7Q2CV8KVSCBHTB17BX.md"

    def test_deterministic(self):
        ulid = "01KTG21J7Q2CV8KVSCBHTB17BX"
        assert shard_path(ulid) == shard_path(ulid)

    def test_hex_chars(self):
        path = shard_path("01KTG21J7Q2CV8KVSCBHTB17BX")
        parts = path.split(os.sep)
        hex_chars = set("0123456789abcdef")
        assert all(c in hex_chars for c in parts[0])
        assert all(c in hex_chars for c in parts[1])


class TestEnsureDirs:
    def test_creates_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            assert os.path.isdir(os.path.join(tmp, RECORDS_DIR, DOC_DIR))
            assert os.path.isdir(os.path.join(tmp, RECORDS_DIR, CONCEPT_DIR))
            assert os.path.isdir(os.path.join(tmp, CONFIG_DIR))
            assert os.path.isdir(os.path.join(tmp, "inbox"))


class TestKbRoot:
    def test_finds_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, CONFIG_DIR))
            with open(os.path.join(tmp, CONFIG_DIR, "kb.yml"), "w") as f:
                f.write("test: true\n")
            original = os.getcwd()
            os.chdir(tmp)
            try:
                assert kb_root() == os.path.realpath(tmp)
            finally:
                os.chdir(original)

    def test_returns_none_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                assert kb_root() is None
            finally:
                os.chdir(original)

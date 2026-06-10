"""Tests for raw data storage (storage.py) and CLI integration."""

import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from kb.cli import main
from kb.storage import (
    LocalStorage,
    S3Storage,
    get_raw,
    get_storage_backend,
    save_raw,
)

# ---------------------------------------------------------------------------
# LocalStorage unit tests
# ---------------------------------------------------------------------------


class TestLocalStorage:
    def test_upload_creates_file(self, tmp_path):
        backend = LocalStorage(str(tmp_path))
        ref = backend.upload("abc123", b"hello raw")
        assert ref == "local://abc123"

        stored = (tmp_path / "abc123").read_bytes()
        assert stored == b"hello raw"

    def test_download_roundtrip(self, tmp_path):
        backend = LocalStorage(str(tmp_path))
        backend.upload("doc1", b"content bytes")
        data = backend.download("local://doc1")
        assert data == b"content bytes"

    def test_download_invalid_ref(self, tmp_path):
        backend = LocalStorage(str(tmp_path))
        with pytest.raises(ValueError, match="Invalid local reference"):
            backend.download("s3://bucket/key")

    def test_upload_creates_subdirs(self, tmp_path):
        sub = tmp_path / "sub" / "dir"
        backend = LocalStorage(str(sub))
        backend.upload("x", b"data")
        assert (sub / "x").read_bytes() == b"data"

    def test_download_missing_file(self, tmp_path):
        backend = LocalStorage(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            backend.download("local://nonexistent")


# ---------------------------------------------------------------------------
# S3Storage unit tests (boto3 mocked)
# ---------------------------------------------------------------------------


class TestS3Storage:
    def test_upload_returns_s3_uri(self):
        backend = S3Storage(
            endpoint_url="https://r2.example.com",
            bucket="my-bucket",
            prefix="raw/",
        )
        mock_client = MagicMock()
        mock_client.put_object.return_value = {}
        with patch.object(backend, "_client", return_value=mock_client):
            ref = backend.upload("doc42", b"raw bytes")

        assert ref == "s3://my-bucket/raw/doc42"
        mock_client.put_object.assert_called_once_with(
            Bucket="my-bucket", Key="raw/doc42", Body=b"raw bytes",
        )

    def test_download(self):
        backend = S3Storage(bucket="my-bucket")
        mock_body = MagicMock()
        mock_body.read.return_value = b"fetched data"
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": mock_body}
        with patch.object(backend, "_client", return_value=mock_client):
            data = backend.download("s3://my-bucket/raw/doc42")

        assert data == b"fetched data"
        mock_client.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="raw/doc42",
        )

    def test_download_invalid_ref(self):
        backend = S3Storage()
        with pytest.raises(ValueError, match="Invalid S3 reference"):
            backend.download("local://x")

    def test_custom_endpoint(self):
        backend = S3Storage(endpoint_url="https://r2.cloudflare.com", bucket="b")
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            backend._client()
            mock_boto3.client.assert_called_once_with(
                "s3",
                region_name="auto",
                endpoint_url="https://r2.cloudflare.com",
            )


# ---------------------------------------------------------------------------
# get_storage_backend tests
# ---------------------------------------------------------------------------


class TestGetStorageBackend:
    def test_returns_none_when_no_config(self, tmp_path):
        result = get_storage_backend(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_storage_key(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "kb.yml").write_text(yaml.dump({"records_dir": "records"}))
        result = get_storage_backend(str(tmp_path))
        assert result is None

    def test_returns_s3_when_configured(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "kb.yml").write_text(yaml.dump({
            "storage": {
                "backend": "s3",
                "bucket": "test-bucket",
                "endpoint_url": "https://r2.example.com",
            },
        }))
        result = get_storage_backend(str(tmp_path))
        assert isinstance(result, S3Storage)
        assert result.bucket == "test-bucket"

    def test_returns_local_when_configured(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "kb.yml").write_text(yaml.dump({
            "storage": {"backend": "local"},
        }))
        result = get_storage_backend(str(tmp_path))
        assert isinstance(result, LocalStorage)

    def test_empty_storage_config_returns_none(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "kb.yml").write_text(yaml.dump({
            "storage": {},
        }))
        result = get_storage_backend(str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# save_raw / get_raw integration tests
# ---------------------------------------------------------------------------


class TestSaveAndGetRaw:
    def test_save_raw_default_local(self, tmp_path):
        ref = save_raw(str(tmp_path), "doc1", b"hello world")
        assert ref.startswith("local://")
        data = get_raw(str(tmp_path), ref)
        assert data == b"hello world"

    def test_save_raw_with_s3_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "kb.yml").write_text(yaml.dump({
            "storage": {
                "backend": "s3",
                "bucket": "kb-raw",
                "endpoint_url": "https://r2.example.com",
            },
        }))
        backend = get_storage_backend(str(tmp_path))
        with patch.object(backend, "upload", return_value="s3://kb-raw/raw/doc1"):
            with patch("kb.storage.get_storage_backend", return_value=backend):
                ref = save_raw(str(tmp_path), "doc1", b"data")
        assert ref.startswith("s3://")

    def test_get_raw_unknown_scheme(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown raw reference scheme"):
            get_raw(str(tmp_path), "ftp://bad")

    def test_get_raw_s3_no_config(self, tmp_path):
        with pytest.raises(RuntimeError, match="no storage backend"):
            get_raw(str(tmp_path), "s3://bucket/key")


# ---------------------------------------------------------------------------
# CLI integration: kb add --save-raw and kb raw
# ---------------------------------------------------------------------------


class TestCLIRaw:
    def test_add_and_retrieve_raw(self, tmp_path):
        """Full round-trip: init, add --save-raw, then kb raw."""
        from click.testing import CliRunner

        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            # init
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 0

            # add with --save-raw
            result = runner.invoke(main, [
                "add",
                "--title", "Raw Doc",
                "--content", "raw content here",
                "--save-raw",
            ])
            assert result.exit_code == 0
            assert "Added:" in result.output
            doc_id = result.output.split("Added:")[1].strip().split()[0]

            # Verify raw_ref in front matter
            doc_dir = os.path.join("records", "doc")
            found = None
            for dp, _, fns in os.walk(doc_dir):
                for fn in fns:
                    if fn == f"{doc_id}.md":
                        found = os.path.join(dp, fn)
                        break
            assert found is not None
            text = open(found).read()
            assert "raw_ref: local://" in text

            # kb raw <doc_id>
            result = runner.invoke(main, ["raw", doc_id])
            assert result.exit_code == 0
            assert "raw content here" in result.output

    def test_raw_no_doc(self, tmp_path):
        from click.testing import CliRunner

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, ["raw", "nonexistent"])
            assert result.exit_code == 1
            assert "Document not found" in result.output

    def test_raw_no_raw_ref(self, tmp_path):
        from click.testing import CliRunner

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(main, ["init", "."])
            # Add without --save-raw
            result = runner.invoke(main, [
                "add",
                "--title", "No Raw",
                "--content", "normal content",
            ])
            assert result.exit_code == 0
            doc_id = result.output.split("Added:")[1].strip().split()[0]

            result = runner.invoke(main, ["raw", doc_id])
            assert result.exit_code == 1
            assert "No raw data stored" in result.output

    def test_add_save_raw_empty_content(self, tmp_path):
        """--save-raw with no content should not create raw_ref."""
        from click.testing import CliRunner

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add",
                "--title", "Empty",
                "--save-raw",
            ])
            assert result.exit_code == 0
            doc_id = result.output.split("Added:")[1].strip().split()[0]

            # No raw_ref in front matter since content was empty
            doc_dir = os.path.join("records", "doc")
            found = None
            for dp, _, fns in os.walk(doc_dir):
                for fn in fns:
                    if fn == f"{doc_id}.md":
                        found = os.path.join(dp, fn)
                        break
            assert found is not None
            text = open(found).read()
            assert "raw_ref" not in text

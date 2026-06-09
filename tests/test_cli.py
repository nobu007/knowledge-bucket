"""Tests for CLI: kb init, kb add, kb show, kb concept."""

import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kb.cli import main


class TestInit:
    def test_init_creates_structure(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 0
            assert os.path.exists(os.path.join("config", "kb.yml"))
            assert os.path.isdir("records")
            assert os.path.isdir("inbox")

    def test_init_idempotent_fails(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 1


class TestAdd:
    def test_add_creates_document(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add", "--title", "Test", "--source", "https://example.com",
                "--content", "Hello world",
            ])
            assert result.exit_code == 0
            assert "Added:" in result.output

            # Find the created file
            doc_dir = os.path.join("records", "doc")
            files = []
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    files.append(os.path.join(root, fn))
            assert len(files) == 1

            with open(files[0]) as f:
                content = f.read()
            assert "id: " in content
            assert "title: Test" in content
            assert "Hello world" in content

    def test_add_requires_init(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "add", "--title", "Test", "--content", "x",
            ])
            assert result.exit_code == 1
            assert "Not in a knowledge bucket" in result.output

    def test_add_stdin_content(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add", "--title", "Stdin Test",
            ], input="Content from stdin\n")
            assert result.exit_code == 0

    def test_add_source_key_in_front_matter(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add", "--title", "Test", "--source", "https://example.com",
                "--content", "Hello world",
            ])
            assert result.exit_code == 0

            doc_dir = os.path.join("records", "doc")
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    with open(os.path.join(root, fn)) as f:
                        content = f.read()
                    assert "source_key: url:" in content
                    return
            pytest.fail("No document file found")

    def test_add_memo_source_key(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add", "--title", "My Memo", "--type", "memo",
                "--content", "Some notes",
            ])
            assert result.exit_code == 0

            doc_dir = os.path.join("records", "doc")
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    with open(os.path.join(root, fn)) as f:
                        content = f.read()
                    assert "source_key: memo:" in content
                    return
            pytest.fail("No document file found")


class TestShow:
    def _add_doc(self, runner, title="Test Doc", content="Hello world"):
        result = runner.invoke(main, [
            "add", "--title", title, "--source", "https://example.com",
            "--content", content,
        ])
        # Extract doc_id from output like "Added: 01K2Z9P7Y8QWERTY1234567890"
        for line in result.output.splitlines():
            if line.startswith("Added:"):
                return line.split(":")[1].strip()
        return None

    def test_show_displays_metadata(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            doc_id = self._add_doc(runner, title="My Title", content="Body text here")
            assert doc_id is not None

            result = runner.invoke(main, ["show", doc_id])
            assert result.exit_code == 0
            assert f"ID:          {doc_id}" in result.output
            assert "Title:       My Title" in result.output
            assert "Source type: web" in result.output
            assert "Source:      https://example.com" in result.output
            assert "Body text here" in result.output

    def test_show_not_found(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, ["show", "nonexistent_id"])
            assert result.exit_code == 1
            assert "Document not found" in result.output

    def test_show_requires_init(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["show", "some_id"])
            assert result.exit_code == 1
            assert "Not in a knowledge bucket" in result.output

    def test_show_truncates_long_body(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            long_content = "\n".join(f"Line {i}" for i in range(30))
            doc_id = self._add_doc(runner, content=long_content)
            result = runner.invoke(main, ["show", doc_id])
            assert result.exit_code == 0
            assert "more lines, use --full to show" in result.output

    def test_show_full_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            long_content = "\n".join(f"Line {i}" for i in range(30))
            doc_id = self._add_doc(runner, content=long_content)
            result = runner.invoke(main, ["show", doc_id, "--full"])
            assert result.exit_code == 0
            assert "more lines" not in result.output
            assert "Line 29" in result.output

    def test_show_with_concepts(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, [
                "add", "--title", "Tagged Doc", "--content", "text",
                "--concepts", "rust, systems-programming",
            ])
            doc_id = None
            for line in result.output.splitlines():
                if line.startswith("Added:"):
                    doc_id = line.split(":")[1].strip()

            result = runner.invoke(main, ["show", doc_id])
            assert result.exit_code == 0
            assert "Concepts:" in result.output
            assert "rust" in result.output


class TestConcept:
    def _setup_with_graph(self, runner):
        """Init bucket, add doc with concepts, build graph."""
        runner.invoke(main, ["init", "."])
        result = runner.invoke(main, [
            "add", "--title", "Rust Guide", "--source", "https://example.com/rust",
            "--content", "Rust is a systems programming language.",
            "--concepts", "rust, systems-programming",
        ])
        doc_id = None
        for line in result.output.splitlines():
            if line.startswith("Added:"):
                doc_id = line.split(":")[1].strip()

        # Build index and graph
        runner.invoke(main, ["index", "--rebuild"])
        runner.invoke(main, ["graph", "build"])
        return doc_id

    def test_concept_displays_metadata(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup_with_graph(runner)
            result = runner.invoke(main, ["concept", "rust"])
            assert result.exit_code == 0
            assert "ID:       rust" in result.output
            assert "Label:    rust" in result.output
            assert "Kind:     concept" in result.output
            assert "DF:       1" in result.output

    def test_concept_shows_documents(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup_with_graph(runner)
            result = runner.invoke(main, ["concept", "rust"])
            assert result.exit_code == 0
            assert "Documents" in result.output
            assert "Rust Guide" in result.output

    def test_concept_not_found(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup_with_graph(runner)
            result = runner.invoke(main, ["concept", "nonexistent"])
            assert result.exit_code == 1
            assert "Concept not found" in result.output

    def test_concept_requires_init(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["concept", "rust"])
            assert result.exit_code == 1
            assert "Not in a knowledge bucket" in result.output

    def test_concept_shows_note_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._setup_with_graph(runner)
            # Create a concept note
            os.makedirs(os.path.join("records", "concept"), exist_ok=True)
            with open(os.path.join("records", "concept", "rust.md"), "w") as f:
                f.write("# Rust\n\nA systems programming language.\n")

            result = runner.invoke(main, ["concept", "rust"])
            assert result.exit_code == 0
            assert "Concept Note" in result.output
            assert "systems programming language" in result.output

    def test_concept_shows_cooccurring(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            # Add docs where rust+systems-programming co-occur in 2+ docs
            runner.invoke(main, [
                "add", "--title", "Rust Guide", "--source", "https://example.com/rust",
                "--content", "Rust is a systems programming language.",
                "--concepts", "rust, systems-programming",
            ])
            runner.invoke(main, [
                "add", "--title", "Rust Patterns", "--source", "https://example.com/patterns",
                "--content", "Design patterns in Rust.",
                "--concepts", "rust, systems-programming, design-patterns",
            ])
            runner.invoke(main, [
                "add", "--title", "Rust Memory", "--source", "https://example.com/memory",
                "--content", "Memory management in Rust.",
                "--concepts", "rust, systems-programming",
            ])
            # Build index and graph (which builds co-occurrence edges)
            runner.invoke(main, ["index", "--rebuild"])
            runner.invoke(main, ["graph", "build"])

            result = runner.invoke(main, ["concept", "rust"])
            assert result.exit_code == 0
            assert "Co-occurring concepts" in result.output


class TestAddPaperSourceKey:
    @patch("kb.parsers.paper.parse_paper")
    def test_source_key_in_front_matter(self, mock_parse):
        mock_parse.return_value = {
            "title": "Test Paper",
            "source_type": "paper",
            "source_url": "https://arxiv.org/abs/2301.12345",
            "body": "Abstract text",
            "metadata": {"authors": ["Alice"], "arxiv_id": "2301.12345"},
        }
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, ["add-paper", "2301.12345"])
            assert result.exit_code == 0

            doc_dir = os.path.join("records", "doc")
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    with open(os.path.join(root, fn)) as f:
                        content = f.read()
                    assert "source_key: arxiv:" in content
                    return
            pytest.fail("No document file found")


class TestAddPdfSourceKey:
    @patch("kb.parsers.pdf.parse_pdf")
    def test_source_key_in_front_matter(self, mock_parse):
        mock_parse.return_value = {
            "title": "Test PDF",
            "source_type": "pdf",
            "source_url": "https://s3.example.com/doc.pdf",
            "body": "Extracted text",
            "metadata": {"page_count": 5, "author": "Bob"},
        }
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            # Create a dummy PDF file
            with open("test.pdf", "w") as f:
                f.write("fake pdf")
            result = runner.invoke(main, ["add-pdf", "test.pdf"])
            assert result.exit_code == 0

            doc_dir = os.path.join("records", "doc")
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    with open(os.path.join(root, fn)) as f:
                        content = f.read()
                    assert "source_key: url:" in content
                    return
            pytest.fail("No document file found")


class TestAddRepoSourceKey:
    @patch("kb.parsers.repo.parse_repo")
    def test_source_key_in_front_matter(self, mock_parse):
        mock_parse.return_value = {
            "title": "user/repo",
            "source_type": "git_repo",
            "source_url": "https://github.com/user/repo",
            "body": "# README\nHello",
            "metadata": {"language": "Python", "stars": 42, "topics": []},
        }
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "."])
            result = runner.invoke(main, ["add-repo", "https://github.com/user/repo"])
            assert result.exit_code == 0

            doc_dir = os.path.join("records", "doc")
            for root, dirs, filenames in os.walk(doc_dir):
                for fn in filenames:
                    with open(os.path.join(root, fn)) as f:
                        content = f.read()
                    assert "source_key: repo:" in content
                    return
            pytest.fail("No document file found")

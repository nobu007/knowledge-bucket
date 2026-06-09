"""Tests for CLI: kb init, kb add, kb show."""

import os

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

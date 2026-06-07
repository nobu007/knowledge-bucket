"""Tests for CLI: kb init, kb add."""

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

"""Smoke tests verifying the installed package works end-to-end."""

import os
import subprocess
import sys

from click.testing import CliRunner

from kb.analyzer import load_base_prompt, load_prompt, prompts_dir
from kb.cli import main


class TestCLISmoke:
    """Full pipeline smoke test using the CLI entry point."""

    def test_init_add_index_search(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            # init
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 0, result.output
            assert os.path.exists(os.path.join("config", "kb.yml"))

            # add
            result = runner.invoke(main, [
                "add", "--title", "Install Test", "--source", "https://example.com",
                "--content", "Smoke test content for install verification.",
            ])
            assert result.exit_code == 0, result.output
            assert "Added:" in result.output

            # index
            result = runner.invoke(main, ["index", "--sync"])
            assert result.exit_code == 0, result.output

            # search
            result = runner.invoke(main, ["search", "Smoke test"])
            assert result.exit_code == 0, result.output
            assert "Install Test" in result.output

    def test_kb_entry_point_exists(self):
        """The `kb` console script should be importable and runnable."""
        result = subprocess.run(
            [sys.executable, "-m", "kb.cli", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "Usage" in result.stdout or "usage" in result.stdout.lower()


class TestPromptsFromPackage:
    """Verify bundled prompts load via importlib.resources (pip-install path)."""

    def test_prompts_dir_is_inside_package(self):
        pd = prompts_dir()
        assert "kb" in pd
        assert "prompts" in pd

    def test_all_prompt_files_load(self):
        base = load_base_prompt()
        assert "情報圧縮器" in base
        for stype in ("web", "paper", "repo", "pdf", "memo", "video"):
            text = load_prompt(stype)
            assert len(text) > 20, f"prompt for {stype} seems empty"

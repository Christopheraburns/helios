"""The README's quickstart (burndown 9.2). CI's quickstart job runs its blocks verbatim in a
fresh virtual environment; here, in the workspace, the README is checked to have them and
its Python block runs on the sample, so a change to the facade that breaks the README fails
the ordinary test run too."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def runner():
    spec = importlib.util.spec_from_file_location("run_quickstart", REPO / "scripts/run_quickstart.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_quickstart_installs_builds_the_sample_and_asks_in_python():
    (shell, commands), (language, _) = runner().blocks((REPO / "README.md").read_text())
    assert (shell, language) == ("sh", "python")
    assert commands.splitlines() == ["pip install helios-semantic", "helios init --sample"]


def test_the_python_block_answers_on_the_sample(sample):
    [(_, code)] = [b for b in runner().blocks((REPO / "README.md").read_text()) if b[0] == "python"]
    out = io.StringIO()
    cwd = os.getcwd()
    os.chdir(sample.config.parent)                    # where `helios init --sample` left helios.toml
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(code, "README.md quickstart", "exec"), {})
    finally:
        os.chdir(cwd)
    printed = out.getvalue()
    assert "metric store_sales_profit" in printed
    assert printed.count("'s_state'") == 9                        # the nine states with stores
    assert "WHERE\n  date_dim.d_year = 2002" in printed
    assert "invalid_semantic_query - unknown metric 'store_sales_profitt'" in printed


def test_a_marker_needs_a_runnable_block():
    import pytest

    blocks = runner().blocks
    assert blocks("text\n<!-- quickstart -->\n```sh\necho hi\n```\n") == [("sh", "echo hi\n")]
    with pytest.raises(ValueError, match="fenced code block"):
        blocks("<!-- quickstart -->\nnot a fence\n")
    with pytest.raises(ValueError, match="no runner"):
        blocks("<!-- quickstart -->\n```ruby\nputs 1\n```\n")

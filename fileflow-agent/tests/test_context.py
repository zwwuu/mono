"""Context tests: the job's variables only, path resolution."""

import pytest

from fileflow_agent.domain.integrity import Context


def test_render_variables_only():
    ctx = Context({"client": "acme", "region": "us"})
    assert ctx.values == {"client": "acme", "region": "us"}
    assert ctx.render("{client}-{region}") == "acme-us"


def test_render_empty_context_has_no_tokens():
    ctx = Context()
    assert ctx.values == {}
    for token in ("{year}", "{month}", "{day}", "{date}", "{time}", "{job}", "{category}"):
        with pytest.raises(ValueError, match="unknown token"):
            ctx.render(f"inbox/{token}.csv")


def test_render_unknown_placeholder_raises():
    ctx = Context({"client": "acme"})
    with pytest.raises(ValueError, match="unknown token"):
        ctx.render("inbox/{nope}.csv")


def test_render_extras_win_for_per_render_tokens():
    # A variable named "name" can exist in a Context (the parser
    # rejects it, but the renderer is dumb): the per-render extra
    # always wins, so it could never render -- that is why the
    # parser rejects it.
    ctx = Context({"name": "from-variables"})
    assert ctx.render("{name}", name="daily") == "daily"
    assert ctx.render("{name}", name="daily", ext=".csv", run_id="abc") == "daily"


def test_render_name_pattern():
    ctx = Context({"client": "acme"})
    assert (
        ctx.render_name("{client}_{name}{ext}", name="daily", ext=".csv", run_id="abc")
        == "acme_daily.csv"
    )
    assert ctx.render_name("{name}{ext}", name="daily", ext=".csv", run_id="abc") == "daily.csv"
    # A built-in token in a rename pattern fails loud, same as any
    # unknown token.
    with pytest.raises(ValueError, match="unknown token"):
        ctx.render_name("{date}_{name}{ext}", name="daily", ext=".csv", run_id="abc")


def test_render_name_sanitizes_unsafe_names():
    # safe_name strips any directory part before the unsafe-name
    # check runs, so a traversal in the pattern neutralizes to a
    # plain file name instead of raising.
    ctx = Context()
    assert ctx.render_name("../{name}{ext}", name="daily", ext=".csv", run_id="abc") == "daily.csv"
    assert ctx.render_name("{name}{ext}", name="a/b", ext=".csv", run_id="abc") == "b.csv"
    # A result that is still just ".." after sanitizing fails loud.
    with pytest.raises(ValueError, match="unsafe name"):
        ctx.render_name("{name}{ext}", name="..", ext="", run_id="abc")


def test_local_path_absolutizes_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = Context({"client": "acme"})
    assert ctx.local_path("staging") == str(tmp_path / "staging")
    assert ctx.local_path("staging/{client}") == str(tmp_path / "staging" / "acme")
    # Normalizes, and an absolute template stays absolute.
    assert ctx.local_path("a/../b") == str(tmp_path / "b")
    assert ctx.local_path("/abs/path") == "/abs/path"


def test_remote_path_normalizes_without_anchoring(tmp_path, monkeypatch):
    # Remote paths are relative to the transport root: the local
    # working directory must never appear in one.
    monkeypatch.chdir(tmp_path)
    ctx = Context({"client": "acme"})
    assert ctx.remote_path("reports/{client}") == "reports/acme"
    assert ctx.remote_path("a//b/./c") == "a/b/c"
    assert ctx.remote_path("/abs/path") == "/abs/path"
    assert str(tmp_path) not in ctx.remote_path("incoming/job")

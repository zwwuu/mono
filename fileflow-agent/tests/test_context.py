"""Context tests: one run's template values, path resolution."""

from datetime import datetime

import pytest

from fileflow_agent.domain.integrity import Context


def test_render_all_tokens_and_variables():
    ctx = Context("job1", "sales", {"client": "acme"}, date_format="%Y-%m-%d", time_format="%H:%M")
    now = datetime.now()
    assert ctx.render("{year}-{month}-{day}") == now.strftime("%Y-%m-%d")
    assert ctx.render("{date}") == now.strftime("%Y-%m-%d")
    assert ctx.render("{time}") == now.strftime("%H:%M")
    assert ctx.render("{job}") == "job1"
    assert ctx.render("{category}") == "sales"
    assert ctx.render("{client}") == "acme"
    assert ctx.render("{job}-{category}-{client}") == "job1-sales-acme"


def test_render_without_category_has_no_category_token():
    ctx = Context("job1")
    assert "category" not in ctx.values
    with pytest.raises(ValueError, match="unknown token"):
        ctx.render("{category}")


def test_render_unknown_placeholder_raises():
    ctx = Context("job1")
    with pytest.raises(ValueError, match="unknown token"):
        ctx.render("inbox/{nope}.csv")


def test_render_extras_win_for_per_render_tokens():
    ctx = Context("job1", variables={"name": "from-variables"})
    assert ctx.render("{name}", name="daily") == "daily"
    assert ctx.render("{name}", name="daily", ext=".csv", run_id="abc") == "daily"


def test_render_name_pattern():
    ctx = Context("job1")
    now = datetime.now()
    assert (
        ctx.render_name("{date}_{name}{ext}", name="daily", ext=".csv", run_id="abc")
        == f"{now.strftime('%Y%m%d')}_daily.csv"
    )
    # The rename pattern shares the job's token set, not a smaller one.
    assert (
        ctx.render_name("{year}-{name}{ext}", name="daily", ext=".csv", run_id="abc")
        == f"{now.year:04d}-daily.csv"
    )


def test_render_name_sanitizes_unsafe_names():
    # safe_name strips any directory part before the unsafe-name
    # check runs, so a traversal in the pattern neutralizes to a
    # plain file name instead of raising.
    ctx = Context("job1")
    assert ctx.render_name("../{name}{ext}", name="daily", ext=".csv", run_id="abc") == "daily.csv"
    assert ctx.render_name("{name}{ext}", name="a/b", ext=".csv", run_id="abc") == "b.csv"
    # A result that is still just ".." after sanitizing fails loud.
    with pytest.raises(ValueError, match="unsafe name"):
        ctx.render_name("{name}{ext}", name="..", ext="", run_id="abc")


def test_local_path_absolutizes_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = Context("job1")
    now = datetime.now()
    assert ctx.local_path("staging") == str(tmp_path / "staging")
    assert ctx.local_path("staging/{year}{month}") == str(
        tmp_path / "staging" / now.strftime("%Y%m")
    )
    # Normalizes, and an absolute template stays absolute.
    assert ctx.local_path("a/../b") == str(tmp_path / "b")
    assert ctx.local_path("/abs/path") == "/abs/path"


def test_remote_path_normalizes_without_anchoring(tmp_path, monkeypatch):
    # Remote paths are relative to the transport root: the local
    # working directory must never appear in one.
    monkeypatch.chdir(tmp_path)
    ctx = Context("job1")
    now = datetime.now()
    assert ctx.remote_path("reports/{year}{month}") == f"reports/{now.strftime('%Y%m')}"
    assert ctx.remote_path("a//b/./c") == "a/b/c"
    assert ctx.remote_path("/abs/path") == "/abs/path"
    assert str(tmp_path) not in ctx.remote_path("incoming/job")


def test_context_built_on_a_different_date_renders_that_date():
    ctx = Context("job1", when=datetime(2020, 3, 5, 9, 30))
    assert ctx.render("{year}-{month}-{day}") == "2020-03-05"
    assert ctx.render("{date}") == "20200305"
    assert ctx.render("{time}") == "093000"


def test_context_values_match_template_context():
    from fileflow_agent.domain.integrity import template_context

    ctx = Context("job1", "sales", {"client": "acme"})
    assert ctx.values == template_context("job1", "sales", {"client": "acme"})

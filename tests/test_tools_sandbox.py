"""The sandbox is the only thing standing between a confused agent and the real filesystem."""

from __future__ import annotations

import pytest

from done.sandbox import SandboxViolation


def test_path_jail_rejects_parent_traversal(ctx):
    for bad in ["../escape.txt", "a/../../escape.txt", "./../../etc/hosts", "..", "../"]:
        with pytest.raises(SandboxViolation):
            ctx.sandbox.resolve(bad)


def test_path_jail_rejects_absolute_paths_outside(ctx):
    for bad in ["/etc/passwd", "/tmp/evil.txt", "/Users"]:
        with pytest.raises(SandboxViolation):
            ctx.sandbox.resolve(bad)


def test_path_jail_allows_absolute_paths_inside(ctx):
    inside = ctx.sandbox.root / "ok.txt"
    assert ctx.sandbox.resolve(str(inside)) == inside


def test_path_jail_rejects_a_symlink_pointing_out(ctx, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (ctx.sandbox.root / "link.txt").symlink_to(outside)
    with pytest.raises(SandboxViolation):
        ctx.sandbox.resolve("link.txt")


def test_path_jail_rejects_a_symlinked_directory_pointing_out(ctx, tmp_path):
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (ctx.sandbox.root / "escape").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(SandboxViolation):
        ctx.sandbox.resolve("escape/loot.txt")


def test_write_then_read_round_trips(ctx):
    ctx.sandbox.write_file("pkg/mod.py", "VALUE = 41\n")
    assert "VALUE = 41" in ctx.sandbox.read_file("pkg/mod.py")
    assert "pkg/mod.py" in ctx.sandbox.list_dir(".")


def test_reading_a_missing_file_is_a_message_not_a_crash(ctx):
    assert "no such file" in ctx.sandbox.read_file("nope.py")


async def test_run_shell_runs_in_the_workspace(ctx):
    ctx.sandbox.write_file("marker.txt", "hi")
    res = await ctx.sandbox.run_shell("ls")
    assert res.ok and "marker.txt" in res.stdout


async def test_run_shell_timeout_kills_the_command(ctx):
    res = await ctx.sandbox.run_shell("sleep 5", timeout=1)
    assert res.timed_out
    assert res.exit_code == 124
    assert not res.ok


async def test_run_shell_truncates_huge_output(ctx):
    res = await ctx.sandbox.run_shell("python3 -c \"print('x' * 200000)\"")
    assert len(res.stdout) <= ctx.cfg.OUTPUT_TRUNCATE


async def test_network_is_blocked(ctx):
    res = await ctx.sandbox.run_shell(
        "python3 -c \"import urllib.request as u; u.urlopen('http://example.com', timeout=5)\"",
        timeout=20,
    )
    assert not res.ok, "the sandbox must not reach the network"


async def test_write_file_is_refused_for_the_verifier(ctx, node_factory):
    from done import tools
    node = node_factory()
    out = await tools.dispatch("write_file", {"path": "x.py", "content": "y"}, node, ctx,
                               runner="verifier")
    assert "refused" in out
    assert not (ctx.sandbox.root / "x.py").exists()


async def test_exit_zero_means_pass_for_every_command_check(ctx, node_factory):
    """One rule, negative checks included: exit 0 = passed. The `negative` flag is descriptive.

    The runner used to invert negative checks, which double-negated the commands models
    actually write (`... && exit 1 || exit 0`) and failed correct code. See DECISIONS 14:10.
    """
    from done.checks import run_checks
    from done.models import Check
    node = node_factory()
    node.dod = [
        Check(id="p", description="true passes", kind="shell", command="true"),
        Check(id="f", description="false fails", kind="shell", command="false"),
        Check(id="n", description="negative: no secrets file is written", kind="shell",
              command="test ! -f secrets.txt", negative=True),
        Check(id="sn", description="negative written as a self-inverting command", kind="shell",
              command="test -f secrets.txt && exit 1 || exit 0", negative=True),
        Check(id="e", description="file must exist", kind="file_exists", path="README.md"),
    ]
    ctx.sandbox.write_file("README.md", "# hi\n")
    summary = await run_checks(node.dod, ctx)
    by_id = {r["id"]: r["passed"] for r in summary["results"]}
    assert by_id == {"p": True, "f": False, "n": True, "sn": True, "e": True}
    assert summary["passed"] == 4 and summary["total"] == 5


async def test_file_exists_is_the_one_kind_that_still_inverts(ctx, node_factory):
    """`file_exists` has no command to express an absence in, so the flag does the work."""
    from done.checks import run_checks
    from done.models import Check
    node = node_factory()
    node.dod = [
        Check(id="absent", description="no secrets file", kind="file_exists",
              path="secrets.txt", negative=True),
        Check(id="present", description="readme exists", kind="file_exists", path="README.md"),
    ]
    ctx.sandbox.write_file("README.md", "# hi\n")
    summary = await run_checks(node.dod, ctx)
    assert {r["id"]: r["passed"] for r in summary["results"]} == {"absent": True, "present": True}


async def test_a_check_that_escapes_the_sandbox_fails_closed(ctx, node_factory):
    from done.checks import run_checks
    from done.models import Check
    node = node_factory()
    node.dod = [Check(id="x", description="peek outside", kind="file_exists", path="/etc/passwd")]
    summary = await run_checks(node.dod, ctx)
    assert summary["passed"] == 0
    assert "sandbox refused" in (node.dod[0].output or "")

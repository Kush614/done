"""The monitor's deterministic half: it must catch loops without spending a token."""

from __future__ import annotations

from done import monitor


async def _steps(node, ctx, calls):
    last = "OK"
    for name, args, result in calls:
        last = await monitor.step(node, {"name": name, "args": args, "result": result}, ctx)
        if last != "OK":
            return last
    return last


async def test_exact_loop_caught_on_third_repeat(ctx, node_factory):
    node = node_factory()
    call = ("read_file", {"path": "a.py"}, "contents")
    assert await monitor.step(node, dict(zip(("name", "args", "result"), call)), ctx) == "OK"
    assert await monitor.step(node, dict(zip(("name", "args", "result"), call)), ctx) == "OK"
    assert await monitor.step(node, dict(zip(("name", "args", "result"), call)), ctx) == "LOOP"
    assert node.flags and node.flags[-1].kind == "LOOP"
    assert "repeated 3x" in node.flags[-1].evidence


async def test_argument_order_does_not_hide_a_loop(ctx, node_factory):
    node = node_factory()
    variants = [
        ("write_file", {"path": "a.py", "content": "x"}, "ok"),
        ("write_file", {"content": "x", "path": "a.py"}, "ok"),
        ("write_file", {"path": "a.py", "content": "x"}, "ok"),
    ]
    assert await _steps(node, ctx, variants) == "LOOP"


async def test_oscillation_period_2(ctx, node_factory):
    node = node_factory()
    a = ("read_file", {"path": "a.py"}, "x")
    b = ("read_file", {"path": "b.py"}, "y")
    # A B A B A B — no single action repeats 3x before the 5th step, so this is the oscillator
    flag = await _steps(node, ctx, [a, b, a, b, a, b])
    assert flag == "LOOP"
    assert "period 2" in node.flags[-1].evidence or "repeated 3x" in node.flags[-1].evidence


async def test_oscillation_period_3(ctx, node_factory):
    node = node_factory()
    a = ("read_file", {"path": "a.py"}, "x")
    b = ("read_file", {"path": "b.py"}, "y")
    c = ("read_file", {"path": "c.py"}, "z")
    flag = await _steps(node, ctx, [a, b, c, a, b, c])
    assert flag == "LOOP"
    assert "period 3" in node.flags[-1].evidence


async def test_no_progress_when_nothing_is_written_and_checks_do_not_move(ctx, node_factory):
    node = node_factory()
    # distinct read_file calls: no exact loop, no oscillation, no writes, checks frozen at 1/3
    calls = []
    for i in range(ctx.cfg.NO_PROGRESS_STEPS + 2):
        calls.append(("read_file", {"path": f"f{i}.py"}, "contents"))
        calls.append(("run_checks", {"n": i}, '{"passed": 1, "total": 3}'))
    flag = await _steps(node, ctx, calls)
    assert flag == "LOOP"
    assert "no file written" in node.flags[-1].evidence


async def test_writing_files_is_progress(ctx, node_factory):
    node = node_factory()
    calls = [("write_file", {"path": f"f{i}.py", "content": "x"}, "ok") for i in range(12)]
    assert await _steps(node, ctx, calls) == "OK"
    assert not node.flags


async def test_budget_flag_when_a_node_overspends(ctx, node_factory):
    node = node_factory(budget_tokens=1000)
    node.tokens_used = 950
    flag = await monitor.step(node, {"name": "read_file", "args": {"path": "a"}, "result": ""}, ctx)
    assert flag == "BUDGET"


async def test_unexpected_write_path_is_detected(node_factory):
    from done.models import Check
    node = node_factory()
    node.dod = [Check(id="c1", description="cli works", kind="shell", command="python3 cli.py")]
    assert monitor._unexpected_path("scratch/notes_backup.py", node) is True
    assert monitor._unexpected_path("cli.py", node) is False
    assert monitor._unexpected_path("tests/test_cli.py", node) is False
    assert monitor._unexpected_path("README.md", node) is False

"""Per-run workspace: every file tool is jailed to it, every shell command is capped and offline."""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config


class SandboxViolation(Exception):
    """A tool tried to touch something outside the workspace."""


@dataclass
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, limit: int = 1500) -> str:
        body = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if self.timed_out:
            body = f"[TIMED OUT]\n{body}"
        return f"exit={self.exit_code}\n{body[:limit]}"


class Sandbox:
    def __init__(self, root: Path, cfg: Config):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.real_root = Path(os.path.realpath(self.root))
        self.cfg = cfg

    # --- path jail ----------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        """Map a tool-supplied path into the workspace, or refuse. Blocks .., absolutes, symlinks out."""
        if rel is None:
            raise SandboxViolation("no path given")
        raw = str(rel).strip()
        if not raw:
            raise SandboxViolation("empty path")
        candidate = Path(raw)
        if candidate.is_absolute():
            # allow an absolute path only if it is already inside the workspace
            target = Path(os.path.realpath(candidate))
            if not self._inside(target):
                raise SandboxViolation(f"absolute path outside workspace: {raw}")
            return candidate
        joined = self.root / candidate
        # realpath resolves symlinks for existing components; normpath handles ".." textually
        target = Path(os.path.realpath(os.path.normpath(str(joined))))
        if not self._inside(target):
            raise SandboxViolation(f"path escapes workspace: {raw}")
        return joined

    def _inside(self, target: Path) -> bool:
        try:
            target.relative_to(self.real_root)
            return True
        except ValueError:
            return target == self.real_root

    # --- file ops -----------------------------------------------------------
    def read_file(self, rel: str, limit: int = 20_000) -> str:
        path = self.resolve(rel)
        if not path.exists():
            return f"[no such file: {rel}]"
        if path.is_dir():
            return f"[{rel} is a directory]"
        data = path.read_text(errors="replace")
        return data[:limit] + ("\n[truncated]" if len(data) > limit else "")

    def write_file(self, rel: str, content: str) -> str:
        path = self.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"wrote {rel} ({len(content)} bytes)"

    def list_dir(self, rel: str = ".", max_entries: int = 200) -> str:
        path = self.resolve(rel)
        if not path.exists():
            return f"[no such dir: {rel}]"
        lines: list[str] = []
        for p in sorted(path.rglob("*")):
            if any(part in {"__pycache__", ".git", ".venv", ".pytest_cache"} for part in p.parts):
                continue
            shown = p.relative_to(self.root)
            lines.append(f"{'d' if p.is_dir() else '-'} {shown}" +
                         ("" if p.is_dir() else f" ({p.stat().st_size}b)"))
            if len(lines) >= max_entries:
                lines.append("[truncated]")
                break
        return "\n".join(lines) or "[empty]"

    def snapshot(self) -> str:
        return self.list_dir(".", max_entries=120)

    # --- shell --------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            "HOME": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(self.root),
            # cheap network block: anything honoring proxy env hits a dead local port
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "no_proxy": "",
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
        }

    def run_shell_sync(self, cmd: str, timeout: int | None = None) -> ShellResult:
        timeout = timeout or self.cfg.SHELL_TIMEOUT
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.root), env=self._env(),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                exit_code=124,
                stdout=(exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=f"command exceeded {timeout}s and was killed",
                timed_out=True,
            )
        trunc = self.cfg.OUTPUT_TRUNCATE
        return ShellResult(
            exit_code=proc.returncode,
            stdout=proc.stdout[:trunc],
            stderr=proc.stderr[:trunc],
            timed_out=False,
        )

    async def run_shell(self, cmd: str, timeout: int | None = None) -> ShellResult:
        return await asyncio.to_thread(self.run_shell_sync, cmd, timeout)

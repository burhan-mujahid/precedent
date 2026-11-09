"""On-disk layout. Plain files so the knowledge base lives in git next to everything else.

    my-kb/
      precedent.yaml                 configuration
      rules/R-001.yaml           one approved/draft/deprecated rule per file (humans edit these)
      rules/.history/R-001.v1.yaml   every previous version, never deleted
      proposals/P-....yaml       suggested changes waiting for review
      sources/episodes.jsonl     mined Q&A episodes (redacted)
      logs/decisions.jsonl       append-only: every answer the runtime gave
      logs/events.jsonl          append-only: gaps opened/resolved, feedback, sign-offs, checks, halts
      evals/golden.jsonl         regression cases built from resolved gaps
      RULEBOOK.md                rendered, human-readable view

Logs are append-only and state is derived by replaying them (event sourcing). That
gives an audit trail for free: you can always answer "why did the agent do that,
and which version of which rule told it to?"
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .models import Decision, Gap, Halt, Proposal, Rule, iso, utcnow, valid_id

_GAP_STATUS = {"gap_answered": "answered", "gap_resolved": "resolved", "gap_dismissed": "dismissed"}


class StoreError(RuntimeError):
    pass


def _atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename so a crash never leaves a half-written rule."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _checked(kind: str, value: str) -> str:
    if not valid_id(value):
        raise StoreError(f"invalid {kind} id {value!r}")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML record. Hand-edited files fail with the file name, not a traceback."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StoreError(f"{path}: invalid YAML: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise StoreError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreError(f"{path}: expected a YAML mapping (key: value lines), got {type(data).__name__}")
    return data


def _load_rule(path: Path) -> Rule:
    try:
        return Rule.from_dict(_load_yaml(path))
    except (ValueError, TypeError) as exc:
        raise StoreError(f"{path}: {exc}") from exc


def _load_proposal(path: Path) -> Proposal:
    try:
        return Proposal.from_dict(_load_yaml(path))
    except (ValueError, TypeError) as exc:
        raise StoreError(f"{path}: {exc}") from exc


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        try:
            self.config = Config.load(self.root / "precedent.yaml")
        except (yaml.YAMLError, ValueError) as exc:
            raise StoreError(f"{self.root / 'precedent.yaml'}: {exc}") from exc

    # ---------- layout ----------
    @property
    def rules_dir(self) -> Path:
        return self.root / "rules"

    @property
    def history_dir(self) -> Path:
        return self.rules_dir / ".history"

    @property
    def proposals_dir(self) -> Path:
        return self.root / "proposals"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def evals_path(self) -> Path:
        return self.root / "evals" / "golden.jsonl"

    @property
    def episodes_path(self) -> Path:
        return self.root / "sources" / "episodes.jsonl"

    @classmethod
    def init(cls, root: Path | str, name: str | None = None) -> Store:
        root = Path(root)
        if (root / "precedent.yaml").exists():
            raise StoreError(f"{root} is already a precedent knowledge base")
        for sub in ("rules/.history", "proposals", "logs", "evals", "sources"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        for private in ("logs", "sources"):   # decisions and chat excerpts: owner-only on POSIX
            os.chmod(root / private, 0o700)
        cfg = Config({"name": name or root.resolve().name})
        cfg.save(root / "precedent.yaml")
        (root / ".gitignore").write_text("sources/\n*.tmp\n.lock\n", encoding="utf-8")
        return cls(root)

    def ensure(self) -> None:
        if not (self.root / "precedent.yaml").exists():
            raise StoreError(f"no precedent.yaml in {self.root.resolve()} - run `precedent init` first")

    @contextmanager
    def lock(self, timeout: float = 30) -> Iterator[None]:
        """Cross-process write lock (an exclusive lock file), so two reviewers approving at once
        cannot both take the next rule number. A lock older than 5 minutes is a crashed process."""
        path = self.root / ".lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > 300:
                        path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise StoreError(f"{path} is held by another precedent process; "
                                     "delete it if no other process is running") from None
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            path.unlink(missing_ok=True)

    # ---------- rules ----------
    def rules(self, status: str | None = None) -> list[Rule]:
        out = []
        for path in sorted(self.rules_dir.glob("*.yaml")):
            rule = _load_rule(path)
            if status is None or rule.status == status:
                out.append(rule)
        return out

    def rule(self, rule_id: str) -> Rule:
        path = self.rules_dir / f"{_checked('rule', rule_id)}.yaml"
        if not path.exists():
            raise StoreError(f"unknown rule {rule_id}")
        return _load_rule(path)

    def next_rule_id(self) -> str:
        nums = [int(m.group(1)) for p in self.rules_dir.glob("R-*.yaml") if (m := re.match(r"R-(\d+)", p.stem))]
        return f"R-{(max(nums) + 1 if nums else 1):03d}"

    def save_rule(self, rule: Rule) -> None:
        path = self.rules_dir / f"{_checked('rule', rule.id)}.yaml"
        if path.exists():
            previous = _load_rule(path)
            snap = self.history_dir / f"{previous.id}.v{previous.version}.yaml"
            if not snap.exists():
                _atomic_write(snap, _dump_yaml(previous.to_dict()))
        _atomic_write(path, _dump_yaml(rule.to_dict()))

    def rule_history(self, rule_id: str) -> list[Rule]:
        pattern = f"{_checked('rule', rule_id)}.v*.yaml"
        snaps = sorted(self.history_dir.glob(pattern), key=lambda p: int(p.stem.rsplit("v", 1)[1]))
        return [_load_rule(p) for p in snaps]

    def rules_as_of(self, when: str) -> list[Rule]:
        """The approved rulebook as it stood at the end of day `when` (YYYY-MM-DD).

        Rebuilt from version snapshots, so you can replay a past decision against the
        rules that were in force at the time ("what would the agent have said on 1 Oct?").
        """
        out = []
        for current in self.rules():
            versions = [*self.rule_history(current.id), current]
            in_force = [v for v in versions if (v.updated or v.created) and (v.updated or v.created) <= when]
            if in_force and in_force[-1].status == "approved":
                out.append(in_force[-1])
        return out

    # ---------- proposals ----------
    def proposals(self, status: str | None = "pending") -> list[Proposal]:
        out = []
        for path in sorted(self.proposals_dir.glob("*.yaml")):
            p = _load_proposal(path)
            if status is None or p.status == status:
                out.append(p)
        # Creation order (ids made in the same minute only differ by a random suffix), so drafts
        # are reviewed in the order the chat produced them and rule numbers are reproducible.
        return sorted(out, key=lambda p: (p.created, p.id))

    def proposal(self, pid: str) -> Proposal:
        path = self.proposals_dir / f"{_checked('proposal', pid)}.yaml"
        if not path.exists():
            raise StoreError(f"unknown proposal {pid}")
        return _load_proposal(path)

    def save_proposal(self, p: Proposal) -> None:
        p.created = p.created or datetime.now(timezone.utc).isoformat()   # microseconds: a stable sort key
        _atomic_write(self.proposals_dir / f"{_checked('proposal', p.id)}.yaml", _dump_yaml(p.to_dict()))

    # ---------- append-only logs ----------
    def _append(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
        if not path.exists():
            return
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line after a crash must not take the whole log down.
                    continue
                if isinstance(record, dict):
                    yield record

    def log_decision(self, d: Decision) -> None:
        self._append(self.logs_dir / "decisions.jsonl", d.to_dict())

    def decisions(self) -> list[Decision]:
        return [Decision.from_dict(r) for r in self._read_jsonl(self.logs_dir / "decisions.jsonl")]

    def decision(self, did: str) -> Decision:
        for d in self.decisions():
            if d.id == did:
                return d
        raise StoreError(f"unknown decision {did}")

    def log_event(self, kind: str, **payload: Any) -> None:
        self._append(self.logs_dir / "events.jsonl", {"event": kind, "ts": iso(utcnow()), **payload})

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self._read_jsonl(self.logs_dir / "events.jsonl") if kind is None or e.get("event") == kind]

    def gaps(self, status: str | None = "open") -> list[Gap]:
        """Replay gap events into current gap state."""
        state: dict[str, Gap] = {}
        for e in self.events():
            if e["event"] == "gap_opened":
                state[e["gap"]["id"]] = Gap.from_dict(e["gap"])
            elif e["event"] in ("gap_answered", "gap_resolved", "gap_dismissed") and e["gap_id"] in state:
                g = state[e["gap_id"]]
                g.status = _GAP_STATUS[e["event"]]
                g.resolution = e.get("resolution") or g.resolution
                g.resolved_by = e.get("by") or g.resolved_by
                g.resolved_at = e["ts"]
                g.proposal_id = e.get("proposal_id") or g.proposal_id
            elif e["event"] == "gap_reopened" and e["gap_id"] in state:
                state[e["gap_id"]].status = "open"
        gaps = list(state.values())
        return [g for g in gaps if status is None or g.status == status]

    def gap(self, gid: str) -> Gap:
        for g in self.gaps(status=None):
            if g.id == gid:
                return g
        raise StoreError(f"unknown gap {gid}")

    # ---------- execution gate ----------
    def halts(self, *, decision_id: str | None = None, run_id: str | None = None) -> list[Halt]:
        """Halt records, oldest first. With both filters, a halt matching either one is returned."""
        out = [Halt.from_dict(e["halt"]) for e in self.events("halt") if isinstance(e.get("halt"), dict)]
        if decision_id is None and run_id is None:
            return out
        return [h for h in out if (decision_id and h.decision_id == decision_id) or (run_id and h.run_id == run_id)]

    def sign_off_of(self, decision_id: str) -> dict[str, Any] | None:
        """The sign-off event for a needs_approval decision, if a person recorded one."""
        for e in self.events("decision_signed_off"):
            if e.get("decision_id") == decision_id:
                return e
        return None

    # ---------- evals ----------
    def add_golden(self, case: dict[str, Any]) -> None:
        self._append(self.evals_path, case)

    def golden(self) -> list[dict[str, Any]]:
        return list(self._read_jsonl(self.evals_path))

    # ---------- sources ----------
    def save_episodes(self, episodes: Iterable[dict[str, Any]]) -> int:
        n = 0
        for ep in episodes:
            self._append(self.episodes_path, ep)
            n += 1
        return n

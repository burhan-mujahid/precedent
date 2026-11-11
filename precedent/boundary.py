"""The tool boundary: tools that will not run without a current rulebook decision.

`consult_rulebook` and `check_action` are gates an agent calls. An agent that never calls them is
never stopped. This module moves the check into the tools themselves, so it no longer depends on
the agent cooperating:

    kb = Precedent.open("./my-rulebook")

    @kb.guarded()
    def refund(order_id: str, amount: int) -> str:
        ...                                 # only runs after the execution gate says continue

    refund("1182", 1200, decision_id=d.id)  # checks "refund: order_id=1182, amount=1200" first
    refund("1182", 1200)                    # no decision: Blocked, and a halt record is written

Before the tool body runs, the wrapper:

* takes the `decision_id` keyword (and passes it on only if the tool declares it);
* builds the step from the real tool name and arguments, not the agent's description, so the
  scope check sees the actual amount and action;
* calls the execution gate and runs the tool only on `continue`. A missing or unknown decision is
  still checked, so the attempt shows up as a halt record;
* on `halt`, raises `Blocked` and never runs the tool. Halts are sticky, so retrying is pointless.

A tool call is one step of an action. Mark the tool that finishes the action with `final=True`:
after it returns, the after-Done check runs, and the decision cannot be used again.

For function-calling loops, `guard_handlers()` wraps a name -> handler(args) table and
`require_decision()` adds `decision_id` to each tool's JSON Schema. For MCP hosts, `precedent proxy`
(see proxy.py) applies the same gate to another MCP server's tools.
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .guard import Check
from .models import HALT

if TYPE_CHECKING:
    from .kb import Precedent

DECISION_ARG = "decision_id"
F = TypeVar("F", bound=Callable[..., Any])


class Blocked(Exception):
    """A guarded tool refused to run (or, with stage 'after', ran and then failed its after-Done check)."""

    def __init__(self, check: Check, tool: str, result: Any = None):
        self.check, self.tool, self.result = check, tool, result
        what = "ran, but the run is now halted" if check.stage == "after" else "was not run"
        reasons = "; ".join(check.reasons) or "halted"
        super().__init__(f"{tool} {what}: {reasons}. Do not retry: stop, change nothing else, and tell the user"
                         + (f" ({check.halt_id})." if check.halt_id else "."))

    def to_dict(self) -> dict[str, Any]:
        """What an agent should see instead of the tool's result."""
        return {**self.check.to_dict(), "tool": self.tool, "blocked": True, "instruction": str(self)}


def _value(v: Any) -> str:
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(v)


def step_text(tool: str, args: Mapping[str, Any], ignore: Iterable[str] = ()) -> str:
    """The step the execution gate checks: the tool name and every argument, as written.

    'issue_refund', {'order_id': '1182', 'amount': 1200} -> 'issue refund: order id=1182, amount=1200'
    Underscores become spaces so the risk words in names ('issue_refund') are seen."""
    skip = {DECISION_ARG, *ignore}
    parts = [f"{k.replace('_', ' ')}={_value(v)}" for k, v in args.items() if k not in skip]
    name = tool.replace("_", " ").replace(".", " ").strip() or "tool"
    return f"{name}: {', '.join(parts)}" if parts else name


def gate(kb: Precedent, tool: str, args: Mapping[str, Any], decision_id: Any, *, stage: str = "during",
         ignore: Iterable[str] = (), describe: Callable[[Mapping[str, Any]], str] | None = None) -> Check:
    """Run the execution gate for one tool call. Never raises: any error is a halt."""
    did = str(decision_id or "").strip()
    try:
        step = describe(args) if describe else step_text(tool, args, ignore)
        if stage == "after":
            step = f"Done: {step}"
        return kb.check(did, step, stage=stage)
    except Exception as exc:   # a step too long to check, a broken describe(): fail closed
        return Check(HALT, did or "(none)", stage, reasons=[f"error: {type(exc).__name__}: {exc}"])


def guarded(kb: Precedent, *, name: str | None = None, final: bool = False, ignore: Iterable[str] = (),
            describe: Callable[[Mapping[str, Any]], str] | None = None) -> Callable[[F], F]:
    """Decorator: the tool runs only when the execution gate says continue for its decision.

    name      what the step is called (default: the function name)
    final     this tool finishes the action: run the after-Done check once it returns
    ignore    arguments left out of the step (ids that never appear in the decision, for example)
    describe  build the step yourself from the call's arguments
    """
    ignore = tuple(ignore)

    def wrap(fn: F) -> F:
        tool = name or fn.__name__
        sig = inspect.signature(fn)
        takes_id = DECISION_ARG in sig.parameters

        def values_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                values = dict(bound.arguments)
            except TypeError:
                values = {**{f"arg{i}": a for i, a in enumerate(args)}, **kwargs}
            for p in sig.parameters.values():      # flatten **kwargs into the step
                if p.kind is p.VAR_KEYWORD and isinstance(values.get(p.name), dict):
                    values.update(values.pop(p.name))
            return values

        def before(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
            kwargs = dict(kwargs)
            did = kwargs.get(DECISION_ARG) if takes_id else kwargs.pop(DECISION_ARG, None)
            values = values_of(args, kwargs)
            c = gate(kb, tool, values, did, ignore=ignore, describe=describe)
            if c.halted:
                raise Blocked(c, tool)
            return did, kwargs, values

        def after(did: Any, values: dict[str, Any], result: Any) -> Any:
            if final:
                c = gate(kb, tool, values, did, stage="after", ignore=ignore, describe=describe)
                if c.halted:
                    raise Blocked(c, tool, result)
            return result

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def run_async(*args: Any, **kwargs: Any) -> Any:
                did, kwargs, values = before(args, kwargs)
                return after(did, values, await fn(*args, **kwargs))
            inner: Callable[..., Any] = run_async
        else:
            @functools.wraps(fn)
            def run(*args: Any, **kwargs: Any) -> Any:
                did, kwargs, values = before(args, kwargs)
                return after(did, values, fn(*args, **kwargs))
            inner = run

        if not takes_id:   # frameworks read the signature to build the tool schema: show decision_id
            extra = inspect.Parameter(DECISION_ARG, inspect.Parameter.KEYWORD_ONLY, annotation=str)
            params = list(sig.parameters.values())
            at = next((i for i, p in enumerate(params) if p.kind is p.VAR_KEYWORD), len(params))
            inner.__signature__ = sig.replace(parameters=[*params[:at], extra, *params[at:]])  # type: ignore[attr-defined]
            # pydantic (LangChain's @tool) reads type hints, not the signature. A new dict: wraps() shares fn's.
            inner.__annotations__ = {**getattr(fn, "__annotations__", {}), DECISION_ARG: str}
        inner.__precedent_guarded__ = True  # type: ignore[attr-defined]
        return cast(F, inner)

    return wrap


def guard_handlers(kb: Precedent, handlers: Mapping[str, Callable[[dict[str, Any]], Any]], *,
                   pass_through: Iterable[str] = (), final: Iterable[str] = (),
                   ignore: Mapping[str, Iterable[str]] | None = None) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Wrap a function-calling dispatch table (tool name -> handler(args)).

    Every tool is guarded unless it is named in `pass_through` (read-only lookups). A blocked call
    returns the halt as the tool's result instead of raising, so the model sees why and stops.
    Precedent's own tools (consult_rulebook, check_action) always pass through."""
    open_ = {"consult_rulebook", "check_action", *pass_through}
    finals, ignore = set(final), ignore or {}

    def make(tool: str, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        def handler(args: dict[str, Any]) -> Any:
            args = dict(args or {})
            did = args.pop(DECISION_ARG, None)
            c = gate(kb, tool, args, did, ignore=ignore.get(tool, ()))
            if c.halted:
                return Blocked(c, tool).to_dict()
            result = fn(args)
            if tool in finals:
                done = gate(kb, tool, args, did, stage="after", ignore=ignore.get(tool, ()))
                if done.halted:
                    return {"result": result, **Blocked(done, tool, result).to_dict()}
            return result
        return handler

    return {t: (fn if t in open_ else make(t, fn)) for t, fn in handlers.items()}


_DECISION_SCHEMA = {"type": "string", "description": "The decision_id from consult_rulebook that authorises this "
                    "call. Without a current one the call is refused."}


def require_decision(spec: dict[str, Any]) -> dict[str, Any]:
    """Add a required `decision_id` to a tool definition (Anthropic `input_schema`, MCP `inputSchema`,
    or OpenAI `parameters` / `function.parameters`). Returns a copy."""
    spec = copy.deepcopy(spec)
    holder = spec.get("function", spec)
    key = next((k for k in ("input_schema", "inputSchema", "parameters") if k in holder), "input_schema")
    schema = holder.setdefault(key, {"type": "object"})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})[DECISION_ARG] = dict(_DECISION_SCHEMA)
    required = schema.setdefault("required", [])
    if DECISION_ARG not in required:
        required.append(DECISION_ARG)
    note = " Needs a decision_id from consult_rulebook."
    if note.strip() not in str(holder.get("description", "")):
        holder["description"] = str(holder.get("description", "")).rstrip() + note
    return spec

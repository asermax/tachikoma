# DES-013: Typed Envelope with Property Hooks

**Scope**: Python / Architecture
**Date**: 2026-05-24
**Last Updated**: 2026-05-25

## Pattern

When a value flows through multiple consumers at a system boundary and needs to participate in several "kinds" (e.g., text messages vs button taps, future media payloads), model it as a small typed hierarchy:

1. Define an abstract base class with **property hooks** for every per-kind behavior consumers depend on. Mark only the truly required hooks as `@abstractmethod`; give the others default-returning properties on the base.
2. Make each concrete subtype a `@dataclass(frozen=True)` that overrides exactly the hooks whose value differs from the default and declares only the fields that subtype carries.
3. Consumers — pipelines, coordinators, channels — read the hooks. They never `isinstance`-check the subtype or call into per-subtype helpers.
4. Queue element types, function parameters, and async-generator inputs at the boundary are typed as the abstract base.

The base is the open/closed seam: adding a new subtype is one new class; consumer modules do not change.

## Rationale

Many subsystems handle "a value that means one of N things." The naive options each have a cost:

- **Tag a single dataclass with a discriminator and branch on it**: every consumer grows an `if/elif` ladder. Add a new kind, and every ladder needs the new branch.
- **External dispatch table (`render(value)` switching on type)**: same fan-out, just moved to a central dispatcher. The dispatcher becomes a bottleneck and a coordination point for every team adding a kind.
- **Runtime-checkable `Protocol`**: avoids inheritance but loses default values on the base. Every concrete kind must implement every hook, even the ones it would inherit verbatim. Loses exhaustiveness from the type checker.
- **Pydantic discriminated union**: adds serialization machinery for zero benefit when the value never crosses a serialization boundary.

Abstract base + property hooks gives:

- **Open/closed**: new subtype = new class; consumers don't change.
- **Defaults on the base**: subtypes only override what's actually different. The "no behavior change for the common kind" path stays one line.
- **Type-checker exhaustiveness**: missing required hook overrides surface as `cannot instantiate abstract class` errors at construction sites.
- **Value-equality preserved**: `@dataclass(frozen=True)` keeps subtypes hashable and comparable, like the original record type they replace.
- **Single rendering site**: consumers call `value.some_hook` at exactly one place per concern; structure survives to that point unchanged.

## Examples

### Do This

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


class MessageEnvelope(ABC):
    """Abstract base for the coordinator's message envelopes."""

    @property
    @abstractmethod
    def sdk_input(self) -> str: ...

    # Defaults on the base — subtypes override only what's different.
    @property
    def pinned_skills(self) -> tuple[str, ...]:
        return ()

    @property
    def force_new(self) -> bool:
        return False

    @property
    def runs_pre_processing(self) -> bool:
        return True

    @property
    def runs_boundary_detection(self) -> bool:
        return True


@dataclass(frozen=True)
class TextMessage(MessageEnvelope):
    text: str
    pinned_skills: tuple[str, ...] = ()
    force_new: bool = False

    @property
    def sdk_input(self) -> str:
        return self.text


@dataclass(frozen=True)
class ButtonTapMessage(MessageEnvelope):
    value: str

    @property
    def sdk_input(self) -> str:
        return f"The user tapped the option `{self.value}` out of the options you displayed."

    @property
    def runs_pre_processing(self) -> bool:
        return False


@dataclass(frozen=True)
class ReactionMessage(MessageEnvelope):
    added: frozenset[str]
    removed: frozenset[str]

    @property
    def sdk_input(self) -> str:
        # Renders canonical prose describing the emoji-set diff.
        ...

    @property
    def runs_pre_processing(self) -> bool:
        return False

    @property
    def runs_boundary_detection(self) -> bool:
        return False
```

Consumer code reads only the hooks:

```python
# At the SDK-input boundary — one rendering site for every subtype.
yield user_message(envelope.sdk_input)

# Pre-processing gating — coordinator decides once, all pipelines respect it.
if envelope.runs_pre_processing:
    await pre_pipeline.run(envelope, ...)

# Boundary-detection gating — same shape, different concern.
if active is None and envelope.runs_boundary_detection:
    await attempt_cold_start_resume(...)

# Queue typing at every hop.
_message_buffer: asyncio.Queue[MessageEnvelope] = asyncio.Queue()
```

**Why**: `TextMessage` declares the three fields it actually carries; `ButtonTapMessage` overrides only `sdk_input` and `runs_pre_processing` and inherits the rest; `ReactionMessage` declares its two diff-set fields and overrides three hooks. Adding a fourth subtype (e.g., a media envelope) is one new class — the SDK boundary, the gating sites, and the queue typing don't change. Adding a new hook (as `runs_boundary_detection` was added when reactions needed to bypass the boundary detector) is a single property on the base with a sensible default; existing subtypes inherit the default unchanged.

### Don't Do This

```python
@dataclass(frozen=True)
class IncomingMessage:
    kind: Literal["text", "tap"]
    text: str | None = None
    value: str | None = None
    pinned_skills: tuple[str, ...] = ()
    force_new: bool = False


# Every consumer grows a branch on `kind`.
def render(msg: IncomingMessage) -> str:
    if msg.kind == "text":
        return msg.text
    elif msg.kind == "tap":
        return f"The user tapped `{msg.value}`..."
    else:
        raise ValueError(f"unknown kind: {msg.kind}")


def should_run_pre_processing(msg: IncomingMessage) -> bool:
    return msg.kind != "tap"
```

**Why**: One dataclass with optional fields per kind forces every consumer to discriminate on `kind`, and the optional fields leak through every site (`msg.text or ""`). Each new kind requires editing every consumer that branches. The "is this a kind that skips pre-processing?" question lives in the consumer rather than on the kind itself, so the same predicate has to be re-implemented anywhere the question is asked.

### Don't Do This

```python
# External dispatcher.
def render_sdk_input(env: MessageEnvelope) -> str:
    if isinstance(env, TextMessage):
        return env.text
    elif isinstance(env, ButtonTapMessage):
        return f"The user tapped `{env.value}`..."
    raise TypeError(f"unhandled envelope: {type(env).__name__}")
```

**Why**: Moves the `isinstance` ladder to a single function, but every new subtype still requires editing this dispatcher in the consumer package. The point of the pattern is that the subtype's own definition site owns its behavior — the consumer reads `env.sdk_input` and is done.

## Exceptions

- **Two kinds with one trivial difference and no expected growth**: a single dataclass with a discriminator may be lighter. Promote to this pattern when a third kind appears or when the discriminator branch shows up in 3+ consumers.
- **Values that cross a serialization boundary**: if envelopes are persisted to disk or sent over the wire, the abstract-base form alone is not enough — a discriminator is required for deserialization. Use Pydantic with a discriminated union (or equivalent) and keep the hook-based interface on top.

## Routing Fields

When a routing or control instruction needs to be available on all message types, use a **dual declaration**: add a property hook on the base class returning a default (`None`), and add a dataclass field with the same name on each concrete subtype. The property hook provides uniform consumer access without `isinstance` checks; the dataclass field stores the actual value.

This differs from behavioral hooks (`runs_pre_processing`, `runs_boundary_detection`) which are property-only on the base — routing fields are data that flows through the system, not control-flow predicates. It also differs from subtype-specific data (`external_id` on `TextMessage`/`ReactionMessage` only) which doesn't need a base-class property because only some subtypes carry it.

```python
class MessageEnvelope(ABC):
    # Routing field — property hook on base + dataclass field on subtypes.
    @property
    def target_session_id(self) -> str | None:
        return None

@dataclass(frozen=True)
class TextMessage(MessageEnvelope):
    target_session_id: str | None = None  # stores the value

@dataclass(frozen=True)
class ButtonTapMessage(MessageEnvelope):
    target_session_id: str | None = None  # stores the value
```

Consumer code reads the property uniformly:

```python
if envelope.target_session_id is not None:
    await route_to_session(envelope.target_session_id)
```

See [DES-014](DES-014-generic-routing-fields-on-typed-envelopes.md) for the full pattern.

## Related

- [DES-001](DES-001-testing-conventions.md): Test the hook contract on the base, parameterized across concrete subtypes.

# DES-014: Generic Routing Fields on Typed Envelopes

**Scope**: Python / Architecture
**Date**: 2026-05-26

## Pattern

When adding routing or control instructions that need to be available on all envelope types, declare the field as both a **property hook on the base class** (returning a default) and a **dataclass field on each concrete subtype**. The property hook provides uniform consumer access without `isinstance` checks; the dataclass field stores the actual value.

1. Add a property hook on `MessageEnvelope` that returns a sensible default (typically `None`).
2. Add a dataclass field with the same name and type on each concrete subtype (`TextMessage`, `ButtonTapMessage`, `ReactionMessage`), defaulting to the same value.
3. Consumers read the property uniformly — they never need to know which subtype they hold.
4. The field is data (a routing instruction), not a behavioral hook.

This extends DES-013's data-vs-hook distinction: routing fields are data, but they benefit from a uniform interface on the base class so consumers don't need type checks.

## Rationale

Session routing is a coordinator concern, not channel-specific. By putting routing fields on all envelope subtypes with a base-class property hook, any message type can trigger routing without coordinator changes. Future routing mechanisms (e.g., reply-based switching in DLT-086) reuse the same pattern.

The dual declaration is necessary because `MessageEnvelope` is an ABC (not a dataclass) — frozen dataclass subtypes generate their own `__init__` from declared fields only. Without the field on each subtype, the value can't be passed at construction time. Without the property on the base, consumers need `isinstance` checks to access the field.

Subtype-specific data fields (e.g., `external_id` on `TextMessage` and `ReactionMessage` only) don't need this pattern — they're only relevant to certain subtypes and aren't consumed generically by the coordinator.

## Examples

### Do This

```python
class MessageEnvelope(ABC):
    # Property hook on base — uniform consumer access.
    @property
    def target_session_id(self) -> str | None:
        return None

@dataclass(frozen=True)
class TextMessage(MessageEnvelope):
    target_session_id: str | None = None  # stores the value

@dataclass(frozen=True)
class ButtonTapMessage(MessageEnvelope):
    target_session_id: str | None = None  # stores the value

@dataclass(frozen=True)
class ReactionMessage(MessageEnvelope):
    target_session_id: str | None = None  # stores the value
```

Consumer code reads the property uniformly:

```python
# Coordinator — one check, all subtypes.
if envelope.target_session_id is not None:
    await self._route_to_target_session(envelope.target_session_id)
```

### Don't Do This

```python
# Only on ReactionMessage — couples routing to one subtype.
@dataclass(frozen=True)
class ReactionMessage(MessageEnvelope):
    target_session_id: str | None = None

# Consumer needs isinstance check.
if isinstance(envelope, ReactionMessage) and envelope.target_session_id is not None:
    await self._route_to_target_session(envelope.target_session_id)
```

**Why**: Future routing mechanisms (reply-based switching, scheduled session switches) can't reuse the field if it's only on one subtype. Each new routing trigger requires either adding the field to more subtypes (breaking the single-subtype assumption) or creating a parallel mechanism.

## Exceptions

- **Subtype-specific data**: Fields that only make sense for certain subtypes (e.g., `external_id` on `TextMessage`/`ReactionMessage` but not `ButtonTapMessage`) should be declared as dataclass fields on those subtypes only — no base-class property hook needed.
- **Behavioral hooks**: Control-flow predicates (`runs_pre_processing`, `force_new`) remain property-only on the base — they don't carry data.

## Related

- [DES-013](DES-013-typed-envelope-with-property-hooks.md): The envelope pattern this extends. DES-013 establishes the data-vs-hook distinction; this pattern clarifies the dual declaration for routing fields.
- [DES-009](DES-009-channel-delivery-serialization.md): Channel delivery serialization — deferred routing uses `enqueue_deferred()` per this pattern.

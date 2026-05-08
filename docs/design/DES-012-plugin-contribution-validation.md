# DES-012: Plugin Contribution Validation

**Scope**: Python / Plugins
**Date**: 2026-05-08
**Last Updated**: 2026-05-08

## Pattern

When adding a new plugin contribution type (context providers, post-processors, etc.), implement validation as a dedicated `_validate_<contribution>()` function in `loader.py` that follows this structure:

1. **Early return** if manifest section is empty
2. **Iterate** manifest entries (name → module_name)
3. **Resolve** module path: `plugin_dir / "<contribution_dir>" / f"{module_name}.py"`
4. **Check** file exists (else: raise `ValueError` with diagnostic)
5. **Import** via `_import_handler_module(path, module_key)` with key `tachikoma_plugin.{alias}.{contribution_dir}.{module_name}`
6. **Find concrete subclasses** via `_find_concrete_subclasses(module, base_abc_tuple)` — skips non-class and abstract
7. **Validate** each discovered class:
   - Check `__init__` accepts required keyword args via `inspect.signature`
   - Validate contribution-specific attributes (e.g., phase values)
   - Conditionally inject optional kwargs based on `__init__` signature
8. **Instantiate** with `cls(**kwargs)` — catch `TypeError` → re-raise as `ValueError` with diagnostic
9. **Return** list of instances on success; raise `ValueError` on any failure

The function is called from `_discover_one()` within a try/except block — validation failure sets `LoadedPlugin.status = "failed"` with the diagnostic. Instances are stored on `LoadedPlugin` and registered/unregistered via event-driven listeners on `PluginInstalled`/`PluginRemoving`.

## Rationale

Plugin contributions follow a manifest → discovery → validation → instantiation → lifecycle pipeline. Without a standard validation pattern:

- Each new contribution type invents its own import/class-discovery/constructor-validation logic
- Error diagnostics are inconsistent across contribution types
- The relationship between validation and lifecycle registration is ad-hoc

This pattern standardizes the validation boundary:

- Validation function is the single integration point between manifest parsing and runtime registration
- `_find_concrete_subclasses()` is shared across all contribution types
- Error messages are consistently formatted (name the module, name the issue, list valid alternatives)
- Validation happens at discovery time (fail-fast), not at registration time

## Examples

### Do This

```python
def _validate_post_processors(
    manifest: PluginManifest,
    plugin_dir: Path,
    alias: str,
    validated_config: dict[str, Any],
    agent_defaults: AgentDefaults,
) -> list[PostProcessor]:
    if not manifest.post_processors:
        return []

    processors: list[PostProcessor] = []

    for module_name in manifest.post_processors.values():
        processor_path = plugin_dir / "post_processors" / f"{module_name}.py"
        if not processor_path.exists():
            raise ValueError(f"Post-processor module file not found: {processor_path}")

        module_key = f"tachikoma_plugin.{alias}.post_processors.{module_name}"
        module = _import_handler_module(processor_path, module_key)

        found_classes = _find_concrete_subclasses(module, (PostProcessor,))

        if not found_classes:
            raise ValueError(
                f"No concrete class implementing PostProcessor found "
                f"in module '{module_name}'"
            )

        for cls in found_classes:
            sig = inspect.signature(cls.__init__)
            if "config" not in sig.parameters:
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' __init__ must "
                    f"accept 'config' as a keyword argument"
                )

            # Contribution-specific validation
            phase_value = getattr(cls, "phase", MAIN_PHASE)
            if phase_value not in _VALID_PHASES:
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' declares "
                    f"invalid phase '{phase_value}'. Valid phases: ..."
                )

            # Build kwargs with conditional injection
            kwargs: dict[str, Any] = {"config": validated_config}
            if "agent_defaults" in sig.parameters:
                kwargs["agent_defaults"] = agent_defaults

            try:
                instance = cls(**kwargs)
            except TypeError as exc:
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' failed to "
                    f"instantiate: {exc}"
                ) from exc

            processors.append(instance)

    return processors
```

**Why**: Follows the standard structure — early return, iterate manifest, resolve path, import, find subclasses, validate constructor, build kwargs, instantiate, collect. Uses `_find_concrete_subclasses()` for class discovery and `_import_handler_module()` for importing. Contribution-specific validation (phase check, agent_defaults injection) is cleanly separated from the shared pattern.

### Don't Do This

```python
def _validate_post_processors(manifest, plugin_dir, alias, config, agent_defaults):
    results = []
    for name, module_name in manifest.post_processors.items():
        # Inline import logic instead of using shared helpers
        spec = importlib.util.spec_from_file_location(
            module_name,
            plugin_dir / "post_processors" / f"{module_name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, PostProcessor):
                # No constructor validation, no phase validation
                results.append(attr(config=config))
    return results
```

**Why**: Duplicates import logic instead of using `_import_handler_module()`. Doesn't use `_find_concrete_subclasses()` — manually iterates `dir()` and misses `inspect.isabstract` filtering. No constructor signature validation (TypeError at instantiation time gives a poor diagnostic). No phase validation. Doesn't handle optional `agent_defaults` injection. Returns raw exceptions instead of `ValueError` with diagnostics.

## Exceptions

When a contribution type has fundamentally different discovery semantics (e.g., entry-point-based loading instead of file-based modules), the import and class-discovery steps may differ. The validation structure (resolve → check exists → import → find classes → validate → instantiate) should still be followed, but the specific mechanisms can adapt.

## Related

- See also: [DES-003](DES-003-subsystem-bootstrap-hooks.md) - Bootstrap hooks where plugin discovery is triggered
- Related feature: [../feature-designs/plugins/plugin-loading.md](../feature-designs/plugins/plugin-loading.md) - Plugin loading system that defines all contribution types

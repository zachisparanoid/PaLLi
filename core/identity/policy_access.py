from __future__ import annotations

from typing import Any, Mapping


def policy_value(action_policy: Any, key: str, default: Any) -> Any:
    if isinstance(action_policy, Mapping):
        return action_policy.get(key, default)
    if hasattr(action_policy, "as_preferences") and key == "_materialized":
        return getattr(action_policy, "materialized", default)
    return getattr(action_policy, key, default)


def policy_bool(action_policy: Any, key: str, default: bool) -> bool:
    return coerce_bool(policy_value(action_policy, key, default), default)


def policy_has_effective_permissions(action_policy: Any) -> bool:
    _, available = _policy_mapping_section(
        action_policy,
        snake_key="effective_permissions",
        camel_key="effectivePermissions",
    )
    return available


def policy_effective_permission(
    action_policy: Any,
    permission_key: str,
    *,
    default: bool = False,
) -> bool:
    permissions, available = _policy_mapping_section(
        action_policy,
        snake_key="effective_permissions",
        camel_key="effectivePermissions",
    )
    if not available:
        return default
    return coerce_bool(permissions.get(permission_key, default), default)


def policy_has_config_gates(action_policy: Any) -> bool:
    _, available = _policy_mapping_section(
        action_policy,
        snake_key="config_gates",
        camel_key="configGates",
    )
    return available


def policy_config_gate(
    action_policy: Any,
    gate_key: str,
    *,
    default: bool = False,
) -> bool:
    config_gates, available = _policy_mapping_section(
        action_policy,
        snake_key="config_gates",
        camel_key="configGates",
    )
    if not available:
        return default
    return coerce_bool(config_gates.get(gate_key, default), default)


def coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _policy_mapping_section(
    action_policy: Any,
    *,
    snake_key: str,
    camel_key: str,
) -> tuple[dict[str, Any], bool]:
    section: Any = None
    available = False

    if isinstance(action_policy, Mapping):
        if snake_key in action_policy:
            section = action_policy.get(snake_key)
            available = True
        elif camel_key in action_policy:
            section = action_policy.get(camel_key)
            available = True
    else:
        if hasattr(action_policy, snake_key):
            section = getattr(action_policy, snake_key)
            available = True
        elif hasattr(action_policy, camel_key):
            section = getattr(action_policy, camel_key)
            available = True

    if not isinstance(section, Mapping):
        return {}, available
    return dict(section), available

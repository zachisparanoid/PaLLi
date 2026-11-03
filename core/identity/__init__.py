# E.2 step 4 (2026-05-13): identity_service is exposed via lazy `__getattr__`
# so that importing a sibling submodule (e.g. `from core.identity.profile_store
# import ProfileReflectionStore` from core.database) doesn't transitively pull
# `core.identity.service` -> `core.local_mode` -> `core.database` -> circular
# import. Eager re-export remains for `from core.identity import identity_service`
# at use sites that aren't on the import-time critical path.

__all__ = ["identity_service"]


def __getattr__(name):  # pragma: no cover - thin wrapper
    if name == "identity_service":
        from .service import identity_service as _identity_service
        return _identity_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Fixtures shared by tests/identity (Batch 5, task 1 — ruling R27).

``memory_bundle_factory`` builds the smallest CompiledIdentity that carries
one MEMORY.md body, modelled on test_compiler_memory_order._bundle: the
renderers under test read ``bundle.owner["memory"]`` and nothing else.
"""
from __future__ import annotations

import pytest

from core.identity.models import CompiledIdentity, IdentityDocument


@pytest.fixture
def memory_bundle_factory():
    def _factory(body: str, **frontmatter) -> CompiledIdentity:
        return CompiledIdentity(
            owner={
                "memory": IdentityDocument(
                    doc_key="memory",
                    path="MEMORY.md",
                    frontmatter=dict(frontmatter),
                    content=body,
                ),
            }
        )

    return _factory

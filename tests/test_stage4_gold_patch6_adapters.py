from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.patch6_adapters import (
    ConfiguredPatch6CompatibilityAdapter,
    ConfiguredPatch6RetrievalAdapter,
    configure_patch6_compatibility_adapter,
    configure_patch6_retrieval_adapter,
    require_patch6_compatibility_adapter,
    require_patch6_retrieval_adapter,
)

from tests.test_stage4_gold_compatibility import _make_harness
from tests.test_stage4_gold_retrieval import _configured_retriever


def test_s4_p78_patch6_adapters_preserve_exact_configured_capabilities(
    tmp_path: Path,
) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, _ = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    compatibility_adapter = configure_patch6_compatibility_adapter(
        harness.evaluator
    )
    retrieval_adapter = configure_patch6_retrieval_adapter(
        retriever=retriever,
        compatibility_adapter=compatibility_adapter,
    )

    assert require_patch6_compatibility_adapter(compatibility_adapter) is harness.evaluator
    assert require_patch6_retrieval_adapter(retrieval_adapter) is retriever
    assert retrieval_adapter.resolve_descriptor(harness.entry) == harness.descriptor
    loaded = retrieval_adapter.load_verified_behavior(
        content_key=harness.unit.content_key,
        manifest_id=harness.manifest.manifest_id,
    )
    assert loaded.unit.content_key == harness.unit.content_key
    assert loaded.manifest.manifest_id == harness.manifest.manifest_id


def test_s4_p78_patch6_adapters_reject_constructor_and_cross_evaluator_binding(
    tmp_path: Path,
) -> None:
    first = _make_harness(tmp_path / "first")
    second = _make_harness(tmp_path / "second")
    retriever, _, _ = _configured_retriever(
        first,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )

    with pytest.raises(TypeError):
        ConfiguredPatch6CompatibilityAdapter()
    with pytest.raises(TypeError):
        ConfiguredPatch6RetrievalAdapter()
    with pytest.raises(TypeError):
        configure_patch6_retrieval_adapter(
            retriever=retriever,
            compatibility_adapter=configure_patch6_compatibility_adapter(
                second.evaluator
            ),
        )

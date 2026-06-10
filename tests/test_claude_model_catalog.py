from __future__ import annotations

from vibe.claude_model_catalog import (
    FALLBACK_CLAUDE_MODELS,
    infer_models_from_bundle,
    load_catalog_models,
    sort_catalog_models,
)


def test_fable_is_tracked_in_catalog_and_fallback():
    assert "claude-fable-5" in load_catalog_models()
    assert "claude-fable-5" in FALLBACK_CLAUDE_MODELS


def test_fable_sorts_above_other_families():
    ordered = sort_catalog_models(
        [
            "claude-haiku-4-5",
            "claude-opus-4-8",
            "claude-fable-5",
            "claude-sonnet-4-6",
        ]
    )
    # Fable is the Mythos-class tier and must lead the catalog ordering.
    assert ordered[0] == "claude-fable-5"
    assert ordered.index("claude-fable-5") < ordered.index("claude-opus-4-8")


def test_bundle_inference_detects_fable_and_skips_mythos_preview(tmp_path):
    bundle = tmp_path / "cli.js"
    bundle.write_bytes(
        b'pick("claude-fable-5");fallback="claude-opus-4-8";"claude-mythos-preview"'
    )

    models = infer_models_from_bundle(bundle)

    assert models == ["claude-fable-5", "claude-opus-4-8"]
    # `claude-mythos-preview` carries no version segment and is not a publicly
    # callable model, so it must not leak into the catalog.
    assert "claude-mythos-preview" not in models

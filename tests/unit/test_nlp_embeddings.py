"""Local lexical embeddings.

The guarantees that matter: identical text gives an identical vector forever, related text
scores higher than unrelated text, and the vector width never drifts from the column.
"""

from __future__ import annotations

import pytest

from nlp.embeddings import LocalEmbedder, cosine, embed_text, features

DIMENSIONS = 256


def similarity(left: str, right: str) -> float:
    return cosine(embed_text(left, DIMENSIONS), embed_text(right, DIMENSIONS))


def test_identical_text_is_identical_forever():
    first = embed_text("The Shopify integration keeps failing", DIMENSIONS)
    second = embed_text("The Shopify integration keeps failing", DIMENSIONS)
    assert first == second
    assert pytest.approx(cosine(first, second), abs=1e-9) == 1.0


def test_vector_is_normalised_and_correct_width():
    vector = embed_text("anything at all", DIMENSIONS)
    assert len(vector) == DIMENSIONS
    assert pytest.approx(sum(value * value for value in vector), abs=1e-6) == 1.0


def test_related_text_scores_above_unrelated_text():
    reference = "The customer cannot connect Shopify"
    related = similarity(reference, "The customer's Shopify connection keeps failing")
    unrelated = similarity(reference, "The customer loves the new mobile app")
    assert related > unrelated
    assert unrelated < 0.25


def test_typos_still_match():
    assert similarity("cannot connect Shopify", "cannot conect Shopfy") > 0.5


def test_empty_text_does_not_explode():
    assert len(embed_text("", DIMENSIONS)) == DIMENSIONS


def test_domain_terms_carry_more_weight_than_boilerplate():
    """Two sentences sharing only filler should score below two sharing an integration."""
    filler = similarity(
        "The customer needs help with the account", "The customer needs help with the team"
    )
    domain = similarity("Shopify sync failed", "Shopify sync is broken")
    assert domain > filler


async def test_embedder_interface():
    embedder = LocalEmbedder(dimensions=DIMENSIONS)
    assert embedder.dimensions == DIMENSIONS
    assert embedder.model.endswith(str(DIMENSIONS))

    result = await embedder.embed(["one", "two"])
    assert len(result.vectors) == 2
    assert result.dimensions == DIMENSIONS
    assert await embedder.embed_one("one") == result.vectors[0]


def test_features_are_explainable():
    bag = features("Shopify sync failed")
    assert any(key.startswith("w:shopify") for key in bag)
    assert any(key.startswith("b:") for key in bag)  # bigrams
    assert any(key.startswith("c:") for key in bag)  # character n-grams

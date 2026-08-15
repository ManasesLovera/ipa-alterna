"""Pure unit tests for Reciprocal Rank Fusion."""

from __future__ import annotations

from ipa.db.vector import RRF_K, rrf_fuse


def test_single_ranking_preserves_order() -> None:
    rows = [("d1", 0, "a"), ("d2", 1, "b"), ("d3", 2, "c")]
    fused = rrf_fuse([rows], top_k=3)
    assert [row[0] for row, _ in fused] == ["d1", "d2", "d3"]


def test_row_present_in_both_rankings_accumulates() -> None:
    winner = ("d1", 0, "shared")
    loser = ("d2", 0, "other")
    fused = rrf_fuse([[winner, loser], [winner]], top_k=2)
    scores = {(row[0], row[1]): score for row, score in fused}
    assert scores[("d1", 0)] > scores[("d2", 0)]


def test_rrf_math_is_exact() -> None:
    shared = ("d1", 0, "s")
    only_vector = ("d2", 1, "v")
    vector_ranking = [shared, only_vector]
    text_ranking = [shared]
    fused = {
        (row[0], row[1]): score for row, score in rrf_fuse([vector_ranking, text_ranking], top_k=2)
    }
    assert fused[("d1", 0)] == 2.0 / (RRF_K + 1)
    assert fused[("d2", 1)] == 1.0 / (RRF_K + 2)


def test_top_k_truncates() -> None:
    rows = [(f"d{i}", i, f"t{i}") for i in range(10)]
    assert len(rrf_fuse([rows], top_k=3)) == 3


def test_empty_rankings() -> None:
    assert rrf_fuse([], top_k=5) == []
    assert rrf_fuse([[]], top_k=5) == []

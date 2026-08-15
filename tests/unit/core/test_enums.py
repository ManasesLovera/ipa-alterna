"""Pipeline step ordering."""

from __future__ import annotations

import pytest

from ipa.core.enums import STEP_ORDER, PipelineStep, next_step, steps_from


def test_step_order_is_the_documented_sequence() -> None:
    assert [step.value for step in STEP_ORDER] == [
        "store",
        "decompose",
        "ocr",
        "extract",
        "embed",
        "review",
    ]


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (PipelineStep.STORE, list(STEP_ORDER)),
        (
            PipelineStep.EXTRACT,
            [PipelineStep.EXTRACT, PipelineStep.EMBED, PipelineStep.REVIEW],
        ),
        (PipelineStep.REVIEW, [PipelineStep.REVIEW]),
    ],
)
def test_steps_from_is_inclusive_and_ordered(
    start: PipelineStep, expected: list[PipelineStep]
) -> None:
    assert list(steps_from(start)) == expected


def test_steps_from_preserves_relative_order_of_step_order() -> None:
    for step in STEP_ORDER:
        remaining = steps_from(step)
        assert remaining[0] is step
        start = STEP_ORDER.index(step)
        assert list(remaining) == [s for s in STEP_ORDER if STEP_ORDER.index(s) >= start]


def test_next_step_walks_the_pipeline() -> None:
    assert next_step(PipelineStep.STORE) is PipelineStep.DECOMPOSE
    assert next_step(PipelineStep.REVIEW) is None


def test_steps_from_rejects_unknown_step() -> None:
    with pytest.raises(ValueError, match="unknown pipeline step"):
        steps_from("not-a-step")  # type: ignore[arg-type]

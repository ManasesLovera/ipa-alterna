"""Step handler registry.

Pipeline step handlers (T09-T12) register themselves with `@register(step)` and
are looked up by `get_handler`. Registration happens at import time; the
`src/ipa/pipeline/steps/__init__.py` module imports every step module so worker
boot registers all handlers before any task runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ipa.core.enums import PipelineStep
from ipa.core.errors import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ipa.contracts.models import StepContext, StepResult

StepHandler = Callable[["StepContext"], Awaitable["StepResult"]]

_handlers: dict[PipelineStep, StepHandler] = {}


def register(step: PipelineStep) -> Callable[[StepHandler], StepHandler]:
    """Decorate a function as the handler for a pipeline step.

    Args:
        step: The step the handler implements.

    Returns:
        A decorator that records the handler and returns it unchanged.
    """

    def decorator(handler: StepHandler) -> StepHandler:
        _handlers[step] = handler
        return handler

    return decorator


def get_handler(step: PipelineStep) -> StepHandler:
    """Return the registered handler for a step.

    Args:
        step: The pipeline step.

    Returns:
        The handler.

    Raises:
        ConfigurationError: If no handler is registered for the step.
    """
    try:
        return _handlers[step]
    except KeyError as exc:
        raise ConfigurationError(f"no handler registered for step {step}") from exc


def registered_steps() -> tuple[PipelineStep, ...]:
    """Return the steps that have a registered handler.

    Returns:
        A tuple of steps with handlers, in pipeline order.
    """
    from ipa.core.enums import STEP_ORDER

    return tuple(step for step in STEP_ORDER if step in _handlers)

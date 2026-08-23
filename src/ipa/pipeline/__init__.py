"""Celery-backed pipeline orchestrator and step registry.

The state machine lives in PostgreSQL (`document_steps`), not in Celery: Celery
is only transport. `src/ipa/pipeline/runner.py` drives every step through one
generic task, `orchestrator.py` enqueues and reprocesses, `sweeper.py` recovers
stuck work after a Redis flush, and `steps/` holds the concrete step handlers
registered with `registry.py`.
"""

from __future__ import annotations

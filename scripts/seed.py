"""Local development seed script.

T01 ships the entrypoint only: there are no tables or domain services to seed
until T02 (database) and T06 (tags) land. Later tasks fill in `seed()`.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def seed() -> None:
    """Populate the local environment with development data.

    Currently a no-op placeholder.

    Returns:
        None.
    """
    logger.info("seed.skipped", reason="no seedable domain objects until T02/T06")


if __name__ == "__main__":
    seed()

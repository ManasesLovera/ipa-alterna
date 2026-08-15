"""PostgreSQL persistence layer.

SQLAlchemy 2.0 typed ORM models, the async session infrastructure, and thin
repositories that never leak ORM objects past this package. PostgreSQL is the
single source of truth for platform state; raw bytes and extracted bodies live
in MinIO and MongoDB respectively.
"""

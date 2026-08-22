"""Seed example tags installed by `make seed`.

Two ready-to-use tags give the UI and the integration tests something real to
work with immediately. They ship `auto_approve_threshold=NULL`, which is the
project default: every document of these tags requires human review.
"""

from __future__ import annotations

from ipa.api.schemas.tags import TagCreate, TagFieldCreate
from ipa.core.enums import FieldType

INVOICE_TAG = TagCreate(
    slug="invoice",
    name="Invoice",
    description="A commercial invoice with vendor, amounts and dates.",
    fields=[
        TagFieldCreate(
            key="invoice_number",
            label="Invoice number",
            field_type=FieldType.STRING,
            is_required=True,
            description="The unique invoice identifier printed on the document.",
        ),
        TagFieldCreate(
            key="vendor",
            label="Vendor",
            field_type=FieldType.STRING,
            is_required=True,
            description="The company issuing the invoice.",
        ),
        TagFieldCreate(
            key="total",
            label="Total amount",
            field_type=FieldType.NUMBER,
            is_required=True,
            description="The total amount due, in the invoice currency.",
        ),
        TagFieldCreate(
            key="currency",
            label="Currency",
            field_type=FieldType.ENUM,
            enum_values=["USD", "EUR", "GBP"],
            description="ISO currency code of the invoice.",
        ),
        TagFieldCreate(
            key="invoice_date",
            label="Invoice date",
            field_type=FieldType.DATE,
            description="The date the invoice was issued.",
        ),
    ],
)

IDENTITY_DOCUMENT_TAG = TagCreate(
    slug="identity_document",
    name="Identity document",
    description="A passport or national identity document.",
    fields=[
        TagFieldCreate(
            key="document_type",
            label="Document type",
            field_type=FieldType.ENUM,
            is_required=True,
            enum_values=["passport", "national_id", "driving_licence"],
            description="The kind of identity document.",
        ),
        TagFieldCreate(
            key="full_name",
            label="Full name",
            field_type=FieldType.STRING,
            is_required=True,
            description="The holder's full name as printed.",
        ),
        TagFieldCreate(
            key="document_number",
            label="Document number",
            field_type=FieldType.STRING,
            is_required=True,
            description="The unique document number.",
        ),
        TagFieldCreate(
            key="date_of_birth",
            label="Date of birth",
            field_type=FieldType.DATE,
            is_required=True,
            description="The holder's date of birth.",
        ),
        TagFieldCreate(
            key="nationality",
            label="Nationality",
            field_type=FieldType.STRING,
            description="The holder's nationality.",
        ),
    ],
)

SEED_TAGS = (INVOICE_TAG, IDENTITY_DOCUMENT_TAG)

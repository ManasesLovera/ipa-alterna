"""Default extraction prompt assembly.

`render_prompt` turns a tag (its name, description and fields) into the system
prompt handed to the extraction LLM. `TagService.compiled_schema` uses the tag's
custom `prompt_template` when set, otherwise this default. T11 imports `render_prompt`
directly rather than re-implementing prompt assembly.
"""

from __future__ import annotations

from ipa.processing.schema import TagFieldSpec

DEFAULT_TEMPLATE = (
    "You are an expert document extraction assistant.\n"
    "You will be given the text of a {tag_name} document{description_clause}.\n"
    "Extract the fields below, returning only a JSON object conforming to the "
    "supplied schema.\n"
    "For every field, provide the value (or null when absent), a confidence in "
    "[0,1], the page number it appears on, and a short verbatim evidence snippet.\n"
    "Never invent values that are not present in the document.\n\n"
    "Fields:\n{field_lines}"
)


def render_prompt(*, tag_name: str, tag_description: str, fields: list[TagFieldSpec]) -> str:
    """Render the default extraction prompt for a tag.

    Args:
        tag_name: The tag's display name.
        tag_description: The tag's description.
        fields: The ordered field definitions.

    Returns:
        The assembled system prompt.
    """
    description_clause = f" ({tag_description})" if tag_description else ""
    lines = []
    for field in fields:
        label = field.label or field.key
        description = field.description or ""
        requirement = " (required)" if field.required else ""
        suffix = f" - {description}" if description else ""
        lines.append(f"- {label} ({field.key}){requirement}{suffix}")
    return DEFAULT_TEMPLATE.format(
        tag_name=tag_name,
        description_clause=description_clause,
        field_lines="\n".join(lines),
    )

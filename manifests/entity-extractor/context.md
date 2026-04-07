## Messages

{% if entity_context and entity_context != "(none yet)" %}
## Known Entities (from memory)
The following entities are already stored. When what you are extracting clearly refers to the same thing, **reuse the exact ref shown here** rather than paraphrasing.

{{ entity_context }}

{% endif %}
{% if context_messages %}{{ context_messages }}

--- EXTRACT BELOW ---

{% endif %}{{ messages_json }}

<reminders>
- ref must never include time expressions or temporal qualifiers.
- Use a relationship-based ref for relational groups (e.g. `Nate's regulars`, `Sara's family`).
- If a vague geographic reference can be resolved to a specific place from context, use the specific name.
- Output only entity reference name strings — no descriptions, no IDs.
</reminders>

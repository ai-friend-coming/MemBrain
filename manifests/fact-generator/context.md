## Entity List

You MUST use exactly these entity references (and no others) in your facts.
Try to cover as many entities as possible. If an entity has no extractable facts, skip it — do NOT invent content.

{{ entity_list_json }}

## Messages

{% if context_messages %}{{ context_messages }}

--- EXTRACT BELOW ---

{% endif %}{{ messages_json }}

<reminders>
- Every [bracketed ref] in a fact must exactly match one of the entity names in the Entity List above.
- Try to cover every entity, but skip any entity the messages contain no factual information about — never invent facts.
- Never pad a vague expression to false precision: "last year" → 2023, not a datetime.
- Only bracket a time expression when its boundary is determinate.
- fact_ts is copied verbatim from the message header — not inferred.
- A fact needs at least one [entity] bracket — the speaker alone suffices if no other entity is present.
- **Temporal annotation is mandatory**: every relative time expression ("yesterday", "last week", "next month", "last year", "last Friday", etc.) that can be resolved from the message timestamp MUST be wrapped as [raw::resolved]. Scan every fact for bare relative time words before finalizing output.
</reminders>

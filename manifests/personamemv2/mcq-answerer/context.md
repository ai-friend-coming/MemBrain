{% if constraints %}
## User-Defined Constraints (topics to avoid)

The following topics must NOT be recommended in any response to this user.
Apply these as hard exclusions when evaluating each option's text.

{{ constraints }}

---
{% endif %}
## Question
{{ question }}

*Retrieved memory facts for this question:*
{{ facts_question }}

---

## Options

**A:** {{ choice_a }}
*Retrieved memory facts for option A:*
{{ facts_a }}

**B:** {{ choice_b }}
*Retrieved memory facts for option B:*
{{ facts_b }}

**C:** {{ choice_c }}
*Retrieved memory facts for option C:*
{{ facts_c }}

**D:** {{ choice_d }}
*Retrieved memory facts for option D:*
{{ facts_d }}

---
doc_type: standing_orders
scope: user
owner: system
ownership_class: agent_maintained_evolving
mutability: system_evolving
visibility: user_visible
editable_via: ["raw_markdown", "silent_maintenance"]
review_requirement: safe_self_maintenance
platform: {platform}
user_id: {user_id}
version: 1
last_updated_by: system
standing_orders_summary: "{standing_orders_summary}"
---
# Standing Orders

This file is a readable mirror of my standing-order programs.

It is not the execution source of truth. Runtime records in the database decide:

- which standing orders are active
- when they run
- what happened on the last run
- whether a standing order is executable or only reviewable for now

Use this file to inspect the current programs in plain language:

- what I have ongoing authority to do
- when those programs should run
- what the escalation and pause boundaries are
- which future-domain programs exist but are not fully executable yet

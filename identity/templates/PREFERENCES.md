---
doc_type: preferences
scope: user
owner: system
ownership_class: agent_maintained_evolving
mutability: system_evolving
visibility: user_visible
editable_via: ["dashboard", "chat", "raw_markdown", "silent_maintenance"]
review_requirement: safe_self_maintenance
platform: {platform}
user_id: {user_id}
version: 1
last_updated_by: system
preferences_summary: "{preferences_summary}"
---
# User Preferences

User-volunteered preferences and stable personal facts captured by
reflection during conversation. Each ``pref_<key>`` frontmatter entry
above is a single key-value pair the user told PaLLi about themselves
— a favorite, a habit, a tool, a stance.

Reflection (``core/turn/reflect.py:_apply_user_facts``) writes here
silently. The dashboard preference editor and chat-driven preference
flows can override or correct these values directly.

This document is the canonical home for user preferences. The
``user_memory`` table is a derived index over this layer, periodically
rebuilt from these documents. When the two disagree, this document
wins.

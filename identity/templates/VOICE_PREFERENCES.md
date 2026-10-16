---
doc_type: voice_preferences
scope: user
owner: shared
ownership_class: user_authored_preferences
mutability: user_editable
visibility: user_visible
editable_via: ["dashboard", "chat", "raw_markdown"]
review_requirement: user_directed
platform: {platform}
user_id: {user_id}
version: 1
last_updated_by: system
voice_engine: "{voice_engine}"
interaction_mode: "{interaction_mode}"
presence_profile: "{presence_profile}"
focus_state: "{focus_state}"
local_transport_mode: "{local_transport_mode}"
---
# Voice Preferences

This file captures how I should behave in live voice sessions for this operator.

Shared defaults:

- Voice engine controls whether I stay on the classic speech stack or request the OpenAI Realtime bridge when that surface supports it.
- Interaction mode decides whether I wait for direct address or can listen passively.
- Presence profile controls how socially available I feel.
- Focus state controls whether my passive mode stays responsive or remains quiet.
- Local transport mode controls how my terminal talk UI starts up on this machine.

These settings are shared across voice surfaces in the local product line.

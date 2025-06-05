# PaLLi
PaLLi – An LLM Discord Bot with Boundaries

**PaLLi** is (currently) a Discord-based assistant powered by a large language model. It’s lightweight, fast, and most importantly—it knows when to *stay quiet*.
Note that this is a **Public Beta Test**

---

## ✨ Why PaLLi?
PaLLi is *not* a basic command bot. It behaves more like a thoughtful observer than a chatbot on auto-pilot. Its goal is to feel like a smart participant—not just a message responder.

*Most bots don’t know when to shut up. PaLLi does.*

---

## 🔍 Key Features (Live Now)
- 🧠 **Response Decision-Making** – Bot uses logic to decide whether to reply.
- 🧩 **Prompt-Based Analysis (Optional)** – Reads tone, urgency, and unspoken goals to shape replies.
- 🧘 **Restraint & Context Awareness** – Doesn’t flood the channel with every message.
- ✍️ **DM Customization** – Users can personalize how it responds in private chats.
- 🛠️ **Slash Command Suite** – Toggle behavior, manage prompts, reset history.

---

## 🚧 Upcoming Features (Rolling Out Weekly During Beta)

We are rolling out additional features **in a staggered schedule**, roughly one per week:
- 🔎 **RAG (Both message history and file-aware context retrieval)** 
- 🔊 **Voice: STT/TTS in chat and channels**
- 🧠 **Proactive Task Awareness & Suggestions**
- 🤖 **Tool Invocation via Intent Recognition**

---

## 🧭 Slash Commands (Available in Beta)
Here are the commands you can use with PaLLi during the MVP public beta:

### 🛠️ Channel & Behavior Controls
- **/status**  
  View the bot’s current behavior mode (e.g. analysis-enabled, decision logic active).

- **/toggle_mention**  
  Toggle *mention-only* mode for this channel. When enabled, PaLLi will only reply if directly mentioned.

- **/toggle_analysis**  
  Enable or disable analysis mode, which enhances responses using emotional tone, intent, urgency, and context cues.

- **/toggle_decisions**  
  Toggle decision-making logic. When enabled, the bot chooses whether a reply is appropriate.

### 🧠 Prompt Customization (DMs Only)
- **/set_system_prompt** `<prompt>`  
  Set a custom system prompt for your private conversation with the bot. This changes its personality or instructions.

- **/clear_system_prompt**  
  Reset your DM prompt back to the default personality.

- **/show_system_prompt**  
  Display the current editable system prompt (works in any channel).

### 🧹 Message History
- **/reset_message_history**  
  (DMs only) Clears the conversation log for your direct messages with PaLLi.

### 🧪 Disabled for Now (Coming Soon)
These commands appear but are **disabled** during the early beta:
- **/toggle_rag**  
- **/toggle_embedding**  
- **/join_voice**  
- **/leave_voice**

They will be re-enabled in stages during the public beta as features are rolled out.
Need help? Just type `/` in any Discord chat with PaLLi to see what’s available.

---

## 🔒 Privacy & Beta Disclaimer
**PaLLi is currently in public beta.** While we strive for stability and thoughtful design, please be aware that occasional bugs, unexpected behavior, or quirks may occur.
All message history is stored **locally** for the purpose of maintaining conversation context within Discord channels. No data is shared externally or used for any commercial purpose.
Custom prompts and message logs are only accessible to the bot owner and are used solely to improve in-channel interactions and performance.
If you encounter any issues or have privacy concerns, feel free to reach out directly via Discord.
Thank you for helping shape PaLLi during this experimental phase!

---

## 🧪 Want to Join the Beta?
We’re hosting public testing through our Discord community.  
Jump in, try it out, break things, and shape the future of human-bot interaction.

🔗 **[Discord Link Coming Today - 6.5.2025]**

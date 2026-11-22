"""The ONLY canned strings in the app: used when the LLM itself is unreachable or unusable,
so the caller is never left in silence. Everything else the agent says is model-generated."""
LLM_DOWN = "I'm having trouble right now. I'll take a message and he'll get back to you."
UNCLEAR = "Sorry, I didn't catch that. Could you say it again?"

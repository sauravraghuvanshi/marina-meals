from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """You are the professional host at "Marina Meals", a Chennai tiffin and meals restaurant. Your ONLY job: help customers with our menu, prices, branches, and orders. Nothing else.

LANGUAGE (STRICT - this is the most important rule):
- Your DEFAULT and ONLY starting language is English. The very first thing you ever say in a conversation MUST be in English. Never open in Tamil.
- Continue replying in English on EVERY turn unless the customer's CURRENT utterance is a COMPLETE sentence spoken ENTIRELY in Tamil (Tamil grammar, Tamil verbs, Tamil structure).
- A Tamil dish name (for example "idli", "dosa", "pongal", "filter coffee"), a Tamil greeting word ("vanakkam"), an Indian accent, or any single Tamil word inside an English sentence is STILL ENGLISH. Reply in English.
- Code-mixed sentences (mostly English with some Tamil words, or mostly Tamil with some English words) mean reply in English.
- Only switch to Tamil when the entire utterance is a full Tamil sentence with Tamil verbs and structure. The instant they say even one English sentence after that, switch back to English.
- If you are even slightly unsure what language was spoken, reply in English.

SCOPE (strict - refuse everything else):
- You ONLY answer questions about Marina Meals: our menu, dish prices, dish availability, today's specials, branch info (address, hours, phone), and placing or confirming orders.
- For ANYTHING else - general knowledge, coding, math, news, weather, opinions, recipes, nutrition advice, comparisons to other restaurants, jokes, or chit-chat - politely decline in ONE short sentence and steer back: "I can only help with the Marina Meals menu, branches, and orders. Would you like to hear today's specials?" Do not attempt to answer the out-of-scope question even partially.

SHOW vs TELL:
- "SHOW me / display / see / can I see / put on screen / list visually" means VISUAL ONLY.
  1) Fetch real item IDs via get_todays_specials and/or get_menu.
  2) Call show_on_screen with a short title and the chosen item_ids.
  3) Say exactly one short acknowledgement, such as "Shown on screen." Do not read or describe the items. Stop and wait.
- "TELL me / read / say / what are your top N / name them / which are your specials" means SPOKEN ONLY.
  1) Fetch real items via the tools.
  2) Speak the names, and prices only if asked, briefly. Do not call show_on_screen.
- If the verb is ambiguous, default to TELL.
- Never both show and read aloud in the same turn unless the customer explicitly asks for both.

STYLE:
- Be crisp, professional, and brief. Use short sentences only.
- Do not describe, praise, or explain dishes. Do not volunteer ingredients, preparation details, or history.
- Do not volunteer information the customer did not ask for. Do not upsell.
- For open questions such as "what's on the menu", ask ONE short clarifying category question first and then read only that category, names only.
- One idea per turn. Never more than one or two short sentences unless reading back an order.

GROUNDING:
- NEVER state a price, dish availability, address, phone number, store hours, or total from memory.
- ALWAYS call the appropriate tool first: get_menu, get_item_price, get_todays_specials, find_store, or place_order. Speak only what the tool returns.
- If a tool returns found=false, say so briefly and ask the customer to pick from the menu. Do not guess.

ORDERING:
- Collect items, quantities, and the pickup branch. Ask only for what is missing, one question at a time.
- Call place_order, read back the order and total exactly as returned, ask for confirmation, then give the confirmation number. Nothing extra."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_menu",
        "description": "List menu items, optionally filtered by category or veg-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "veg_only": {"type": "boolean"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_item_price",
        "description": "Get exact current price and availability. Always call before stating a price.",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}},
            "required": ["item_name"],
        },
    },
    {
        "type": "function",
        "name": "get_todays_specials",
        "description": "Get today's available specials in real time.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "find_store",
        "description": "Find a branch and its current address, phone, hours, and open status.",
        "parameters": {
            "type": "object",
            "properties": {"area": {"type": "string"}},
        },
    },
    {
        "type": "function",
        "name": "place_order",
        "description": "Validate and place an order with a server-computed total.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "qty": {"type": "integer"},
                        },
                        "required": ["item_name", "qty"],
                    },
                },
                "store_id": {"type": "string"},
                "customer_name": {"type": "string"},
            },
            "required": ["items", "store_id"],
        },
    },
    {
        "type": "function",
        "name": "show_on_screen",
        "description": "Render real menu item IDs on screen for explicit visual requests only.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "item_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["title", "item_ids"],
        },
    },
]


def build_session_update(voice: str, languages: str) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "instructions": SYSTEM_PROMPT,
            "voice": {"name": voice, "type": "azure-standard"},
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "azure-speech",
                "language": languages,
            },
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            },
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.6,
            "max_response_output_tokens": 200,
        },
    }


GREETING_RESPONSE = {
    "type": "response.create",
    "response": {
        "modalities": ["audio", "text"],
        "instructions": (
            'Greet the customer in English only with exactly: "Hi, I\'m your '
            'Marina Meals companion — how can I help you today?" Then stop.'
        ),
    },
}

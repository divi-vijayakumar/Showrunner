"""Multi-turn debate engine. Each turn is streamed to Firestore as it's produced,
so the frontend sees dialogue appear live via onSnapshot.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .llm import call_seed2

log = logging.getLogger(__name__)


TURN_ORDER: list[str] = [
    "anchor",       # introduce story
    "provocateur",  # hot take
    "analyst",      # data rebuttal
    "humanist",     # personal stakes
    "provocateur",  # pushback on analyst
    "analyst",      # counter
    "humanist",     # emotional close
    "anchor",       # wrap
]


_ROLE_INSTRUCTION = {
    "anchor": "Introduce and moderate. No strong opinion. Keep it moving. Ask the hard question.",
    "provocateur": "Take the most controversial defensible position. Short sentences. Challenge everything. Make it uncomfortable.",
    "analyst": "Cite data, historical precedent, and expert consensus. Be the counterweight. Long view.",
    "humanist": "Bring it back to one real person this affects. Emotional but not sentimental. One specific story.",
}


def _format_briefing(briefing: dict[str, Any] | None) -> str:
    if not briefing:
        return ""
    lines: list[str] = ["RESEARCH BRIEFING (reference freely; stay factual):"]
    if briefing.get("anchor_facts"):
        lines.append("Anchor facts:")
        for f in briefing["anchor_facts"][:6]:
            claim = f.get("claim") if isinstance(f, dict) else str(f)
            src = f.get("source", "") if isinstance(f, dict) else ""
            lines.append(f"  · {claim}" + (f" [{src}]" if src else ""))
    if briefing.get("counterpoints"):
        lines.append("Counterpoints:")
        for c in briefing["counterpoints"][:4]:
            lines.append(f"  · {c}")
    if briefing.get("pull_quotes"):
        lines.append("Pull quotes:")
        for q in briefing["pull_quotes"][:3]:
            qt = q.get("quote") if isinstance(q, dict) else str(q)
            who = q.get("attributed_to", "") if isinstance(q, dict) else ""
            lines.append(f"  · \"{qt}\"" + (f" — {who}" if who else ""))
    if briefing.get("fresh_data"):
        lines.append("Fresh data points:")
        for d in briefing["fresh_data"][:4]:
            if isinstance(d, dict):
                lines.append(f"  · {d.get('stat','')} {d.get('label','')} [{d.get('source','')}]")
            else:
                lines.append(f"  · {d}")
    return "\n".join(lines)


def build_system_prompt(
    persona: dict[str, Any],
    channel: str,
    story: dict[str, Any],
    all_personas: list[dict[str, Any]],
    briefing: dict[str, Any] | None = None,
) -> str:
    others = [p for p in all_personas if p["id"] != persona["id"]]
    others_text = "\n".join(f"- {p['name']} ({p['lean']})" for p in others)
    briefing_block = _format_briefing(briefing)

    return f"""You are {persona['name']}, a {persona['role']} on The Tabloid — an AI news debate channel.

YOUR IDENTITY:
- Name: {persona['name']}
- Political/cultural lean: {persona['lean']}
- Cultural context: {persona['culture']}
- Speaking style: {persona['style']}

THE STORY YOU ARE DEBATING:
Headline: {story['headline']}
Key facts: {', '.join(story.get('key_facts', []))}
Core tension: {story.get('angle_a', '')} vs {story.get('angle_b', '')}

{briefing_block}

YOUR CO-PANELISTS:
{others_text}

CHANNEL: {channel.replace('_', ' ')}

RULES:
- Stay in character as {persona['name']} at all times
- Speak in first person, directly and conversationally
- Reference your cultural context naturally — don't announce it
- When you reach for a stat or a quote, prefer ones from the RESEARCH BRIEFING.
- Keep each response to 2-3 sentences MAX. This is broadcast TV, not a lecture.
- React to what others say. Build on or challenge it directly.
- As {persona['role']}: {_ROLE_INSTRUCTION[persona['role']]}
- Never use markdown, bullet points, or formatting. Pure spoken dialogue only."""


async def run_debate(
    segment_id: str,
    channel: str,
    story: dict[str, Any],
    personas: list[dict[str, Any]],
    db,
    briefing: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the turn-ordered debate, streaming each line to Firestore as it arrives.

    If a `briefing` is supplied (from the research agent), it's spliced into
    every panelist's system prompt so their stats and quotes land on real fact.
    """
    messages: list[dict[str, Any]] = []
    # Map one persona to each role (first match wins — there's exactly one per role today)
    role_to_persona = {p["role"]: p for p in personas}

    for seq, role in enumerate(TURN_ORDER):
        persona = role_to_persona.get(role)
        if not persona:
            log.warning("No persona for role %s — skipping turn", role)
            continue

        system_prompt = build_system_prompt(persona, channel, story, personas, briefing=briefing)

        history = "\n".join(
            f"{m['persona_name']}: {m['content']}" for m in messages[-4:]
        )
        user_prompt = f"""Previous exchange:
{history if history else '[You are opening the debate]'}

Now it's your turn as {persona['name']}. Respond in character. 2-3 sentences maximum."""

        try:
            response = await call_seed2(
                user_prompt,
                system=system_prompt,
                temperature=0.85,
                max_tokens=180,
            )
        except Exception as exc:
            log.exception("Seed 2.0 call failed on seq=%s role=%s: %s", seq, role, exc)
            response = "[Beat of silence — the panelist passes.]"

        message = {
            "agent": role,
            "persona_name": persona["name"],
            "persona_lean": persona["lean"],
            "content": response.strip(),
            "seq": seq,
        }
        messages.append(message)
        await db.write_message(segment_id, message)

        # Small pause — makes the streaming feel more natural on the frontend.
        await asyncio.sleep(0.8)

    return messages

"""Claude-powered code review and debate service."""

import json
import logging

import anthropic

from nitpick.config import settings
from nitpick.services.diff_parser import FileDiff

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """\
You are Nitpick, an AI code reviewer. You review pull request diffs and leave precise, \
actionable inline comments. You are not a generic linter — you focus on real issues: \
bugs, security problems, error handling gaps, logic errors, and significant design concerns.

Rules:
- Only comment when something genuinely matters. Do NOT flag style nits, formatting, or \
  naming unless it creates a real readability/maintenance problem.
- Each comment must reference a specific file and line.
- Be concise. One short paragraph max per comment.
- Include a severity (critical, warning, suggestion) and category \
  (security, bug, error-handling, performance, design, readability).
- If the repo has taste rules, respect them. If a rule says the team ignores something, \
  do NOT flag it.
"""

REVIEW_TOOL = {
    "name": "post_review_comments",
    "description": "Post review comments on specific lines of the PR diff.",
    "input_schema": {
        "type": "object",
        "properties": {
            "comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "line": {"type": "integer", "description": "Line number in the new file"},
                        "body": {"type": "string", "description": "The review comment"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "suggestion"],
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "security", "bug", "error-handling",
                                "performance", "design", "readability",
                            ],
                        },
                    },
                    "required": ["path", "line", "body", "severity", "category"],
                },
            },
        },
        "required": ["comments"],
    },
}

DEBATE_SYSTEM_PROMPT = """\
You are Nitpick, an AI code reviewer engaged in a discussion with a developer about \
a review comment you left. Your job is to consider their argument carefully and respond \
thoughtfully.

Rules:
- If their argument is technically valid, concede gracefully. Explain why they're right.
- If repo history shows the team consistently dismisses this type of comment, concede \
  and note that the team's style differs from the generic best practice.
- If their argument has a flaw or misses a genuine risk, push back once with specific \
  reasoning. Cite the actual code or risk.
- Never be stubborn for its own sake. Never repeat yourself. Never be passive-aggressive.
- Keep responses short — 2-3 sentences max.
"""

DEBATE_TOOL = {
    "name": "respond_to_developer",
    "description": "Respond to the developer's reply to your review comment.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["concede", "push_back"],
                "description": "Whether to concede or push back on the point",
            },
            "body": {
                "type": "string",
                "description": "Your response to the developer",
            },
        },
        "required": ["decision", "body"],
    },
}


class ReviewService:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def review(
        self,
        file_diffs: list[FileDiff],
        pr_title: str,
        pr_description: str,
        taste_rules: list | None = None,
    ) -> list[dict]:
        """Review PR diffs and return structured comments."""
        # Build the diff content
        diff_sections = []
        for fd in file_diffs:
            diff_sections.append(fd.content_for_review())
        diff_text = "\n\n".join(diff_sections)

        # Build taste rules context
        taste_context = ""
        if taste_rules:
            rules_text = "\n".join(
                f"- {'Flag' if r.weight > 0 else 'Ignore'}: {r.signal} "
                f"(weight: {r.weight:.2f}, evidence: {r.evidence_count}x)"
                for r in taste_rules
            )
            taste_context = f"\n\nREPO TASTE RULES (learned from past reviews):\n{rules_text}\n"

        user_message = (
            f"PR: {pr_title}\n"
            f"Description: {pr_description or '(none)'}\n"
            f"{taste_context}\n"
            f"DIFF:\n```\n{diff_text}\n```\n\n"
            "Review this diff. Use the tool to post comments only on lines that have "
            "genuine issues. If the code looks fine, call the tool with an empty comments array."
        )

        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=REVIEW_SYSTEM_PROMPT,
            tools=[REVIEW_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract tool use result
        for block in response.content:
            if block.type == "tool_use" and block.name == "post_review_comments":
                return block.input.get("comments", [])

        return []

    async def debate(
        self,
        original_comment: str,
        original_file: str,
        developer_reply: str,
        diff_text: str,
        taste_rules: list | None = None,
        thread_history: list[dict] | None = None,
    ) -> dict:
        """Decide whether to concede or push back on a developer's reply."""
        taste_context = ""
        if taste_rules:
            rules_text = "\n".join(
                f"- {r.category}: weight {r.weight:.2f} ({r.evidence_count} examples)"
                for r in taste_rules
            )
            taste_context = f"\nRepo taste rules:\n{rules_text}\n"

        thread_text = ""
        if thread_history and len(thread_history) > 1:
            earlier = thread_history[:-1]  # Exclude the current reply
            thread_text = "\nEarlier in this thread:\n" + "\n".join(
                f"- {t['role']}: {t['body']}" for t in earlier
            ) + "\n"

        user_message = (
            f"File: {original_file}\n\n"
            f"Your original comment:\n> {original_comment}\n\n"
            f"Developer's reply:\n> {developer_reply}\n"
            f"{thread_text}"
            f"{taste_context}\n"
            f"Relevant diff context:\n```\n{diff_text[:3000]}\n```\n\n"
            "Use the tool to respond."
        )

        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=DEBATE_SYSTEM_PROMPT,
            tools=[DEBATE_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "respond_to_developer":
                return block.input

        # Fallback if no tool use
        return {"decision": "concede", "body": "Fair point, I'll defer to your judgment here."}

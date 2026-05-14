"""Tests for the Claude review and debate service."""

from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest

from nitpick.services.diff_parser import FileDiff, DiffHunk


def _make_tool_use_block(name, input_data):
    """Create a mock tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    return block


def _make_text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


@dataclass
class FakeTasteRule:
    category: str
    signal: str
    weight: float
    evidence_count: int


@pytest.fixture
def mock_anthropic():
    with patch("nitpick.services.reviewer.settings") as mock_settings:
        mock_settings.anthropic_api_key = "test-key"
        with patch("nitpick.services.reviewer.anthropic") as mock_mod:
            mock_client = AsyncMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            yield mock_client


@pytest.fixture
def sample_diffs():
    return [
        FileDiff(
            path="src/auth.py",
            hunks=[
                DiffHunk(
                    header="@@ -1,4 +1,5 @@",
                    old_start=1, old_count=4, new_start=1, new_count=5,
                    lines=[" import hashlib", "+import secrets", ""],
                    start_position=1,
                )
            ],
            added_lines=1,
            removed_lines=0,
        )
    ]


class TestReviewService:
    async def test_review_returns_comments(self, mock_anthropic, sample_diffs):
        from nitpick.services.reviewer import ReviewService

        comments_data = [
            {"path": "src/auth.py", "line": 5, "body": "Use bcrypt", "severity": "warning", "category": "security"}
        ]
        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("post_review_comments", {"comments": comments_data})]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.review(sample_diffs, "Fix auth", "Update password hashing")

        assert len(result) == 1
        assert result[0]["path"] == "src/auth.py"
        assert result[0]["severity"] == "warning"

    async def test_review_empty_comments(self, mock_anthropic, sample_diffs):
        from nitpick.services.reviewer import ReviewService

        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("post_review_comments", {"comments": []})]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.review(sample_diffs, "Clean code", "Refactor")

        assert result == []

    async def test_review_no_tool_use_returns_empty(self, mock_anthropic, sample_diffs):
        """If Claude doesn't use the tool, return empty list."""
        from nitpick.services.reviewer import ReviewService

        mock_response = MagicMock()
        mock_response.content = [_make_text_block("Looks good to me!")]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.review(sample_diffs, "Good PR", "")

        assert result == []

    async def test_review_includes_taste_rules(self, mock_anthropic, sample_diffs):
        from nitpick.services.reviewer import ReviewService

        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("post_review_comments", {"comments": []})]
        mock_anthropic.messages.create.return_value = mock_response

        rules = [
            FakeTasteRule(category="security", signal="Always flag: hardcoded secrets", weight=0.9, evidence_count=10),
            FakeTasteRule(category="readability", signal="Ignore: single-letter vars", weight=-0.5, evidence_count=5),
        ]

        service = ReviewService()
        await service.review(sample_diffs, "PR Title", "Description", taste_rules=rules)

        # Verify the prompt includes taste rules
        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "REPO TASTE RULES" in user_msg
        assert "hardcoded secrets" in user_msg
        assert "single-letter vars" in user_msg
        assert "Flag" in user_msg   # weight > 0
        assert "Ignore" in user_msg  # weight < 0

    async def test_review_sends_correct_model(self, mock_anthropic, sample_diffs):
        from nitpick.services.reviewer import ReviewService

        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("post_review_comments", {"comments": []})]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        await service.review(sample_diffs, "Title", "Desc")

        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert "claude-sonnet" in call_kwargs["model"]


class TestDebateService:
    async def test_debate_push_back(self, mock_anthropic):
        from nitpick.services.reviewer import ReviewService

        decision = {"decision": "push_back", "body": "The risk is real because..."}
        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("respond_to_developer", decision)]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.debate(
            original_comment="This SQL is injectable",
            original_file="src/db.py",
            developer_reply="We sanitize inputs upstream",
            diff_text="+ cursor.execute(f'SELECT * FROM {table}')",
        )

        assert result["decision"] == "push_back"
        assert "risk" in result["body"]

    async def test_debate_concede(self, mock_anthropic):
        from nitpick.services.reviewer import ReviewService

        decision = {"decision": "concede", "body": "You're right, the upstream validation covers this."}
        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("respond_to_developer", decision)]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.debate(
            original_comment="Missing null check",
            original_file="src/handler.py",
            developer_reply="The ORM guarantees non-null here",
            diff_text="+ user = db.get_user(id)",
        )

        assert result["decision"] == "concede"

    async def test_debate_fallback_on_no_tool_use(self, mock_anthropic):
        from nitpick.services.reviewer import ReviewService

        mock_response = MagicMock()
        mock_response.content = [_make_text_block("I think you make a good point.")]
        mock_anthropic.messages.create.return_value = mock_response

        service = ReviewService()
        result = await service.debate(
            original_comment="Unused import",
            original_file="src/x.py",
            developer_reply="It's used in a type annotation",
            diff_text="+import typing",
        )

        assert result["decision"] == "concede"
        assert "defer" in result["body"].lower()

    async def test_debate_includes_thread_history(self, mock_anthropic):
        from nitpick.services.reviewer import ReviewService

        decision = {"decision": "push_back", "body": "Still concerned."}
        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("respond_to_developer", decision)]
        mock_anthropic.messages.create.return_value = mock_response

        thread = [
            {"role": "bot", "body": "This could leak memory"},
            {"role": "developer", "body": "We have a cleanup handler"},
            {"role": "developer", "body": "Also see the test coverage"},
        ]

        service = ReviewService()
        await service.debate(
            original_comment="Memory leak risk",
            original_file="src/pool.py",
            developer_reply="Also see the test coverage",
            diff_text="+pool = Pool()",
            thread_history=thread,
        )

        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "Earlier in this thread" in user_msg
        assert "cleanup handler" in user_msg

    async def test_debate_includes_taste_rules(self, mock_anthropic):
        from nitpick.services.reviewer import ReviewService

        decision = {"decision": "concede", "body": "Team prefers this style."}
        mock_response = MagicMock()
        mock_response.content = [_make_tool_use_block("respond_to_developer", decision)]
        mock_anthropic.messages.create.return_value = mock_response

        rules = [
            FakeTasteRule(category="readability", signal="single-letter vars ok", weight=-0.4, evidence_count=8),
        ]

        service = ReviewService()
        await service.debate(
            original_comment="Use descriptive names",
            original_file="src/math.py",
            developer_reply="x and y are standard in math code",
            diff_text="+def cross(x, y):",
            taste_rules=rules,
        )

        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "taste rules" in user_msg.lower() or "readability" in user_msg

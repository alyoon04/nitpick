"""Tests for worker tasks — mock GitHub, Claude, and DB."""

from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest


# Fake DB models for worker tests
@dataclass
class FakeRepo:
    id: int = 1
    github_repo_id: int = 99999
    owner: str = "octocat"
    name: str = "hello-world"
    installation_id: int = 12345


@dataclass
class FakePR:
    id: int = 10
    repo_id: int = 1
    github_pr_number: int = 42
    merged: bool = False


@dataclass
class FakeComment:
    id: int = 100
    repo_id: int = 1
    pr_id: int = 10
    github_comment_id: int = 777
    file_path: str = "src/auth.py"
    body: str = "This is insecure"
    category: str = "security"
    severity: str = "warning"
    posted_by_bot: bool = True
    was_addressed: bool = False
    was_dismissed: bool = False


@dataclass
class FakeTasteRule:
    id: int = 1
    repo_id: int = 1
    category: str = "security"
    signal: str = "Flag insecure hashing"
    weight: float = 0.5
    evidence_count: int = 3


@dataclass
class FakeThread:
    id: int = 1
    root_comment_id: int = 100
    repo_id: int = 1
    pr_id: int = 10
    turns: list = None
    outcome: str = None

    def __post_init__(self):
        if self.turns is None:
            self.turns = []


class TestReviewTask:
    @patch("nitpick.workers.review.ReviewService")
    @patch("nitpick.workers.review.GitHubClient")
    @patch("nitpick.workers.review.async_session")
    @patch("nitpick.workers.review.parse_diff")
    async def test_review_posts_comments(self, mock_parse, mock_session, mock_gh_cls, mock_reviewer_cls):
        from nitpick.workers.review import review_task
        from nitpick.services.diff_parser import FileDiff, DiffHunk

        # Setup GitHub mock
        mock_gh = AsyncMock()
        mock_gh.get_pull_request.return_value = {
            "title": "Fix auth",
            "body": "Update hashing",
            "user": {"login": "octocat"},
            "base": {"sha": "abc", "repo": {"id": 99999}},
            "head": {"sha": "def"},
        }
        mock_gh.get_pull_request_diff.return_value = "diff content"
        mock_gh.create_review.return_value = [{"id": 888}]
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Setup diff parser
        file_diff = FileDiff(path="src/auth.py", added_lines=2)
        mock_parse.return_value = [file_diff]

        # Setup DB mock
        mock_db = AsyncMock()
        mock_db.add = MagicMock()  # add() is sync in SQLAlchemy
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # No existing repo
        mock_result.scalars.return_value.all.return_value = []  # No taste rules
        mock_db.execute.return_value = mock_result
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        # Setup reviewer mock
        mock_reviewer = AsyncMock()
        mock_reviewer.review.return_value = [
            {"path": "src/auth.py", "line": 5, "body": "Use bcrypt", "severity": "warning", "category": "security"}
        ]
        mock_reviewer_cls.return_value = mock_reviewer

        await review_task({}, installation_id=12345, repo_full_name="octocat/hello-world", pr_number=42)

        # Verify review was posted to GitHub
        mock_gh.create_review.assert_called_once()
        # Verify comments stored in DB
        assert mock_db.add.call_count >= 2  # repo + PR + at least 1 comment
        mock_db.commit.assert_called_once()

    @patch("nitpick.workers.review.GitHubClient")
    @patch("nitpick.workers.review.async_session")
    @patch("nitpick.workers.review.parse_diff")
    async def test_review_skips_empty_diff(self, mock_parse, mock_session, mock_gh_cls):
        from nitpick.workers.review import review_task

        mock_gh = AsyncMock()
        mock_gh.get_pull_request.return_value = {
            "title": "Chore", "body": "",
            "user": {"login": "bot"},
            "base": {"sha": "a", "repo": {"id": 1}},
            "head": {"sha": "b"},
        }
        mock_gh.get_pull_request_diff.return_value = ""
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        mock_parse.return_value = []  # No reviewable files

        await review_task({}, installation_id=1, repo_full_name="a/b", pr_number=1)

        # Should not touch the DB or call reviewer
        mock_session.assert_not_called()


class TestReplyTask:
    @patch("nitpick.workers.reply.ReviewService")
    @patch("nitpick.workers.reply.GitHubClient")
    @patch("nitpick.workers.reply.async_session")
    async def test_reply_concede_decreases_weight(self, mock_session, mock_gh_cls, mock_reviewer_cls):
        from nitpick.workers.reply import reply_task

        # Setup DB
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        call_count = [0]
        original_comment = FakeComment()
        thread = None  # No existing thread
        taste_rule = FakeTasteRule(weight=0.7)

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = original_comment  # Find original comment
            elif call_count[0] == 2:
                result.scalar_one_or_none.return_value = thread  # No existing thread
            elif call_count[0] == 3:
                result.scalars.return_value.all.return_value = [taste_rule]  # Taste rules
            elif call_count[0] == 4:
                result.scalar_one_or_none.return_value = taste_rule  # Find rule to adjust
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        # Setup GitHub
        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "diff text"
        mock_gh.reply_to_comment.return_value = {}
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        # Setup reviewer - concede
        mock_reviewer = AsyncMock()
        mock_reviewer.debate.return_value = {"decision": "concede", "body": "Fair point."}
        mock_reviewer_cls.return_value = mock_reviewer

        await reply_task(
            {},
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
            comment_id=777,
            reply_body="This is fine actually",
            reply_user="octocat",
        )

        # Verify reply posted
        mock_gh.reply_to_comment.assert_called_once()
        # Verify weight decreased (0.7 - 0.1 = 0.6)
        assert taste_rule.weight == pytest.approx(0.6)
        mock_db.commit.assert_called_once()

    @patch("nitpick.workers.reply.async_session")
    async def test_reply_ignores_non_bot_comment(self, mock_session):
        from nitpick.workers.reply import reply_task

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Comment not found
        mock_db.execute.return_value = mock_result
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        await reply_task(
            {},
            installation_id=1,
            repo_full_name="a/b",
            pr_number=1,
            comment_id=999,
            reply_body="hello",
            reply_user="someone",
        )

        # Should return early, no commit
        mock_db.commit.assert_not_called()


class TestIngestTask:
    @patch("nitpick.workers.ingest.GitHubClient")
    @patch("nitpick.workers.ingest.async_session")
    async def test_ingest_addressed_increases_weight(self, mock_session, mock_gh_cls):
        from nitpick.workers.ingest import ingest_task

        pr = FakePR()
        comment = FakeComment(file_path="src/auth.py")
        taste_rule = FakeTasteRule(weight=0.5, evidence_count=3)

        call_count = [0]

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = pr
            elif call_count[0] == 2:
                result.scalars.return_value.all.return_value = [comment]
            elif call_count[0] == 3:
                result.scalar_one_or_none.return_value = taste_rule
            return result

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        # GitHub returns diff containing the file
        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "diff --git a/src/auth.py b/src/auth.py\n+fixed"
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        await ingest_task(
            {},
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

        assert pr.merged is True
        assert comment.was_addressed is True
        assert comment.was_dismissed is False
        assert taste_rule.weight == pytest.approx(0.55)  # 0.5 + 0.05
        assert taste_rule.evidence_count == 4

    @patch("nitpick.workers.ingest.GitHubClient")
    @patch("nitpick.workers.ingest.async_session")
    async def test_ingest_dismissed_decreases_weight(self, mock_session, mock_gh_cls):
        from nitpick.workers.ingest import ingest_task

        pr = FakePR()
        comment = FakeComment(file_path="src/auth.py")
        taste_rule = FakeTasteRule(weight=0.5, evidence_count=5)

        call_count = [0]

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = pr
            elif call_count[0] == 2:
                result.scalars.return_value.all.return_value = [comment]
            elif call_count[0] == 3:
                result.scalar_one_or_none.return_value = taste_rule
            return result

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        # GitHub returns diff NOT containing the file
        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "diff --git a/other.py b/other.py\n+something"
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        await ingest_task(
            {},
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

        assert comment.was_addressed is False
        assert comment.was_dismissed is True
        assert taste_rule.weight == pytest.approx(0.45)  # 0.5 - 0.05
        assert taste_rule.evidence_count == 6

    @patch("nitpick.workers.ingest.async_session")
    async def test_ingest_unknown_pr_skips(self, mock_session):
        from nitpick.workers.ingest import ingest_task

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        await ingest_task(
            {},
            installation_id=1,
            repo_full_name="a/b",
            pr_number=999,
        )

        mock_db.commit.assert_not_called()

    @patch("nitpick.workers.ingest.GitHubClient")
    @patch("nitpick.workers.ingest.async_session")
    async def test_ingest_creates_new_taste_rule(self, mock_session, mock_gh_cls):
        from nitpick.workers.ingest import ingest_task

        pr = FakePR()
        comment = FakeComment(file_path="src/new.py", category="performance")

        call_count = [0]

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = pr
            elif call_count[0] == 2:
                result.scalars.return_value.all.return_value = [comment]
            elif call_count[0] == 3:
                result.scalar_one_or_none.return_value = None  # No existing rule
            return result

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        # File IS in diff → addressed → initial weight 0.5
        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "src/new.py something"
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        await ingest_task(
            {},
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

        # Verify a new rule was added
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestTasteWeightBounds:
    """Verify weight clamping at boundaries."""

    @patch("nitpick.workers.ingest.GitHubClient")
    @patch("nitpick.workers.ingest.async_session")
    async def test_weight_clamped_at_max(self, mock_session, mock_gh_cls):
        from nitpick.workers.ingest import ingest_task

        pr = FakePR()
        comment = FakeComment(file_path="src/x.py")
        taste_rule = FakeTasteRule(weight=0.98)

        call_count = [0]

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = pr
            elif call_count[0] == 2:
                result.scalars.return_value.all.return_value = [comment]
            elif call_count[0] == 3:
                result.scalar_one_or_none.return_value = taste_rule
            return result

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "src/x.py modified"
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        await ingest_task({}, installation_id=1, repo_full_name="a/b", pr_number=1)

        assert taste_rule.weight <= 1.0

    @patch("nitpick.workers.reply.ReviewService")
    @patch("nitpick.workers.reply.GitHubClient")
    @patch("nitpick.workers.reply.async_session")
    async def test_concede_weight_clamped_at_min(self, mock_session, mock_gh_cls, mock_reviewer_cls):
        from nitpick.workers.reply import reply_task

        original_comment = FakeComment()
        taste_rule = FakeTasteRule(weight=-0.95)

        call_count = [0]

        def mock_execute(query):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = original_comment
            elif call_count[0] == 2:
                result.scalar_one_or_none.return_value = None  # No thread
            elif call_count[0] == 3:
                result.scalars.return_value.all.return_value = [taste_rule]
            elif call_count[0] == 4:
                result.scalar_one_or_none.return_value = taste_rule
            return result

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_db

        mock_gh = AsyncMock()
        mock_gh.get_pull_request_diff.return_value = "diff"
        mock_gh.reply_to_comment.return_value = {}
        mock_gh.__aenter__ = AsyncMock(return_value=mock_gh)
        mock_gh.__aexit__ = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        mock_reviewer = AsyncMock()
        mock_reviewer.debate.return_value = {"decision": "concede", "body": "Ok."}
        mock_reviewer_cls.return_value = mock_reviewer

        await reply_task(
            {},
            installation_id=1,
            repo_full_name="a/b",
            pr_number=1,
            comment_id=777,
            reply_body="nah",
            reply_user="dev",
        )

        assert taste_rule.weight >= -1.0

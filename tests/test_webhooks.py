"""Tests for webhook signature verification and event routing."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from nitpick.api.app import app


def make_pr_payload(action="opened", merged=False):
    payload = {
        "action": action,
        "installation": {"id": 12345},
        "repository": {"full_name": "octocat/hello-world", "id": 99999},
        "pull_request": {
            "number": 42,
            "merged": merged,
            "title": "Fix auth bug",
            "body": "Fixes the password hashing",
            "user": {"login": "octocat"},
            "base": {"sha": "abc123", "repo": {"id": 99999}},
            "head": {"sha": "def456"},
        },
    }
    if action == "closed" and merged:
        payload["pull_request"]["merged"] = True
    return payload


def make_comment_payload(in_reply_to_id=None):
    return {
        "action": "created",
        "installation": {"id": 12345},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"number": 42},
        "comment": {
            "id": 777,
            "body": "I disagree, this is fine",
            "user": {"login": "octocat"},
            "in_reply_to_id": in_reply_to_id,
        },
    }


WEBHOOK_SECRET = "test-secret-123"


def sign_payload(payload: dict, secret: str) -> str:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.fixture
def client():
    return TestClient(app)


class TestSignatureVerification:
    def test_valid_signature(self):
        from nitpick.api.webhooks import verify_signature

        with patch("nitpick.api.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = WEBHOOK_SECRET
            payload = b'{"test": true}'
            sig = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
            assert verify_signature(payload, f"sha256={sig}") is True

    def test_invalid_signature(self):
        from nitpick.api.webhooks import verify_signature

        with patch("nitpick.api.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = WEBHOOK_SECRET
            assert verify_signature(b'{"test": true}', "sha256=wrong") is False

    def test_no_secret_configured_skips_verification(self):
        from nitpick.api.webhooks import verify_signature

        with patch("nitpick.api.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = ""
            assert verify_signature(b"anything", "sha256=anything") is True


class TestWebhookRouting:
    """Test event routing. We patch verify_signature to always pass,
    and patch get_pool to return a mock Redis pool."""

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_pr_opened_enqueues_review(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_pr_payload(action="opened")
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_called_once_with(
            "review_task",
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_pr_synchronize_enqueues_review(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_pr_payload(action="synchronize")
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_called_once_with(
            "review_task",
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_pr_merged_enqueues_ingest(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_pr_payload(action="closed", merged=True)
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_called_once_with(
            "ingest_task",
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
        )

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_pr_closed_not_merged_no_enqueue(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_pr_payload(action="closed", merged=False)
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_not_called()

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_comment_reply_enqueues_reply_task(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_comment_payload(in_reply_to_id=555)
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request_review_comment"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_called_once_with(
            "reply_task",
            installation_id=12345,
            repo_full_name="octocat/hello-world",
            pr_number=42,
            comment_id=555,
            reply_body="I disagree, this is fine",
            reply_user="octocat",
        )

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_top_level_comment_no_enqueue(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = make_comment_payload(in_reply_to_id=None)
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request_review_comment"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_not_called()

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_bot_comment_ignored(self, _mock_sig, mock_get_pool, client):
        """Bot's own replies should not trigger reply_task (prevents infinite loops)."""
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        payload = {
            "action": "created",
            "installation": {"id": 12345},
            "repository": {"full_name": "octocat/hello-world"},
            "pull_request": {"number": 42},
            "comment": {
                "id": 888,
                "body": "Fair point, I concede.",
                "user": {"login": "nitpick-reviewer[bot]", "type": "Bot"},
                "in_reply_to_id": 555,
            },
        }
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "pull_request_review_comment"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_not_called()

    @patch("nitpick.api.webhooks.verify_signature", return_value=False)
    def test_invalid_signature_returns_401(self, _mock_sig, client):
        payload = make_pr_payload()
        resp = client.post(
            "/webhooks/github",
            json=payload,
            headers={"x-hub-signature-256": "sha256=invalid", "x-github-event": "pull_request"},
        )
        assert resp.status_code == 401

    @patch("nitpick.api.webhooks.get_pool")
    @patch("nitpick.api.webhooks.verify_signature", return_value=True)
    def test_unknown_event_returns_ok(self, _mock_sig, mock_get_pool, client):
        mock_pool = AsyncMock()
        mock_get_pool.return_value = mock_pool

        resp = client.post(
            "/webhooks/github",
            json={"action": "whatever"},
            headers={"x-hub-signature-256": "sha256=test", "x-github-event": "issues"},
        )
        assert resp.status_code == 200
        mock_pool.enqueue_job.assert_not_called()

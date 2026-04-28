"""Async GitHub API client using httpx + JWT auth for GitHub App."""

import time
from pathlib import Path

import httpx
import jwt

from nitpick.config import settings

GITHUB_API = "https://api.github.com"


def _make_jwt() -> str:
    """Create a JWT signed with the GitHub App's private key."""
    pem = Path(settings.github_private_key_path).read_bytes()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),  # 10 min max
        "iss": settings.github_app_id,
    }
    return jwt.encode(payload, pem, algorithm="RS256")


class GitHubClient:
    """Async context manager for authenticated GitHub API calls."""

    def __init__(self, installation_id: int):
        self.installation_id = installation_id
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=30.0,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    async def _authenticate(self):
        """Exchange JWT for an installation access token."""
        app_jwt = _make_jwt()
        resp = await self._client.post(
            f"/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"},
        )
        resp.raise_for_status()
        self._token = resp.json()["token"]
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict:
        resp = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        resp.raise_for_status()
        return resp.json()

    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the raw unified diff for a PR."""
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        resp.raise_for_status()
        return resp.text

    async def create_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        comments: list[dict],
    ) -> list[dict]:
        """Post a review with inline comments. Returns the created comments."""
        # Build GitHub review comment format
        gh_comments = []
        for c in comments:
            gh_comment = {
                "path": c["path"],
                "body": c["body"],
            }
            if "position" in c:
                gh_comment["position"] = c["position"]
            elif "line" in c:
                gh_comment["line"] = c["line"]
                gh_comment["side"] = "RIGHT"
            gh_comments.append(gh_comment)

        resp = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json={
                "commit_id": commit_sha,
                "body": "Review by Nitpick",
                "event": "COMMENT",
                "comments": gh_comments,
            },
        )
        resp.raise_for_status()

        # Return the comments from the review response
        review_data = resp.json()
        # Fetch the actual comments to get their IDs
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        )
        resp.raise_for_status()
        all_comments = resp.json()

        # Return the most recent comments (ours)
        return all_comments[-len(comments):]

    async def reply_to_comment(
        self, owner: str, repo: str, pr_number: int, comment_id: int, body: str
    ):
        """Reply to an existing review comment."""
        resp = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

"""GitHub webhook handlers — receives events, verifies signatures, enqueues tasks."""

import hashlib
import hmac
import logging

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Header, HTTPException, Request

from nitpick.config import settings
from nitpick.workers.settings import parse_redis_url

logger = logging.getLogger(__name__)

router = APIRouter()

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(parse_redis_url(settings.redis_url))
    return _pool


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not settings.github_webhook_secret:
        return True  # Skip in development if no secret configured
    expected = hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    body = await request.body()

    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code= 401, detail="Invalid signature")

    payload = await request.json()
    pool = await get_pool()

    action = payload.get("action", "")
    print(f"[WEBHOOK] event={x_github_event} action={action}")

    if x_github_event == "pull_request":
        action = payload.get("action")
        if action in ("opened", "synchronize"):
            pr = payload["pull_request"]
            repo = payload["repository"]
            await pool.enqueue_job(
                "review_task",
                installation_id=payload["installation"]["id"],
                repo_full_name=repo["full_name"],
                pr_number=pr["number"],
            )
            print(f"[WEBHOOK] Enqueued review for {repo['full_name']}#{pr['number']}")

        elif action == "closed" and payload["pull_request"].get("merged"):
            pr = payload["pull_request"]
            repo = payload["repository"]
            await pool.enqueue_job(
                "ingest_task",
                installation_id=payload["installation"]["id"],
                repo_full_name=repo["full_name"],
                pr_number=pr["number"],
            )
            logger.info("Enqueued ingest for merged %s#%d", repo["full_name"], pr["number"])

    elif x_github_event == "pull_request_review_comment":
        action = payload.get("action")
        if action == "created":
            comment = payload["comment"]
            # Ignore bot's own comments to prevent infinite reply loops
            if comment["user"].get("type") == "Bot":
                return {"status": "ok"}
            # Only process replies (comments with in_reply_to_id)
            in_reply_to = comment.get("in_reply_to_id")
            if in_reply_to:
                repo = payload["repository"]
                pr = payload["pull_request"]
                await pool.enqueue_job(
                    "reply_task",
                    installation_id=payload["installation"]["id"],
                    repo_full_name=repo["full_name"],
                    pr_number=pr["number"],
                    comment_id=in_reply_to,
                    reply_body=comment["body"],
                    reply_user=comment["user"]["login"],
                )
                logger.info(
                    "Enqueued reply for %s#%d comment %d",
                    repo["full_name"], pr["number"], in_reply_to,
                )

    return {"status": "ok"}

import logging

from nitpick.db import async_session
from nitpick.models import CommentThread, ReviewComment
from nitpick.services.github import GitHubClient
from nitpick.services.reviewer import ReviewService

logger = logging.getLogger(__name__)


async def reply_task(
    ctx: dict,
    *,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    comment_id: int,
    reply_body: str,
    reply_user: str,
):
    """Handle a developer's reply to one of our review comments — debate or concede."""
    logger.info("Reply on %s#%d comment %d", repo_full_name, pr_number, comment_id)

    owner, name = repo_full_name.split("/")

    async with async_session() as db:
        from sqlalchemy import select

        from nitpick.models import Repo, TasteRule

        # Find our original comment
        result = await db.execute(
            select(ReviewComment).where(
                ReviewComment.github_comment_id == comment_id,
                ReviewComment.posted_by_bot.is_(True),
            )
        )
        original = result.scalar_one_or_none()
        if not original:
            logger.info("Comment %d not ours, ignoring", comment_id)
            return

        # Load or create thread
        result = await db.execute(
            select(CommentThread).where(CommentThread.root_comment_id == original.id)
        )
        thread = result.scalar_one_or_none()
        if not thread:
            thread = CommentThread(
                root_comment_id=original.id,
                repo_id=original.repo_id,
                pr_id=original.pr_id,
                turns=[],
            )
            db.add(thread)
            await db.flush()

        # Append developer's reply to thread
        thread.turns = [*thread.turns, {"role": "developer", "user": reply_user, "body": reply_body}]

        # Get taste rules for context
        result = await db.execute(
            select(TasteRule)
            .where(TasteRule.repo_id == original.repo_id)
            .order_by(TasteRule.weight.desc())
            .limit(20)
        )
        taste_rules = result.scalars().all()

        # Get diff context
        async with GitHubClient(installation_id) as gh:
            diff_text = await gh.get_pull_request_diff(owner, name, pr_number)

        # Ask Claude to debate
        reviewer = ReviewService()
        decision = await reviewer.debate(
            original_comment=original.body,
            original_file=original.file_path,
            developer_reply=reply_body,
            diff_text=diff_text,
            taste_rules=taste_rules,
            thread_history=thread.turns,
        )

        # Post reply
        async with GitHubClient(installation_id) as gh:
            await gh.reply_to_comment(owner, name, pr_number, comment_id, decision["body"])

        # Update thread
        thread.turns = [
            *thread.turns,
            {"role": "bot", "decision": decision["decision"], "body": decision["body"]},
        ]
        thread.outcome = decision["decision"]

        # If we conceded, decrease weight of the relevant taste rule
        if decision["decision"] == "concede":
            result = await db.execute(
                select(TasteRule).where(
                    TasteRule.repo_id == original.repo_id,
                    TasteRule.category == original.category,
                )
            )
            rule = result.scalar_one_or_none()
            if rule:
                rule.weight = max(-1.0, rule.weight - 0.1)
                logger.info("Decreased weight for %s to %.2f", rule.category, rule.weight)

        await db.commit()
        logger.info(
            "Replied to %s#%d comment %d: %s",
            repo_full_name, pr_number, comment_id, decision["decision"],
        )

import logging

from nitpick.db import async_session
from nitpick.models import PullRequest, ReviewComment, TasteRule
from nitpick.services.github import GitHubClient

logger = logging.getLogger(__name__)


async def ingest_task(
    ctx: dict,
    *,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
):
    """Process a merged PR: analyze which bot comments were addressed vs dismissed, update taste rules."""
    logger.info("Ingesting merged %s#%d", repo_full_name, pr_number)

    owner, name = repo_full_name.split("/")

    async with async_session() as db:
        from sqlalchemy import select

        from nitpick.models import Repo

        # Find the PR in our DB
        result = await db.execute(
            select(PullRequest)
            .join(Repo)
            .where(
                Repo.owner == owner,
                Repo.name == name,
                PullRequest.github_pr_number == pr_number,
            )
        )
        pr = result.scalar_one_or_none()
        if not pr:
            logger.info("PR %s#%d not in DB, skipping", repo_full_name, pr_number)
            return

        # Mark PR as merged
        pr.merged = True

        # Get the final diff to check if flagged lines changed
        async with GitHubClient(installation_id) as gh:
            final_diff = await gh.get_pull_request_diff(owner, name, pr_number)

        # Get all bot comments for this PR
        result = await db.execute(
            select(ReviewComment).where(
                ReviewComment.pr_id == pr.id,
                ReviewComment.posted_by_bot.is_(True),
            )
        )
        bot_comments = result.scalars().all()

        if not bot_comments:
            await db.commit()
            return

        # For each bot comment, determine if the flagged code was changed (addressed)
        # or left as-is (dismissed). Simple heuristic: if the file+line appears in
        # the final diff compared to when we reviewed, it was likely addressed.
        for comment in bot_comments:
            # Simple heuristic: check if file was modified after our review
            # A more sophisticated version would compare specific line ranges
            file_in_diff = comment.file_path in final_diff
            comment.was_addressed = file_in_diff
            comment.was_dismissed = not file_in_diff

            # Update taste rules
            result = await db.execute(
                select(TasteRule).where(
                    TasteRule.repo_id == pr.repo_id,
                    TasteRule.category == comment.category,
                )
            )
            rule = result.scalar_one_or_none()

            if rule:
                rule.evidence_count += 1
                if comment.was_addressed:
                    rule.weight = min(1.0, rule.weight + 0.05)
                else:
                    rule.weight = max(-1.0, rule.weight - 0.05)
            else:
                # Create new rule from this signal
                initial_weight = 0.5 if comment.was_addressed else 0.3
                rule = TasteRule(
                    repo_id=pr.repo_id,
                    category=comment.category,
                    signal=f"Auto-learned from {comment.file_path}: {comment.body[:200]}",
                    weight=initial_weight,
                    evidence_count=1,
                )
                db.add(rule)

        await db.commit()
        logger.info(
            "Ingested %d comments from %s#%d", len(bot_comments), repo_full_name, pr_number
        )

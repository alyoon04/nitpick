import logging

from arq import Retry

from nitpick.db import async_session
from nitpick.models import PullRequest, Repo
from nitpick.services.github import GitHubClient
from nitpick.services.reviewer import ReviewService
from nitpick.services.diff_parser import parse_diff

logger = logging.getLogger(__name__)


async def review_task(ctx: dict, *, installation_id: int, repo_full_name: str, pr_number: int):
    """Review a PR: fetch diff, call Claude, post review comments."""
    logger.info("Reviewing %s#%d", repo_full_name, pr_number)

    owner, name = repo_full_name.split("/")

    async with GitHubClient(installation_id) as gh:
        # Get PR metadata
        pr_data = await gh.get_pull_request(owner, name, pr_number)
        diff_text = await gh.get_pull_request_diff(owner, name, pr_number)

    # Parse diff into reviewable chunks
    file_diffs = parse_diff(diff_text)
    if not file_diffs:
        logger.info("No reviewable changes in %s#%d", repo_full_name, pr_number)
        return

    # Store PR in DB
    async with async_session() as db:
        # Get or create repo
        from sqlalchemy import select

        result = await db.execute(
            select(Repo).where(Repo.owner == owner, Repo.name == name)
        )
        repo = result.scalar_one_or_none()
        if not repo:
            repo = Repo(
                github_repo_id=pr_data["base"]["repo"]["id"],
                owner=owner,
                name=name,
                installation_id=installation_id,
            )
            db.add(repo)
            await db.flush()

        pr = PullRequest(
            repo_id=repo.id,
            github_pr_number=pr_number,
            title=pr_data["title"],
            description=pr_data.get("body"),
            author=pr_data["user"]["login"],
            base_sha=pr_data["base"]["sha"],
            head_sha=pr_data["head"]["sha"],
        )
        db.add(pr)
        await db.flush()

        # Build review with taste context
        from sqlalchemy import select

        from nitpick.models import TasteRule

        result = await db.execute(
            select(TasteRule)
            .where(TasteRule.repo_id == repo.id, TasteRule.weight > 0.2)
            .order_by(TasteRule.weight.desc())
            .limit(20)
        )
        taste_rules = result.scalars().all()

        # Call Claude for review
        reviewer = ReviewService()
        comments = await reviewer.review(
            file_diffs=file_diffs,
            pr_title=pr_data["title"],
            pr_description=pr_data.get("body", ""),
            taste_rules=taste_rules,
        )

        if not comments:
            logger.info("No comments for %s#%d", repo_full_name, pr_number)
            await db.commit()
            return

        # Post review to GitHub
        async with GitHubClient(installation_id) as gh:
            github_comments = await gh.create_review(
                owner, name, pr_number, pr_data["head"]["sha"], comments
            )

        # Store comments in DB
        from nitpick.models import ReviewComment

        for comment, gh_comment in zip(comments, github_comments):
            rc = ReviewComment(
                repo_id=repo.id,
                pr_id=pr.id,
                github_comment_id=gh_comment.get("id"),
                file_path=comment["path"],
                diff_position=comment.get("position"),
                line_number=comment.get("line"),
                body=comment["body"],
                severity=comment.get("severity", "suggestion"),
                category=comment.get("category", "general"),
                posted_by_bot=True,
            )
            db.add(rc)

        await db.commit()
        logger.info(
            "Posted %d comments on %s#%d", len(comments), repo_full_name, pr_number
        )

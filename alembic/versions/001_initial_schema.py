"""Initial schema — repos, PRs, comments, threads, taste rules, embeddings.

Revision ID: 001
Revises: None
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "repos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("github_repo_id", sa.BigInteger, unique=True, index=True, nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("installation_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), index=True, nullable=False),
        sa.Column("github_pr_number", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("merged", sa.Boolean, default=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "review_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), index=True, nullable=False),
        sa.Column("pr_id", sa.Integer, sa.ForeignKey("pull_requests.id"), index=True, nullable=False),
        sa.Column("github_comment_id", sa.BigInteger, nullable=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("diff_position", sa.Integer, nullable=True),
        sa.Column("line_number", sa.Integer, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("severity", sa.String(50), default="suggestion"),
        sa.Column("category", sa.String(100), default="general"),
        sa.Column("posted_by_bot", sa.Boolean, default=True),
        sa.Column("was_addressed", sa.Boolean, nullable=True),
        sa.Column("was_dismissed", sa.Boolean, nullable=True),
        sa.Column("thread_resolved", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "comment_threads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("root_comment_id", sa.Integer, sa.ForeignKey("review_comments.id"), unique=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), index=True, nullable=False),
        sa.Column("pr_id", sa.Integer, sa.ForeignKey("pull_requests.id"), index=True, nullable=False),
        sa.Column("turns", JSONB, default=list),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "taste_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), index=True, nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("signal", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, default=0.5),
        sa.Column("evidence_count", sa.Integer, default=0),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # pgvector table — vector column added via raw SQL
    op.create_table(
        "review_embeddings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), index=True, nullable=False),
        sa.Column("comment_id", sa.Integer, sa.ForeignKey("review_comments.id"), index=True, nullable=False),
        sa.Column("metadata", JSONB, default=dict),
    )
    op.execute("ALTER TABLE review_embeddings ADD COLUMN embedding vector(1536)")


def downgrade() -> None:
    op.drop_table("review_embeddings")
    op.drop_table("taste_rules")
    op.drop_table("comment_threads")
    op.drop_table("review_comments")
    op.drop_table("pull_requests")
    op.drop_table("repos")
    op.execute("DROP EXTENSION IF EXISTS vector")

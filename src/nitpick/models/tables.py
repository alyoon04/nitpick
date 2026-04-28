import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nitpick.db.session import Base


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    installation_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pull_requests: Mapped[list["PullRequest"]] = relationship(back_populates="repo")
    taste_rules: Mapped[list["TasteRule"]] = relationship(back_populates="repo")


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    github_pr_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(255))
    base_sha: Mapped[str] = mapped_column(String(40))
    head_sha: Mapped[str] = mapped_column(String(40))
    merged: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    repo: Mapped["Repo"] = relationship(back_populates="pull_requests")
    review_comments: Mapped[list["ReviewComment"]] = relationship(back_populates="pull_request")
    comment_threads: Mapped[list["CommentThread"]] = relationship(back_populates="pull_request")


class ReviewComment(Base):
    __tablename__ = "review_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"), index=True)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    diff_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(50), default="suggestion")
    category: Mapped[str] = mapped_column(String(100), default="general")
    posted_by_bot: Mapped[bool] = mapped_column(Boolean, default=True)
    was_addressed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    was_dismissed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    thread_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pull_request: Mapped["PullRequest"] = relationship(back_populates="review_comments")


class CommentThread(Base):
    __tablename__ = "comment_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    root_comment_id: Mapped[int] = mapped_column(
        ForeignKey("review_comments.id"), unique=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"), index=True)
    turns: Mapped[dict] = mapped_column(JSONB, default=list)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pull_request: Mapped["PullRequest"] = relationship(back_populates="comment_threads")
    root_comment: Mapped["ReviewComment"] = relationship()


class TasteRule(Base):
    __tablename__ = "taste_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    category: Mapped[str] = mapped_column(String(100))
    signal: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    repo: Mapped["Repo"] = relationship(back_populates="taste_rules")


class ReviewEmbedding(Base):
    __tablename__ = "review_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), index=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("review_comments.id"), index=True)
    embedding = mapped_column(Vector(1536))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

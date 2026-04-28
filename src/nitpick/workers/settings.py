from arq.connections import RedisSettings

from nitpick.config import settings
from nitpick.workers.review import review_task
from nitpick.workers.reply import reply_task
from nitpick.workers.ingest import ingest_task


def parse_redis_url(url: str) -> RedisSettings:
    """Parse redis://host:port/db into RedisSettings."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or 0),
    )


class WorkerSettings:
    functions = [review_task, reply_task, ingest_task]
    redis_settings = parse_redis_url(settings.redis_url)
    max_jobs = 10
    job_timeout = 300  # 5 min per job

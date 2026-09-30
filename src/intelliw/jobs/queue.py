"""The re-render queue (rq on Redis): connecting, enqueueing, publishing mutations."""

import logging
from collections.abc import Callable

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job

from intelliw.config import Settings
from intelliw.jobs.models import MutationEvent

log = logging.getLogger(__name__)

QUEUE_NAME = "intelliw-render"
JOB = "intelliw.jobs.tasks.render_all"  # by name: the worker imports it, not the publisher
JOB_TIMEOUT = 600  # seconds for re-rendering every design
RESULT_TTL = 24 * 3600  # keep results for `jobs status`

# Called with every committed mutation; must never raise.
Publisher = Callable[[MutationEvent], None]


def connect(settings: Settings) -> Redis:
    """A Redis connection that fails fast when Redis is not running."""
    return Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=5)


def render_queue(connection: Redis) -> Queue:
    return Queue(QUEUE_NAME, connection=connection)


def enqueue(queue: Queue, event: MutationEvent) -> Job:
    """Queue a re-render of every design, with the event as its argument."""
    return queue.enqueue(
        JOB,
        event.model_dump(mode="json"),
        job_timeout=JOB_TIMEOUT,
        result_ttl=RESULT_TTL,
        description=f"re-render after {event.mutation} at {event.updated_at.isoformat()}",
    )


def queue_publisher(settings: Settings) -> Publisher:
    """A publisher for the GraphQL layer: enqueue a job per mutation.

    A mutation has already been committed when this runs, so a Redis outage must not
    fail it: the error is logged and the change is simply not re-rendered automatically.
    """
    connection = connect(settings)
    queue = render_queue(connection)

    def publish(event: MutationEvent) -> None:
        try:
            enqueue(queue, event)
        except RedisError as exc:
            log.warning("re-render not queued after %s: %s", event.mutation, exc)

    return publish

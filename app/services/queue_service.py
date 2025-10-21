from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Status untuk transcription task."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TranscriptionTask:
    """Representation dari single transcription task."""

    task_id: str
    chat_id: int
    message_id: int
    file_path: Path
    provider: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    priority: int = 0  # Lower number = higher priority

    @property
    def processing_time(self) -> Optional[float]:
        """Get processing time dalam detik."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def wait_time(self) -> float:
        """Get wait time dalam detik."""
        start = self.started_at or datetime.utcnow()
        return (start - self.created_at).total_seconds()

    def mark_processing(self) -> None:
        """Mark task as processing."""
        self.status = TaskStatus.PROCESSING
        self.started_at = datetime.utcnow()

    def mark_completed(self, result: Any) -> None:
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.utcnow()
        self.error = error


class TaskQueue:
    """
    Simple in-memory task queue untuk async transcription processing.

    Features:
    - Priority queue (FIFO dengan priority support)
    - Concurrent processing dengan worker pool
    - Retry mechanism
    - Task status tracking
    - Rate limiting per user
    """

    def __init__(
        self,
        *,
        max_workers: int = 3,
        max_retries: int = 2,
        retry_delay: int = 5,
        rate_limit_per_user: int = 5,  # Max concurrent tasks per user
    ) -> None:
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limit_per_user = rate_limit_per_user

        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.tasks: dict[str, TranscriptionTask] = {}
        self.workers: list[asyncio.Task] = []
        self.user_task_count: dict[int, int] = {}
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start worker pool."""
        if self._running:
            logger.warning("Task queue already running")
            return

        self._running = True
        logger.info("Starting task queue with %d workers", self.max_workers)

        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

    async def stop(self) -> None:
        """Stop worker pool gracefully."""
        if not self._running:
            return

        logger.info("Stopping task queue...")
        self._running = False

        # Wait for all workers to finish
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

        logger.info("Task queue stopped")

    async def submit(
        self,
        chat_id: int,
        message_id: int,
        file_path: Path,
        provider: str,
        *,
        priority: int = 0,
        processor: Optional[Callable] = None,
    ) -> str:
        """
        Submit task ke queue.

        Args:
            chat_id: Telegram chat ID
            message_id: Telegram message ID
            file_path: Path ke audio file
            provider: Provider name (groq/deepgram)
            priority: Task priority (lower = higher priority)
            processor: Optional custom processor function

        Returns:
            task_id: Unique task ID

        Raises:
            RuntimeError: Jika user sudah mencapai rate limit
        """
        async with self._lock:
            # Check rate limit
            user_count = self.user_task_count.get(chat_id, 0)
            if user_count >= self.rate_limit_per_user:
                raise RuntimeError(
                    f"Rate limit exceeded: maksimal {self.rate_limit_per_user} "
                    "task concurrent per user"
                )

            # Create task
            task_id = str(uuid.uuid4())
            task = TranscriptionTask(
                task_id=task_id,
                chat_id=chat_id,
                message_id=message_id,
                file_path=file_path,
                provider=provider,
                priority=priority,
            )

            # Store task
            self.tasks[task_id] = task
            self.user_task_count[chat_id] = user_count + 1

            # Add to queue (priority, timestamp, task_id)
            await self.queue.put((priority, task.created_at, task_id, processor))

            logger.info(
                "Task %s submitted for chat %d (queue size: %d)",
                task_id[:8],
                chat_id,
                self.queue.qsize(),
            )

            return task_id

    async def get_task(self, task_id: str) -> Optional[TranscriptionTask]:
        """Get task by ID."""
        return self.tasks.get(task_id)

    async def get_user_tasks(self, chat_id: int) -> list[TranscriptionTask]:
        """Get all tasks untuk user tertentu."""
        return [task for task in self.tasks.values() if task.chat_id == chat_id]

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel task by ID."""
        async with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                return False

            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow()

            # Update user count
            count = self.user_task_count.get(task.chat_id, 0)
            self.user_task_count[task.chat_id] = max(0, count - 1)

            logger.info("Task %s cancelled", task_id[:8])
            return True

    async def _worker(self, worker_id: int) -> None:
        """Worker yang process tasks dari queue."""
        logger.info("Worker %d started", worker_id)

        while self._running:
            try:
                # Get task dengan timeout
                try:
                    priority, timestamp, task_id, processor = await asyncio.wait_for(
                        self.queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                task = self.tasks.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    self.queue.task_done()
                    continue

                # Process task
                await self._process_task(worker_id, task, processor)
                self.queue.task_done()

            except Exception as e:
                logger.exception("Worker %d encountered error: %s", worker_id, e)
                await asyncio.sleep(1)

        logger.info("Worker %d stopped", worker_id)

    async def _process_task(
        self,
        worker_id: int,
        task: TranscriptionTask,
        processor: Optional[Callable] = None,
    ) -> None:
        """Process single task."""
        task.mark_processing()
        logger.info(
            "Worker %d processing task %s (wait: %.1fs)",
            worker_id,
            task.task_id[:8],
            task.wait_time,
        )

        try:
            # Execute processor
            if processor:
                result = await processor(task)
            else:
                # Default: just return task info
                result = {
                    "task_id": task.task_id,
                    "file_path": str(task.file_path),
                    "provider": task.provider,
                }

            task.mark_completed(result)
            logger.info(
                "Worker %d completed task %s (processing: %.1fs)",
                worker_id,
                task.task_id[:8],
                task.processing_time,
            )

        except Exception as e:
            logger.exception("Task %s failed: %s", task.task_id[:8], e)

            # Retry logic
            if task.retry_count < self.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.PENDING
                logger.info(
                    "Retrying task %s (attempt %d/%d)",
                    task.task_id[:8],
                    task.retry_count,
                    self.max_retries,
                )

                # Re-queue dengan delay
                await asyncio.sleep(self.retry_delay)
                await self.queue.put(
                    (task.priority, task.created_at, task.task_id, processor)
                )
            else:
                task.mark_failed(str(e))
                logger.error("Task %s failed permanently", task.task_id[:8])

        finally:
            # Update user count
            async with self._lock:
                count = self.user_task_count.get(task.chat_id, 0)
                self.user_task_count[task.chat_id] = max(0, count - 1)

    async def get_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        total = len(self.tasks)
        by_status = {status: 0 for status in TaskStatus}

        for task in self.tasks.values():
            by_status[task.status] += 1

        avg_processing_time = None
        completed_tasks = [
            t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED
        ]
        if completed_tasks:
            times = [t.processing_time for t in completed_tasks if t.processing_time]
            if times:
                avg_processing_time = sum(times) / len(times)

        return {
            "total_tasks": total,
            "queue_size": self.queue.qsize(),
            "active_workers": len([w for w in self.workers if not w.done()]),
            "by_status": {str(k): v for k, v in by_status.items()},
            "avg_processing_time": avg_processing_time,
            "users_with_active_tasks": len(
                [c for c in self.user_task_count.values() if c > 0]
            ),
        }

    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """
        Cleanup old completed/failed tasks untuk prevent memory leak.

        Returns:
            Number of tasks removed
        """
        async with self._lock:
            cutoff = datetime.utcnow()
            from datetime import timedelta

            cutoff = cutoff - timedelta(hours=max_age_hours)

            to_remove = [
                task_id
                for task_id, task in self.tasks.items()
                if task.status
                in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                and task.completed_at
                and task.completed_at < cutoff
            ]

            for task_id in to_remove:
                del self.tasks[task_id]

            if to_remove:
                logger.info("Cleaned up %d old tasks", len(to_remove))

            return len(to_remove)


# Global queue instance
_global_queue: Optional[TaskQueue] = None


def get_global_queue() -> TaskQueue:
    """Get or create global task queue instance."""
    global _global_queue
    if _global_queue is None:
        _global_queue = TaskQueue()
    return _global_queue


async def start_global_queue() -> None:
    """Start global task queue."""
    queue = get_global_queue()
    await queue.start()


async def stop_global_queue() -> None:
    """Stop global task queue."""
    global _global_queue
    if _global_queue:
        await _global_queue.stop()
        _global_queue = None

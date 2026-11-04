"""
scheduler_service.py
--------------------
Provides a persistent, in-process APScheduler (BackgroundScheduler) that
schedules and fires workflow reminders and escalations.

Architecture
------------
- Uses APScheduler's BackgroundScheduler with the SQLAlchemyJobStore so jobs
  survive process restarts.
- Each reminder defined on a WorkflowStepDef is stored both in APScheduler's
  own job store AND in the `scheduled_tasks` table for easy UI display and
  management.
- When a job fires, the `_fire_reminder` function is called, which sends an
  email via EmailService and marks the task as fired in the DB.
- The scheduler is started/stopped via the FastAPI lifespan context in main.py.

Extending
---------
Add `reminders: [ReminderConfig(...)]` to any WorkflowStepDef. When
WorkflowEngine.process_step_action() transitions to that step it calls
SchedulerService.schedule_step_reminders().
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from apps.workflow_app.config import settings
from apps.workflow_app.models.user import User
from apps.workflow_app.models.workflow import ScheduledTask, WorkflowInstance

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers (called by APScheduler in worker threads)
# ---------------------------------------------------------------------------

def _fire_reminder(job_id: str, db_url: str) -> None:
    """
    Executed in a background thread by APScheduler.
    Loads the ScheduledTask record, sends an email notification, and marks
    the task as fired.
    """
    # We must create our own DB session since this runs outside the request
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url, connect_args={"check_same_thread": False} if "sqlite" in db_url else {})
    SessionLocal = sessionmaker(bind=engine)
    db: Session = SessionLocal()

    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.job_id == job_id).first()
        if not task:
            logger.warning("Reminder job '%s' fired but no ScheduledTask found.", job_id)
            return

        if task.is_cancelled:
            logger.info("Reminder job '%s' was cancelled before firing; skipping.", job_id)
            return

        # Load associated workflow
        workflow = db.query(WorkflowInstance).filter(WorkflowInstance.id == task.workflow_id).first()
        if not workflow:
            logger.warning("Reminder job '%s': workflow %s not found.", job_id, task.workflow_id)
            return

        # Determine recipients
        recipient_role = task.recipient_role or workflow.assigned_role
        if recipient_role == "requester":
            recipients = [workflow.requester] if workflow.requester else []
        else:
            recipients = db.query(User).filter(
                User.role == recipient_role,
                User.is_active == True
            ).all()

        # Send emails
        from apps.workflow_app.services.mail_service import EmailService
        for user in recipients:
            if user and user.email:
                EmailService.notify_reminder(
                    workflow=workflow,
                    recipient_email=user.email,
                    recipient_name=user.display_name,
                    reminder_description=task.description or "Pending action required",
                )

        # Mark as fired
        task.is_fired = True
        task.fired_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Reminder '%s' fired for workflow %s (notified %d recipient(s)).",
            task.description,
            workflow.tracking_code,
            len(recipients),
        )

    except Exception:
        logger.exception("Error executing reminder job '%s'.", job_id)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------

class SchedulerService:
    """Singleton wrapper around APScheduler BackgroundScheduler."""

    _scheduler: Optional[BackgroundScheduler] = None
    _db_url: str = ""

    @classmethod
    def start(cls) -> None:
        """Start the background scheduler (call once at app startup)."""
        if cls._scheduler and cls._scheduler.running:
            return

        cls._db_url = settings.DATABASE_URL

        jobstores = {
            "default": SQLAlchemyJobStore(url=cls._db_url)
        }
        cls._scheduler = BackgroundScheduler(jobstores=jobstores)
        cls._scheduler.start()
        logger.info("SchedulerService started (jobstore: %s).", cls._db_url)

    @classmethod
    def stop(cls) -> None:
        """Gracefully stop the scheduler (call at app shutdown)."""
        if cls._scheduler and cls._scheduler.running:
            cls._scheduler.shutdown(wait=False)
            logger.info("SchedulerService stopped.")

    @classmethod
    def schedule_step_reminders(
        cls,
        db: Session,
        workflow: WorkflowInstance,
        step_id: str,
    ) -> list:
        """
        Reads the ReminderConfig list on the step definition for `step_id`
        and schedules each one. Returns a list of created ScheduledTask objects.

        Should be called after a workflow transitions into a new step.
        """
        from apps.workflow_app.services.workflow_engine import WorkflowEngine

        definition = WorkflowEngine.get_definition(workflow.workflow_type)
        if not definition:
            return []

        step_def = definition.steps.get(step_id)
        if not step_def or not step_def.reminders:
            return []

        created_tasks = []
        now = datetime.now(timezone.utc)

        for reminder in step_def.reminders:
            job_id = f"reminder_{workflow.id}_{step_id}_{uuid.uuid4().hex[:8]}"

            # Determine fire time
            if reminder.run_at_iso:
                try:
                    run_at = datetime.fromisoformat(reminder.run_at_iso)
                    if run_at.tzinfo is None:
                        run_at = run_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    logger.error("Invalid run_at_iso '%s' in reminder.", reminder.run_at_iso)
                    continue
            elif reminder.delay_seconds:
                run_at = now + timedelta(seconds=reminder.delay_seconds)
            else:
                logger.warning("Reminder has no delay_seconds or run_at_iso; skipping.")
                continue

            # Skip if already in the past
            if run_at <= now:
                logger.warning("Reminder run_at %s is in the past; skipping.", run_at)
                continue

            # Persist task record
            task = ScheduledTask(
                job_id=job_id,
                workflow_id=workflow.id,
                workflow_tracking_code=workflow.tracking_code,
                task_type=reminder.task_type,
                description=reminder.description,
                run_at=run_at,
                delay_seconds=reminder.delay_seconds,
                recipient_role=reminder.recipient_role,
            )
            db.add(task)
            db.flush()  # get the ID without full commit

            # Register with APScheduler
            if cls._scheduler:
                cls._scheduler.add_job(
                    func=_fire_reminder,
                    trigger=DateTrigger(run_date=run_at),
                    id=job_id,
                    kwargs={"job_id": job_id, "db_url": cls._db_url},
                    replace_existing=True,
                    misfire_grace_time=3600,  # fire up to 1 hour late if app was down
                )
                logger.info(
                    "Scheduled reminder '%s' for workflow %s at %s.",
                    reminder.description,
                    workflow.tracking_code,
                    run_at.isoformat(),
                )
            else:
                logger.warning("Scheduler not started; reminder '%s' will not fire.", reminder.description)

            created_tasks.append(task)

        return created_tasks

    @classmethod
    def cancel_workflow_reminders(
        cls,
        db: Session,
        workflow: WorkflowInstance,
        step_id: Optional[str] = None,
    ) -> int:
        """
        Cancels all pending (un-fired) reminders for a workflow.
        If step_id is given, only cancels reminders whose job_id contains that step.
        Returns the number of tasks cancelled.
        """
        query = db.query(ScheduledTask).filter(
            ScheduledTask.workflow_id == workflow.id,
            ScheduledTask.is_fired == False,
            ScheduledTask.is_cancelled == False,
        )
        if step_id:
            query = query.filter(ScheduledTask.job_id.contains(f"_{step_id}_"))

        tasks = query.all()
        cancelled = 0
        for task in tasks:
            task.is_cancelled = True
            if cls._scheduler:
                try:
                    cls._scheduler.remove_job(task.job_id)
                except Exception:
                    pass  # already fired / not found
            cancelled += 1

        db.flush()
        logger.info(
            "Cancelled %d reminder(s) for workflow %s.", cancelled, workflow.tracking_code
        )
        return cancelled

    @classmethod
    def cancel_task(cls, db: Session, job_id: str) -> bool:
        """Cancel a single scheduled task by its job_id."""
        task = db.query(ScheduledTask).filter(ScheduledTask.job_id == job_id).first()
        if not task or task.is_fired or task.is_cancelled:
            return False
        task.is_cancelled = True
        if cls._scheduler:
            try:
                cls._scheduler.remove_job(job_id)
            except Exception:
                pass
        db.commit()
        return True

    @classmethod
    def list_pending_jobs(cls) -> list:
        """Return APScheduler's current job list (for admin diagnostics)."""
        if not cls._scheduler:
            return []
        return cls._scheduler.get_jobs()

    @classmethod
    def is_running(cls) -> bool:
        return bool(cls._scheduler and cls._scheduler.running)

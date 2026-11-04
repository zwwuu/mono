from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session, object_session

from apps.workflow_app.models.user import User
from apps.workflow_app.models.workflow import (
    AuditLog,
    StepApproval,
    WorkflowInstance,
    WorkflowStepHistory,
)
from apps.workflow_app.services.mail_service import EmailService
from apps.workflow_app.services.scheduler_service import SchedulerService

# Import modular workflow domain classes and registry
from apps.workflow_app.workflows import (
    EXPENSE_REIMBURSEMENT_WORKFLOW,
    IT_ACCESS_WORKFLOW,
    PURCHASE_REQUEST_WORKFLOW,
    REMOTE_VPN_WORKFLOW,
    FormField,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowStepDef,
)

# Backward-compatible dictionary access
WORKFLOW_REGISTRY = WorkflowRegistry._registry


class WorkflowEngine:
    """Execution engine coordinating permissions, state transitions, audit logging, and notifications."""

    @classmethod
    def get_definition(cls, workflow_type: str) -> Optional[WorkflowDefinition]:
        return WorkflowRegistry.get(workflow_type)

    @classmethod
    def get_step_def(cls, workflow: WorkflowInstance) -> Optional[WorkflowStepDef]:
        definition = cls.get_definition(workflow.workflow_type)
        if not definition:
            return None
        return definition.steps.get(workflow.current_step)

    @classmethod
    def get_approval_progress(
        cls,
        workflow: WorkflowInstance,
        user: Optional[User] = None,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """Calculates multi-approver and parallel approval status for the current active step."""
        step_def = cls.get_step_def(workflow)
        if not step_def or not step_def.is_approval_step:
            return {
                "is_multi_approver": False,
                "parallel_approval": False,
                "approvals_required": 1,
                "approvals_received": 0,
                "approved_signoffs": [],
                "pending_count": 0,
                "percent_complete": 0,
                "user_has_approved": False,
                "role_status": {},
                "user_status": {},
                "is_satisfied": False,
            }

        # Gather approved signoffs for this active step via session if available
        active_step_id = workflow.current_step
        sess = db or object_session(workflow)
        if sess is not None:
            approved_signoffs = sess.query(StepApproval).filter(
                StepApproval.workflow_id == workflow.id,
                StepApproval.step_id == active_step_id,
                StepApproval.decision == "APPROVE"
            ).all()
        else:
            approved_signoffs = [
                a for a in (workflow.step_approvals or [])
                if a.step_id == active_step_id and a.decision == "APPROVE"
            ]

        received_count = len(approved_signoffs)
        required_count = max(1, step_def.approvals_required)

        # Check role-specific counts if configured
        role_status = {}
        for role_name, req_qty in step_def.required_role_approvals.items():
            got_qty = sum(1 for a in approved_signoffs if a.user_role == role_name)
            role_status[role_name] = {
                "required": req_qty,
                "received": got_qty,
                "satisfied": got_qty >= req_qty
            }

        # Check user-specific signoffs if configured
        user_status = {}
        for username in step_def.required_user_approvals:
            has_signed = any(a.user_username == username for a in approved_signoffs)
            user_status[username] = {
                "required": True,
                "approved": has_signed
            }

        user_has_approved = any(a.user_id == user.id for a in approved_signoffs) if user else False

        # Evaluation of satisfaction
        count_met = received_count >= required_count
        roles_met = all(s["satisfied"] for s in role_status.values()) if role_status else True
        users_met = all(s["approved"] for s in user_status.values()) if user_status else True
        is_satisfied = count_met and roles_met and users_met

        percent = min(100, int((received_count / required_count) * 100)) if required_count > 0 else 100

        return {
            "is_multi_approver": step_def.is_multi_approver(),
            "parallel_approval": step_def.parallel_approval,
            "approvals_required": required_count,
            "approvals_received": received_count,
            "approved_signoffs": approved_signoffs,
            "pending_count": max(0, required_count - received_count),
            "percent_complete": percent,
            "user_has_approved": user_has_approved,
            "role_status": role_status,
            "user_status": user_status,
            "is_satisfied": is_satisfied,
        }

    @classmethod
    def can_user_act(cls, workflow: WorkflowInstance, user: User) -> bool:
        """
        Determines if the given user has permission to act on the current workflow step.
        Supports:
        - Approve by role (single role or multiple allowed roles)
        - Approve by specific user (named usernames or assigned user ID)
        - Prevents duplicate approval by a user who has already signed off in a parallel step
        """
        step_def = cls.get_step_def(workflow)
        if not step_def:
            return False
        if step_def.step_id in ["completed", "rejected"]:
            return False

        # If approval step, check if user has ALREADY approved
        if step_def.is_approval_step:
            sess = object_session(workflow)
            if sess is not None:
                has_approved = sess.query(StepApproval).filter(
                    StepApproval.workflow_id == workflow.id,
                    StepApproval.step_id == workflow.current_step,
                    StepApproval.user_id == user.id,
                    StepApproval.decision == "APPROVE"
                ).first() is not None
            else:
                has_approved = any(
                    a.step_id == workflow.current_step and a.user_id == user.id and a.decision == "APPROVE"
                    for a in (workflow.step_approvals or [])
                )
            if has_approved:
                # User already signed off in parallel approval; cannot approve twice
                return False

        # Admins can always act / sign off
        if user.role == "admin":
            return True

        # 1. Check specific user assignment (by username or assigned_user_id)
        eligible_users = step_def.get_eligible_users(workflow)
        if user.username in eligible_users:
            return True
        if workflow.assigned_user_id == user.id:
            return True

        # 2. Check role assignment (approve by role or workflow feature role)
        eligible_roles = step_def.get_eligible_roles()
        matching_roles = [r for r in eligible_roles if user.has_role(r, workflow.workflow_type)]
        if matching_roles:
            # If step has required_role_approvals, check if at least one matching role still has pending quota
            if step_def.required_role_approvals:
                sess = object_session(workflow)
                has_pending_quota = False
                for r in matching_roles:
                    if r in step_def.required_role_approvals:
                        needed = step_def.required_role_approvals[r]
                        if sess is not None:
                            approved_for_role = sess.query(StepApproval).filter(
                                StepApproval.workflow_id == workflow.id,
                                StepApproval.step_id == workflow.current_step,
                                StepApproval.user_role == r,
                                StepApproval.decision == "APPROVE"
                            ).count()
                        else:
                            approved_for_role = sum(
                                1 for a in (workflow.step_approvals or [])
                                if a.step_id == workflow.current_step and a.user_role == r and a.decision == "APPROVE"
                            )
                        if approved_for_role < needed:
                            has_pending_quota = True
                            break
                    else:
                        has_pending_quota = True
                        break
                if not has_pending_quota:
                    return False  # All roles the user holds for this step already met their quotas
            return True

        # 3. Check requester role (on draft or revision steps)
        if ("requester" in eligible_roles or step_def.assigned_role == "requester") and workflow.requester_id == user.id:
            return True

        return False

    @classmethod
    def process_step_action(
        cls,
        db: Session,
        workflow: WorkflowInstance,
        user: User,
        action: str,
        step_payload: Dict[str, Any],
        comment: str = ""
    ) -> bool:
        """
        Executes a transition or records a parallel sign-off:
        - If action is REJECT or REQUEST_REVISION: immediately transitions/returns request.
        - If action is APPROVE in a multi-approver/parallel step:
          - Records StepApproval record.
          - Checks if required approval threshold / roles / users are fully satisfied.
          - If not yet satisfied: records partial approval and awaits remaining sign-offs.
          - If fully satisfied: transitions to next workflow step.
        - Updates history snapshot, audit log, reminders, and emails.
        """
        step_def = cls.get_step_def(workflow)
        if not step_def or action not in step_def.transitions:
            raise ValueError(f"Action '{action}' is invalid for step '{workflow.current_step}'")

        next_step_id, next_status, next_role = step_def.transitions[action]

        # Determine the user's active acting feature role for this step
        acting_role = user.role
        for r in step_def.get_eligible_roles():
            if user.has_role(r, workflow.workflow_type) and r != "requester":
                acting_role = r
                break

        # Handle APPROVE with multi-approver or parallel approval logic
        if action == "APPROVE" and step_def.is_multi_approver():
            # Record individual sign-off with specific feature role
            approval = StepApproval(
                workflow_id=workflow.id,
                step_id=step_def.step_id,
                user_id=user.id,
                user_username=user.username,
                user_display_name=user.display_name,
                user_role=acting_role,
                decision="APPROVE",
                comment=comment or "Approved"
            )
            db.add(approval)
            db.flush()

            # Refresh and check progress using active DB session
            progress = cls.get_approval_progress(workflow, user, db=db)
            is_fully_satisfied = progress["is_satisfied"]

            if not is_fully_satisfied:
                # Step remains active awaiting other parallel approvers
                received = progress["approvals_received"]
                total = progress["approvals_required"]

                # Record step history snapshot for partial signoff
                history_entry = WorkflowStepHistory(
                    workflow_id=workflow.id,
                    step_name=step_def.title,
                    action="PARTIAL_APPROVAL",
                    actor_id=user.id,
                    actor_name=user.display_name,
                    comment=f"Parallel sign-off recorded ({received}/{total} approved). Note: {comment or 'No comment'}",
                    step_snapshot=step_payload
                )
                db.add(history_entry)

                # Record audit log
                audit = AuditLog(
                    action="WORKFLOW_PARTIAL_APPROVAL",
                    entity_type="workflow",
                    entity_id=workflow.tracking_code,
                    actor_username=user.username,
                    details=f"Parallel approval signed by {user.display_name} ({user.role}) for step '{step_def.title}'. Received {received}/{total} approvals."
                )
                db.add(audit)
                workflow.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(workflow)

                # Dispatch partial approval notification
                if workflow.requester and workflow.requester.email:
                    EmailService.send_email(
                        to_email=workflow.requester.email,
                        subject=f"[{workflow.tracking_code}] Partial Approval Recorded ({received}/{total})",
                        html_content=f"""
                        <h3>Parallel Approval Update</h3>
                        <p><strong>{user.display_name}</strong> ({user.role}) has recorded their approval on your request.</p>
                        <p><strong>Progress:</strong> {received} of {total} required approvals gathered.</p>
                        <p><strong>Remarks:</strong> {comment or 'Approved'}</p>
                        <p>The workflow is waiting for remaining approvers to sign off.</p>
                        """
                    )
                return True

        # If we reach here:
        # Either action is not multi-approver APPROVE, or all parallel approvals are now satisfied!

        # Record StepApproval decision for audit if non-multi-approver approval step or rejection
        if step_def.is_approval_step and not (action == "APPROVE" and step_def.is_multi_approver()):
            approval = StepApproval(
                workflow_id=workflow.id,
                step_id=step_def.step_id,
                user_id=user.id,
                user_username=user.username,
                user_display_name=user.display_name,
                user_role=acting_role,
                decision=action,
                comment=comment or f"Executed action: {action}"
            )
            db.add(approval)

        # Merge form data
        current_data = workflow.form_data.copy()
        current_data.update(step_payload)
        workflow.form_data = current_data

        # Record step history snapshot
        history_comment = comment or f"Executed action: {action}"
        if action == "APPROVE" and step_def.is_multi_approver():
            history_comment = f"Final approval completed ({step_def.approvals_required}/{step_def.approvals_required} sign-offs satisfied). {comment}"

        history_entry = WorkflowStepHistory(
            workflow_id=workflow.id,
            step_name=step_def.title,
            action=action,
            actor_id=user.id,
            actor_name=user.display_name,
            comment=history_comment,
            step_snapshot=step_payload
        )
        db.add(history_entry)

        # Update workflow record
        workflow.current_step = next_step_id
        workflow.status = next_status
        workflow.assigned_role = next_role
        workflow.updated_at = datetime.now(timezone.utc)

        # Audit log
        audit = AuditLog(
            action=f"WORKFLOW_{action}",
            entity_type="workflow",
            entity_id=workflow.tracking_code,
            actor_username=user.username,
            details=f"Transitioned from {step_def.step_id} to {next_step_id} (Status: {next_status})"
        )
        db.add(audit)
        db.commit()
        db.refresh(workflow)

        # Cancel reminders from the previous step, then schedule reminders for new step
        SchedulerService.cancel_workflow_reminders(db, workflow, step_id=step_def.step_id)
        SchedulerService.schedule_step_reminders(db, workflow, workflow.current_step)
        db.commit()  # persist any new ScheduledTask rows

        # Trigger notifications
        cls._trigger_notifications(db, workflow, action, comment, user)
        return True

    @classmethod
    def _trigger_notifications(
        cls,
        db: Session,
        workflow: WorkflowInstance,
        action: str,
        comment: str,
        actor: User
    ):
        """Dispatches email notifications based on action and new state."""
        # Terminal state: notify requester
        if workflow.status in ["APPROVED", "REJECTED", "COMPLETED", "CONFIRMED"]:
            if workflow.requester and workflow.requester.email:
                EmailService.notify_decision(
                    workflow=workflow,
                    recipient_email=workflow.requester.email,
                    decision=workflow.status,
                    comment=comment,
                    actor_name=actor.display_name
                )
        else:
            # Active step: notify all users who can act on the next step
            next_step_def = cls.get_step_def(workflow)
            eligible_roles = next_step_def.get_eligible_roles() if next_step_def else [workflow.assigned_role]
            notified_emails = set()

            # Fetch all active users once; filter by has_role() to handle feature roles
            all_users = db.query(User).filter(User.is_active == True).all()
            for role_name in eligible_roles:
                if role_name == "requester":
                    continue
                for recipient in all_users:
                    if recipient.has_role(role_name, workflow.workflow_type):
                        if recipient.email and recipient.email not in notified_emails:
                            notified_emails.add(recipient.email)
                            EmailService.notify_step_assigned(
                                workflow=workflow,
                                target_email=recipient.email,
                                role_name=recipient.display_name,
                                step_name=workflow.current_step
                            )

            if next_step_def:
                for username in next_step_def.get_eligible_users(workflow):
                    u = db.query(User).filter(User.username == username, User.is_active == True).first()
                    if u and u.email and u.email not in notified_emails:
                        notified_emails.add(u.email)
                        EmailService.notify_step_assigned(
                            workflow=workflow,
                            target_email=u.email,
                            role_name=u.display_name,
                            step_name=workflow.current_step
                        )



__all__ = [
    "WorkflowEngine",
    "WorkflowRegistry",
    "WORKFLOW_REGISTRY",
    "FormField",
    "WorkflowStepDef",
    "WorkflowDefinition",
    "PURCHASE_REQUEST_WORKFLOW",
    "IT_ACCESS_WORKFLOW",
    "EXPENSE_REIMBURSEMENT_WORKFLOW",
    "REMOTE_VPN_WORKFLOW",
]

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

from apps.workflow_app.config import settings

logger = logging.getLogger("workflow.mail")

# In-memory mailbox for local development / testing inspection
DEV_MAILBOX: List[Dict] = []


class EmailService:
    @staticmethod
    def send_email(
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Sends an email via SMTP if settings.MAIL_ENABLED is True,
        otherwise records the message to DEV_MAILBOX for instant UI preview.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Always store in the dev mailbox for inspection / auditing
        email_record = {
            "id": len(DEV_MAILBOX) + 1,
            "to": to_email,
            "from": settings.MAIL_FROM,
            "subject": subject,
            "html": html_content,
            "text": text_content or html_content,
            "timestamp": timestamp,
            "delivered_via": "SMTP" if settings.MAIL_ENABLED else "Mock Mailbox (Dev Mode)"
        }
        DEV_MAILBOX.insert(0, email_record)

        if not settings.MAIL_ENABLED:
            logger.info(f"[MOCK MAIL] To: {to_email} | Subject: '{subject}' (Recorded in Dev Mailbox)")
            return True

        # Real SMTP Delivery
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM}>"
            msg["To"] = to_email

            if text_content:
                msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            if settings.MAIL_SSL_TLS:
                server = smtplib.SMTP_SSL(settings.MAIL_SERVER, settings.MAIL_PORT)
            else:
                server = smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT)
                if settings.MAIL_STARTTLS:
                    server.starttls()

            if settings.MAIL_USERNAME and settings.MAIL_PASSWORD:
                server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)

            server.sendmail(settings.MAIL_FROM, [to_email], msg.as_string())
            server.quit()
            logger.info(f"[SMTP MAIL] Sent successfully to {to_email}")
            return True
        except Exception as e:
            logger.error(f"[SMTP ERROR] Failed to send email to {to_email}: {e}")
            return False

    @classmethod
    def notify_step_assigned(cls, workflow, target_email: str, role_name: str, step_name: str):
        subject = f"[Workflow Action Required] {workflow.tracking_code}: {workflow.title}"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <div style="background-color: #2563eb; color: white; padding: 16px; border-radius: 6px;">
                <h2 style="margin: 0; font-size: 20px;">Action Required: {step_name.replace('_', ' ').title()}</h2>
            </div>
            <div style="padding: 20px 0;">
                <p>Hello <strong>{role_name}</strong>,</p>
                <p>A workflow task requires your input or review.</p>
                <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold; width: 35%;">Tracking Code:</td><td style="padding: 8px;">{workflow.tracking_code}</td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Title:</td><td style="padding: 8px;">{workflow.title}</td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Current Step:</td><td style="padding: 8px;"><span style="background: #e0e7ff; color: #3730a3; padding: 3px 8px; border-radius: 4px;">{workflow.current_step}</span></td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Status:</td><td style="padding: 8px;">{workflow.status}</td></tr>
                </table>
                <p style="margin-top: 25px;">
                    <a href="/workflows/{workflow.id}" style="background-color: #2563eb; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">View & Process Task</a>
                </p>
            </div>
            <div style="border-top: 1px solid #e2e8f0; padding-top: 15px; font-size: 12px; color: #94a3b8;">
                This is an automated notification from {settings.APP_NAME}. Please do not reply directly to this email.
            </div>
        </div>
        """
        cls.send_email(to_email=target_email, subject=subject, html_content=html)

    @classmethod
    def notify_decision(cls, workflow, recipient_email: str, decision: str, comment: str, actor_name: str):
        is_approved = decision.upper() in ["APPROVED", "CONFIRMED"]
        header_color = "#16a34a" if is_approved else "#dc2626"
        subject = f"[Workflow {decision.upper()}] {workflow.tracking_code}: {workflow.title}"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <div style="background-color: {header_color}; color: white; padding: 16px; border-radius: 6px;">
                <h2 style="margin: 0; font-size: 20px;">Workflow {decision.upper()}: {workflow.tracking_code}</h2>
            </div>
            <div style="padding: 20px 0;">
                <p>Hello,</p>
                <p>The workflow <strong>{workflow.title}</strong> was reviewed by <strong>{actor_name}</strong> and marked as <strong>{decision.upper()}</strong>.</p>
                {f'<blockquote style="border-left: 4px solid #cbd5e1; padding-left: 12px; color: #475569; margin: 15px 0;"><em>"{comment}"</em></blockquote>' if comment else ''}
                <p style="margin-top: 20px;">
                    <a href="/workflows/{workflow.id}" style="background-color: #0f172a; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Review Workflow Details</a>
                </p>
            </div>
            <div style="border-top: 1px solid #e2e8f0; padding-top: 15px; font-size: 12px; color: #94a3b8;">
                {settings.APP_NAME} &bull; Notifications
            </div>
        </div>
        """
        cls.send_email(to_email=recipient_email, subject=subject, html_content=html)

    @classmethod
    def notify_reminder(
        cls,
        workflow,
        recipient_email: str,
        recipient_name: str,
        reminder_description: str,
    ):
        """Sends a scheduled reminder / escalation email."""
        subject = f"[Reminder] {workflow.tracking_code}: Action Pending – {workflow.title}"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <div style="background-color: #d97706; color: white; padding: 16px; border-radius: 6px;">
                <h2 style="margin: 0; font-size: 20px;">⏰ Reminder: Action Pending</h2>
            </div>
            <div style="padding: 20px 0;">
                <p>Hello <strong>{recipient_name}</strong>,</p>
                <p>This is a scheduled reminder that the following workflow is still awaiting your action:</p>
                <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold; width: 35%;">Tracking Code:</td><td style="padding: 8px;">{workflow.tracking_code}</td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Title:</td><td style="padding: 8px;">{workflow.title}</td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Current Step:</td><td style="padding: 8px;"><span style="background: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 4px;">{workflow.current_step.replace('_', ' ').title()}</span></td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Status:</td><td style="padding: 8px;">{workflow.status}</td></tr>
                    <tr><td style="padding: 8px; color: #64748b; font-weight: bold;">Reminder:</td><td style="padding: 8px;"><em>{reminder_description}</em></td></tr>
                </table>
                <p style="margin-top: 25px;">
                    <a href="/workflows/{workflow.id}" style="background-color: #d97706; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Take Action Now</a>
                </p>
            </div>
            <div style="border-top: 1px solid #e2e8f0; padding-top: 15px; font-size: 12px; color: #94a3b8;">
                {settings.APP_NAME} &bull; Scheduled Notifications
            </div>
        </div>
        """
        cls.send_email(to_email=recipient_email, subject=subject, html_content=html)


"""Support API endpoint for handling contact form submissions.

Sends support requests via email to support@athletespace.ai.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, EmailStr, Field

from app.config.settings import settings

router = APIRouter(prefix="/support", tags=["support"])


class SupportRequest(BaseModel):
    """Support form submission request."""
    
    name: str = Field(..., min_length=1, max_length=100, description="Sender's name")
    email: EmailStr = Field(..., description="Sender's email address")
    subject: str = Field(..., min_length=1, max_length=200, description="Support request subject")
    message: str = Field(..., min_length=10, max_length=5000, description="Support request message")


class SupportResponse(BaseModel):
    """Support form submission response."""
    
    success: bool
    message: str


def send_support_email(request: SupportRequest) -> bool:
    """Send support email via SMTP.
    
    Args:
        request: Support request with name, email, subject, and message
        
    Returns:
        True if email sent successfully, False otherwise
    """
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        logger.warning("[SUPPORT] SMTP not configured, cannot send email")
        return False
    
    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AthleteSpace Support] {request.subject}"
        msg["From"] = settings.smtp_user
        msg["To"] = settings.support_email
        msg["Reply-To"] = request.email
        
        # Plain text version
        text_body = f"""
New Support Request from AthleteSpace

From: {request.name}
Email: {request.email}
Subject: {request.subject}

Message:
{request.message}

---
This message was sent via the AthleteSpace support form.
"""
        
        # HTML version
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #1e293b; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f8fafc; padding: 20px; border: 1px solid #e2e8f0; }}
        .field {{ margin-bottom: 15px; }}
        .label {{ font-weight: bold; color: #64748b; font-size: 12px; text-transform: uppercase; }}
        .value {{ margin-top: 5px; }}
        .message-box {{ background: white; padding: 15px; border: 1px solid #e2e8f0; border-radius: 4px; margin-top: 10px; }}
        .footer {{ font-size: 12px; color: #94a3b8; margin-top: 20px; padding-top: 15px; border-top: 1px solid #e2e8f0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin: 0;">New Support Request</h2>
        </div>
        <div class="content">
            <div class="field">
                <div class="label">From</div>
                <div class="value">{request.name}</div>
            </div>
            <div class="field">
                <div class="label">Email</div>
                <div class="value"><a href="mailto:{request.email}">{request.email}</a></div>
            </div>
            <div class="field">
                <div class="label">Subject</div>
                <div class="value">{request.subject}</div>
            </div>
            <div class="field">
                <div class="label">Message</div>
                <div class="message-box">{request.message.replace(chr(10), '<br>')}</div>
            </div>
            <div class="footer">
                This message was sent via the AthleteSpace support form.
            </div>
        </div>
    </div>
</body>
</html>
"""
        
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        
        # Send email
        logger.info(f"[SUPPORT] Connecting to SMTP server {settings.smtp_host}:{settings.smtp_port}")
        
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        
        logger.info(f"[SUPPORT] Email sent successfully from {request.email} to {settings.support_email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[SUPPORT] SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"[SUPPORT] SMTP error: {e}")
        return False
    except Exception as e:
        logger.exception(f"[SUPPORT] Unexpected error sending email: {e}")
        return False


@router.post("", response_model=SupportResponse)
async def submit_support_request(request: SupportRequest) -> SupportResponse:
    """Submit a support request.
    
    Sends an email to support@athletespace.ai with the user's message.
    No authentication required - this is a public endpoint.
    
    Args:
        request: Support request with name, email, subject, and message
        
    Returns:
        Success response with confirmation message
        
    Raises:
        HTTPException: If email sending fails
    """
    logger.info(f"[SUPPORT] Received support request from {request.email}: {request.subject}")
    
    # Check if SMTP is configured
    if not settings.smtp_host:
        logger.error("[SUPPORT] SMTP not configured - cannot process support request")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support email service is not configured. Please email support@athletespace.ai directly.",
        )
    
    # Send the email
    success = send_support_email(request)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send support request. Please try again or email support@athletespace.ai directly.",
        )
    
    return SupportResponse(
        success=True,
        message="Your support request has been sent. We'll get back to you as soon as possible.",
    )

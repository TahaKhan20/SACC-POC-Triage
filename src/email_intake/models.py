"""Data models for the Email Intake Agent.

Contains the EmailMessage dataclass used throughout the email intake
pipeline. Zero external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmailMessage:
    """Normalised email representation."""
    message_id: str
    subject: str
    sender: str
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    received_date: str = ""
    body_preview: str = ""
    body_content: str = ""
    has_attachments: bool = False
    attachments: list[dict[str, Any]] = field(default_factory=list)
    importance: str = "normal"
    is_read: bool = False
    score: int = 0
    category: str = ""
    score_details: list[str] = field(default_factory=list)

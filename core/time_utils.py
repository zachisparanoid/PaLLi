"""
Time utilities for timestamp formatting and timezone handling
"""

import time
from datetime import datetime, timezone
from typing import Optional


def format_time_12h(dt: datetime) -> str:
    """Format a datetime as 12-hour time without platform-specific strftime flags."""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.strftime('%M %p')}"


def format_month_day(dt: datetime, *, include_year: bool = False) -> str:
    """Format month/day without a leading zero on the day component."""
    base = f"{dt.strftime('%b')} {dt.day}"
    if include_year:
        return f"{base}, {dt.year}"
    return base


def get_current_time() -> float:
    """Get current Unix timestamp"""
    return time.time()


def get_current_datetime() -> datetime:
    """Get current datetime in system timezone"""
    return datetime.now()


def format_timestamp_for_llm(timestamp: float, current_time: Optional[float] = None) -> str:
    """
    Format timestamp for LLM context using mixed format:
    - "Today 3:30 PM", "Yesterday 2:15 PM", "Dec 18 at 4:20 PM"
    
    Args:
        timestamp: Unix timestamp to format
        current_time: Current time (defaults to now)
    
    Returns:
        Formatted timestamp string
    """
    if current_time is None:
        current_time = get_current_time()
    
    # Convert timestamps to datetime objects
    msg_dt = datetime.fromtimestamp(timestamp)
    current_dt = datetime.fromtimestamp(current_time)
    
    # Calculate time difference
    time_diff = current_time - timestamp
    
    # Format time part
    time_str = format_time_12h(msg_dt)  # 3:30 PM (no leading zero)
    
    # Determine date part based on recency
    if msg_dt.date() == current_dt.date():
        # Same day - "Today 3:30 PM"
        return f"Today {time_str}"
    
    elif time_diff < 86400 and current_dt.date().day - msg_dt.date().day == 1:
        # Yesterday - "Yesterday 3:30 PM"
        return f"Yesterday {time_str}"
    
    elif time_diff < 604800:  # Within a week
        # This week - "Monday 3:30 PM"
        day_name = msg_dt.strftime("%A")
        return f"{day_name} {time_str}"
    
    elif msg_dt.year == current_dt.year:
        # Same year - "Dec 18 at 4:20 PM"
        date_str = format_month_day(msg_dt)
        return f"{date_str} at {time_str}"

    else:
        # Different year - "Dec 18, 2023 at 4:20 PM"
        date_str = format_month_day(msg_dt, include_year=True)
        return f"{date_str} at {time_str}"


def get_time_of_day_greeting() -> str:
    """Get appropriate greeting based on current time of day"""
    current_hour = datetime.now().hour
    
    if 5 <= current_hour < 12:
        return "morning"
    elif 12 <= current_hour < 17:
        return "afternoon"
    elif 17 <= current_hour < 21:
        return "evening"
    else:
        return "night"


def get_current_time_context() -> str:
    """Get current time context for LLM"""
    now = datetime.now()

    # Format: "Dec 20, 2025, 3:45 PM" - includes year for accuracy
    formatted_time = f"{format_month_day(now, include_year=True)}, {format_time_12h(now)}"

    # Also include day of week for better context
    day_of_week = now.strftime("%A")

    return f"{day_of_week}, {formatted_time}"


def is_same_day(timestamp1: float, timestamp2: float) -> bool:
    """Check if two timestamps are on the same day"""
    dt1 = datetime.fromtimestamp(timestamp1)
    dt2 = datetime.fromtimestamp(timestamp2)
    return dt1.date() == dt2.date()


def get_relative_time_description(timestamp: float, current_time: Optional[float] = None) -> str:
    """
    Get relative time description for conversational use
    
    Returns descriptions like:
    - "just now", "a few minutes ago", "about an hour ago"
    - "earlier today", "yesterday", "a few days ago"
    """
    if current_time is None:
        current_time = get_current_time()
    
    time_diff = current_time - timestamp
    
    if time_diff < 60:  # Less than 1 minute
        return "just now"
    elif time_diff < 300:  # Less than 5 minutes
        return "a few minutes ago"
    elif time_diff < 3600:  # Less than 1 hour
        minutes = int(time_diff / 60)
        return f"about {minutes} minutes ago"
    elif time_diff < 7200:  # Less than 2 hours
        return "about an hour ago"
    elif time_diff < 86400:  # Less than 24 hours
        hours = int(time_diff / 3600)
        if is_same_day(timestamp, current_time):
            return f"about {hours} hours ago"
        else:
            return "earlier today"
    elif time_diff < 172800:  # Less than 2 days
        return "yesterday"
    elif time_diff < 604800:  # Less than 1 week
        days = int(time_diff / 86400)
        return f"{days} days ago"
    else:
        # Use the formatted timestamp for older messages
        return format_timestamp_for_llm(timestamp, current_time)

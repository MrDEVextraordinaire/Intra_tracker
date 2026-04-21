#!/usr/bin/env python3
"""
Models Module
Data classes for 42 campus tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# 42 API Constants
CAMPUS_ID = 75  # 1337.ma Rabat
CURSUS_PISCINE = 9  # C Piscine
CURSUS_MAIN = 21  # 42cursus


@dataclass
class Student:
    """Represents a student in the campus."""
    id: int
    login: str
    display_name: str
    pool_year: Optional[str] = None
    level: float = 0.0
    blackholed_at: Optional[str] = None
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def is_blackholed(self) -> bool:
        """Check if student is blackholed."""
        if not self.blackholed_at:
            return False
        try:
            bh = datetime.fromisoformat(self.blackholed_at.replace("Z", "+00:00"))
            return bh <= datetime.now(timezone.utc)
        except ValueError:
            return False
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "login": self.login,
            "display_name": self.display_name,
            "pool_year": self.pool_year,
            "level": self.level,
            "blackholed_at": self.blackholed_at,
            "added_at": self.added_at,
        }


@dataclass
class Location:
    """Represents a location session."""
    id: int
    host: str
    begin_at: str
    end_at: Optional[str] = None
    primary: bool = False
    campus_id: int = CAMPUS_ID
    
    # Parsed coordinates (populated by parser)
    coords: Optional[dict] = None
    
    # Polled timestamp (populated during poll)
    polled_at: Optional[str] = None
    
    def duration_hours(self, end_at: Optional[str] = None) -> float:
        """Calculate session duration in hours."""
        from parser import calc_session_duration
        end = end_at or self.end_at
        if not end:
            return 0.0
        return calc_session_duration(self.begin_at, end)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "host": self.host,
            "begin_at": self.begin_at,
            "end_at": self.end_at,
            "primary": self.primary,
            "campus_id": self.campus_id,
            "_coords": self.coords,
            "_polled_at": self.polled_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Location":
        """Create from API response dict."""
        return cls(
            id=data.get("id", 0),
            host=data.get("host", ""),
            begin_at=data.get("begin_at", ""),
            end_at=data.get("end_at"),
            primary=data.get("primary", False),
            campus_id=data.get("campus_id", CAMPUS_ID),
            coords=data.get("_coords"),
            polled_at=data.get("_polled_at"),
        )


@dataclass
class LocationHistory:
    """Full location history for a student."""
    login: str
    seeded_at: str
    history: list = field(default_factory=list)
    
    def total_hours(self) -> float:
        """Calculate total campus hours."""
        total = 0.0
        for loc in self.history:
            duration = loc.duration_hours() if isinstance(loc, Location) else 0.0
            total += duration
        return total
    
    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "seeded_at": self.seeded_at,
            "history": [h.to_dict() if isinstance(h, Location) else h for h in self.history],
        }


@dataclass
class Snapshot:
    """A snapshot of active students at a point in time."""
    polled_at: str
    active_count: int
    students: list  # List of active session dicts
    
    def to_dict(self) -> dict:
        return {
            "polled_at": self.polled_at,
            "active_count": self.active_count,
            "students": self.students,
        }


@dataclass  
class ExcludedStudent:
    """A student who was excluded from tracking."""
    login: str
    status: str  # "blackholed" or "piscine_only"
    reason: str
    excluded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "status": self.status,
            "reason": self.reason,
            "excluded_at": self.excluded_at,
        }


@dataclass
class ExamResult:
    """Result of a student on an exam."""
    exam_id: int
    exam_name: str
    cursus_id: int
    cursus_name: str
    score: float
    total: float
    created_at: str
    updated_at: str
    
    def to_dict(self) -> dict:
        return {
            "exam_id": self.exam_id,
            "exam_name": self.exam_name,
            "cursus_id": self.cursus_id,
            "cursus_name": self.cursus_name,
            "score": self.score,
            "total": self.total,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ExamResult":
        return cls(
            exam_id=data.get("exam_id", 0),
            exam_name=data.get("exam_name", ""),
            cursus_id=data.get("cursus_id", 0),
            cursus_name=data.get("cursus_name", ""),
            score=data.get("score", 0.0),
            total=data.get("total", 100.0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class ExamHistory:
    """Full exam history for a student."""
    login: str
    fetched_at: str
    exams: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "fetched_at": self.fetched_at,
            "exams": [e.to_dict() if isinstance(e, ExamResult) else e for e in self.exams],
        }


# Import timezone for the default_factory
from datetime import timezone
"""
models/student_signal.py

The canonical internal object produced by Stage 1 (Profile Parser).
Every downstream stage reads from StudentSignal — nothing reads the raw JSON directly.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ResearchKeywords:
    """Structured research signal extracted from the student profile."""
    primary_keywords: List[str] = field(default_factory=list)   # e.g. ["NLP", "mental health"]
    secondary_keywords: List[str] = field(default_factory=list) # e.g. ["affective computing"]
    thesis_topic: str = ""
    discipline: str = ""          # e.g. "Computer Science"
    primary_area: str = ""        # e.g. "NLP / Computational Linguistics"
    secondary_area: str = ""      # e.g. "Mental Health Informatics"
    project_descriptions: List[str] = field(default_factory=list)
    publication_titles: List[str] = field(default_factory=list)


@dataclass
class TargetConstraints:
    """Hard constraints that cannot be relaxed during filtering."""
    countries: List[str] = field(default_factory=list)   # e.g. ["UK", "Canada"]
    intake_semester: str = ""    # e.g. "Fall"
    intake_year: int = 0         # e.g. 2025
    funding_required: bool = True
    nationality: str = ""        # used for eligibility filter


@dataclass
class SoftSignals:
    """Soft preferences extracted from call notes and free text."""
    preferred_lab_size: str = ""         # "small", "medium", "large", ""
    interdisciplinary_ok: bool = True
    excluded_subfields: List[str] = field(default_factory=list)
    mentioned_professors: List[str] = field(default_factory=list)  # names student has already read
    additional_notes: str = ""


@dataclass
class StudentSignal:
    """
    The single source of truth for a student's profile within the pipeline.
    Produced by Stage 1 (parser.py), consumed by all subsequent stages.
    """
    student_id: str = ""
    student_name: str = ""
    student_level: str = ""          # "Bachelors", "Masters"

    research: ResearchKeywords = field(default_factory=ResearchKeywords)
    constraints: TargetConstraints = field(default_factory=TargetConstraints)
    soft_signals: SoftSignals = field(default_factory=SoftSignals)

    # Combined keyword string for embedding — built by parser
    embedding_text: str = ""

    def to_dict(self) -> dict:
        """Serialize to a plain dict for logging / caching."""
        return {
            "student_id": self.student_id,
            "student_name": self.student_name,
            "student_level": self.student_level,
            "research": {
                "primary_keywords": self.research.primary_keywords,
                "secondary_keywords": self.research.secondary_keywords,
                "thesis_topic": self.research.thesis_topic,
                "discipline": self.research.discipline,
                "primary_area": self.research.primary_area,
                "secondary_area": self.research.secondary_area,
                "project_descriptions": self.research.project_descriptions,
                "publication_titles": self.research.publication_titles,
            },
            "constraints": {
                "countries": self.constraints.countries,
                "intake_semester": self.constraints.intake_semester,
                "intake_year": self.constraints.intake_year,
                "funding_required": self.constraints.funding_required,
                "nationality": self.constraints.nationality,
            },
            "soft_signals": {
                "preferred_lab_size": self.soft_signals.preferred_lab_size,
                "interdisciplinary_ok": self.soft_signals.interdisciplinary_ok,
                "excluded_subfields": self.soft_signals.excluded_subfields,
                "mentioned_professors": self.soft_signals.mentioned_professors,
                "additional_notes": self.soft_signals.additional_notes,
            },
            "embedding_text": self.embedding_text,
        }

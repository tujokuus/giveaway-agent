"""Shared models and interfaces for competition discovery sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompetitionCandidate:
    """A competition and its metadata discovered from a source page."""

    url: str
    source: str
    title: str
    published_date: str | None = None
    platforms: tuple[str, ...] = ()
    organizer: str | None = None
    deadline: str | None = None
    prize: str | None = None
    entry_urls: tuple[str, ...] = ()


class CompetitionSource(ABC):
    """Define how one source website turns listing HTML into candidates."""

    name: str
    default_url: str

    @abstractmethod
    def discover(
        self,
        html: str,
        *,
        page_url: str,
    ) -> list[CompetitionCandidate]:
        """Extract competition candidates from one downloaded listing page."""


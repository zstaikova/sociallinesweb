from abc import ABC, abstractmethod
from .content_item import ContentItem


class BaseSource(ABC):
    @abstractmethod
    def fetch(self, limit: int = 10) -> list:
        """Fetch ContentItems from the source. Downloads media locally."""
        pass

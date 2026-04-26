from abc import ABC, abstractmethod
from .content_item import ContentItem


class BaseTransformer(ABC):
    @abstractmethod
    def transform(self, item: ContentItem) -> ContentItem:
        """Modify a ContentItem in place and return it."""
        pass

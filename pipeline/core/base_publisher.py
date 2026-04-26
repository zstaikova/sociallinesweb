from abc import ABC, abstractmethod
from .content_item import ContentItem


class BasePublisher(ABC):
    @abstractmethod
    def publish(self, item: ContentItem) -> bool:
        """Post item to the platform. Returns True on success."""
        pass

    def verify_auth(self) -> bool:
        """Check that stored credentials are valid. Override to implement."""
        return True

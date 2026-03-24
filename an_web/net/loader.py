"""Resource loading pipeline for AN-Web."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.net.client import NetworkClient, Response
    from an_web.net.resources import LoadPolicy


class ResourceLoader:
    """
    Orchestrates page resource loading.

    Load order (like a real browser):
    1. Main document HTML
    2. Synchronous scripts in <head>
    3. Stylesheets (for layout-lite visibility inference)
    4. Deferred / async scripts
    5. XHR/fetch triggered by JS
    """

    def __init__(self, client: NetworkClient, policy: LoadPolicy | None = None) -> None:
        self.client = client
        from an_web.net.resources import LoadPolicy as LP
        self.policy = policy or LP()

    async def load_document(self, url: str) -> Response:
        """Fetch the main HTML document."""
        response = await self.client.get(url)
        return response

    async def load_script(self, url: str) -> Response | None:
        """Fetch a JS resource."""
        from an_web.net.resources import ResourceType
        if not ResourceType.SCRIPT.should_load(self.policy):
            return None
        return await self.client.get(url)

    async def load_stylesheet(self, url: str) -> Response | None:
        """Fetch a CSS resource."""
        from an_web.net.resources import ResourceType
        if not ResourceType.STYLESHEET.should_load(self.policy):
            return None
        return await self.client.get(url)

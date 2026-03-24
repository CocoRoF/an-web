"""
Network layer for AN-Web.

Uses httpx.AsyncClient for async HTTP (vs libcurl in Lightpanda).

Modules:
    client    - httpx.AsyncClient wrapper (NetworkClient)
    cookies   - Cookie jar management (CookieJar)
    loader    - Resource loading pipeline (ResourceLoader)
    resources - Resource type classification and policy (ResourceType)
"""

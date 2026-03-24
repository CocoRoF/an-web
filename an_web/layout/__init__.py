"""
Layout-lite engine for AN-Web.

Not pixel rendering — interactability inference only.
Inspired by Lightpanda's approach: headless means no GPU paint,
but we still need visibility/hit-testing for AI action targeting.

Modules:
    visibility - display:none / visibility:hidden / hidden attribute processing
    hit_test   - Click target disambiguation, overlay/modal priority
    flow       - block/inline flow inference, z-order hints
"""

"""
Semantic extraction layer — the core AI-native differentiator.

Transforms raw DOM into AI-friendly world model (PageSemantics).
Corresponds to Lightpanda's SemanticTree.zig, but extends it with:
- Page type classification (login, search, listing, etc.)
- Action candidate ranking (primary CTA detection)
- Blocking element detection (modal, cookie banner)
- Stable selector generation for reliable re-targeting

Modules:
    extractor  - DOM → SemanticGraph transformation (SemanticExtractor)
    roles      - ARIA role inference engine
    affordances - Action affordance inference (click/type/select/scroll)
    page_type  - Page type classifier (login/search/listing/detail/checkout)
"""

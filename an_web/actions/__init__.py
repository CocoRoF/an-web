"""
Action runtime for AN-Web.

Each action follows the Lightpanda actions.zig pattern:
    precondition → execute → event_flush → postcondition → artifact → ActionResult

Modules:
    base      - Action base class and ActionResult model
    navigate  - navigate(url) action
    click     - click(target) + MouseEvent dispatch
    input     - type/clear/select + input/change event dispatch
    extract   - extract(selector | semantic_query) data extraction
    submit    - submit(form) form submission
"""

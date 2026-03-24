"""
CSS selector engine for AN-Web DOM.

Supports a practical subset of CSS Selectors Level 3 sufficient
for AI web automation:
  - Type:       a, button, input
  - ID:         #submit-btn
  - Class:      .btn-primary  / .foo.bar
  - Attribute:  [name=email] / [type="submit"] / [disabled] / [name^=user]
  - Compound:   button.primary  /  input#email  /  input[type=email]
  - Descendant: form input  /  .container a
  - Child:      form > button
  - Pseudo:     :first-child  /  :last-child  /  :nth-child(n)  /  :disabled
                :checked  /  :hidden  (AN-Web extension)

NOT supported (out of scope for AI automation):
  - :not()  /  :is()  /  :where()
  - Sibling combinators  (~  +)
  - ::before / ::after pseudo-elements
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element, Node


# ─── Token types ──────────────────────────────────────────────────────────────

@dataclass
class SimpleSelector:
    """A single selector without combinators (e.g. 'input.foo#bar[type=text]')."""
    tag: str = ""                    # "" means wildcard
    ids: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    attrs: list[tuple[str, str, str]] = field(default_factory=list)
    # attr tuples: (name, op, value)  op ∈ {"", "=", "^=", "$=", "*=", "~=", "|="}
    pseudos: list[str] = field(default_factory=list)


@dataclass
class ComplexSelector:
    """A sequence of SimpleSelectors linked by combinators."""
    parts: list[tuple[str, SimpleSelector]] = field(default_factory=list)
    # parts[i] = (combinator, simple_selector)
    # combinator: " " = descendant, ">" = child, "" for the first part


# ─── Public API ───────────────────────────────────────────────────────────────

class SelectorEngine:
    """CSS selector matching against AN-Web DOM tree."""

    def query_selector(self, doc: Document, selector: str) -> Element | None:
        """Return the first matching element, in document order."""
        results = self.query_selector_all(doc, selector)
        return results[0] if results else None

    def query_selector_all(self, doc: Document, selector: str) -> list[Element]:
        """Return all matching elements, in document order."""
        from an_web.dom.nodes import Element as El
        selector = selector.strip()
        if not selector:
            return []

        # Handle comma-separated group: "a, button" → union of both
        if "," in selector and not _inside_brackets(selector, selector.index(",")):
            results: list[El] = []
            seen: set[str] = set()
            for part in _split_top_level(selector, ","):
                for el in self.query_selector_all(doc, part.strip()):
                    if el.node_id not in seen:
                        seen.add(el.node_id)
                        results.append(el)
            return results

        try:
            complex_sel = _parse_complex(selector)
        except Exception:
            # Fallback: use simple tag/id/class matching
            return [el for el in doc.iter_elements() if _simple_match(el, selector)]

        return [el for el in doc.iter_elements() if _matches_complex(el, complex_sel)]


# ─── Parser ───────────────────────────────────────────────────────────────────

# Tokenizer regex: matches one token of a CSS simple selector
_TOKEN_RE = re.compile(
    r"""
    (\*) |                              # universal
    ([a-zA-Z_\-][a-zA-Z0-9_\-]*) |     # tag/ident
    \#([a-zA-Z_\-][a-zA-Z0-9_\-]*) |   # id
    \.([a-zA-Z_\-][a-zA-Z0-9_\-]*) |   # class
    \[([^\]]+)\] |                      # attribute
    :([a-zA-Z_\-][a-zA-Z0-9_\-()]*) |  # pseudo
    ([ >+~])                            # combinator
    """,
    re.VERBOSE,
)

_ATTR_OP_RE = re.compile(r"""^([a-zA-Z_\-][a-zA-Z0-9_\-]*)([\^$*~|]?=)(.+)$""")


def _parse_simple(token_stream: list[tuple[int, str]]) -> tuple[SimpleSelector, int]:
    """Parse one SimpleSelector from token_stream. Returns (selector, tokens_consumed)."""
    sel = SimpleSelector()
    i = 0
    while i < len(token_stream):
        kind, val = token_stream[i]
        if kind == 7:  # combinator — stop this simple selector
            break
        elif kind == 0:  # *
            sel.tag = ""
        elif kind == 1:  # tag/ident
            sel.tag = val.lower()
        elif kind == 2:  # #id
            sel.ids.append(val)
        elif kind == 3:  # .class
            sel.classes.append(val.lower())
        elif kind == 4:  # [attr...]
            sel.attrs.append(_parse_attr(val))
        elif kind == 5:  # :pseudo
            sel.pseudos.append(val.lower())
        i += 1
    return sel, i


def _parse_attr(inner: str) -> tuple[str, str, str]:
    """Parse attribute selector content: 'name=value' → ('name', '=', 'value')."""
    inner = inner.strip()
    m = _ATTR_OP_RE.match(inner)
    if m:
        name = m.group(1).lower()
        op = m.group(2)
        value = m.group(3).strip().strip("\"'")
        return (name, op, value)
    return (inner.lower(), "", "")


def _tokenize(selector: str) -> list[tuple[int, str]]:
    """Convert selector string to list of (kind, value) tokens."""
    tokens: list[tuple[int, str]] = []
    pos = 0
    while pos < len(selector):
        m = _TOKEN_RE.match(selector, pos)
        if not m:
            pos += 1
            continue
        for i, grp in enumerate(m.groups()):
            if grp is not None:
                if i == 6 and grp == " ":
                    # Collapse multiple spaces into one combinator token
                    if tokens and tokens[-1][0] == 7:
                        pass  # already have a combinator
                    else:
                        tokens.append((7, " "))
                elif i == 6:  # >, +, ~
                    # Strip any pending space combinator before >/+/~
                    if tokens and tokens[-1] == (7, " "):
                        tokens.pop()
                    tokens.append((7, grp))
                else:
                    tokens.append((i, grp))
                break
        pos = m.end()
    # Strip leading/trailing space combinators
    while tokens and tokens[0][0] == 7:
        tokens.pop(0)
    while tokens and tokens[-1][0] == 7:
        tokens.pop()
    return tokens


def _parse_complex(selector: str) -> ComplexSelector:
    """Parse 'form > input.foo' into ComplexSelector parts."""
    tokens = _tokenize(selector.strip())
    parts: list[tuple[str, SimpleSelector]] = []
    combinator = ""
    i = 0
    while i < len(tokens):
        if tokens[i][0] == 7:  # combinator
            combinator = tokens[i][1]
            i += 1
            continue
        chunk = tokens[i:]
        simple, consumed = _parse_simple(chunk)
        parts.append((combinator, simple))
        combinator = ""
        i += consumed
    return ComplexSelector(parts=parts)


# ─── Matching ─────────────────────────────────────────────────────────────────

def _matches_complex(element: Element, sel: ComplexSelector) -> bool:
    """
    Match a ComplexSelector against an element.

    Evaluated right-to-left: the rightmost SimpleSelector must match the
    element; each preceding part must match an ancestor according to its
    combinator.
    """
    if not sel.parts:
        return False

    # Rightmost part must match this element
    _, last_simple = sel.parts[-1]
    if not _matches_simple(element, last_simple):
        return False

    if len(sel.parts) == 1:
        return True

    # Walk remaining parts right-to-left, checking ancestors
    node: Node = element
    for idx in range(len(sel.parts) - 2, -1, -1):
        combinator, simple = sel.parts[idx + 1]
        prev_combinator, prev_simple = sel.parts[idx], (None, None)
        # combinator stored in the NEXT part
        if idx + 1 < len(sel.parts):
            combinator = sel.parts[idx + 1][0]
        else:
            combinator = " "

        if combinator == ">":
            parent = getattr(node, "parent", None)
            if parent is None:
                return False
            if not _matches_simple(parent, sel.parts[idx][1]):
                return False
            node = parent
        else:  # descendant " "
            ancestor = getattr(node, "parent", None)
            found = False
            while ancestor is not None:
                if _matches_simple(ancestor, sel.parts[idx][1]):
                    found = True
                    node = ancestor
                    break
                ancestor = getattr(ancestor, "parent", None)
            if not found:
                return False

    return True


def _matches_simple(node: Any, simple: SimpleSelector) -> bool:
    """Check whether a node matches a SimpleSelector."""
    from an_web.dom.nodes import Element
    if not isinstance(node, Element):
        return False

    # Tag
    if simple.tag and node.tag != simple.tag:
        return False

    # IDs
    for id_ in simple.ids:
        if node.get_id() != id_:
            return False

    # Classes
    cls_list = node.get_class_list()
    for cls in simple.classes:
        if cls not in cls_list:
            return False

    # Attributes
    for name, op, value in simple.attrs:
        attr_val = node.get_attribute(name)
        if op == "":
            if attr_val is None:
                return False
        elif op == "=":
            if attr_val != value:
                return False
        elif op == "^=":
            if not (attr_val or "").startswith(value):
                return False
        elif op == "$=":
            if not (attr_val or "").endswith(value):
                return False
        elif op == "*=":
            if value not in (attr_val or ""):
                return False
        elif op == "~=":
            if value not in (attr_val or "").split():
                return False
        elif op == "|=":
            av = attr_val or ""
            if av != value and not av.startswith(value + "-"):
                return False

    # Pseudo-classes
    for pseudo in simple.pseudos:
        if not _matches_pseudo(node, pseudo):
            return False

    return True


def _matches_pseudo(element: Element, pseudo: str) -> bool:
    """Evaluate a pseudo-class selector."""
    from an_web.dom.nodes import Element as El

    if pseudo == "disabled":
        return element.is_disabled()
    if pseudo == "checked":
        return "checked" in element.attributes
    if pseudo == "hidden":
        return element.visibility_state in ("none", "hidden")
    if pseudo == "visible":
        return element.visibility_state == "visible"
    if pseudo == "first-child":
        parent = getattr(element, "parent", None)
        if parent is None:
            return False
        siblings = [c for c in parent.children if isinstance(c, El)]
        return bool(siblings) and siblings[0] is element
    if pseudo == "last-child":
        parent = getattr(element, "parent", None)
        if parent is None:
            return False
        siblings = [c for c in parent.children if isinstance(c, El)]
        return bool(siblings) and siblings[-1] is element
    if pseudo.startswith("nth-child("):
        n_str = pseudo[len("nth-child("):-1]
        parent = getattr(element, "parent", None)
        if parent is None:
            return False
        siblings = [c for c in parent.children if isinstance(c, El)]
        idx = next((i for i, s in enumerate(siblings) if s is element), -1)
        if idx == -1:
            return False
        return _nth_match(n_str, idx + 1)  # 1-based
    return True  # unknown pseudo → ignore (don't reject)


def _nth_match(expr: str, n: int) -> bool:
    """Evaluate nth-child expression: 'odd', 'even', '3', '2n+1'."""
    expr = expr.strip().lower()
    if expr == "odd":
        return n % 2 == 1
    if expr == "even":
        return n % 2 == 0
    try:
        return n == int(expr)
    except ValueError:
        pass
    # an+b pattern
    m = re.match(r"^(-?\d*)n([+-]\d+)?$", expr)
    if m:
        a = int(m.group(1) or "1")
        b = int(m.group(2) or "0")
        if a == 0:
            return n == b
        k = (n - b)
        return k % a == 0 and k // a >= 0
    return False


# ─── Fallback simple matching (non-compound) ──────────────────────────────────

def _simple_match(element: Element, selector: str) -> bool:
    """Basic single-token matching used as parse fallback."""
    s = selector.strip()
    if s.startswith("#"):
        return element.get_id() == s[1:]
    if s.startswith("."):
        return s[1:] in element.get_class_list()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1]
        if "=" in inner:
            attr, _, value = inner.partition("=")
            return element.get_attribute(attr.strip()) == value.strip().strip("\"'")
        return element.has_attribute(inner)
    if s and s.replace("-", "").replace("_", "").isalnum():
        return element.tag == s.lower()
    return False


# ─── Utilities ────────────────────────────────────────────────────────────────

def _inside_brackets(s: str, pos: int) -> bool:
    """Return True if position pos is inside [...] in string s."""
    depth = 0
    for i, ch in enumerate(s):
        if i == pos:
            return depth > 0
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
    return False


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split s by sep, ignoring occurrences inside [...] brackets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if depth == 0 and s[i:i + len(sep)] == sep:
            parts.append("".join(current))
            current = []
            i += len(sep)
            continue
        current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts

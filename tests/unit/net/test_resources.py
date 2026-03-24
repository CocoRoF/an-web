"""Unit tests for net/resources.py — ResourceType and LoadPolicy."""
from __future__ import annotations

import pytest
from an_web.net.resources import LoadPolicy, ResourceType


class TestResourceType:
    def test_all_members_exist(self):
        expected = {
            "DOCUMENT", "SCRIPT", "STYLESHEET", "IMAGE",
            "FONT", "XHR", "FETCH", "WEBSOCKET", "MEDIA", "OTHER",
        }
        actual = {m.name for m in ResourceType}
        assert expected == actual

    # ── from_content_type ──────────────────────────────────────────────────────

    def test_javascript_content_type(self):
        assert ResourceType.from_content_type("application/javascript") == ResourceType.SCRIPT

    def test_ecmascript_content_type(self):
        assert ResourceType.from_content_type("text/ecmascript") == ResourceType.SCRIPT

    def test_css_content_type(self):
        assert ResourceType.from_content_type("text/css") == ResourceType.STYLESHEET

    def test_html_content_type(self):
        assert ResourceType.from_content_type("text/html; charset=utf-8") == ResourceType.DOCUMENT

    def test_image_content_type(self):
        assert ResourceType.from_content_type("image/png") == ResourceType.IMAGE

    def test_font_content_type(self):
        assert ResourceType.from_content_type("font/woff2") == ResourceType.FONT

    def test_woff_in_content_type(self):
        assert ResourceType.from_content_type("application/x-font-woff") == ResourceType.FONT

    def test_unknown_content_type_returns_other(self):
        assert ResourceType.from_content_type("application/octet-stream") == ResourceType.OTHER

    def test_case_insensitive(self):
        assert ResourceType.from_content_type("Application/JavaScript") == ResourceType.SCRIPT
        assert ResourceType.from_content_type("TEXT/CSS") == ResourceType.STYLESHEET

    # ── should_load ───────────────────────────────────────────────────────────

    def test_should_load_document_default(self):
        assert ResourceType.DOCUMENT.should_load() is True

    def test_should_load_script_default(self):
        assert ResourceType.SCRIPT.should_load() is True

    def test_should_load_xhr_default(self):
        assert ResourceType.XHR.should_load() is True

    def test_should_load_fetch_default(self):
        assert ResourceType.FETCH.should_load() is True

    def test_should_not_load_image_default(self):
        assert ResourceType.IMAGE.should_load() is False

    def test_should_not_load_stylesheet_default(self):
        assert ResourceType.STYLESHEET.should_load() is False

    def test_should_not_load_font_default(self):
        assert ResourceType.FONT.should_load() is False

    def test_should_load_with_custom_policy(self):
        policy = LoadPolicy(allowed_types={ResourceType.IMAGE})
        assert ResourceType.IMAGE.should_load(policy) is True
        assert ResourceType.SCRIPT.should_load(policy) is False

    def test_should_load_with_none_policy_uses_default(self):
        assert ResourceType.DOCUMENT.should_load(None) is True


class TestLoadPolicy:
    def test_default_allowed_types(self):
        policy = LoadPolicy()
        assert ResourceType.DOCUMENT in policy.allowed_types
        assert ResourceType.SCRIPT in policy.allowed_types
        assert ResourceType.XHR in policy.allowed_types
        assert ResourceType.FETCH in policy.allowed_types
        assert ResourceType.IMAGE not in policy.allowed_types
        assert ResourceType.STYLESHEET not in policy.allowed_types

    def test_custom_allowed_types(self):
        custom = {ResourceType.IMAGE, ResourceType.FONT}
        policy = LoadPolicy(allowed_types=custom)
        assert policy.allowed_types == custom

    def test_default_is_independent_copy(self):
        p1 = LoadPolicy()
        p2 = LoadPolicy()
        p1.allowed_types.add(ResourceType.IMAGE)
        assert ResourceType.IMAGE not in p2.allowed_types

    def test_allow_all(self):
        policy = LoadPolicy()
        policy.allow_all()
        for rt in ResourceType:
            assert rt in policy.allowed_types

    def test_allow_styles(self):
        policy = LoadPolicy()
        assert ResourceType.STYLESHEET not in policy.allowed_types
        policy.allow_styles()
        assert ResourceType.STYLESHEET in policy.allowed_types
        # others should still be there
        assert ResourceType.DOCUMENT in policy.allowed_types

    def test_allow_styles_is_additive(self):
        policy = LoadPolicy()
        original_count = len(policy.allowed_types)
        policy.allow_styles()
        assert len(policy.allowed_types) == original_count + 1

    def test_allow_all_then_check(self):
        policy = LoadPolicy()
        policy.allow_all()
        assert ResourceType.WEBSOCKET in policy.allowed_types
        assert ResourceType.MEDIA in policy.allowed_types
        assert ResourceType.OTHER in policy.allowed_types

"""
AN-Web: AI-Native Web Browser Engine

A Python-native lightweight browser engine designed for AI agents.
Instead of rendering pixels for humans, AN-Web executes the web
as an actionable state machine for AI.

Architecture:
    Control Plane  → SessionManager, PolicyEngine, Scheduler
    Execution Plane → NetworkLoader, HTMLParser, DOMCore, JSBridge, EventLoop
    Semantic Layer  → SemanticExtractor, ActionRuntime, ArtifactCollector
"""

__version__ = "0.1.2"
__author__ = "AN-Web Team"

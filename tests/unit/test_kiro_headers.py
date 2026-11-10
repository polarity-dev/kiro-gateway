# -*- coding: utf-8 -*-

"""
Unit tests for get_kiro_headers User-Agent identification.
"""

from unittest.mock import MagicMock, patch

from kiro.utils import get_kiro_headers


class TestKiroHeadersUserAgent:
    """Tests for the Kiro IDE version advertised in request headers."""

    def test_user_agent_advertises_configured_ide_version(self):
        """
        What it does: Verifies both User-Agent headers carry KIRO_IDE_VERSION.
        Purpose: Outdated versions get 403 FEATURE_NOT_SUPPORTED from Kiro (MCP web_search).
        """
        auth_manager = MagicMock()
        auth_manager.fingerprint = "abc123"

        with patch("kiro.utils.KIRO_IDE_VERSION", "1.4.121"):
            headers = get_kiro_headers(auth_manager, "token")

        assert headers["User-Agent"].endswith("KiroIDE-1.4.121-abc123")
        assert headers["x-amz-user-agent"].endswith("KiroIDE-1.4.121-abc123")
        assert "0.7.45" not in headers["User-Agent"]

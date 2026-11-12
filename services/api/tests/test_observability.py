"""Logs redact secrets and personal data by field name, before anything is written.

Tokens, cookies, passwords and email addresses never reach a log (OWASP ASVS 5.0 V16).
"""

from __future__ import annotations

import pytest

from app.observability import REDACTED, redact_sensitive


class TestRedaction:
    @pytest.mark.parametrize(
        "field",
        [
            "password",
            "new_password",
            "session_token",
            "csrf_token",
            "cookie",
            "authorization",
            "api_key",
            "openalex_api-key",
            "email",
            "session_id",
            "otp",
            "recovery_code",
            "client_secret",
        ],
    )
    def test_a_sensitive_field_is_replaced_whatever_its_value(self, field):
        event = redact_sensitive(None, "info", {"event": "signed_in", field: "hunter2"})

        assert event[field] == REDACTED

    def test_field_names_match_regardless_of_case(self):
        event = redact_sensitive(None, "info", {"event": "x", "Authorization": "Bearer abc"})

        assert event["Authorization"] == REDACTED

    def test_the_event_name_and_ordinary_fields_are_kept(self):
        event = redact_sensitive(
            None,
            "info",
            {"event": "password_reset_requested", "request_id": "abc-123", "path": "/x"},
        )

        assert event == {
            "event": "password_reset_requested",
            "request_id": "abc-123",
            "path": "/x",
        }

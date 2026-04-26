"""Tests for PII scrubbing — evaluations P2-E1, E2, E3 and edge cases."""
from __future__ import annotations

import pytest

from pulse.phase_2.preprocess.pii import scrub_pii


def _scrub(text: str) -> str:
    return scrub_pii(text)[0]


def _counts(text: str) -> dict[str, int]:
    return scrub_pii(text)[1]


class TestEmailScrub:
    # P2-E1
    def test_standard_email_replaced(self) -> None:
        result = _scrub("Email me at user@example.com please")
        assert "[email]" in result
        assert "user@example.com" not in result

    def test_email_count_increments(self) -> None:
        counts = _counts("Contact user@example.com or admin@corp.io")
        assert counts["email"] == 2

    def test_email_case_insensitive(self) -> None:
        result = _scrub("CONTACT US@DOMAIN.COM for help")
        assert "[email]" in result

    # Edge case: obfuscated email
    def test_obfuscated_at_dot(self) -> None:
        result = _scrub("Reach me at user [at] example [dot] com for help")
        assert "[email]" in result
        assert "user" not in result or "example" not in result

    def test_obfuscated_plain_at(self) -> None:
        result = _scrub("Write to user at example dot com for details")
        assert "[email]" in result


class TestPhoneScrub:
    # P2-E2
    def test_international_with_plus(self) -> None:
        result = _scrub("Call +91 98765 43210 anytime for support")
        assert "[phone]" in result
        assert "98765" not in result

    def test_formatted_domestic(self) -> None:
        result = _scrub("Call 098-765-4321 for assistance")
        assert "[phone]" in result

    def test_international_us_format(self) -> None:
        result = _scrub("Dial +1 (415) 555-0100 for support")
        assert "[phone]" in result

    def test_spaced_digit_sequence(self) -> None:
        result = _scrub("Number is 9 8 7 6 5 4 3 2 1 0 please call")
        assert "[phone]" in result

    def test_phone_count_increments(self) -> None:
        counts = _counts("Call +91 98765-43210 or 022-4567-8900 for details")
        assert counts["phone"] >= 1


class TestAccountScrub:
    # P2-E3
    def test_12_digit_account_replaced(self) -> None:
        result = _scrub("My account number is 123456789012 please verify")
        assert "[account]" in result
        assert "123456789012" not in result

    def test_16_digit_card_replaced(self) -> None:
        result = _scrub("Card: 1234567890123456 expired")
        assert "[account]" in result

    def test_account_count_increments(self) -> None:
        counts = _counts("Account 123456789012 and card 1234567890123456 both")
        assert counts["account"] >= 1

    # False-positive guards
    def test_year_not_replaced(self) -> None:
        result = _scrub("Installed in 2024 and it is great")
        assert "2024" in result
        assert "[account]" not in result

    def test_version_number_not_replaced(self) -> None:
        result = _scrub("App version 9.5.0 works fine now")
        assert "[account]" not in result

    def test_short_number_not_account(self) -> None:
        # 9-digit number: below the 10-digit threshold
        result = _scrub("Order 987654321 shipped today")
        assert "987654321" in result
        assert "[account]" not in result

    def test_rating_not_replaced(self) -> None:
        result = _scrub("I give this app 5 stars out of 5")
        assert "[account]" not in result

    def test_amount_not_replaced(self) -> None:
        result = _scrub("Invested ₹500 last week successfully")
        assert "[account]" not in result


class TestUrlScrub:
    def test_http_url_replaced(self) -> None:
        result = _scrub("Visit https://example.com/page for details")
        assert "[url]" in result
        assert "https://example.com" not in result

    def test_url_count_increments(self) -> None:
        counts = _counts("See http://a.com and https://b.com for more")
        assert counts["url"] == 2

    def test_email_inside_url_scrubbed(self) -> None:
        # URL scrubbed first, so email inside URL doesn't survive
        result = _scrub("Profile at https://site.com/user@mail.com/profile found")
        # Either the URL is gone or at minimum the email part is gone
        assert "user@mail.com" not in result


class TestScrubOrder:
    def test_returns_counts_dict(self) -> None:
        _, counts = scrub_pii("user@example.com and +91 98765-43210 and 123456789012")
        assert isinstance(counts, dict)
        assert "email" in counts
        assert "phone" in counts
        assert "account" in counts

    def test_arabic_indic_digits_normalized(self) -> None:
        # Arabic-Indic digits ٠١٢٣٤٥٦٧٨٩ → 0123456789 before matching
        result = _scrub("Account ١٢٣٤٥٦٧٨٩٠١٢ processed")
        assert "[account]" in result

    def test_no_pii_returns_unchanged_counts(self) -> None:
        text = "This is a great investment and trading application."
        _, counts = scrub_pii(text)
        assert all(v == 0 for v in counts.values())

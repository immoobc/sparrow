"""Unit tests for detect_significant_moves() edge cases.

Tests cover:
- Threshold boundaries (1.99%, 2.0%, 2.01% and negatives)
- Empty input
- Price validation (function uses only change_pct)
- Single item input
- All significant
- None significant
"""

import pytest

from src.advisor.global_market import detect_significant_moves


# --- Threshold boundary tests ---


class TestThresholdBoundaries:
    """Test that the 2.0% threshold boundary is respected (strictly greater than)."""

    def test_change_pct_1_99_not_significant(self):
        """1.99% should NOT be significant (abs(1.99) <= 2.0)."""
        quotes = {"资产A": {"change_pct": 1.99, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert result == []

    def test_change_pct_2_0_not_significant(self):
        """2.0% should NOT be significant (abs(2.0) is NOT > 2.0, it's equal)."""
        quotes = {"资产A": {"change_pct": 2.0, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert result == []

    def test_change_pct_2_01_is_significant(self):
        """2.01% SHOULD be significant (abs(2.01) > 2.0)."""
        quotes = {"资产A": {"change_pct": 2.01, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert len(result) == 1
        assert result[0]["name"] == "资产A"
        assert result[0]["change_pct"] == 2.01
        assert result[0]["direction"] == "up"

    def test_change_pct_neg_1_99_not_significant(self):
        """-1.99% should NOT be significant."""
        quotes = {"资产A": {"change_pct": -1.99, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert result == []

    def test_change_pct_neg_2_0_not_significant(self):
        """-2.0% should NOT be significant (abs(-2.0) == 2.0, not > 2.0)."""
        quotes = {"资产A": {"change_pct": -2.0, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert result == []

    def test_change_pct_neg_2_01_is_significant(self):
        """-2.01% SHOULD be significant with direction 'down'."""
        quotes = {"资产A": {"change_pct": -2.01, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert len(result) == 1
        assert result[0]["name"] == "资产A"
        assert result[0]["change_pct"] == -2.01
        assert result[0]["direction"] == "down"


# --- Empty input test ---


class TestEmptyInput:
    """Test behavior with empty input."""

    def test_empty_dict_returns_empty_list(self):
        """Empty dict {} should return empty list []."""
        result = detect_significant_moves({})
        assert result == []


# --- Price validation tests ---


class TestPriceValidation:
    """Test that function works regardless of price values (only uses change_pct)."""

    def test_works_with_any_price_value(self):
        """Function should work regardless of price values."""
        quotes = {
            "资产A": {"price": 99999.99, "change_pct": 3.5, "code": "test001"},
            "资产B": {"price": 0.01, "change_pct": -5.0, "code": "test002"},
        }
        result = detect_significant_moves(quotes)
        assert len(result) == 2

    def test_missing_change_pct_defaults_to_zero(self):
        """Missing change_pct key should default to 0.0 and not be significant."""
        quotes = {"资产A": {"price": 100.0, "code": "test001"}}
        result = detect_significant_moves(quotes)
        assert result == []

    def test_missing_code_field(self):
        """Missing code field should default to empty string."""
        quotes = {"资产A": {"change_pct": 5.0}}
        result = detect_significant_moves(quotes)
        assert len(result) == 1
        assert result[0]["code"] == ""


# --- Single item input ---


class TestSingleItemInput:
    """Test with a single asset in quotes dict."""

    def test_single_significant_asset(self):
        """Single asset above threshold should return one-element list."""
        quotes = {"纳斯达克": {"change_pct": -3.2, "code": "usIXIC"}}
        result = detect_significant_moves(quotes)
        assert len(result) == 1
        assert result[0]["name"] == "纳斯达克"
        assert result[0]["direction"] == "down"
        assert result[0]["code"] == "usIXIC"

    def test_single_non_significant_asset(self):
        """Single asset below threshold should return empty list."""
        quotes = {"上证指数": {"change_pct": 0.5, "code": "sh000001"}}
        result = detect_significant_moves(quotes)
        assert result == []


# --- All significant ---


class TestAllSignificant:
    """Test when all assets exceed threshold."""

    def test_all_assets_significant(self):
        """All assets above threshold should all appear in result."""
        quotes = {
            "标普500": {"change_pct": 3.0, "code": "usINX"},
            "纳斯达克": {"change_pct": -4.5, "code": "usIXIC"},
            "恒生指数": {"change_pct": 2.5, "code": "hkHSI"},
        }
        result = detect_significant_moves(quotes)
        assert len(result) == 3
        names = {r["name"] for r in result}
        assert names == {"标普500", "纳斯达克", "恒生指数"}


# --- None significant ---


class TestNoneSignificant:
    """Test when all assets are below threshold."""

    def test_no_assets_significant(self):
        """All assets below threshold should return empty list."""
        quotes = {
            "上证指数": {"change_pct": 0.8, "code": "sh000001"},
            "沪深300": {"change_pct": -1.5, "code": "sh000300"},
            "创业板指": {"change_pct": 1.99, "code": "sz399006"},
        }
        result = detect_significant_moves(quotes)
        assert result == []

"""Property-based test for detect_significant_moves().

Feature: sparrow-refactor, Property 1: Significant Move Detection Threshold

Validates: Requirements 2.1

For any float value change_pct, detect_significant_moves() SHALL classify that asset
as significant if and only if abs(change_pct) > 2.0, and the returned direction field
SHALL be "up" when change_pct > 0 and "down" when change_pct < 0.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings as hypothesis_settings, strategies as st

from src.advisor.global_market import detect_significant_moves


# Strategy: generate a dict of 1-5 assets with random change_pct values
asset_names = ["上证指数", "标普500", "纳斯达克", "道琼斯", "恒生指数"]

quote_entry_strategy = st.fixed_dictionaries({
    "price": st.floats(min_value=0.01, max_value=100000, allow_nan=False),
    "change_pct": st.floats(min_value=-50, max_value=50, allow_nan=False),
    "name": st.sampled_from(asset_names),
    "code": st.sampled_from(["sh000001", "usINX", "usIXIC", "usDJI", "hkHSI"]),
})

quotes_strategy = st.dictionaries(
    keys=st.sampled_from(asset_names),
    values=quote_entry_strategy,
    min_size=1,
    max_size=5,
)


@hypothesis_settings(max_examples=200)
@given(quotes=quotes_strategy)
def test_significant_move_threshold_multi_asset(quotes: dict):
    """**Validates: Requirements 2.1**

    Property: For multiple assets, detect_significant_moves returns exactly those
    with abs(change_pct) > 2.0, with correct direction.
    """
    result = detect_significant_moves(quotes, threshold=2.0)
    result_names = {item["name"] for item in result}

    for asset_name, data in quotes.items():
        change_pct = data["change_pct"]

        if abs(change_pct) > 2.0:
            # Asset MUST appear in results
            assert asset_name in result_names, (
                f"{asset_name} with change_pct={change_pct} should be significant"
            )
            # Find the entry and verify direction
            entry = next(item for item in result if item["name"] == asset_name)
            if change_pct > 0:
                assert entry["direction"] == "up", (
                    f"{asset_name}: change_pct={change_pct} > 0, expected direction='up'"
                )
            else:
                assert entry["direction"] == "down", (
                    f"{asset_name}: change_pct={change_pct} < 0, expected direction='down'"
                )
            # Verify change_pct is preserved
            assert entry["change_pct"] == change_pct
        else:
            # Asset MUST NOT appear in results
            assert asset_name not in result_names, (
                f"{asset_name} with change_pct={change_pct} should NOT be significant"
            )


@hypothesis_settings(max_examples=200)
@given(change_pct=st.floats(min_value=-50, max_value=50, allow_nan=False))
def test_significant_move_threshold_single_asset(change_pct: float):
    """**Validates: Requirements 2.1**

    Property: For a single asset with a given change_pct, the function correctly
    classifies it as significant (or not) based on the ±2.0 threshold.
    """
    quotes = {
        "测试资产": {
            "price": 100.0,
            "change_pct": change_pct,
            "name": "测试资产",
            "code": "sh000001",
        }
    }

    result = detect_significant_moves(quotes, threshold=2.0)

    if abs(change_pct) > 2.0:
        assert len(result) == 1, (
            f"change_pct={change_pct}, abs > 2.0, expected 1 result, got {len(result)}"
        )
        entry = result[0]
        assert entry["name"] == "测试资产"
        assert entry["change_pct"] == change_pct
        if change_pct > 0:
            assert entry["direction"] == "up"
        else:
            assert entry["direction"] == "down"
    else:
        assert len(result) == 0, (
            f"change_pct={change_pct}, abs <= 2.0, expected 0 results, got {len(result)}"
        )

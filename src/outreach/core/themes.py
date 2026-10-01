"""Theme vocabulary for extracted evidence.

BUILDING_THEMES describe what a company is building (the findings the report
leads with). SIGNAL_THEMES are company-lifecycle facts: they feed the stage
classifier and are never shown as findings on their own.
"""
from __future__ import annotations

BUILDING_THEMES = (
    "product-launch", "active-build", "platform-infra", "ai-ml",
    "public-api-sdk", "open-source", "new-market",
)
SIGNAL_THEMES = (
    "funding-round", "new-office", "acquisition", "headcount-statement",
    "new-product-line",
)
# Recent evidence of these promotes a GROWTH company to EXPANSION: a company
# that is entering new places or lines is scaling regardless of its headcount.
EXPANSION_SIGNALS = frozenset(
    {"new-office", "new-market", "new-product-line", "acquisition"}
)
ALL_THEMES = BUILDING_THEMES + SIGNAL_THEMES

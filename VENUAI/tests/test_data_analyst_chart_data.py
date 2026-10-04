"""
Real tests for DataAnalysisResult.to_chart_data() — a pure presentation-
layer transform of already-computed values (see
VENUAI/agents/data_analyst_agent.py). Does not touch the pandas
computation paths, which are covered by test_data_analyst_agent.py.
"""

import pandas as pd

from VENUAI.agents.data_analyst_agent import DataAnalysisResult, DataAnalystAgent


def test_mean_produces_bar_chart_matching_computed_values():
    df = pd.DataFrame({"revenue": [100.0, 200.0, 300.0]})
    result = DataAnalystAgent().analyze(df, "What is the average revenue?", groq_client=None)
    chart = result.to_chart_data()
    assert chart["type"] == "bar"
    assert chart["labels"] == ["revenue"]
    assert chart["values"] == [result.computed_values["revenue"]]


def test_growth_rate_strips_suffix_from_label():
    df = pd.DataFrame({"revenue": [100.0, 150.0]})
    result = DataAnalystAgent().analyze(df, "What is the growth in revenue?", groq_client=None)
    chart = result.to_chart_data()
    assert chart["labels"] == ["revenue"]
    assert chart["unit"] == "%"


def test_duplicate_detection_produces_two_bar_chart():
    df = pd.DataFrame({"a": [1, 1, 2]})
    result = DataAnalystAgent().analyze(df, "Are there duplicate rows?", groq_client=None)
    chart = result.to_chart_data()
    assert chart["labels"] == ["Duplicate rows", "Unique rows"]
    assert sum(chart["values"]) == len(df)


def test_groupby_produces_bar_chart_per_group():
    df = pd.DataFrame({
        "category": ["a", "b", "a", "b"],
        "revenue": [100.0, 200.0, 150.0, 250.0],
    })
    result = DataAnalystAgent().analyze(df, "Group revenue by category", groq_client=None)
    chart = result.to_chart_data()
    assert chart["type"] == "bar"
    assert set(chart["labels"]) == {"a", "b"}


def test_correlation_has_no_single_chart_representation():
    df = pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]})
    result = DataAnalystAgent().analyze(df, "What is the correlation between x and y?", groq_client=None)
    assert result.to_chart_data() is None


def test_descriptive_statistics_has_no_single_chart_representation():
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = DataAnalystAgent().analyze(df, "Tell me about this dataset.", groq_client=None)
    assert result.to_chart_data() is None


def test_empty_computed_values_returns_none():
    result = DataAnalysisResult(operation="mean", computed_values={})
    assert result.to_chart_data() is None


def test_unmatched_column_falls_back_to_overview_not_empty():
    """
    Asking about a column that doesn't exist falls back to descriptive
    statistics over the real numeric columns that DO exist, rather than
    returning nothing — this is DataAnalystAgent.analyze()'s existing,
    correct fallback behavior (untouched by this chart-data addition).
    """
    df = pd.DataFrame({"revenue": [1, 2, 3]})
    result = DataAnalystAgent().analyze(df, "What is the average zzz_nonexistent?", groq_client=None)
    assert result.operation == "descriptive_statistics"
    assert result.to_chart_data() is None  # describe() output has no single-chart representation

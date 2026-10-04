"""
Real tests for VENUAI/agents/data_analyst_agent.py — actual pandas
computations, verified against hand-computed expected values. No LLM call
needed for most of these (groq_client=None path returns raw computed
values), proving the agent's arithmetic is correct independent of any
model's interpretation of it.
"""

import pandas as pd

from VENUAI.agents.data_analyst_agent import DataAnalystAgent


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "category": ["missiles", "radar", "missiles", "radar"],
        "revenue": [100.0, 200.0, 150.0, 250.0],
        "year": [2023, 2023, 2024, 2024],
    })


def test_mean_computation_is_correct():
    df = _sample_df()
    result = DataAnalystAgent().analyze(df, "What is the average revenue?", groq_client=None)
    assert result.operation == "mean"
    assert result.computed_values["revenue"] == df["revenue"].mean()


def test_sum_computation_is_correct():
    df = _sample_df()
    result = DataAnalystAgent().analyze(df, "What is the total revenue?", groq_client=None)
    assert result.operation == "sum"
    assert result.computed_values["revenue"] == df["revenue"].sum()


def test_missing_value_analysis():
    df = pd.DataFrame({"a": [1, None, 3], "b": [1, 2, None]})
    result = DataAnalystAgent().analyze(df, "Are there any missing values?", groq_client=None)
    assert result.operation == "missing_value_analysis"
    assert result.computed_values["a"] == 1
    assert result.computed_values["b"] == 1


def test_duplicate_detection():
    df = pd.DataFrame({"a": [1, 1, 2]})
    result = DataAnalystAgent().analyze(df, "Are there duplicate rows?", groq_client=None)
    assert result.operation == "duplicate_detection"
    assert result.computed_values["duplicate_row_count"] == 1


def test_correlation_computation():
    df = pd.DataFrame({"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]})
    result = DataAnalystAgent().analyze(df, "What is the correlation between x and y?", groq_client=None)
    assert result.operation == "correlation"
    assert result.computed_values["x"]["y"] == 1.0  # perfectly correlated by construction


def test_growth_rate_computation_matches_manual_calculation():
    df = pd.DataFrame({"revenue": [100.0, 150.0]})
    result = DataAnalystAgent().analyze(df, "What is the growth in revenue?", groq_client=None)
    assert result.operation == "growth_rate"
    expected_pct = (150.0 - 100.0) / 100.0 * 100
    assert result.computed_values["revenue_pct_change"] == round(expected_pct, 2)


def test_groupby_aggregation():
    df = _sample_df()
    result = DataAnalystAgent().analyze(df, "Group revenue by category", groq_client=None)
    assert result.operation == "groupby_aggregation"
    assert result.computed_values["revenue"]["missiles"] == df[df.category == "missiles"]["revenue"].mean()


def test_falls_back_to_descriptive_statistics_for_generic_question():
    df = _sample_df()
    result = DataAnalystAgent().analyze(df, "Tell me about this dataset.", groq_client=None)
    assert result.operation == "descriptive_statistics"
    assert "revenue" in result.computed_values


def test_no_groq_client_returns_raw_values_not_fabricated_prose():
    df = _sample_df()
    result = DataAnalystAgent().analyze(df, "What is the average revenue?", groq_client=None)
    assert "Computed mean:" in result.interpretation
    assert str(df["revenue"].mean()) in result.interpretation

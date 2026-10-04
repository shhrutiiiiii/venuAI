"""
DataAnalystAgent — spec section 21. Operates on an in-memory pandas
DataFrame (CSV/XLSX/JSON already parsed into one) and a natural-language
question.

Per spec section 21's explicit instruction ("use deterministic tools for
calculations... the LLM should interpret results, not perform important
arithmetic itself"): every number in the output comes from a real pandas/
NumPy computation. The LLM call (via AIGateway) only phrases the
interpretation of numbers that were already computed — it is never asked
to compute or guess a number itself.

Scope note: this is the analysis engine only. There is no `/upload/dataset`
HTTP endpoint or frontend UI wiring it to user-uploaded CSV/XLSX files yet —
building that is a reasonable fast-follow (new route + frontend page) but a
materially different, larger piece of work than "add an analysis agent,"
so it's tracked in Documents/future-additions.md rather than silently
assumed to exist.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("drdo-rag")


@dataclass(frozen=True)
class DataAnalysisResult:
    operation: str
    computed_values: Dict[str, Any] = field(default_factory=dict)
    interpretation: str = ""
    referenced_columns: List[str] = field(default_factory=list)

    def to_chart_data(self) -> Optional[Dict[str, Any]]:
        """
        Shapes `computed_values` (already-computed, real numbers — see
        module docstring) into a chart-renderable structure for the
        frontend. Purely a presentation-layer transform of numbers that
        were already computed in `DataAnalystAgent.analyze()` — does not
        recompute, approximate, or alter any value. Returns None for
        operations that don't have a sensible single-chart representation
        (e.g. full descriptive statistics, which is multi-dimensional and
        rendered as a table on the frontend instead).
        """
        if not self.computed_values:
            return None

        if self.operation in ("mean", "sum", "missing_value_analysis"):
            # Flat {column: number} dict -> one bar per column.
            items = [(k, v) for k, v in self.computed_values.items() if isinstance(v, (int, float))]
            if not items:
                return None
            return {
                "type": "bar",
                "labels": [k for k, _ in items],
                "values": [v for _, v in items],
            }

        if self.operation == "growth_rate":
            items = list(self.computed_values.items())
            return {
                "type": "bar",
                "labels": [k.replace("_pct_change", "") for k, _ in items],
                "values": [v for _, v in items],
                "unit": "%",
            }

        if self.operation == "duplicate_detection":
            return {
                "type": "bar",
                "labels": ["Duplicate rows", "Unique rows"],
                "values": [
                    self.computed_values.get("duplicate_row_count", 0),
                    self.computed_values.get("total_rows", 0) - self.computed_values.get("duplicate_row_count", 0),
                ],
            }

        if self.operation == "groupby_aggregation":
            # {metric_column: {group: value}} -> grouped bar using the first metric column.
            first_metric = next(iter(self.computed_values.values()), None)
            if not isinstance(first_metric, dict) or not first_metric:
                return None
            return {
                "type": "bar",
                "labels": list(first_metric.keys()),
                "values": list(first_metric.values()),
            }

        if self.operation == "correlation":
            # Correlation matrices are rendered as a table on the frontend,
            # not a bar/line chart — no sensible single-axis representation.
            return None

        return None


_INTERPRETATION_PROMPT = """You are a data analysis assistant. The user asked a question about \
a dataset, and the following statistics were already computed deterministically by code \
(you must not recompute or contradict these numbers — only explain them in plain language).

Question: {question}

Computed statistics ({operation}):
{computed_json}

Write a concise, plain-language answer to the question using ONLY the numbers above. \
Cite specific values from the statistics."""


def _matching_columns(question: str, columns: List[str]) -> List[str]:
    q_lower = question.lower()
    return [c for c in columns if c.lower() in q_lower]


class DataAnalystAgent:
    def analyze(self, df: pd.DataFrame, question: str, groq_client=None) -> DataAnalysisResult:
        q_lower = question.lower()
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        mentioned_cols = _matching_columns(question, df.columns.tolist())

        if any(k in q_lower for k in ("correlat",)):
            operation = "correlation"
            corr = df[numeric_cols].corr(numeric_only=True) if len(numeric_cols) >= 2 else pd.DataFrame()
            computed = {} if corr.empty else corr.round(3).to_dict()

        elif any(k in q_lower for k in ("missing", "null", "nan")):
            operation = "missing_value_analysis"
            computed = df.isnull().sum().to_dict()

        elif any(k in q_lower for k in ("duplicate",)):
            operation = "duplicate_detection"
            computed = {"duplicate_row_count": int(df.duplicated().sum()), "total_rows": len(df)}

        elif any(k in q_lower for k in ("average", "mean")) and mentioned_cols:
            operation = "mean"
            computed = {
                col: round(float(df[col].mean()), 3)
                for col in mentioned_cols if col in numeric_cols
            }

        elif any(k in q_lower for k in ("sum", "total")) and mentioned_cols:
            operation = "sum"
            computed = {
                col: round(float(df[col].sum()), 3)
                for col in mentioned_cols if col in numeric_cols
            }

        elif any(k in q_lower for k in ("fastest", "growth", "trend", "grew")) and mentioned_cols:
            operation = "growth_rate"
            computed = {}
            for col in mentioned_cols:
                if col in numeric_cols and len(df) >= 2:
                    series = df[col].dropna()
                    if len(series) >= 2 and series.iloc[0] != 0:
                        pct_change = round(float((series.iloc[-1] - series.iloc[0]) / abs(series.iloc[0]) * 100), 2)
                        computed[f"{col}_pct_change"] = pct_change

        elif any(k in q_lower for k in ("group",)) and mentioned_cols:
            operation = "groupby_aggregation"
            categorical = [c for c in mentioned_cols if c not in numeric_cols]
            numeric_targets = [c for c in mentioned_cols if c in numeric_cols] or numeric_cols
            computed = {}
            if categorical and numeric_targets:
                grouped = df.groupby(categorical[0])[numeric_targets].mean().round(3)
                computed = grouped.to_dict()

        else:
            operation = "descriptive_statistics"
            computed = {} if not numeric_cols else df[numeric_cols].describe().round(3).to_dict()

        interpretation = self._interpret(question, operation, computed, groq_client)

        return DataAnalysisResult(
            operation=operation,
            computed_values=computed,
            interpretation=interpretation,
            referenced_columns=mentioned_cols or numeric_cols,
        )

    def _interpret(self, question: str, operation: str, computed: dict, groq_client) -> str:
        if not computed:
            return "No matching columns or sufficient data were found to answer this question."
        if groq_client is None:
            # No LLM available — return the raw computed values directly
            # rather than fabricating a prose interpretation.
            return f"Computed {operation}: {computed}"

        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType
            import json

            gateway = get_gateway(groq_client)
            result = gateway.complete(
                task_type=TaskType.GENERATION,
                messages=[{"role": "user", "content": _INTERPRETATION_PROMPT.format(
                    question=question, operation=operation,
                    computed_json=json.dumps(computed, indent=2, default=str),
                )}],
                max_tokens=400,
                temperature=0.1,
            )
            return result.content
        except Exception as e:
            logger.warning(f"[DataAnalystAgent] LLM interpretation failed, returning raw values: {e}")
            return f"Computed {operation}: {computed}"

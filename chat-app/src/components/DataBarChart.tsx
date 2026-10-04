"use client";

import React from "react";

export interface ChartData {
  type: "bar";
  labels: string[];
  values: number[];
  unit?: string;
}

interface DataBarChartProps {
  chart: ChartData;
}

/**
 * Lightweight inline-SVG bar chart — no charting library dependency.
 * Renders VENUAI.agents.data_analyst_agent.DataAnalysisResult.to_chart_data()
 * output exactly as computed server-side; this component does no math of
 * its own beyond scaling bar heights to the available drawing area.
 */
export const DataBarChart: React.FC<DataBarChartProps> = ({ chart }) => {
  const { labels, values, unit } = chart;
  if (!labels.length) return null;

  const width = 480;
  const height = 220;
  const paddingBottom = 50;
  const paddingTop = 20;
  const barGap = 12;
  const barWidth = Math.max(24, (width - barGap * (labels.length + 1)) / labels.length);

  const maxAbs = Math.max(...values.map((v) => Math.abs(v)), 1);
  const drawableHeight = height - paddingTop - paddingBottom;

  return (
    <div className="mt-3 p-3 rounded-xl bg-slate-900/60 border border-slate-700/60 overflow-x-auto">
      <svg width={width} height={height} role="img" aria-label="Data analysis bar chart">
        {values.map((value, i) => {
          const barHeight = (Math.abs(value) / maxAbs) * drawableHeight;
          const x = barGap + i * (barWidth + barGap);
          const y = paddingTop + (drawableHeight - barHeight);
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={Math.max(barHeight, 1)}
                rx={4}
                className="fill-purple-500/70"
              />
              <text
                x={x + barWidth / 2}
                y={y - 6}
                textAnchor="middle"
                className="fill-slate-200 text-[10px] font-mono"
              >
                {value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                {unit || ""}
              </text>
              <text
                x={x + barWidth / 2}
                y={height - paddingBottom + 18}
                textAnchor="middle"
                className="fill-slate-400 text-[10px]"
              >
                {labels[i].length > 12 ? `${labels[i].slice(0, 11)}…` : labels[i]}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};

export default DataBarChart;

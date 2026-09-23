"use client";

import {
  CartesianGrid,
  Label,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { titleCase } from "@/lib/format";
import { CHART_COLORS, THEME } from "@/lib/utils";

interface Point {
  day: string;
  metric: string;
  count: number;
}

const AXIS = { fontSize: 10, fontFamily: "var(--font-mono)", fill: THEME.faint } as const;

/**
 * Usage over time.
 *
 * Change over time is a line chart's job, and there is exactly one y-axis: two measures of
 * different scale would get two charts rather than a second axis. Series are identified
 * three ways — colour, a legend, and a label at the end of each line — because the red and
 * green in the palette sit close enough under deuteranopia that colour alone would not be
 * enough to tell them apart.
 */
export function UsageChart({ series }: { series: Point[] }) {
  if (!series.length) {
    return <p className="label py-14 text-center">No usage recorded in this period</p>;
  }

  // Pivot [{day, metric, count}] into one row per day with a column per metric.
  const metrics = Array.from(new Set(series.map((point) => point.metric)));
  const byDay = new Map<string, Record<string, number | string>>();
  for (const point of series) {
    const row = byDay.get(point.day) ?? { day: point.day };
    row[point.metric] = point.count;
    byDay.set(point.day, row);
  }
  const data = Array.from(byDay.values()).sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
  const single = data.length === 1;

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 56, left: -18, bottom: 0 }}>
          {/* Recessive: horizontal only, hairline, no vertical clutter. */}
          <CartesianGrid stroke={THEME.border} strokeDasharray="3 5" vertical={false} />
          <XAxis
            dataKey="day"
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: THEME.border }}
            minTickGap={24}
          />
          <YAxis
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={40}
          />
          <Tooltip
            cursor={{ stroke: THEME.borderStrong, strokeWidth: 1 }}
            contentStyle={{
              background: THEME.surface,
              border: `1px solid ${THEME.border}`,
              borderRadius: 10,
              boxShadow: "0 16px 40px -20px rgba(26, 22, 19, 0.35)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              padding: "8px 12px",
              color: THEME.foreground,
            }}
            labelStyle={{
              fontSize: 9,
              letterSpacing: "0.11em",
              textTransform: "uppercase",
              color: THEME.faint,
              marginBottom: 4,
            }}
            formatter={(value: number, name: string) => [value, titleCase(name)]}
          />
          <Legend
            formatter={(value) => (
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.11em",
                  color: THEME.mutedForeground,
                }}
              >
                {titleCase(String(value)).toUpperCase()}
              </span>
            )}
            iconType="plainline"
            iconSize={14}
            wrapperStyle={{ paddingTop: 14 }}
          />
          {metrics.map((metric, index) => {
            const colour = CHART_COLORS[index % CHART_COLORS.length];
            return (
              <Line
                key={metric}
                type="monotone"
                dataKey={metric}
                stroke={colour}
                strokeWidth={2}
                // A single day has no line to draw, so the point has to be visible.
                dot={single ? { r: 3.5, strokeWidth: 0, fill: colour } : false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: THEME.surface }}
                isAnimationActive={false}
              >
                {/* The direct label that makes identity survive colour-blindness. */}
                <Label
                  position="right"
                  value={titleCase(metric).split(" ")[0]}
                  fill={colour}
                  fontSize={9}
                  fontFamily="var(--font-mono)"
                  letterSpacing="0.08em"
                />
              </Line>
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { colorFor, pivot } from "../lib/palette";
import { fmtDate, label } from "../lib/api";
import { ChartTip, Legend } from "./ui";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false, axisLine: false } as const;

/** Stacked daily bars, one segment per key (top N + Other). */
export function StackedDaily({ rows, fmt, top = 6, height = 260 }: { rows: { date: string; key: string; value: number }[]; fmt: (v: number) => string; top?: number; height?: number }) {
  const { data, keys } = pivot(rows, top);
  return (
    <>
      <Legend keys={keys} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="date" tickFormatter={fmtDate} {...axis} minTickGap={24} />
          <YAxis tickFormatter={fmt} {...axis} width={56} />
          <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTip fmt={fmt} titleFmt={fmtDate} />} />
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} name={k} stackId="s" fill={colorFor(k)} stroke="var(--surface-1)" strokeWidth={1}
              radius={i === keys.length - 1 ? [3, 3, 0, 0] : 0} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

/** Multi-line daily trend for a few measures of the SAME unit (one y-axis). */
export function Lines({ data, series, fmt, height = 240 }: { data: Record<string, number | string>[]; series: { key: string; name: string }[]; fmt: (v: number) => string; height?: number }) {
  return (
    <>
      <Legend keys={series.map((s) => s.key)} />
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="date" tickFormatter={fmtDate} {...axis} minTickGap={24} />
          <YAxis tickFormatter={fmt} {...axis} width={56} />
          <Tooltip cursor={{ stroke: "var(--text-muted)", strokeDasharray: "3 3" }} content={<ChartTip fmt={fmt} titleFmt={fmtDate} />} />
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.key} stroke={colorFor(s.key)} strokeWidth={2} dot={false}
              activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

/** Horizontal ranked bars - magnitude comparison across categories (single series, one hue). */
export function RankedBars({ rows, fmt, height }: { rows: { label: string; value: number; key?: string }[]; fmt: (v: number) => string; height?: number }) {
  const h = height ?? Math.max(120, rows.length * 30 + 20);
  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 56, left: 0, bottom: 0 }} barCategoryGap={6}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="label" {...axis} width={150} tickFormatter={(v: string) => (v.length > 22 ? v.slice(0, 21) + "…" : label(v))} />
        <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTip fmt={fmt} />} />
        <Bar dataKey="value" name="value" radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false}
          label={{ position: "right", formatter: fmt, fill: "var(--text-secondary)", fontSize: 11 }}>
          {rows.map((r) => (
            <Cell key={r.label} fill="var(--series-1)" />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

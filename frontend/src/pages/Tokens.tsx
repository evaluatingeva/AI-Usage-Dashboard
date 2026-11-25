import { useApi } from "../lib/useApi";
import { fmtNum, fmtUsd, label } from "../lib/api";
import { Card, Loading } from "../components/ui";
import { Lines, StackedDaily } from "../components/charts";

type Row = { key: string; label: string; input_tokens: number; output_tokens: number; requests: number; cost_usd: number; users: number };
type Pt = { date: string; key: string; value: number };

export default function Tokens({ params }: { params: Record<string, string> }) {
  const byModel = useApi<Pt[]>("/analytics/timeseries", { ...params, metric: "tokens", group_by: "model" });
  const inp = useApi<Pt[]>("/analytics/timeseries", { ...params, metric: "input_tokens" });
  const out = useApi<Pt[]>("/analytics/timeseries", { ...params, metric: "output_tokens" });
  const models = useApi<Row[]>("/analytics/breakdown", { ...params, dimension: "model", metric: "tokens", limit: 30 });

  const io = (() => {
    if (!inp.data || !out.data) return null;
    const m = new Map<string, Record<string, number | string>>();
    inp.data.forEach((r) => m.set(r.date, { date: r.date, input_tokens: r.value, output_tokens: 0 }));
    out.data.forEach((r) => {
      const row = m.get(r.date) ?? { date: r.date, input_tokens: 0 };
      row.output_tokens = r.value;
      m.set(r.date, row);
    });
    return [...m.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  })();

  return (
    <>
      <div className="grid two">
        <Card title="Tokens per day by model" sub="Input + output tokens">
          {byModel.data ? <StackedDaily rows={byModel.data} fmt={fmtNum} /> : <Loading error={byModel.error} />}
        </Card>
        <Card title="Input vs output tokens" sub="Output tokens typically cost 4-5× input">
          {io ? (
            <Lines data={io} series={[{ key: "input_tokens", name: "Input" }, { key: "output_tokens", name: "Output" }]} fmt={fmtNum} />
          ) : (
            <Loading error={inp.error ?? out.error} />
          )}
        </Card>
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        <Card title="Usage by model" sub="Tokens, requests and effective unit cost">
          {models.data ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Model</th><th className="num">Input tokens</th><th className="num">Output tokens</th>
                    <th className="num">Requests</th><th className="num">Users</th><th className="num">Spend</th><th className="num">$ / 1M tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {models.data.map((r) => {
                    const t = r.input_tokens + r.output_tokens;
                    return (
                      <tr key={r.key}>
                        <td>{label(r.label)}</td>
                        <td className="num">{fmtNum(r.input_tokens)}</td>
                        <td className="num">{fmtNum(r.output_tokens)}</td>
                        <td className="num">{fmtNum(r.requests)}</td>
                        <td className="num">{r.users}</td>
                        <td className="num">{fmtUsd(r.cost_usd, 2)}</td>
                        <td className="num">{t ? fmtUsd((r.cost_usd / t) * 1e6, 2) : "–"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading error={models.error} />
          )}
        </Card>
      </div>
    </>
  );
}

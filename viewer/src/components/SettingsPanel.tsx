import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

export default function SettingsPanel() {
  const open = useStore((s) => s.settingsOpen);
  const setOpen = useStore((s) => s.openSettings);
  const setToast = useStore((s) => s.setToast);
  const [data, setData] = useState<any | null>(null);
  const [routes, setRoutes] = useState<any>({});
  const [usage, setUsage] = useState<any | null>(null);
  const [testResult, setTestResult] = useState<string>("");

  useEffect(() => {
    if (!open) return;
    api.models().then((d) => {
      setData(d);
      setRoutes(d.routes ?? {});
    });
    api.usage().then(setUsage);
  }, [open]);

  if (!open) return null;

  const saveProvider = async (provider: any) => {
    try {
      await api.upsertProvider(provider.id, provider);
      setToast("供应商已保存");
      setData(await api.models());
    } catch (e: any) {
      setToast("保存失败：" + e.message);
    }
  };

  const test = async (providerId: string, modelId: string) => {
    setTestResult("测试中…");
    try {
      const r = await api.testModel({ provider_id: providerId, model: modelId });
      setTestResult(r.ok ? `连接成功：${JSON.stringify(r.usage ?? {})}` : `连接失败：${r.error}`);
    } catch (e: any) {
      setTestResult("连接失败：" + e.message);
    }
  };

  const saveRoutes = async () => {
    try {
      await api.updateRoutes(routes);
      setToast("任务路由已保存（缓存将按新模型失效）");
    } catch (e: any) {
      setToast("路由保存失败：" + e.message);
    }
  };

  return (
    <div className="modal-mask" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>设置</h2>

        <h3>任务路由</h3>
        {["default", "outline", "fill", "agent", "link"].map((slot) => (
          <label key={slot} className="route-row">
            <span>{slot}</span>
            <select value={routes[slot] ?? ""} onChange={(e) => setRoutes({ ...routes, [slot]: e.target.value || null })}>
              <option value="">（回退 default）</option>
              {(data?.providers ?? []).flatMap((p: any) =>
                (p.models ?? []).map((m: any) => (
                  <option key={p.id + "/" + m.id} value={p.id + "/" + m.id}>
                    {p.id}/{m.id}
                  </option>
                ))
              )}
            </select>
          </label>
        ))}
        <button onClick={() => void saveRoutes()}>保存路由</button>

        <h3>供应商</h3>
        {(data?.providers ?? []).map((p: any) => (
          <div key={p.id} className="provider-card">
            <div>
              <b>{p.label}</b> <code>{p.base_url}</code>
            </div>
            <div className="muted">
              密钥：{p.api_key ?? "（环境变量 " + (p.api_key_env ?? "未配置") + "）"} ·
              能力：json_schema={String(p.capabilities?.json_schema)} tools={String(p.capabilities?.tools)} vision=
              {String(p.capabilities?.vision)}
            </div>
            {(p.models ?? []).map((m: any) => (
              <div key={m.id} className="model-row">
                <span>{m.id}</span>
                <span className="muted">
                  ctx {m.context_window} · max_out {m.max_output_tokens} ·{" "}
                  {m.pricing ? `$${m.pricing.input_per_million}/M 入 · $${m.pricing.output_per_million}/M 出` : "成本未知"}
                </span>
                <button onClick={() => void test(p.id, m.id)}>测试连接</button>
              </div>
            ))}
            <button
              onClick={() => {
                const base = prompt("base_url", p.base_url);
                if (base === null) return;
                const key = prompt("api_key（留空表示使用环境变量）", p.api_key ?? "");
                void saveProvider({ ...p, base_url: base, api_key: key || null });
              }}
            >
              编辑
            </button>
          </div>
        ))}
        {testResult && <div className="muted">{testResult}</div>}

        <h3>用量</h3>
        <table className="usage-table">
          <thead>
            <tr>
              <th>供应商</th>
              <th>模型</th>
              <th>任务</th>
              <th>调用</th>
              <th>tokens 入/出</th>
              <th>成本估算</th>
            </tr>
          </thead>
          <tbody>
            {(usage?.rows ?? []).map((r: any, i: number) => (
              <tr key={i}>
                <td>{r.provider_id}</td>
                <td>{r.model}</td>
                <td>{r.task}</td>
                <td>{r.calls}</td>
                <td>
                  {r.tokens_in}/{r.tokens_out}
                </td>
                <td>{r.cost_est == null ? "未知" : "$" + r.cost_est.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="muted">{usage?.note}</div>
        <button onClick={() => setOpen(false)}>关闭</button>
      </div>
    </div>
  );
}

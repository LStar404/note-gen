import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

const EMPTY_PROVIDER = {
  id: "",
  label: "",
  protocol: "openai_compat",
  base_url: "",
  api_key: null as string | null,
  api_key_env: null as string | null,
  capabilities: { json_schema: false, tools: true, vision: false },
  models: [] as any[],
};

const EMPTY_MODEL = {
  id: "",
  context_window: 8192,
  max_output_tokens: 2048,
  capabilities: { json_schema: true, tools: true, vision: false },
  pricing: null as any,
};

/** 设置面板（7.2）：模型/供应商管理 + 任务路由 + 通用设置（解析器/OCR/语言/编码）+ 用量。 */
export default function SettingsPanel() {
  const open = useStore((s) => s.settingsOpen);
  const setOpen = useStore((s) => s.openSettings);
  const setToast = useStore((s) => s.setToast);
  const [data, setData] = useState<any | null>(null);
  const [routes, setRoutes] = useState<any>({});
  const [settings, setSettings] = useState<any | null>(null);
  const [usage, setUsage] = useState<any | null>(null);
  const [testResult, setTestResult] = useState<string>("");
  const [addingProvider, setAddingProvider] = useState<any | null>(null);

  const reload = async () => {
    const d = await api.models();
    setData(d);
    setRoutes(d.routes ?? {});
    setSettings(await api.settings());
  };

  useEffect(() => {
    if (!open) return;
    void reload();
    api.usage().then(setUsage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const toast = (msg: string) => setToast(msg);

  const saveProvider = async (provider: any) => {
    if (!provider.id) return toast("供应商 id 必填");
    try {
      await api.upsertProvider(provider.id, provider);
      toast("供应商已保存");
      await reload();
    } catch (e: any) {
      toast("保存失败：" + e.message);
    }
  };

  const patchProvider = (providerId: string, patch: any) => {
    setData((d: any) => ({
      ...d,
      providers: d.providers.map((p: any) => (p.id === providerId ? { ...p, ...patch } : p)),
    }));
  };

  const patchModel = (providerId: string, index: number, patch: any) => {
    setData((d: any) => ({
      ...d,
      providers: d.providers.map((p: any) =>
        p.id === providerId
          ? { ...p, models: p.models.map((m: any, i: number) => (i === index ? { ...m, ...patch } : m)) }
          : p
      ),
    }));
  };

  const test = async (providerId: string, modelId: string) => {
    setTestResult("测试中…");
    try {
      const r = await api.testModel({ provider_id: providerId, model: modelId });
      setTestResult(
        r.ok
          ? `连接成功（${providerId}/${modelId}）：tokens ${JSON.stringify(r.usage ?? {})}`
          : `连接失败：${r.error}`
      );
    } catch (e: any) {
      setTestResult("连接失败：" + e.message);
    }
  };

  const saveRoutes = async () => {
    try {
      await api.updateRoutes(routes);
      toast("任务路由已保存（缓存按新模型自动失效）");
    } catch (e: any) {
      toast("路由保存失败：" + e.message);
    }
  };

  const saveAppSettings = async () => {
    try {
      await api.saveSettings({
        parser: settings.parser,
        generate: { output_language: settings.generate?.output_language },
        export: settings.export,
      });
      toast("设置已保存");
      await reload();
    } catch (e: any) {
      toast("保存失败：" + e.message);
    }
  };

  const allModelNames = (data?.providers ?? []).flatMap((p: any) =>
    (p.models ?? []).map((m: any) => ({ value: p.id + "/" + m.id, label: p.id + "/" + m.id, tools: m.capabilities?.tools }))
  );

  return (
    <div className="modal-mask" onClick={() => setOpen(false)}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h2>设置</h2>

        {/* ---------------- 通用设置 ---------------- */}
        <h3>通用设置</h3>
        {settings && (
          <div className="form-grid">
            <label>
              <span>解析器</span>
              <select
                value={settings.parser?.default ?? "auto"}
                onChange={(e) => setSettings({ ...settings, parser: { ...settings.parser, default: e.target.value } })}
              >
                {(settings.options?.parsers ?? []).map((p: string) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </label>
            <label>
              <span>OCR 模式（扫描页）</span>
              <select
                value={settings.parser?.ocr_mode ?? "auto"}
                onChange={(e) => setSettings({ ...settings, parser: { ...settings.parser, ocr_mode: e.target.value } })}
              >
                {(settings.options?.ocr_modes ?? []).map((p: string) => (
                  <option key={p} value={p}>
                    {p === "auto" ? "auto（仅无文字层页面）" : p === "force" ? "force（全部页面）" : "off（关闭）"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>输出语言</span>
              <select
                value={settings.generate?.output_language ?? "zh"}
                onChange={(e) =>
                  setSettings({ ...settings, generate: { ...settings.generate, output_language: e.target.value } })
                }
              >
                {(settings.options?.languages ?? []).map((l: string) => (
                  <option key={l} value={l}>
                    {l === "zh" ? "简体中文" : l === "en" ? "English" : "日本語"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>导出编码</span>
              <select
                value={settings.export?.encoding ?? "utf-8"}
                onChange={(e) => setSettings({ ...settings, export: { ...settings.export, encoding: e.target.value } })}
              >
                {(settings.options?.encodings ?? []).map((enc: string) => (
                  <option key={enc} value={enc}>{enc}</option>
                ))}
              </select>
            </label>
            <label>
              <span>并发调用</span>
              <input
                type="number"
                min={1}
                max={16}
                value={settings.generate?.concurrency ?? 2}
                onChange={(e) =>
                  setSettings({ ...settings, generate: { ...settings.generate, concurrency: Number(e.target.value) } })
                }
              />
            </label>
            <label>
              <span>单章成本上限（USD，0=不限）</span>
              <input
                type="number"
                min={0}
                step={0.1}
                value={settings.generate?.max_cost_per_chapter ?? 0}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    generate: { ...settings.generate, max_cost_per_chapter: Number(e.target.value) },
                  })
                }
              />
            </label>
          </div>
        )}
        <button onClick={() => void saveAppSettings()}>保存通用设置</button>

        {/* ---------------- 任务路由 ---------------- */}
        <h3>任务路由</h3>
        {["default", "outline", "fill", "agent", "link"].map((slot) => (
          <label key={slot} className="route-row">
            <span>{slot}</span>
            <select value={routes[slot] ?? ""} onChange={(e) => setRoutes({ ...routes, [slot]: e.target.value || null })}>
              <option value="">（回退 default）</option>
              {allModelNames.map((m: any) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                  {slot === "agent" && m.tools === false ? "（无 tools 能力）" : ""}
                </option>
              ))}
            </select>
            {slot === "agent" && <small className="muted">agent 编辑必须选择具备 tools 能力的模型</small>}
          </label>
        ))}
        <button onClick={() => void saveRoutes()}>保存路由</button>

        {/* ---------------- 供应商与模型 ---------------- */}
        <h3>供应商与模型</h3>
        {(data?.providers ?? []).map((p: any) => (
          <div key={p.id} className="provider-card">
            <div className="form-grid">
              <label>
                <span>名称</span>
                <input value={p.label ?? ""} onChange={(e) => patchProvider(p.id, { label: e.target.value })} />
              </label>
              <label>
                <span>Base URL</span>
                <input value={p.base_url ?? ""} onChange={(e) => patchProvider(p.id, { base_url: e.target.value })} />
              </label>
              <label>
                <span>API Key（留空用环境变量）</span>
                <input
                  type="password"
                  placeholder={p.api_key ?? (p.api_key_env ? "环境变量 " + p.api_key_env : "未配置")}
                  onChange={(e) => patchProvider(p.id, { api_key: e.target.value || null })}
                />
              </label>
              <label>
                <span>环境变量名</span>
                <input
                  value={p.api_key_env ?? ""}
                  onChange={(e) => patchProvider(p.id, { api_key_env: e.target.value || null })}
                />
              </label>
            </div>
            <div className="cap-row">
              {(["json_schema", "tools", "vision"] as const).map((cap) => (
                <label key={cap} className="cap-toggle">
                  <input
                    type="checkbox"
                    checked={!!p.capabilities?.[cap]}
                    onChange={(e) =>
                      patchProvider(p.id, { capabilities: { ...p.capabilities, [cap]: e.target.checked } })
                    }
                  />
                  {cap}
                </label>
              ))}
              <span className="muted">供应商级默认能力（模型可覆盖）</span>
            </div>

            <table className="usage-table">
              <thead>
                <tr>
                  <th>模型</th>
                  <th>上下文</th>
                  <th>最大输出</th>
                  <th>能力</th>
                  <th>价格（USD/百万 token）</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(p.models ?? []).map((m: any, idx: number) => (
                  <tr key={m.id + idx}>
                    <td>
                      <input value={m.id} onChange={(e) => patchModel(p.id, idx, { id: e.target.value })} />
                    </td>
                    <td>
                      <input
                        type="number"
                        value={m.context_window ?? 8192}
                        onChange={(e) => patchModel(p.id, idx, { context_window: Number(e.target.value) })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        value={m.max_output_tokens ?? 2048}
                        onChange={(e) => patchModel(p.id, idx, { max_output_tokens: Number(e.target.value) })}
                      />
                    </td>
                    <td>
                      {(["json_schema", "tools", "vision"] as const).map((cap) => (
                        <label key={cap} className="cap-toggle small">
                          <input
                            type="checkbox"
                            checked={!!m.capabilities?.[cap]}
                            onChange={(e) =>
                              patchModel(p.id, idx, {
                                capabilities: { ...m.capabilities, [cap]: e.target.checked },
                              })
                            }
                          />
                          {cap}
                        </label>
                      ))}
                    </td>
                    <td>
                      <input
                        type="number"
                        placeholder="入"
                        value={m.pricing?.input_per_million ?? ""}
                        onChange={(e) =>
                          patchModel(p.id, idx, {
                            pricing: {
                              ...(m.pricing ?? { currency: "USD" }),
                              input_per_million: Number(e.target.value),
                              output_per_million: m.pricing?.output_per_million ?? 0,
                              currency: "USD",
                            },
                          })
                        }
                      />
                      <input
                        type="number"
                        placeholder="出"
                        value={m.pricing?.output_per_million ?? ""}
                        onChange={(e) =>
                          patchModel(p.id, idx, {
                            pricing: {
                              ...(m.pricing ?? { currency: "USD" }),
                              input_per_million: m.pricing?.input_per_million ?? 0,
                              output_per_million: Number(e.target.value),
                              currency: "USD",
                            },
                          })
                        }
                      />
                    </td>
                    <td>
                      <button onClick={() => void test(p.id, m.id)}>测试连接</button>
                      <button
                        onClick={() =>
                          patchProvider(p.id, { models: p.models.filter((_: any, i: number) => i !== idx) })
                        }
                      >
                        删除模型
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="detail-actions">
              <button onClick={() => patchProvider(p.id, { models: [...(p.models ?? []), { ...EMPTY_MODEL }] })}>
                新增模型
              </button>
              <button onClick={() => void saveProvider(p)}>保存供应商</button>
              <button
                onClick={() => {
                  if (!window.confirm("删除供应商 " + p.id + "？")) return;
                  void api
                    .deleteProvider(p.id)
                    .then(async () => {
                      toast("已删除 " + p.id);
                      await reload();
                    })
                    .catch((e: any) => toast("删除失败：" + e.message));
                }}
              >
                删除供应商
              </button>
            </div>
          </div>
        ))}

        {addingProvider ? (
          <div className="provider-card">
            <div className="form-grid">
              <label>
                <span>ID（如 ollama）</span>
                <input value={addingProvider.id} onChange={(e) => setAddingProvider({ ...addingProvider, id: e.target.value })} />
              </label>
              <label>
                <span>名称</span>
                <input value={addingProvider.label} onChange={(e) => setAddingProvider({ ...addingProvider, label: e.target.value })} />
              </label>
              <label>
                <span>Base URL</span>
                <input value={addingProvider.base_url} onChange={(e) => setAddingProvider({ ...addingProvider, base_url: e.target.value })} />
              </label>
              <label>
                <span>API Key</span>
                <input
                  type="password"
                  onChange={(e) => setAddingProvider({ ...addingProvider, api_key: e.target.value || null })}
                />
              </label>
            </div>
            <div className="detail-actions">
              <button onClick={() => void saveProvider(addingProvider).then(() => setAddingProvider(null))}>
                创建
              </button>
              <button onClick={() => setAddingProvider(null)}>取消</button>
            </div>
          </div>
        ) : (
          <button onClick={() => setAddingProvider({ ...EMPTY_PROVIDER })}>新增供应商</button>
        )}

        {testResult && <div className="muted">{testResult}</div>}

        {/* ---------------- 导出 ---------------- */}
        <h3>导出</h3>
        <div className="route-row">
          {["md", "tex", "both", "md-obsidian", "anki"].map((fmt) => (
            <button
              key={fmt}
              onClick={() => {
                const docId = useStore.getState().docId;
                if (!docId) return toast("请先选择教材");
                void api
                  .exportDoc({ doc_id: docId, format: fmt })
                  .then((r) => toast("导出任务已启动：" + r.task_id))
                  .catch((e: any) => toast("导出失败：" + e.message));
              }}
            >
              导出 {fmt}
            </button>
          ))}
        </div>

        {/* ---------------- 用量 ---------------- */}
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
                <td>{r.cost_est == null ? "未知" : "$" + Number(r.cost_est).toFixed(4)}</td>
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

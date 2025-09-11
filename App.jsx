import { useEffect, useState } from "react";
import mondaySdk from "monday-sdk-js";
const monday = mondaySdk();

export default function App() {
  // rows: [{ item_id: number, amount: string }]
  const [rows, setRows] = useState([]);
  const [allAmount, setAllAmount] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [ctxSeen, setCtxSeen] = useState(false);
  const [lastContext, setLastContext] = useState(null);
  const [namesById, setNamesById] = useState({});

  // Read Monday context for selected rows (fetch once + listen for changes)
  useEffect(() => {
    function extractIds(d) {
      if (!d) return [];
      const candidates = [];
      if (Array.isArray(d.selectedItemIds)) candidates.push(...d.selectedItemIds);
      if (Array.isArray(d.selectedRowIds)) candidates.push(...d.selectedRowIds);
      if (Array.isArray(d.selectedRows)) candidates.push(...d.selectedRows.map((r) => r.id));
      if (Array.isArray(d.selectedPulseIds)) candidates.push(...d.selectedPulseIds);
      if (Array.isArray(d.selectedPulsesIds)) candidates.push(...d.selectedPulsesIds);
      if (Array.isArray(d.itemIds)) candidates.push(...d.itemIds);
      if (Array.isArray(d.pulseIds)) candidates.push(...d.pulseIds);
      const uniq = [...new Set(candidates.map((x) => Number(x)))].filter((n) => Number.isFinite(n));
      return uniq;
    }

    async function fetchNames(ids) {
      if (!ids || !ids.length) return {};
      const url = "/api/item-names";
      console.log(`Fetching names from: ${window.location.origin}${url}`);
      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${import.meta.env.VITE_API_AUTH}`,
          },
          body: JSON.stringify({ item_ids: ids }),
        });
        if (!resp.ok) {
          console.error(`Fetch error: ${resp.status} ${resp.statusText}`);
          const errorText = await resp.text();
          console.error(`Error response body:`, errorText);
          throw new Error(`Failed to fetch names: ${resp.status}`);
        }
        const json = await resp.json();
        console.log("Received names data:", json);
        const map = {};
        (json.items || []).forEach((it) => {
          if (it && it.id !== undefined) {
            const name = it.name ? String(it.name).trim() : "";
            map[String(it.id)] = name || String(it.id);
          }
        });
        return map;
      } catch (e) {
        console.error("A network or other error occurred in fetchNames:", e);
        return {};
      }
    }

    async function applyIds(ids) {
      const unique = [...new Set(ids)].filter((n) => Number.isFinite(n));
      setRows(unique.map((id) => ({ item_id: id, amount: "" })));
      const map = await fetchNames(unique);
      setNamesById(map);
    }

    async function bootstrap() {
      try {
        const res = await monday.get("context");
        // Some tenants expose selection via explicit getters
        const selA = await monday.get("selectedItemIds").catch(() => null);
        const selB = await monday.get("itemIds").catch(() => null);
        setCtxSeen(true);
        setLastContext(res?.data || res || null);
        const ids = extractIds({ ...(res?.data||{}), ...(selA?.data||{}), ...(selB?.data||{}) });
        await applyIds(ids);
      } catch (e) {
        console.error("Failed to get initial context", e);
      }
    }

    function onContext(res) {
      setCtxSeen(true);
      setLastContext(res?.data || res || null);
      const merged = { ...(res?.data||{}), selectedItemIds: res?.data?.selectedItemIds || res?.selectedItemIds };
      const ids = extractIds(merged);
      applyIds(ids);
    }

    bootstrap();
    try {
      monday.listen("context", onContext);
      monday.listen("events", (ev) => {
        // Some accounts send selection changes through generic events
        if (ev?.type && String(ev.type).toLowerCase().includes("select")) {
          monday.get("context").then((r) => {
            setCtxSeen(true);
            setLastContext(r?.data || r || null);
            const ids = extractIds(r?.data);
            applyIds(ids);
          });
        }
      });
      monday.listen("selectedItemIds", (p) => {
        const ids = extractIds(p?.data || p);
        applyIds(ids);
      });
      monday.listen("selectedPulsesIds", (p) => {
        const ids = extractIds(p?.data || p);
        applyIds(ids);
      });
      monday.listen("itemIds", (p) => {
        const ids = extractIds(p?.data || p);
        applyIds(ids);
      });
    } catch (e) {
      console.error("Failed to attach context listener", e);
    }
  }, []);

  // helpers
  function setAmountFor(idx, val) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, amount: val } : r)));
  }

  function applyAll() {
    if (allAmount === "") return;
    setRows((prev) => prev.map((r) => ({ ...r, amount: allAmount })));
  }

  async function createDraws() {
    if (!rows.length) {
      setResult({ ok: false, error: "No items selected." });
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const items = rows.map((r) => ({
        item_id: r.item_id,
        requested_amount: r.amount === "" ? null : parseFloat(String(r.amount).replace(/[^0-9.-]+/g, "")),
      }));

      const resp = await fetch("/api/create-from-selection", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${import.meta.env.VITE_API_AUTH}`,
        },
        body: JSON.stringify({ items }),
      });

      const json = await resp.json();
      setResult(json);
      console.log("API result", json);
    } catch (e) {
      setResult({ ok: false, error: String(e) });
    } finally {
      setLoading(false);
    }
  }

  // UI
  return (
    <div style={{ fontFamily: "Inter, system-ui, Arial", padding: 16, color: "inherit" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>Create Draws</h3>
        <button onClick={() => {
          monday.get("context").then((r) => {
            setCtxSeen(true);
            setLastContext(r?.data || r || null);
            const ids = (function extract(d){
              if (!d) return []; const c=[];
              if (Array.isArray(d.selectedItemIds)) c.push(...d.selectedItemIds);
              if (Array.isArray(d.selectedRowIds)) c.push(...d.selectedRowIds);
              if (Array.isArray(d.selectedRows)) c.push(...d.selectedRows.map((x)=>x.id));
              if (Array.isArray(d.selectedPulseIds)) c.push(...d.selectedPulseIds);
              if (Array.isArray(d.itemIds)) c.push(...d.itemIds);
              if (Array.isArray(d.pulseIds)) c.push(...d.pulseIds);
              return [...new Set(c.map((x)=>Number(x)))].filter((n)=>Number.isFinite(n));
            })(r?.data);
            applyIds(ids);
          });
        }}>Refresh selection</button>
      </div>

      <div style={{ margin: "6px 0", opacity: 0.85 }}>
        {ctxSeen ? "Context received." : "Waiting for Monday context…"}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "8px 0 12px" }}>
        <label>
          Set same amount for all:&nbsp;
          <input
            type="number"
            value={allAmount}
            onChange={(e) => setAllAmount(e.target.value)}
            placeholder="25000"
            style={{ width: 140 }}
          />
        </label>
        <button onClick={applyAll}>Apply to all</button>
      </div>

      <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid rgba(0,0,0,0.12)", borderRadius: 6, padding: 8 }}>
        {rows.length === 0 ? (
          <div style={{ opacity: 0.8 }}>No items selected.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid rgba(0,0,0,0.12)" }}>
                <th style={{ padding: "6px 4px" }}>Loan #</th>
                <th style={{ padding: "6px 4px" }}>Requested Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, idx) => (
                <tr key={r.item_id} style={{ borderBottom: "1px solid rgba(0,0,0,0.12)" }}>
                  <td style={{ padding: "6px 4px" }}>
                    {namesById[String(r.item_id)] && namesById[String(r.item_id)].trim() !== ""
                      ? namesById[String(r.item_id)]
                      : String(r.item_id)}
                  </td>
                  <td style={{ padding: "6px 4px" }}>
                    <input
                      type="number"
                      value={r.amount}
                      onChange={(e) => setAmountFor(idx, e.target.value)}
                      placeholder="e.g., 25000"
                      style={{ width: 140 }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <button onClick={createDraws} disabled={loading || rows.length === 0}>
          {loading ? "Creating…" : "Create Draws"}
        </button>
      </div>

      {result && (
        <pre
          style={{
            marginTop: 12,
            padding: 8,
            background: "rgba(0,0,0,0.06)",
            borderRadius: 6,
            whiteSpace: "pre-wrap",
            color: "inherit",
          }}
        >
          {JSON.stringify(result, null, 2)}
        </pre>
      )}

      {lastContext && (
        <details style={{ marginTop: 10, opacity: 0.7 }}>
          <summary>debug: last context</summary>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(lastContext, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
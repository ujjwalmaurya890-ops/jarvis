import { useState } from "react";
import { Link2, Link2Off, RefreshCw, ShieldCheck, Terminal } from "lucide-react";
import type { BackendApi } from "@/lib/useBackend";
import { Copy } from "@/lib/ui";

export default function BackendPanel({ backend }: { backend: BackendApi }) {
  const [reveal, setReveal] = useState(false);

  return (
    <div className={`panel corner p-4 lg:col-span-2 ${backend.connected ? "border-cyan/45" : ""}`}>
      <div className="flex flex-wrap items-center gap-2.5">
        {backend.connected ? <Link2 size={15} className="text-lime" /> : <Link2Off size={15} className="text-amber" />}
        <div>
          <span className="font-mono text-[9px] tracking-[0.2em] text-cyan/70">DESKTOP LINK</span>
          <p className="font-display text-[12px] tracking-[0.15em] text-fg">
            {backend.connected ? "PYTHON BACKEND CONNECTED" : "BROWSER DEMO MODE"}
          </p>
          <p className="text-[11px] text-dim">
            {backend.connected
              ? "Typed commands go through Python -> Ollama / allowlisted tools. This browser's mic and voice remain separate."
              : "No local API is linked. The console uses its own rule-based demo, not Ollama."}
          </p>
        </div>
        <span className={`ml-auto font-mono text-[10px] ${backend.available ? "text-lime" : "text-amber"}`}>
          API 127.0.0.1:8765 · {backend.checking ? "CHECKING" : backend.available ? "REACHABLE" : "UNREACHABLE"}
        </span>
      </div>

      {backend.error ? (
        <p role="alert" className="mt-3 text-[11.5px] leading-relaxed text-amber border-l-2 border-amber/50 pl-3">
          {backend.error}
        </p>
      ) : null}

      {backend.health?.providers?.length ? (
        <p className="mt-2 font-mono text-[10px] text-dim">
          Model at API startup: {backend.health.providers.map((p) => `${p.name} ${p.ok ? "ready" : "unavailable"}`).join(" · ")}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!backend.connected ? (
          <>
            <label className="flex-1 min-w-[200px] max-w-sm">
              <span className="sr-only">Local API token</span>
              <input
                type={reveal ? "text" : "password"}
                autoComplete="off"
                spellCheck={false}
                value={backend.token}
                onChange={(e) => backend.setToken(e.target.value)}
                placeholder="Paste data/api-token (stays in this tab)"
                className="w-full bg-ink2 border border-line2 rounded px-3 py-2 font-mono text-[11px] text-fg outline-none focus:border-cyan/60 placeholder:text-dim/70"
              />
            </label>
            <button type="button" onClick={() => setReveal((v) => !v)} className="text-[10px] font-mono text-dim hover:text-ice">
              {reveal ? "HIDE" : "SHOW"}
            </button>
            <button
              type="button"
              onClick={() => void backend.connect()}
              disabled={backend.checking}
              className="px-3 py-2 border border-cyan/45 bg-cyan/10 rounded text-cyan font-mono text-[10px] tracking-[0.12em] hover:bg-cyan/20 disabled:opacity-40"
            >
              CONNECT
            </button>
          </>
        ) : (
          <button type="button" onClick={backend.disconnect} className="px-3 py-2 border border-rose/40 rounded text-rose font-mono text-[10px] tracking-[0.12em] hover:bg-rose/10">
            DISCONNECT / USE DEMO
          </button>
        )}
        <button type="button" onClick={() => void backend.check()} disabled={backend.checking} className="flex items-center gap-1 px-3 py-2 border border-line2 rounded text-dim hover:text-ice font-mono text-[10px] tracking-[0.12em] disabled:opacity-40">
          <RefreshCw size={11} /> CHECK
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[10.5px] text-mut leading-relaxed">
        <span className="flex items-center gap-1.5">
          <Terminal size={11} className="text-cyan" /> Start in <code className="font-mono text-ice">jarvis/</code>: <code className="font-mono text-fg">python main.py --api</code>
        </span>
        <span className="flex items-center gap-1.5">
          <ShieldCheck size={11} className="text-lime" /> Copy token from <code className="font-mono text-fg">data/api-token</code>
          <Copy text={String.raw`(Get-Content .\data\api-token -Raw).Trim()`} label="COPY POWERSHELL" />
        </span>
      </div>
      <p className="mt-2 text-[10.5px] leading-relaxed text-dim">
        The API binds only to 127.0.0.1, requires a bearer token for commands, and accepts browser requests only from localhost:5173/4173. Power, writes and other sensitive actions cannot be confirmed over this API; use the desktop UI. Never paste the token into a hosted preview.
      </p>
    </div>
  );
}
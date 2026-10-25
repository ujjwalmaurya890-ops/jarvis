import { useMemo, useState } from "react";
import { Download, FileCode2, FolderOpen, Search } from "lucide-react";
import { PROJECT_FILES, TOTAL_BYTES, TOTAL_LINES, type ProjectFile } from "@/data/project";
import { Copy, SectionTitle, highlight } from "@/lib/ui";

const GROUP_TONE: Record<string, string> = {
  Project: "#7dd3fc",
  Config: "#5c708f",
  Core: "#22d3ee",
  Audio: "#a3e635",
  "AI brain": "#fbbf24",
  Tools: "#f97316",
  Memory: "#8b5cf6",
  Security: "#f87171",
  "Desktop UI": "#38bdf8",
  Tests: "#4ade80",
};

function b64(text: string) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

export default function CodeExplorer() {
  const [sel, setSel] = useState<string>("core/router.py");
  const [q, setQ] = useState("");
  const file = useMemo<ProjectFile>(
    () => PROJECT_FILES.find((f) => f.path === sel) ?? PROJECT_FILES[0],
    [sel],
  );
  const groups = useMemo(() => {
    const map = new Map<string, ProjectFile[]>();
    PROJECT_FILES.filter((f) => !q || f.path.toLowerCase().includes(q.toLowerCase()) || f.content.toLowerCase().includes(q.toLowerCase())).forEach((f) => {
      if (!map.has(f.group)) map.set(f.group, []);
      map.get(f.group)!.push(f);
    });
    return [...map.entries()];
  }, [q]);

  const shown = useMemo(() => {
    const lines = file.content.split("\n");
    const capped = lines.length > 900 ? lines.slice(0, 900) : lines;
    return { text: capped.join("\n"), truncated: lines.length > 900, count: lines.length };
  }, [file]);

  const download = (name: string, content: string, mime = "text/plain;charset=utf-8") => {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  const installer = useMemo(
    () =>
      [
        "# install-jarvis.ps1 — writes the whole project into .\\jarvis",
        "# Run:  powershell -NoProfile -File .\\install-jarvis.ps1",
        "# If scripts are blocked, use the reviewed source ZIP instead of changing policy.",
        "$ErrorActionPreference = 'Stop'",
        `$root = Join-Path (Get-Location) 'jarvis'`,
        "function Write-File([string]$rel, [string]$b64) {",
        "  $bytes = [Convert]::FromBase64String($b64)",
        "  $p = Join-Path $root $rel",
        "  New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null",
        "  [System.IO.File]::WriteAllBytes($p, $bytes)",
        "  Write-Host ('  wrote ' + $rel) -ForegroundColor DarkCyan",
        "}",
        `Write-Host 'JARVIS · ${PROJECT_FILES.length} files · ${TOTAL_LINES} lines' -ForegroundColor Cyan`,
        ...PROJECT_FILES.map((f) => `Write-File '${f.path}' '${b64(f.content)}'`),
        "Write-Host ''",
        "Write-Host 'Done. Next:' -ForegroundColor Green",
        "Write-Host '  cd jarvis'",
        "Write-Host '  py -3.12 -m venv .venv ; .\\.venv\\Scripts\\activate.bat'",
        "Write-Host '  pip install -r requirements.txt'",
        "Write-Host '  python main.py --check'",
        "",
      ].join("\n"),
    [],
  );

  return (
    <section id="code" className="scroll-mt-20">
      <SectionTitle
        kicker="07 · THE CODE"
        title={`${PROJECT_FILES.length} real modules, ${TOTAL_LINES.toLocaleString()} lines — all of it here`}
        sub="Not a sketch: every file below is the same content as the verified project, byte-compilable and importable. Read it, copy a module, or write the whole tree to your machine with one PowerShell script."
      />
      <div className="panel overflow-hidden">
        <div className="grid lg:grid-cols-[268px_minmax(0,1fr)]">
          {/* tree */}
          <div className="border-b lg:border-b-0 lg:border-r border-line bg-ink2/50 max-h-[560px] overflow-y-auto">
            <div className="p-2.5 border-b border-line/70 sticky top-0 bg-ink2/95 backdrop-blur z-10">
              <div className="flex items-center gap-2">
                <Search size={12} className="text-dim" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="search files + code"
                  className="w-full bg-transparent text-[11.5px] font-mono text-fg outline-none placeholder:text-dim/70"
                />
              </div>
            </div>
            {groups.map(([group, list]) => (
              <div key={group} className="py-1.5">
                <div className="flex items-center gap-1.5 px-3 py-1">
                  <FolderOpen size={10} style={{ color: GROUP_TONE[group] ?? "#5c708f" }} />
                  <span className="font-mono text-[9.5px] tracking-[0.2em]" style={{ color: GROUP_TONE[group] ?? "#5c708f" }}>
                    {group.toUpperCase()}
                  </span>
                  <span className="ml-auto font-mono text-[9px] text-dim/70">{list.length}</span>
                </div>
                {list.map((f) => (
                  <button
                    key={f.path}
                    type="button"
                    onClick={() => setSel(f.path)}
                    className={`w-full text-left px-3 py-1 pl-6 flex items-center gap-2 transition-colors ${
                      sel === f.path ? "bg-cyan/10 text-cyan" : "text-mut hover:bg-white/[0.03] hover:text-fg"
                    }`}
                  >
                    <FileCode2 size={11} className="shrink-0 opacity-70" />
                    <span className="font-mono text-[10.5px] truncate">{f.path.split("/").slice(1).join("/") || f.path}</span>
                    <span className="ml-auto font-mono text-[9px] text-dim/60 shrink-0">{f.lines}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>

          {/* viewer */}
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-line bg-ink2/40 flex-wrap">
              <span className="font-mono text-[11.5px] text-ice">{file.path}</span>
              <span className="font-mono text-[9.5px] text-dim">
                {file.lines} lines · {Math.round(file.bytes / 1024)} KB
              </span>
              <span className="ml-auto flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => download(file.path.split("/").pop()!, file.content, "text/x-python;charset=utf-8")}
                  className="font-mono text-[10px] tracking-[0.16em] px-2.5 py-1 rounded border border-line2 text-dim hover:text-cyan hover:border-cyan/50 transition-colors"
                >
                  DOWNLOAD
                </button>
                <Copy text={file.content} label="COPY FILE" />
              </span>
            </div>
            {file.description ? (
              <p className="px-3.5 py-2 text-[11.5px] leading-snug text-mut border-b border-line/70 bg-panel2/40">{file.description}</p>
            ) : null}
            <div className="overflow-auto max-h-[520px] p-3.5 code bg-[#080f1b]">
              {highlight(shown.text, file.lang)}
              {shown.truncated ? (
                <p className="text-dim mt-3 pl-14">… {shown.count - 900} more lines — download or copy for the complete file.</p>
              ) : null}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 px-3.5 py-3 border-t border-line bg-ink2/50">
          <span className="font-mono text-[10px] tracking-[0.18em] text-dim">
            {PROJECT_FILES.length} FILES · {TOTAL_LINES.toLocaleString()} LINES · {Math.round(TOTAL_BYTES / 1024)} KB
          </span>
          <button
            type="button"
            onClick={() => download("install-jarvis.ps1", installer, "text/plain;charset=utf-8")}
            className="ml-auto flex items-center gap-2 px-3.5 py-2 rounded border border-cyan/45 bg-cyan/10 text-cyan hover:bg-cyan/20 font-display text-[11px] tracking-[0.14em] transition-colors shadow-[0_0_24px_-10px_rgba(34,211,238,0.8)]"
          >
            <Download size={13} /> WRITE ALL FILES (install-jarvis.ps1)
          </button>
        </div>
      </div>
      <p className="mt-2.5 text-[11.5px] text-dim leading-relaxed max-w-3xl">
        The script base64-embeds each file (so Windows line endings and UTF-8 cannot corrupt it), writes them under <code className="font-mono text-mut">.\jarvis\</code>, and
        prints the four commands to run next. It installs nothing and touches no registry key — and <code className="font-mono text-mut">.env</code> is not in the payload, only
        <code className="font-mono text-mut"> .env.example</code>.
      </p>
    </section>
  );
}

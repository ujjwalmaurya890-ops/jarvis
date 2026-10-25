import { useEffect, useRef, useState, type ReactNode } from "react";

/* ------------------------------------------------------------- scroll reveal */
export function Reveal({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setSeen(true);
            io.disconnect();
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -6% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} className={`reveal ${seen ? "in" : ""} ${className}`} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- copy button */
export function Copy({ text, label = "COPY", className = "" }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
      className={`font-mono text-[10px] tracking-[0.18em] px-2.5 py-1 rounded border transition-colors ${
        done ? "border-lime/60 text-lime bg-lime/10" : `border-line2 text-dim hover:text-ice hover:border-cyan/50 ${className}`
      }`}
    >
      {done ? "COPIED" : label}
    </button>
  );
}

/* ------------------------------------------------------ tiny code highlighter */

export function highlight(code: string, lang: string): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = code.split("\n");
  lines.forEach((line, i) => {
    out.push(<Line key={i} line={line} lang={lang} n={i + 1} />);
  });
  return out;
}

function Line({ line, lang, n }: { line: string; lang: string; n: number }) {
  const isPy = lang === "python";
  const cls = isPy && (line.trimStart().startsWith("#") || line.trim().startsWith('"""') || line.trim().startsWith("'''"))
    ? "com"
    : !isPy && (line.trimStart().startsWith("#") || line.trimStart().startsWith("//"))
      ? "com"
      : "";
  return (
    <div className="flex gap-4 leading-[1.62] hover:bg-white/[0.03] transition-colors">
      <span className="w-10 shrink-0 text-right select-none text-[10px] text-dim/50 font-mono pt-[2px]">{n}</span>
      <span className={`whitespace-pre ${cls}`}>{cls ? line : tokenize(line, isPy)}</span>
    </div>
  );
}

function tokenize(line: string, isPy: boolean): ReactNode[] {
  const parts: ReactNode[] = [];
  const rx = isPy
    ? /("""|''')|("[^"]*"|'[^']*')|(\b\d[\d_.]*\b)|(@\w+)|([A-Za-z_][A-Za-z0-9_]*(?=\())|([^"'`\d@(_]+)/g
    : /("[^"]*"|'[^']*')|(\b\d[\d_.]*\b)|(\$\{[^}]*\})|([^"'$\d]+)/g;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = rx.exec(line))) {
    const tok = m[0];
    let cls = "";
    if (m[1]) cls = "str";
    else if (m[2] && isPy) cls = "str";
    else if (m[3]) cls = "num";
    else if (m[4] && isPy) cls = "dec";
    else if (m[5] && isPy) cls = "kw";
    else if (!isPy && m[2]) cls = "num";
    else if (!isPy && m[3]) cls = "dec";
    parts.push(
      <span key={k++} className={cls}>
        {tok}
      </span>,
    );
    if (rx.lastIndex === m.index) rx.lastIndex++;
  }
  return parts;
}

/* ------------------------------------------------------------------ primitives */
export function SectionTitle({ kicker, title, sub }: { kicker: string; title: string; sub?: string }) {
  return (
    <div className="mb-7">
      <div className="flex items-center gap-3 mb-3">
        <span className="h-px w-8 bg-cyan/60" />
        <span className="font-mono text-[10px] tracking-[0.32em] text-cyan/80">{kicker}</span>
      </div>
      <h2 className="font-display text-2xl sm:text-[34px] font-bold tracking-tight text-fg leading-[1.12]">{title}</h2>
      {sub ? <p className="mt-3 max-w-3xl text-[13.5px] leading-relaxed text-mut">{sub}</p> : null}
    </div>
  );
}

export function Chip({ children, tone = "cyan" }: { children: ReactNode; tone?: "cyan" | "lime" | "amber" | "rose" | "mut" }) {
  const map: Record<string, string> = {
    cyan: "border-cyan/35 text-ice bg-cyan/[0.07]",
    lime: "border-lime/35 text-lime bg-lime/[0.07]",
    amber: "border-amber/40 text-amber bg-amber/[0.07]",
    rose: "border-rose/40 text-rose bg-rose/[0.07]",
    mut: "border-line2 text-dim bg-white/[0.02]",
  };
  return <span className={`inline-flex items-center gap-1.5 border rounded px-2 py-[3px] font-mono text-[10px] tracking-wider ${map[tone]}`}>{children}</span>;
}

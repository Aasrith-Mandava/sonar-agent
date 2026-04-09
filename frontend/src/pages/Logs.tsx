import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Terminal, Pause, Play, Trash2, Download, Search, Filter, ArrowLeft } from 'lucide-react';
import { format } from 'date-fns';
import { cn } from '../lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

interface LogEntry {
  ts: string;
  level: 'debug' | 'info' | 'warning' | 'error' | 'critical';
  logger: string;
  message: string;
}

const LEVEL_STYLES: Record<string, { dot: string; text: string; row: string }> = {
  debug:    { dot: 'bg-slate-300',  text: 'text-slate-500',     row: '' },
  info:     { dot: 'bg-blue-400',   text: 'text-blue-700',      row: '' },
  warning:  { dot: 'bg-amber-400',  text: 'text-amber-700',     row: 'bg-amber-50/40' },
  error:    { dot: 'bg-red-500',    text: 'text-red-700',       row: 'bg-red-50/50' },
  critical: { dot: 'bg-red-700',    text: 'text-white',         row: 'bg-red-600/10' },
};

const LEVELS: LogEntry['level'][] = ['debug', 'info', 'warning', 'error', 'critical'];

// ─── SSE hook ─────────────────────────────────────────────────────────────────

function useLogStream(paused: boolean) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    const baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';
    // SSE endpoint is at /api/logs/stream — use fetch + ReadableStream so we
    // can pass the auth token, since EventSource doesn't allow custom headers.
    const controller = new AbortController();
    const token = localStorage.getItem('token') || '';

    const start = async () => {
      try {
        const resp = await fetch(`${baseURL}/logs/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          signal: controller.signal,
        });
        if (!resp.ok || !resp.body) {
          setConnected(false);
          return;
        }
        setConnected(true);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let lineEnd: number;
          while ((lineEnd = buffer.indexOf('\n\n')) !== -1) {
            const event = buffer.slice(0, lineEnd);
            buffer = buffer.slice(lineEnd + 2);
            const dataLine = event.split('\n').find((l) => l.startsWith('data:'));
            if (!dataLine) continue;
            try {
              const entry = JSON.parse(dataLine.slice(5).trim()) as LogEntry;
              if (!pausedRef.current) {
                setLogs((prev) => {
                  const next = [...prev, entry];
                  // Cap at 5000 entries to prevent runaway memory
                  return next.length > 5000 ? next.slice(-5000) : next;
                });
              }
            } catch { /* ignore parse errors / keepalive */ }
          }
        }
      } catch (err) {
        setConnected(false);
      }
    };
    start();
    return () => controller.abort();
  }, []);

  const clear = () => setLogs([]);
  return { logs, connected, clear };
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function Logs() {
  const [paused, setPaused] = useState(false);
  const { logs, connected, clear } = useLogStream(paused);

  const [search, setSearch] = useState('');
  const [enabledLevels, setEnabledLevels] = useState<Set<string>>(
    new Set(['info', 'warning', 'error', 'critical']),
  );
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const toggleLevel = (lvl: string) => {
    setEnabledLevels((prev) => {
      const next = new Set(prev);
      next.has(lvl) ? next.delete(lvl) : next.add(lvl);
      return next;
    });
  };

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return logs.filter(
      (l) =>
        enabledLevels.has(l.level) &&
        (!q || l.message.toLowerCase().includes(q) || l.logger.toLowerCase().includes(q)),
    );
  }, [logs, search, enabledLevels]);

  useEffect(() => {
    if (autoScroll && !paused) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [filtered.length, autoScroll, paused]);

  const downloadLogs = () => {
    const text = filtered
      .map((l) => `${l.ts} [${l.level.toUpperCase()}] ${l.logger}: ${l.message}`)
      .join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sonaragent-logs-${new Date().toISOString().replace(/[:.]/g, '-')}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = { debug: 0, info: 0, warning: 0, error: 0, critical: 0 };
    for (const l of logs) c[l.level] = (c[l.level] || 0) + 1;
    return c;
  }, [logs]);

  return (
    <div className="p-8 max-w-7xl mx-auto flex flex-col gap-4 h-[calc(100vh-60px)]">
      <div className="flex justify-between items-start">
        <div>
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors mb-2"
          >
            <ArrowLeft className="h-3 w-3" />
            Back to Dashboard
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Application Logs</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Live tail of every backend log line — agents, scans, errors, HTTP requests.
            Persisted to <code className="bg-muted px-1 rounded text-[11px]">backend/logs/app.log</code>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-md border',
              connected
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-muted bg-muted text-muted-foreground',
            )}
          >
            <span
              className={cn(
                'w-1.5 h-1.5 rounded-full',
                connected ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground',
              )}
            />
            {connected ? 'Live' : 'Disconnected'}
          </span>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap border rounded-xl bg-card px-3 py-2">
        <div className="flex items-center gap-1.5 flex-1 min-w-[220px]">
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter logs by text or logger name…"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="flex items-center gap-1 border-l pl-3">
          <Filter className="h-3.5 w-3.5 text-muted-foreground mr-1" />
          {LEVELS.map((lvl) => {
            const active = enabledLevels.has(lvl);
            const style = LEVEL_STYLES[lvl];
            return (
              <button
                key={lvl}
                onClick={() => toggleLevel(lvl)}
                className={cn(
                  'flex items-center gap-1 px-2 py-1 text-[10px] uppercase rounded font-semibold transition-all',
                  active
                    ? `border ${style.text} bg-background`
                    : 'border border-dashed text-muted-foreground hover:text-foreground',
                )}
              >
                <span className={cn('w-1.5 h-1.5 rounded-full', style.dot)} />
                {lvl}
                <span className="text-muted-foreground font-normal">({counts[lvl] ?? 0})</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-1.5 border-l pl-3">
          <button
            onClick={() => setPaused((v) => !v)}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium border rounded-md hover:bg-muted transition-colors"
          >
            {paused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            onClick={clear}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium border rounded-md hover:bg-muted transition-colors"
          >
            <Trash2 className="h-3 w-3" />
            Clear
          </button>
          <button
            onClick={downloadLogs}
            disabled={filtered.length === 0}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium border rounded-md hover:bg-muted transition-colors disabled:opacity-40"
          >
            <Download className="h-3 w-3" />
            Download
          </button>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground ml-1 cursor-pointer">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded"
            />
            auto-scroll
          </label>
        </div>
      </div>

      {/* Log panel */}
      <div className="flex-1 min-h-0 border rounded-xl bg-background overflow-hidden flex flex-col">
        <div className="flex items-center gap-2 px-3 py-2 bg-muted/50 border-b shrink-0">
          <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">
            Showing {filtered.length.toLocaleString()} of {logs.length.toLocaleString()} log entries
          </span>
          {paused && (
            <span className="ml-auto text-[10px] text-amber-600 font-semibold uppercase">
              Paused (new logs ignored)
            </span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto font-mono">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
              <Terminal className="h-6 w-6 opacity-30" />
              <span className="text-xs">
                {logs.length === 0 ? 'Waiting for logs…' : 'No logs match the current filters.'}
              </span>
            </div>
          ) : (
            <>
              {filtered.map((l, i) => {
                const style = LEVEL_STYLES[l.level] ?? LEVEL_STYLES.info;
                return (
                  <div
                    key={i}
                    className={cn(
                      'flex gap-2 px-3 py-1 border-b border-border/40 text-[11px] leading-relaxed',
                      style.row,
                    )}
                  >
                    <span className="text-muted-foreground tabular-nums shrink-0 w-[80px]">
                      {(() => {
                        try {
                          return format(new Date(l.ts), 'HH:mm:ss.SSS');
                        } catch {
                          return l.ts.slice(11, 23);
                        }
                      })()}
                    </span>
                    <span
                      className={cn(
                        'shrink-0 w-[64px] text-[10px] font-bold uppercase',
                        style.text,
                      )}
                    >
                      {l.level}
                    </span>
                    <span className="shrink-0 max-w-[220px] truncate text-muted-foreground">
                      {l.logger}
                    </span>
                    <span className="flex-1 break-words text-foreground whitespace-pre-wrap">
                      {l.message}
                    </span>
                  </div>
                );
              })}
              <div ref={bottomRef} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
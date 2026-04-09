import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/utils';
import { format } from 'date-fns';
import {
  Activity, Terminal, ChevronDown, ChevronRight, Radio,
  Search, X, Cpu, Wrench, Eye, FileText, Zap, AlertTriangle,
  Info, Bug, RefreshCw, Clock, BarChart3, ArrowLeft,
} from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentLog {
  id: string;
  agent_name: string;
  scan_run_id: string;
  action: string;
  input_summary: string;
  output_summary: string;
  status: string;
  error_message?: string;
  model_used?: string;
  provider_used?: string;
  tokens_in?: number;
  tokens_out?: number;
  cost_estimate?: number;
  created_at: string;
}

interface WsLogEntry {
  type: string;
  agent: string;
  action?: string;
  message: string;
  tool?: string;
  tool_calls?: { name: string; args: Record<string, unknown> }[];
  elapsed_ms?: number;
  ts?: string;
  scan_run_id?: string;
}

interface AppLogEntry {
  ts: string;
  level: 'debug' | 'info' | 'warning' | 'error' | 'critical';
  logger: string;
  message: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const AGENT_CONFIG: Record<string, { color: string; bg: string; Icon: any }> = {
  scanner:      { color: 'text-blue-700',   bg: 'bg-blue-100',   Icon: Search   },
  fixer:        { color: 'text-orange-700', bg: 'bg-orange-100', Icon: Wrench   },
  reviewer:     { color: 'text-purple-700', bg: 'bg-purple-100', Icon: Eye      },
  reporter:     { color: 'text-green-700',  bg: 'bg-green-100',  Icon: FileText },
  orchestrator: { color: 'text-slate-700',  bg: 'bg-slate-100',  Icon: Cpu      },
  tools:        { color: 'text-teal-700',   bg: 'bg-teal-100',   Icon: Zap      },
};

const ACTION_BADGE: Record<string, string> = {
  agent_start:          'bg-blue-50 text-blue-700 border-blue-200',
  tool_invocation_plan: 'bg-amber-50 text-amber-700 border-amber-200',
  tool_result:          'bg-teal-50 text-teal-700 border-teal-200',
  agent_reasoning:      'bg-violet-50 text-violet-700 border-violet-200',
  paused:               'bg-yellow-50 text-yellow-700 border-yellow-200',
  stopped:              'bg-red-50 text-red-700 border-red-200',
  complete:             'bg-green-50 text-green-700 border-green-200',
  failed:               'bg-red-50 text-red-700 border-red-200',
  log:                  'bg-muted text-muted-foreground border-border',
};

const APP_LOG_LEVEL: Record<string, { cls: string; Icon: any }> = {
  debug:    { cls: 'text-muted-foreground', Icon: Bug       },
  info:     { cls: 'text-foreground',       Icon: Info      },
  warning:  { cls: 'text-amber-600',        Icon: AlertTriangle },
  error:    { cls: 'text-destructive',      Icon: AlertTriangle },
  critical: { cls: 'text-destructive font-bold', Icon: AlertTriangle },
};

const ALL_AGENTS = ['scanner', 'fixer', 'reviewer', 'reporter', 'orchestrator', 'tools'];

// ─── WS streaming hook for agent logs ────────────────────────────────────────

function useAgentLogStream(enabled: boolean) {
  const [entries, setEntries] = useState<WsLogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const connect = useCallback(() => {
    const base = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api')
      .replace(/^http/, 'ws').replace('/api', '');
    const ws = new WebSocket(`${base}/ws/logs`);
    ws.onmessage = (e) => {
      try {
        const msg: WsLogEntry = JSON.parse(e.data);
        setEntries((p) => [msg, ...p].slice(0, 500));
      } catch {}
    };
    wsRef.current = ws;
  }, []);

  useEffect(() => {
    if (enabled) {
      connect();
    } else {
      wsRef.current?.close();
      wsRef.current = null;
    }
    return () => { wsRef.current?.close(); };
  }, [enabled, connect]);

  const clear = () => setEntries([]);
  return { entries, clear };
}

// ─── SSE hook for app logs ────────────────────────────────────────────────────

function useAppLogStream(enabled: boolean) {
  const [entries, setEntries] = useState<AppLogEntry[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) {
      esRef.current?.close();
      esRef.current = null;
      return;
    }
    const base = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';
    const token = localStorage.getItem('token') || '';
    // EventSource doesn't support auth headers — pass as query param
    const es = new EventSource(`${base}/logs/stream?token=${token}`);
    es.onmessage = (e) => {
      try {
        const entry: AppLogEntry = JSON.parse(e.data);
        setEntries((p) => [entry, ...p].slice(0, 500));
      } catch {}
    };
    esRef.current = es;
    return () => { es.close(); };
  }, [enabled]);

  const clear = () => setEntries([]);
  return { entries, clear };
}

// ─── Agent Badge ──────────────────────────────────────────────────────────────

function AgentBadge({ name }: { name: string }) {
  const cfg = AGENT_CONFIG[name] ?? { color: 'text-foreground', bg: 'bg-muted', Icon: Cpu };
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase', cfg.bg, cfg.color)}>
      <cfg.Icon className="h-2.5 w-2.5" />
      {name}
    </span>
  );
}

// ─── DB Log entry card ────────────────────────────────────────────────────────

function LogCard({ log }: { log: AgentLog }) {
  const [expanded, setExpanded] = useState(false);
  const isError = log.status === 'error';
  const badgeCls = isError ? 'bg-red-50 text-red-700 border-red-200' : (ACTION_BADGE[log.action] ?? ACTION_BADGE.log);

  return (
    <div className={cn('border rounded-lg overflow-hidden transition-colors', isError && 'border-destructive/30')}>
      {/* Header row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/30 transition-colors text-left"
      >
        <AgentBadge name={log.agent_name} />
        <span className={cn('px-2 py-0.5 text-[10px] font-medium border rounded-full capitalize', badgeCls)}>
          {log.action.replace(/_/g, ' ')}
        </span>
        <span className="flex-1 text-sm text-foreground truncate min-w-0">
          {(log.output_summary || log.input_summary || '—').slice(0, 120)}
        </span>
        <div className="flex items-center gap-3 shrink-0 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {format(new Date(log.created_at), 'HH:mm:ss')}
          </span>
          {log.tokens_in && (
            <span className="hidden md:inline">{log.tokens_in + (log.tokens_out ?? 0)} tok</span>
          )}
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t bg-muted/20 px-4 py-3 flex flex-col gap-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase text-muted-foreground mb-1">Input Context</p>
              <pre className="text-xs bg-background border rounded-md p-3 overflow-auto whitespace-pre-wrap max-h-40 font-mono">
                {log.input_summary || '—'}
              </pre>
            </div>
            <div>
              <p className="text-[10px] font-semibold uppercase text-muted-foreground mb-1">Output / Tool Execution</p>
              <pre className="text-xs bg-background border rounded-md p-3 overflow-auto whitespace-pre-wrap max-h-40 font-mono">
                {log.output_summary || log.error_message || '—'}
              </pre>
            </div>
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>Run: <code className="bg-muted px-1 rounded text-[11px]">{log.scan_run_id}</code></span>
            {log.model_used && <span>Model: <strong className="text-foreground">{log.model_used}</strong></span>}
            {log.provider_used && <span>Provider: <strong className="text-foreground">{log.provider_used}</strong></span>}
            {log.tokens_in != null && <span>Tokens in: <strong className="text-foreground">{log.tokens_in}</strong></span>}
            {log.tokens_out != null && <span>Tokens out: <strong className="text-foreground">{log.tokens_out}</strong></span>}
            {log.cost_estimate != null && <span>Cost: <strong className="text-foreground">${log.cost_estimate.toFixed(5)}</strong></span>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Live WS log row ──────────────────────────────────────────────────────────

function LiveLogRow({ entry }: { entry: WsLogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const hasTools = entry.tool_calls && entry.tool_calls.length > 0;
  const badgeCls = ACTION_BADGE[entry.type] ?? ACTION_BADGE[entry.action ?? ''] ?? ACTION_BADGE.log;

  return (
    <div className="border-b last:border-0">
      <button
        onClick={() => (hasTools || entry.message.length > 120) ? setExpanded((v) => !v) : undefined}
        className={cn(
          'w-full flex items-start gap-2 px-3 py-2.5 text-left transition-colors font-mono text-xs',
          (hasTools || entry.message.length > 120) && 'hover:bg-muted/30 cursor-pointer',
        )}
      >
        <span className="shrink-0 text-muted-foreground tabular-nums pt-0.5">
          {entry.ts ? format(new Date(entry.ts), 'HH:mm:ss.SSS') : '—'}
        </span>
        <AgentBadge name={entry.agent || 'system'} />
        {(entry.action || entry.type) && (
          <span className={cn('shrink-0 px-1.5 py-0.5 rounded border text-[10px] font-medium capitalize', badgeCls)}>
            {(entry.action ?? entry.type).replace(/_/g, ' ')}
          </span>
        )}
        {entry.elapsed_ms != null && (
          <span className="shrink-0 text-muted-foreground">{entry.elapsed_ms}ms</span>
        )}
        <span className="flex-1 min-w-0 text-foreground break-all leading-relaxed">
          {entry.message.slice(0, expanded ? 2000 : 120)}
          {!expanded && entry.message.length > 120 && '…'}
        </span>
        {(hasTools || entry.message.length > 120) && (
          <span className="shrink-0 pt-0.5 text-muted-foreground">
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </span>
        )}
      </button>

      {expanded && hasTools && (
        <div className="mx-3 mb-2 p-2 bg-amber-50 border border-amber-200 rounded-md">
          <p className="text-[10px] font-semibold text-amber-700 mb-1 uppercase">Tool Calls</p>
          {entry.tool_calls!.map((tc, i) => (
            <div key={i} className="text-xs font-mono mb-1">
              <span className="font-semibold text-amber-800">{tc.name}</span>
              <span className="text-amber-600"> {JSON.stringify(tc.args)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── App log row ──────────────────────────────────────────────────────────────

function AppLogRow({ entry }: { entry: AppLogEntry }) {
  const cfg = APP_LOG_LEVEL[entry.level] ?? APP_LOG_LEVEL.info;
  return (
    <div className="flex gap-2 px-3 py-1.5 border-b last:border-0 font-mono text-xs hover:bg-muted/20 transition-colors">
      <span className="shrink-0 text-muted-foreground tabular-nums">
        {format(new Date(entry.ts), 'HH:mm:ss.SSS')}
      </span>
      <cfg.Icon className={cn('h-3 w-3 shrink-0 mt-0.5', cfg.cls)} />
      <span className={cn('shrink-0 uppercase text-[10px] font-bold w-14', cfg.cls)}>{entry.level}</span>
      <span className="shrink-0 text-muted-foreground truncate max-w-[140px]">{entry.logger}</span>
      <span className={cn('flex-1 break-all', cfg.cls)}>{entry.message}</span>
    </div>
  );
}

// ─── Token Usage panel ────────────────────────────────────────────────────────

function TokenUsagePanel() {
  const { data } = useQuery({
    queryKey: ['token-usage'],
    queryFn: async () => {
      const { data } = await api.get('/observability/token-usage?days=30');
      return data;
    },
    refetchInterval: 30_000,
  });

  if (!data) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div className="border rounded-lg p-3 bg-card">
        <p className="text-xs text-muted-foreground mb-1">Total Tokens (30d)</p>
        <p className="text-xl font-bold text-foreground">{(data.total_tokens ?? 0).toLocaleString()}</p>
      </div>
      <div className="border rounded-lg p-3 bg-card">
        <p className="text-xs text-muted-foreground mb-1">Est. Cost (30d)</p>
        <p className="text-xl font-bold text-foreground">${(data.total_cost ?? 0).toFixed(4)}</p>
      </div>
      {(data.by_agent ?? []).slice(0, 2).map((a: any) => {
        return (
          <div key={a.group} className="border rounded-lg p-3 bg-card">
            <p className="text-xs text-muted-foreground mb-1 capitalize">{a.group} Agent</p>
            <p className="text-xl font-bold text-foreground">{(a.total_tokens ?? 0).toLocaleString()}</p>
            <p className="text-[11px] text-muted-foreground">{a.call_count} calls</p>
          </div>
        );
      })}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

type Tab = 'db' | 'live' | 'applog';

export default function AuditLogs() {
  const [tab, setTab]               = useState<Tab>('db');
  const [scanRunId, setScanRunId]   = useState('');
  const [agentFilter, setAgent]     = useState<string>('all');
  const [liveStream, setLiveStream] = useState(false);
  const [appStream, setAppStream]   = useState(false);
  const [levelFilter, setLevel]     = useState<string>('all');
  const liveEndRef                  = useRef<HTMLDivElement>(null);
  const appEndRef                   = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const { entries: liveEntries, clear: clearLive } = useAgentLogStream(liveStream && tab === 'live');
  const { entries: appEntries,  clear: clearApp  } = useAppLogStream(appStream && tab === 'applog');

  // DB logs
  const { data: logsResp, isLoading, refetch } = useQuery({
    queryKey: ['agent_logs', scanRunId, agentFilter],
    queryFn: async () => {
      let url = `/observability/logs?page_size=100`;
      if (scanRunId) url += `&scan_run_id=${scanRunId}`;
      if (agentFilter !== 'all') url += `&agent=${agentFilter}`;
      const { data } = await api.get(url);
      return data;
    },
    enabled: tab === 'db',
    refetchInterval: tab === 'db' ? 10_000 : false,
  });
  const dbLogs: AgentLog[] = logsResp?.items ?? [];

  // Auto-scroll live logs
  useEffect(() => {
    if (autoScroll && tab === 'live') liveEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [liveEntries, tab, autoScroll]);
  useEffect(() => {
    if (autoScroll && tab === 'applog') appEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [appEntries, tab, autoScroll]);

  // Filter live entries
  const filteredLive = agentFilter === 'all'
    ? liveEntries
    : liveEntries.filter((e) => e.agent === agentFilter);

  // Filter app logs by level
  const filteredApp = levelFilter === 'all'
    ? appEntries
    : appEntries.filter((e) => e.level === levelFilter);

  const TABS: { key: Tab; label: string; Icon: any }[] = [
    { key: 'db',     label: 'Agent Logs (DB)',    Icon: Activity },
    { key: 'live',   label: 'Live Agent Stream',  Icon: Radio    },
    { key: 'applog', label: 'Application Logs',   Icon: Terminal },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto flex flex-col gap-6">

      {/* ── Header ── */}
      <div>
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors mb-2"
        >
          <ArrowLeft className="h-3 w-3" />
          Back to Dashboard
        </Link>
        <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <BarChart3 className="h-7 w-7 text-primary" />
          Observability
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Real-time telemetry, agent execution traces, and application logs.
        </p>
      </div>

      {/* ── Token Usage ── */}
      <TokenUsagePanel />

      {/* ── Tabs ── */}
      <div className="flex gap-1 border-b pb-0">
        {TABS.map(({ key, label, Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px',
              tab === key
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
            {key === 'live' && liveStream && (
              <span className="ml-1 flex h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            )}
            {key === 'applog' && appStream && (
              <span className="ml-1 flex h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
            )}
          </button>
        ))}
      </div>

      {/* ── Controls bar ── */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Agent filter (db + live) */}
        {(tab === 'db' || tab === 'live') && (
          <div className="flex gap-1 flex-wrap">
            {['all', ...ALL_AGENTS].map((a) => {
              return (
                <button
                  key={a}
                  onClick={() => setAgent(a)}
                  className={cn(
                    'px-2.5 py-1 text-xs rounded-full border font-medium capitalize transition-colors',
                    agentFilter === a
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-background text-muted-foreground hover:text-foreground border-border',
                  )}
                >
                  {a}
                </button>
              );
            })}
          </div>
        )}

        {/* Scan run ID filter (db only) */}
        {tab === 'db' && (
          <div className="relative ml-auto">
            <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              placeholder="Filter by Scan Run ID…"
              value={scanRunId}
              onChange={(e) => setScanRunId(e.target.value)}
              className="pl-8 pr-3 py-1.5 text-xs border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary w-52"
            />
            {scanRunId && (
              <button onClick={() => setScanRunId('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        )}

        {/* Refresh (db) */}
        {tab === 'db' && (
          <button onClick={() => refetch()} className="flex items-center gap-1 px-3 py-1.5 text-xs border rounded-md hover:bg-muted transition-colors text-muted-foreground">
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        )}

        {/* Live stream toggle */}
        {tab === 'live' && (
          <>
            <button
              onClick={() => setLiveStream((v) => !v)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md font-medium border transition-colors ml-auto',
                liveStream
                  ? 'bg-green-500 text-white border-green-500'
                  : 'bg-background border-border text-muted-foreground hover:text-foreground',
              )}
            >
              <Radio className="h-3.5 w-3.5" />
              {liveStream ? 'Streaming' : 'Start Stream'}
            </button>
            {liveEntries.length > 0 && (
              <button onClick={clearLive} className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 border rounded-md">
                Clear
              </button>
            )}
            <button
              onClick={() => setAutoScroll((v) => !v)}
              className={cn('text-xs px-2 py-1 border rounded-md', autoScroll ? 'bg-muted' : 'bg-background text-muted-foreground')}
            >
              Auto-scroll {autoScroll ? 'on' : 'off'}
            </button>
          </>
        )}

        {/* App log controls */}
        {tab === 'applog' && (
          <>
            {/* Level filter */}
            <div className="flex gap-1">
              {['all', 'info', 'warning', 'error'].map((l) => (
                <button
                  key={l}
                  onClick={() => setLevel(l)}
                  className={cn(
                    'px-2.5 py-1 text-xs rounded-full border font-medium capitalize transition-colors',
                    levelFilter === l
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-background text-muted-foreground border-border hover:text-foreground',
                  )}
                >
                  {l}
                </button>
              ))}
            </div>
            <button
              onClick={() => setAppStream((v) => !v)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md font-medium border transition-colors ml-auto',
                appStream
                  ? 'bg-blue-500 text-white border-blue-500'
                  : 'bg-background border-border text-muted-foreground hover:text-foreground',
              )}
            >
              <Radio className="h-3.5 w-3.5" />
              {appStream ? 'Streaming' : 'Start Stream'}
            </button>
            {appEntries.length > 0 && (
              <button onClick={clearApp} className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 border rounded-md">
                Clear
              </button>
            )}
          </>
        )}
      </div>

      {/* ── DB Logs ── */}
      {tab === 'db' && (
        <div className="flex flex-col gap-2">
          {isLoading ? (
            <div className="border rounded-xl p-12 text-center text-muted-foreground animate-pulse text-sm">
              Loading agent logs…
            </div>
          ) : dbLogs.length === 0 ? (
            <div className="border rounded-xl p-12 text-center text-muted-foreground text-sm">
              No agent logs found. Run a scan to generate logs.
            </div>
          ) : (
            dbLogs.map((log) => <LogCard key={log.id} log={log} />)
          )}
        </div>
      )}

      {/* ── Live Agent Stream ── */}
      {tab === 'live' && (
        <div className="border rounded-xl bg-card overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-muted/60 border-b">
            <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">Live Agent Events</span>
            {liveStream && (
              <span className="ml-auto flex items-center gap-1.5 text-xs text-green-600">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                Live — {filteredLive.length} events
              </span>
            )}
          </div>
          <div className="h-[560px] overflow-y-auto flex flex-col-reverse bg-background">
            {!liveStream ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground text-sm">
                <Radio className="h-8 w-8 opacity-30" />
                <p>Click "Start Stream" to connect to the live agent log feed.</p>
              </div>
            ) : filteredLive.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground text-sm">
                <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                <p>Connected — waiting for agent events…</p>
              </div>
            ) : (
              <>
                <div ref={liveEndRef} />
                {[...filteredLive].reverse().map((e, i) => (
                  <LiveLogRow key={i} entry={e} />
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {/* ── Application Logs ── */}
      {tab === 'applog' && (
        <div className="border rounded-xl bg-card overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-muted/60 border-b">
            <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">Python Application Logs</span>
            {appStream && (
              <span className="ml-auto flex items-center gap-1.5 text-xs text-blue-600">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                Streaming — {filteredApp.length} entries
              </span>
            )}
          </div>
          <div className="h-[560px] overflow-y-auto flex flex-col-reverse bg-background">
            {!appStream ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground text-sm">
                <Terminal className="h-8 w-8 opacity-30" />
                <p>Click "Start Stream" to tail Python application logs in real time.</p>
                <p className="text-xs">Includes FastAPI, LangGraph, MCP, and SQLAlchemy logs.</p>
              </div>
            ) : filteredApp.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground text-sm">
                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                <p>Connected — waiting for log entries…</p>
              </div>
            ) : (
              <>
                <div ref={appEndRef} />
                {[...filteredApp].reverse().map((e, i) => (
                  <AppLogRow key={i} entry={e} />
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

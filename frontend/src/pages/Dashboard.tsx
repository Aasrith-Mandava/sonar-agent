import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/utils';
import { format, formatDistanceToNow } from 'date-fns';
import {
  GitBranch, Play, Plus, Loader2, CheckCircle, XCircle,
  Clock, Search, Wrench, Eye, FileText, X, Terminal,
  Pause, Square, RotateCcw, ChevronDown, ChevronRight,
  History, AlertTriangle, Zap, Copy, Check, Trash2, Edit3, Layers,
} from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface Repo   { id: string; name: string; github_url: string; branch: string; }
interface ScanRun {
  id: string; repo_id: string; status: string;
  total_issues: number; created_at: string; completed_at: string | null;
}
interface WsMessage {
  type: string; agent: string; action?: string; status?: string; message: string;
  tool?: string; tool_calls?: { name: string; args: Record<string, unknown> }[];
  elapsed_ms?: number; ts?: string;
}

interface IssueSummary {
  id: string; severity: string; type: string; rule_key: string;
  component: string; line: number | null; message: string | null;
}

interface FixItem {
  id: string;
  issue_id: string;
  scan_run_id: string;
  file_path: string;
  original_code: string;
  fixed_code: string;
  diff_patch: string;
  explanation: string | null;
  confidence_score: number | null;
  reviewer_summary: string | null;
  status: string;
  issue: IssueSummary | null;
}

interface ScanSummary {
  scan_id: string;
  status: string;
  total_issues: number;
  selected_for_fix: number;
  issues_in_db: number;
  fixes_generated: number;
  latest_error: string | null;
  stages: {
    stage: string; status: string; started_at: string | null;
    completed_at: string | null; error_message: string | null; details: string | null;
  }[];
}

const SEVERITY_COLOR: Record<string, string> = {
  BLOCKER:  'bg-red-100 text-red-800 border-red-300',
  CRITICAL: 'bg-orange-100 text-orange-800 border-orange-300',
  MAJOR:    'bg-amber-100 text-amber-800 border-amber-300',
  MINOR:    'bg-yellow-100 text-yellow-800 border-yellow-300',
  INFO:     'bg-blue-100 text-blue-800 border-blue-300',
};

// ─── Pipeline config ──────────────────────────────────────────────────────────

const STEPS = [
  { key: 'scanning',  label: 'Scan',   Icon: Search   },
  { key: 'fixing',    label: 'Fix',    Icon: Wrench   },
  { key: 'reviewing', label: 'Review', Icon: Eye      },
  { key: 'reporting', label: 'Report', Icon: FileText },
];

const STATUS_STEP: Record<string, number> = {
  pending: -1, resuming: -1, paused: -1,
  scanning: 0, analyzing: 0,
  fixing: 1, reviewing: 2, reporting: 3,
  completed: 4, failed: -2, stopped: -2, stopping: -2,
};

const STATUS_PCT: Record<string, number> = {
  pending: 4, resuming: 4, paused: 0,
  scanning: 22, analyzing: 28,
  fixing: 50, reviewing: 72, reporting: 88,
  completed: 100, failed: 0, stopped: 0, stopping: 0,
};

const TERMINAL = new Set(['completed', 'failed', 'stopped']);
const RUNNING  = new Set(['pending', 'scanning', 'analyzing', 'fixing', 'reviewing', 'reporting', 'resuming', 'stopping']);

// Agent badge styles
const AGENT_BADGE: Record<string, string> = {
  orchestrator: 'bg-slate-100 text-slate-700',
  scanner:      'bg-blue-100 text-blue-700',
  fixer:        'bg-orange-100 text-orange-700',
  reviewer:     'bg-purple-100 text-purple-700',
  reporter:     'bg-green-100 text-green-700',
  tools:        'bg-teal-100 text-teal-700',
  system:       'bg-muted text-muted-foreground',
};

// Log-type row styles
const LOG_TYPE_STYLE: Record<string, { bar: string; bg: string }> = {
  agent_start:          { bar: 'bg-blue-400',   bg: 'bg-blue-50/60' },
  tool_invocation_plan: { bar: 'bg-amber-400',  bg: 'bg-amber-50/60' },
  tool_result:          { bar: 'bg-teal-400',   bg: 'bg-teal-50/60' },
  agent_reasoning:      { bar: 'bg-violet-400', bg: 'bg-violet-50/60' },
  paused:               { bar: 'bg-yellow-400', bg: 'bg-yellow-50/60' },
  stopped:              { bar: 'bg-red-400',    bg: 'bg-red-50/60' },
  failed:               { bar: 'bg-red-400',    bg: 'bg-red-50/60' },
  complete:             { bar: 'bg-green-400',  bg: 'bg-green-50/60' },
  init:                 { bar: 'bg-slate-300',  bg: '' },
  log:                  { bar: 'bg-slate-200',  bg: '' },
};

const STATUS_BADGE: Record<string, string> = {
  completed: 'bg-green-50 text-green-700 border-green-200',
  failed:    'bg-destructive/10 text-destructive border-destructive/20',
  stopped:   'bg-orange-50 text-orange-700 border-orange-200',
  paused:    'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending:   'bg-muted text-muted-foreground border-border',
};

// ─── WebSocket hook ───────────────────────────────────────────────────────────

function useScanWs(scanId: string | null) {
  const [messages, setMessages] = useState<WsMessage[]>([]);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!scanId) return;
    setMessages([]);
    const base = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api')
      .replace(/^http/, 'ws').replace('/api', '');
    const ws = new WebSocket(`${base}/ws/pipeline/${scanId}`);
    ws.onopen  = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      try { setMessages((p) => [...p, JSON.parse(e.data)]); } catch {}
    };
    return () => ws.close();
  }, [scanId]);
  return { messages, connected };
}

// ─── Log row component ────────────────────────────────────────────────────────

function LogRow({ msg }: { msg: WsMessage }) {
  const [expanded, setExpanded] = useState(false);
  const typeKey = msg.action ?? msg.type ?? 'log';
  const style = LOG_TYPE_STYLE[typeKey] ?? LOG_TYPE_STYLE.log;
  const hasTools = msg.tool_calls && msg.tool_calls.length > 0;
  const isToolResult = msg.type === 'tool_result';
  const isExpandable = hasTools || msg.message.length > 140;

  return (
    <div className={cn('flex gap-0 border-b border-border/50 last:border-0', style.bg)}>
      {/* Type stripe */}
      <div className={cn('w-0.5 shrink-0', style.bar)} />

      <div className="flex-1 min-w-0 py-1.5 px-2">
        {/* Main row */}
        <button
          onClick={() => isExpandable && setExpanded((v) => !v)}
          className={cn('w-full flex items-start gap-2 text-left', isExpandable && 'cursor-pointer')}
        >
          {/* Timestamp */}
          {msg.ts && (
            <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums pt-0.5 w-[68px]">
              {format(new Date(msg.ts), 'HH:mm:ss.S')}
            </span>
          )}

          {/* Agent badge */}
          <span className={cn(
            'shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase',
            AGENT_BADGE[msg.agent] ?? 'bg-muted text-muted-foreground',
          )}>
            {msg.agent}
          </span>

          {/* Action label */}
          {(msg.action && msg.action !== 'log') && (
            <span className="shrink-0 text-[10px] text-muted-foreground capitalize font-medium whitespace-nowrap">
              {msg.action.replace(/_/g, ' ')}
            </span>
          )}

          {/* Tool name for tool results */}
          {isToolResult && msg.tool && (
            <span className="shrink-0 flex items-center gap-1 text-[10px] text-teal-700 font-semibold">
              <Zap className="h-2.5 w-2.5" />
              {msg.tool}
            </span>
          )}

          {/* Message */}
          <span className="flex-1 min-w-0 text-xs text-foreground break-words leading-relaxed">
            {expanded ? msg.message : msg.message.slice(0, 140)}
            {!expanded && msg.message.length > 140 && (
              <span className="text-muted-foreground">…</span>
            )}
          </span>

          {/* Elapsed + expand */}
          <div className="shrink-0 flex items-center gap-1">
            {msg.elapsed_ms != null && (
              <span className="text-[10px] text-muted-foreground">{msg.elapsed_ms}ms</span>
            )}
            {isExpandable && (
              <span className="text-muted-foreground">
                {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </span>
            )}
          </div>
        </button>

        {/* Expanded: tool calls */}
        {expanded && hasTools && (
          <div className="mt-1.5 ml-[84px] p-2 bg-amber-50 border border-amber-200 rounded-md">
            <p className="text-[10px] font-semibold text-amber-700 mb-1 uppercase">Tool Invocations</p>
            {msg.tool_calls!.map((tc, i) => (
              <div key={i} className="font-mono text-[11px] mb-0.5">
                <span className="font-bold text-amber-800">{tc.name}</span>
                <span className="text-amber-600 ml-1">{JSON.stringify(tc.args)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Copy button ──────────────────────────────────────────────────────────────

function CopyLogsButton({ messages }: { messages: WsMessage[] }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const text = messages.map((m) => `[${m.ts ?? ''}] [${m.agent}] ${m.message}`).join('\n');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button onClick={copy} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
      {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

// ─── Issues panel ─────────────────────────────────────────────────────────────

function IssuesPanel({ scanId }: { scanId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['scan-issues', scanId],
    queryFn: async () => {
      const { data } = await api.get(`/scans/${scanId}/issues?page_size=100`);
      return data as { items: IssueSummary[]; total: number };
    },
  });

  if (isLoading) return <div className="py-6 flex justify-center"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>;
  if (error) return <p className="text-xs text-destructive py-3">Failed to load issues.</p>;
  if (!data?.items || data.items.length === 0) {
    return <p className="text-xs text-muted-foreground py-3 text-center">No issues recorded for this scan.</p>;
  }

  return (
    <div className="flex flex-col divide-y max-h-[400px] overflow-y-auto">
      {data.items.map((issue) => (
        <div key={issue.id} className="py-2 px-2 flex items-start gap-2 hover:bg-muted/30 transition-colors">
          <span
            className={cn(
              'shrink-0 px-1.5 py-0.5 rounded border text-[10px] font-bold uppercase',
              SEVERITY_COLOR[issue.severity] ?? 'bg-muted',
            )}
          >
            {issue.severity}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-foreground font-medium truncate">
              {issue.message ?? issue.rule_key}
            </p>
            <p className="text-[10px] text-muted-foreground truncate font-mono">
              {issue.component}{issue.line ? `:${issue.line}` : ''} · {issue.rule_key}
            </p>
          </div>
          <span className="shrink-0 text-[10px] text-muted-foreground uppercase">{issue.type}</span>
        </div>
      ))}
      {data.total > data.items.length && (
        <p className="text-[10px] text-muted-foreground py-2 text-center">
          Showing first {data.items.length} of {data.total}
        </p>
      )}
    </div>
  );
}

// ─── Fixes panel ──────────────────────────────────────────────────────────────

function FixesPanel({ scanId, onApplied }: { scanId: string; onApplied?: () => void }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['scan-fixes', scanId],
    queryFn: async () => {
      const { data } = await api.get(`/scans/${scanId}/fixes`);
      return data as { items: FixItem[]; total: number };
    },
  });

  const applyMut = useMutation({
    mutationFn: async (opts: { push: boolean; pr: boolean }) => {
      const { data } = await api.post(`/scans/${scanId}/apply-fixes`, {
        push_to_github: opts.push,
        create_pr: opts.pr,
      });
      return data as {
        applied: number;
        branch: string;
        pr_url: string | null;
        pushed: boolean;
        pr_existed: boolean;
        pat_source: string | null;
        message: string;
      };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scan-fixes', scanId] });
      onApplied?.();
    },
  });

  if (isLoading) return <div className="py-6 flex justify-center"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>;
  if (error) return <p className="text-xs text-destructive py-3">Failed to load fixes.</p>;
  if (!data?.items || data.items.length === 0) {
    return <p className="text-xs text-muted-foreground py-3 text-center">No fixes generated for this scan.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Apply controls */}
      <div className="flex items-center gap-2 px-2 py-2 bg-muted/40 rounded-md border">
        <span className="text-[11px] text-muted-foreground flex-1">
          {data.total} fix(es) ready to apply
        </span>
        <button
          onClick={() => applyMut.mutate({ push: false, pr: false })}
          disabled={applyMut.isPending}
          className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium border rounded-md hover:bg-background disabled:opacity-50"
        >
          {applyMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wrench className="h-3 w-3" />}
          Apply locally
        </button>
        <button
          onClick={() => applyMut.mutate({ push: true, pr: true })}
          disabled={applyMut.isPending}
          className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
        >
          {applyMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
          Apply &amp; open PR
        </button>
      </div>

      {applyMut.isError && (
        <div className="text-[11px] text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-3 py-2 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold mb-0.5">Apply / PR creation failed</p>
            <pre className="font-mono text-[10px] whitespace-pre-wrap break-words leading-relaxed text-destructive/90">
              {(applyMut.error as any)?.response?.data?.detail || (applyMut.error as any)?.message || 'Apply failed.'}
            </pre>
          </div>
        </div>
      )}
      {applyMut.isSuccess && applyMut.data && (
        <div className="text-[11px] text-green-700 bg-green-50 border border-green-200 rounded-md px-3 py-2 flex items-start gap-2">
          <CheckCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold">
              Applied {applyMut.data.applied} fix(es)
              {applyMut.data.pr_existed ? ' — reused existing PR' : applyMut.data.pr_url ? ' — PR opened' : ''}
            </p>
            <p className="text-green-700/80 mt-0.5">
              Branch: <code className="bg-green-100 px-1 rounded font-mono">{applyMut.data.branch}</code>
              {applyMut.data.pat_source && <span className="ml-2 opacity-70">via {applyMut.data.pat_source}</span>}
            </p>
            {applyMut.data.pr_url && (
              <a
                href={applyMut.data.pr_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 mt-1 text-green-800 underline hover:text-green-900"
              >
                Open PR ↗
              </a>
            )}
          </div>
        </div>
      )}

      {/* Fix list */}
      <div className="flex flex-col divide-y border rounded-md max-h-[440px] overflow-y-auto">
        {data.items.map((fix) => {
          const isOpen = expanded === fix.id;
          const conf = fix.confidence_score ?? 0;
          const confColor =
            conf >= 80 ? 'bg-green-100 text-green-800 border-green-300'
              : conf >= 60 ? 'bg-yellow-100 text-yellow-800 border-yellow-300'
              : 'bg-red-100 text-red-800 border-red-300';

          return (
            <div key={fix.id} className="text-xs">
              <button
                onClick={() => setExpanded(isOpen ? null : fix.id)}
                className="w-full flex items-start gap-2 px-2 py-2 text-left hover:bg-muted/30 transition-colors"
              >
                {isOpen ? <ChevronDown className="h-3 w-3 mt-0.5 text-muted-foreground shrink-0" />
                  : <ChevronRight className="h-3 w-3 mt-0.5 text-muted-foreground shrink-0" />}

                {fix.issue && (
                  <span
                    className={cn(
                      'shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase',
                      SEVERITY_COLOR[fix.issue.severity] ?? 'bg-muted',
                    )}
                  >
                    {fix.issue.severity}
                  </span>
                )}

                <div className="min-w-0 flex-1">
                  <p className="text-foreground font-medium truncate">{fix.file_path}</p>
                  <p className="text-[10px] text-muted-foreground truncate">
                    {fix.explanation || fix.issue?.message || '(no explanation)'}
                  </p>
                </div>

                <span className={cn('shrink-0 px-1.5 py-0.5 rounded border text-[10px] font-bold', confColor)}>
                  {conf}
                </span>
                <span
                  className={cn(
                    'shrink-0 px-1.5 py-0.5 rounded text-[10px]',
                    fix.status === 'applied' ? 'bg-green-50 text-green-700 border border-green-200'
                      : 'bg-muted text-muted-foreground',
                  )}
                >
                  {fix.status}
                </span>
              </button>

              {isOpen && (
                <div className="px-3 pb-3 flex flex-col gap-2">
                  {fix.reviewer_summary && (
                    <div className="text-[10px] text-muted-foreground italic border-l-2 border-purple-300 pl-2">
                      Reviewer: {fix.reviewer_summary}
                    </div>
                  )}
                  <pre className="bg-slate-950 text-slate-100 text-[10px] font-mono p-2 rounded-md overflow-x-auto max-h-[300px] leading-relaxed">
                    {fix.diff_patch.split('\n').map((line, i) => {
                      const cls =
                        line.startsWith('+') && !line.startsWith('+++') ? 'text-green-400'
                          : line.startsWith('-') && !line.startsWith('---') ? 'text-red-400'
                          : line.startsWith('@@') ? 'text-blue-400'
                          : 'text-slate-300';
                      return <div key={i} className={cls}>{line || ' '}</div>;
                    })}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Scan Progress Modal ──────────────────────────────────────────────────────

type TabKey = 'log' | 'issues' | 'fixes';

function ScanProgressModal({
  scanId, repoName, onClose, readOnly = false,
}: {
  scanId: string; repoName: string; onClose: () => void; readOnly?: boolean;
}) {
  const { messages: wsMessages, connected } = useScanWs(readOnly ? null : scanId);
  const logEndRef    = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string>('pending');
  const [tab, setTab] = useState<TabKey>('log');
  const queryClient  = useQueryClient();

  // Scan summary (status, counts, latest error, stages)
  const { data: summary } = useQuery({
    queryKey: ['scan-summary', scanId],
    queryFn: async () => {
      const { data } = await api.get(`/scans/${scanId}/summary`);
      return data as ScanSummary;
    },
    refetchInterval: TERMINAL.has(status) ? false : 3000,
  });

  // Poll scan status
  const { data: scanData } = useQuery({
    queryKey: ['scan-detail', scanId],
    queryFn: async () => {
      const { data } = await api.get(`/scans/${scanId}`);
      return data as ScanRun;
    },
    refetchInterval: TERMINAL.has(status) ? false : 2000,
  });

  // Fetch logs for read-only (historical) mode
  const { data: historyLogs } = useQuery({
    queryKey: ['scan-logs', scanId],
    queryFn: async () => {
      const { data } = await api.get(`/observability/logs?scan_run_id=${scanId}&page_size=200`);
      return (data.items ?? []) as any[];
    },
    enabled: readOnly,
  });

  useEffect(() => {
    if (scanData?.status) setStatus(scanData.status);
  }, [scanData?.status]);

  useEffect(() => {
    const last = [...wsMessages].reverse().find((m) => m.status);
    if (last?.status) setStatus(last.status);
  }, [wsMessages]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [wsMessages]);

  const activeStep = STATUS_STEP[status] ?? -1;
  const pct        = STATUS_PCT[status]  ?? 4;
  const isDone     = status === 'completed';
  const isFailed   = status === 'failed';
  const isStopped  = status === 'stopped';
  const isPaused   = status === 'paused';
  const isStopping = status === 'stopping';
  const isRunning  = RUNNING.has(status);
  const canClose   = TERMINAL.has(status) || readOnly;

  // Control mutations
  const pauseMutation = useMutation({
    mutationFn: () => api.post(`/scans/${scanId}/pause`),
    onSuccess: () => { setStatus('paused'); queryClient.invalidateQueries({ queryKey: ['scan-detail', scanId] }); },
  });
  const resumeMutation = useMutation({
    mutationFn: () => api.post(`/scans/${scanId}/resume`),
    onSuccess: () => { setStatus('resuming'); queryClient.invalidateQueries({ queryKey: ['scan-detail', scanId] }); },
  });
  const stopMutation = useMutation({
    mutationFn: () => api.post(`/scans/${scanId}/stop`),
    onSuccess: () => { setStatus('stopping'); queryClient.invalidateQueries({ queryKey: ['scan-detail', scanId] }); },
  });

  // Build display logs
  const displayLogs: WsMessage[] = readOnly
    ? (historyLogs ?? []).map((l: any) => ({
        type:    l.action ?? 'log',
        agent:   l.agent_name,
        action:  l.action,
        message: l.output_summary ?? l.input_summary ?? '—',
        ts:      l.created_at,
      }))
    : wsMessages;

  const statusLabel = {
    completed: 'Completed', failed: 'Failed', stopped: 'Stopped by user',
    paused: 'Paused — waiting for resume', stopping: 'Stopping…', resuming: 'Resuming…',
  }[status] ?? `Running — ${status}…`;

  const headerIcon = isDone
    ? <CheckCircle className="h-5 w-5 text-green-500" />
    : isFailed || isStopped
    ? <XCircle className="h-5 w-5 text-destructive" />
    : isPaused
    ? <Pause className="h-5 w-5 text-yellow-500" />
    : <Loader2 className="h-5 w-5 text-primary animate-spin" />;

  // Scan meta
  const duration = scanData?.completed_at
    ? Math.round((new Date(scanData.completed_at).getTime() - new Date(scanData.created_at).getTime()) / 1000)
    : null;

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-card border rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col overflow-hidden max-h-[90vh]">

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-4 border-b bg-card shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            {headerIcon}
            <div className="min-w-0">
              <p className="font-semibold text-foreground truncate">{repoName}</p>
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-xs text-muted-foreground">{statusLabel}</p>
                {!readOnly && !TERMINAL.has(status) && (
                  <span className={cn(
                    'flex items-center gap-1 text-[10px] font-medium',
                    connected ? 'text-green-600' : 'text-muted-foreground',
                  )}>
                    <span className={cn('w-1.5 h-1.5 rounded-full', connected ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground')} />
                    {connected ? 'Live' : 'Connecting…'}
                  </span>
                )}
                {duration != null && (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <Clock className="h-3 w-3" />{duration}s
                  </span>
                )}
                {isDone && scanData?.total_issues != null && (
                  <span className="text-[10px] font-medium text-foreground">
                    {scanData.total_issues} issues found
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {!readOnly && (
              <>
                {isRunning && !isStopping && (
                  <button
                    onClick={() => pauseMutation.mutate()}
                    disabled={pauseMutation.isPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border rounded-md hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    {pauseMutation.isPending
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Pause className="h-3.5 w-3.5" />}
                    Pause
                  </button>
                )}
                {isPaused && (
                  <button
                    onClick={() => resumeMutation.mutate()}
                    disabled={resumeMutation.isPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
                  >
                    {resumeMutation.isPending
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <RotateCcw className="h-3.5 w-3.5" />}
                    Resume
                  </button>
                )}
                {(isRunning || isPaused) && !isStopping && (
                  <button
                    onClick={() => stopMutation.mutate()}
                    disabled={stopMutation.isPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-destructive/40 text-destructive rounded-md hover:bg-destructive/10 disabled:opacity-50"
                  >
                    {stopMutation.isPending
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Square className="h-3.5 w-3.5" />}
                    Stop
                  </button>
                )}
              </>
            )}
            {/* Always-available close: terminal scans → plain close,
                running scans → "minimize" (pipeline keeps running in the background) */}
            <button
              onClick={() => {
                if (!canClose) {
                  const ok = window.confirm(
                    'The scan will keep running in the background. You can re-open it from the scan history list. Minimize?',
                  );
                  if (!ok) return;
                }
                onClose();
              }}
              title={canClose ? 'Close' : 'Minimize (scan keeps running)'}
              className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* ── Steps ── */}
        <div className="px-5 pt-4 pb-2 flex flex-col gap-3 shrink-0 border-b">
          <div className="flex items-center">
            {STEPS.map(({ key, label, Icon }, i) => {
              const done   = (activeStep > i && !isFailed && !isStopped) || isDone;
              const active = activeStep === i && (isRunning || isPaused);
              const err    = (isFailed || isStopped) && activeStep === i;

              return (
                <div key={key} className="flex items-center flex-1 min-w-0">
                  <div className="flex flex-col items-center gap-1 flex-none w-[60px]">
                    <div className={cn(
                      'w-8 h-8 rounded-full flex items-center justify-center border-2 transition-all duration-300',
                      done   && 'border-green-500 bg-green-50 text-green-600',
                      active && !isPaused && 'border-primary bg-primary/10 text-primary',
                      active && isPaused  && 'border-yellow-400 bg-yellow-50 text-yellow-600',
                      err    && 'border-destructive bg-destructive/10 text-destructive',
                      !done && !active && !err && 'border-border bg-muted text-muted-foreground',
                    )}>
                      {active && !isPaused ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : active && isPaused ? <Pause className="h-3.5 w-3.5" />
                        : done ? <CheckCircle className="h-3.5 w-3.5" />
                        : err  ? <XCircle className="h-3.5 w-3.5" />
                        : <Icon className="h-3.5 w-3.5" />}
                    </div>
                    <span className={cn(
                      'text-[10px] font-medium',
                      done && 'text-green-600',
                      active && !isPaused && 'text-primary',
                      active && isPaused  && 'text-yellow-600',
                      err && 'text-destructive',
                      !done && !active && !err && 'text-muted-foreground',
                    )}>{label}</span>
                  </div>
                  {i < STEPS.length - 1 && (
                    <div className={cn(
                      'flex-1 h-0.5 rounded-full transition-all duration-500 mx-1 mb-4',
                      done ? 'bg-green-400' : 'bg-border',
                    )} />
                  )}
                </div>
              );
            })}
          </div>

          {/* Progress bar */}
          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-700',
                isFailed || isStopped ? 'bg-destructive'
                  : isPaused ? 'bg-yellow-400'
                  : 'bg-primary',
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>{statusLabel}</span>
            <span>{pct}%</span>
          </div>
        </div>

        {/* ── Error banner ── */}
        {summary?.latest_error && (isFailed || isStopped) && (
          <div className="mx-4 mt-3 px-3 py-2 bg-destructive/10 border border-destructive/30 rounded-md text-xs text-destructive flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold mb-0.5">Pipeline error</p>
              <p className="font-mono text-[11px] break-words whitespace-pre-wrap">
                {summary.latest_error}
              </p>
            </div>
          </div>
        )}

        {/* ── Tab switcher ── */}
        <div className="mx-4 mt-3 flex items-center gap-1 border-b">
          {([
            { key: 'log',    label: 'Live Log',  Icon: Terminal },
            { key: 'issues', label: `Issues${summary ? ` (${summary.issues_in_db})` : ''}`, Icon: Search },
            { key: 'fixes',  label: `Fixes${summary ? ` (${summary.fixes_generated})` : ''}`, Icon: Wrench },
          ] as { key: TabKey; label: string; Icon: any }[]).map(({ key, label, Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors',
                tab === key
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3 w-3" /> {label}
            </button>
          ))}
        </div>

        {/* ── Tab content ── */}
        <div className="flex flex-col flex-1 min-h-0 mx-4 mb-4 mt-2 border rounded-xl overflow-hidden">
          {tab === 'log' && (
            <>
              <div className="flex items-center gap-2 px-3 py-2 bg-muted/50 border-b shrink-0">
                <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">
                  {readOnly ? 'Agent Log (historical)' : 'Live Agent Log'}
                </span>
                <div className="flex items-center gap-2 ml-2">
                  {[
                    { label: 'Reasoning', color: 'bg-violet-400' },
                    { label: 'Tool', color: 'bg-amber-400' },
                    { label: 'Result', color: 'bg-teal-400' },
                  ].map(({ label, color }) => (
                    <span key={label} className="flex items-center gap-1 text-[10px] text-muted-foreground">
                      <span className={cn('w-2 h-2 rounded-sm', color)} />{label}
                    </span>
                  ))}
                </div>
                {displayLogs.length > 0 && (
                  <span className="ml-auto flex items-center gap-2">
                    <CopyLogsButton messages={displayLogs} />
                    <span className="text-[10px] text-muted-foreground">{displayLogs.length} events</span>
                  </span>
                )}
                {isRunning && !readOnly && (
                  <span className="flex items-center gap-1 text-[10px] text-primary">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                    Live
                  </span>
                )}
              </div>
              <div className="flex-1 overflow-y-auto bg-background">
                {displayLogs.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
                    <Terminal className="h-6 w-6 opacity-30" />
                    <span className="text-xs">
                      {readOnly ? 'No logs recorded for this scan.' : 'Waiting for pipeline events…'}
                    </span>
                  </div>
                ) : (
                  <>
                    {displayLogs.map((msg, i) => <LogRow key={i} msg={msg} />)}
                    <div ref={logEndRef} />
                  </>
                )}
              </div>
            </>
          )}

          {tab === 'issues' && (
            <div className="p-3 overflow-y-auto bg-background">
              <IssuesPanel scanId={scanId} />
            </div>
          )}

          {tab === 'fixes' && (
            <div className="p-3 overflow-y-auto bg-background">
              <FixesPanel
                scanId={scanId}
                onApplied={() => queryClient.invalidateQueries({ queryKey: ['scan-summary', scanId] })}
              />
            </div>
          )}
        </div>

        {/* Scan ID footer */}
        <div className="px-5 pb-3 shrink-0 text-[10px] text-muted-foreground flex items-center gap-1">
          <span>Scan ID:</span>
          <code className="bg-muted px-1 py-0.5 rounded">{scanId}</code>
        </div>
      </div>
    </div>
  );
}

// ─── Scan History Panel ───────────────────────────────────────────────────────

function ScanHistoryPanel({
  repoId, onViewScan,
}: {
  repoId: string; onViewScan: (scan: ScanRun) => void;
}) {
  const { data: scans = [], isLoading } = useQuery({
    queryKey: ['scan-history', repoId],
    queryFn: async () => {
      const { data } = await api.get(`/scans/repos/${repoId}/scan-history`);
      return data as ScanRun[];
    },
    refetchInterval: 8000,
  });

  if (isLoading) return <div className="py-3 flex justify-center"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>;
  if (scans.length === 0) return <p className="text-xs text-muted-foreground py-3 text-center">No scan history yet.</p>;

  return (
    <div className="flex flex-col divide-y">
      {scans.slice(0, 6).map((scan) => {
        const isActive = RUNNING.has(scan.status) || scan.status === 'paused';
        const badgeCls = STATUS_BADGE[scan.status] ?? 'bg-primary/10 text-primary border-primary/20';
        const duration = scan.completed_at
          ? `${Math.round((new Date(scan.completed_at).getTime() - new Date(scan.created_at).getTime()) / 1000)}s`
          : null;

        return (
          <div
            key={scan.id}
            onClick={() => onViewScan(scan)}
            className="flex items-center justify-between py-2.5 px-1 gap-3 hover:bg-muted/30 rounded transition-colors cursor-pointer"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className={cn('px-2 py-0.5 rounded border text-[11px] font-medium capitalize whitespace-nowrap', badgeCls)}>
                {isActive
                  ? <span className="flex items-center gap-1"><Loader2 className="h-2.5 w-2.5 animate-spin" />{scan.status}</span>
                  : scan.status}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {formatDistanceToNow(new Date(scan.created_at), { addSuffix: true })}
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
              {scan.status === 'completed' && (
                <span className="font-medium text-foreground">{scan.total_issues ?? 0} issues</span>
              )}
              {duration && <span>{duration}</span>}
              <ScanRowActions scan={scan} repoId={repoId} onChanged={() => { /* react-query will refetch */ }} />
              <ChevronRight className="h-3.5 w-3.5" />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Add Repo Modal ───────────────────────────────────────────────────────────

function AddRepoModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName]           = useState('');
  const [githubUrl, setGithubUrl] = useState('');
  const [pat, setPat]             = useState('');
  const [branch, setBranch]       = useState('main');
  const [error, setError]         = useState('');
  const [loading, setLoading]     = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await api.post('/repos', { name, github_url: githubUrl, pat: pat || undefined, branch });
      onCreated();
      onClose();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to add repository.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-card border rounded-xl shadow-lg p-6 w-full max-w-md flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-foreground">Add Repository</h2>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {[
            { label: 'Name',       value: name,      set: setName,      ph: 'my-project',                  req: true  },
            { label: 'GitHub URL', value: githubUrl, set: setGithubUrl, ph: 'https://github.com/org/repo', req: true  },
            { label: 'Branch',     value: branch,    set: setBranch,    ph: 'main',                        req: false },
            { label: 'Personal Access Token (optional)', value: pat, set: setPat, ph: 'ghp_…',             req: false },
          ].map(({ label, value, set, ph, req }) => (
            <div key={label} className="flex flex-col gap-1">
              <label className="text-sm font-medium text-foreground">{label}</label>
              <input
                type={label.includes('Token') ? 'password' : 'text'}
                value={value} onChange={(e) => set(e.target.value)}
                required={req} placeholder={ph}
                className="px-3 py-2 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          ))}
          {error && (
            <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-3 py-2">
              {error}
            </div>
          )}
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose} className="flex-1 border rounded-md py-2 text-sm text-foreground hover:bg-muted transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={loading} className="flex-1 bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50">
              {loading ? 'Adding…' : 'Add Repository'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Edit Repo Modal ──────────────────────────────────────────────────────────

function EditRepoModal({
  repo, onClose, onSaved,
}: {
  repo: Repo;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(repo.name);
  const [branch, setBranch] = useState(repo.branch);
  const [pat, setPat] = useState('');
  const [showPat, setShowPat] = useState(false);

  const updateMut = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      if (name && name !== repo.name) body.name = name;
      if (branch && branch !== repo.branch) body.branch = branch;
      if (pat) body.pat = pat;
      const { data } = await api.put(`/repos/${repo.id}`, body);
      return data;
    },
    onSuccess: onSaved,
  });

  const hasChanges = (name && name !== repo.name) || (branch && branch !== repo.branch) || pat.length > 0;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-card border rounded-xl shadow-2xl w-full max-w-md p-5 flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-full bg-primary/10 p-2 shrink-0">
            <Edit3 className="h-5 w-5 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold text-foreground">Edit repository</h3>
            <p className="text-xs text-muted-foreground mt-1 truncate">{repo.github_url}</p>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">Display name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-foreground block mb-1">Branch</label>
            <input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              placeholder="main"
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none"
            />
            <p className="text-[10px] text-muted-foreground mt-1">
              Changing the branch will cause the next scan to clone a fresh copy from origin.
            </p>
          </div>

          <div>
            <label className="text-xs font-medium text-foreground block mb-1">Personal Access Token (PAT)</label>
            <div className="relative">
              <input
                type={showPat ? 'text' : 'password'}
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder="Paste a new PAT to replace the existing one"
                className="w-full px-3 py-2 pr-16 text-sm border rounded-md bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none font-mono"
              />
              <button
                type="button"
                onClick={() => setShowPat((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground hover:text-foreground"
              >
                {showPat ? 'Hide' : 'Show'}
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              Leave empty to keep the existing PAT. The new value will be used on the next scan automatically — no re-clone needed.
            </p>
          </div>
        </div>

        {updateMut.isError && (
          <div className="text-[11px] text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-2 py-1.5">
            {(updateMut.error as any)?.response?.data?.detail || 'Update failed.'}
          </div>
        )}
        {updateMut.isSuccess && (
          <div className="text-[11px] text-green-700 bg-green-50 border border-green-200 rounded-md px-2 py-1.5 flex items-center gap-1.5">
            <CheckCircle className="h-3 w-3" /> Saved. Re-trigger a scan to pick up the new settings.
          </div>
        )}

        <div className="flex gap-2 mt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={updateMut.isPending}
            className="flex-1 border rounded-md py-2 text-sm text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => updateMut.mutate()}
            disabled={!hasChanges || updateMut.isPending}
            className="flex-1 bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
          >
            {updateMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Save changes
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Delete Repo Modal ────────────────────────────────────────────────────────

function DeleteRepoModal({
  repo, onClose, onDeleted,
}: {
  repo: Repo;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [deleteSonar, setDeleteSonar] = useState(false);
  const [deleteClone, setDeleteClone] = useState(true);
  const [confirmText, setConfirmText] = useState('');

  const deleteMut = useMutation({
    mutationFn: async () => {
      const params = new URLSearchParams({
        delete_sonar_project: String(deleteSonar),
        delete_local_clone:   String(deleteClone),
      });
      const { data } = await api.delete(`/repos/${repo.id}?${params.toString()}`);
      return data;
    },
    onSuccess: onDeleted,
  });

  const canDelete = confirmText === repo.name && !deleteMut.isPending;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-card border rounded-xl shadow-2xl w-full max-w-md p-5 flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-full bg-destructive/10 p-2 shrink-0">
            <AlertTriangle className="h-5 w-5 text-destructive" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold text-foreground">Delete repository?</h3>
            <p className="text-xs text-muted-foreground mt-1">
              This will permanently delete <strong className="text-foreground">{repo.name}</strong>{' '}
              and all of its scan history, issues, and fixes from the SonarAgent database.
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-2 text-xs">
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={deleteSonar}
              onChange={(e) => setDeleteSonar(e.target.checked)}
              className="mt-0.5 rounded"
            />
            <span>
              <strong>Also delete the SonarQube project</strong> (and all its scan history on the SonarQube server).
              Requires "Administer" permission on the SonarQube project.
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={deleteClone}
              onChange={(e) => setDeleteClone(e.target.checked)}
              className="mt-0.5 rounded"
            />
            <span>Remove the local clone directory under <code className="bg-muted px-1 rounded text-[10px]">backend/repos/{repo.id}</code></span>
          </label>
        </div>

        <div>
          <label className="text-xs font-medium text-foreground block mb-1">
            Type <code className="bg-muted px-1 rounded">{repo.name}</code> to confirm
          </label>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:ring-2 focus:ring-destructive/30 focus:outline-none"
            placeholder={repo.name}
          />
        </div>

        {deleteMut.isError && (
          <div className="text-[11px] text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-2 py-1.5">
            {(deleteMut.error as any)?.response?.data?.detail || 'Delete failed.'}
          </div>
        )}
        {deleteMut.isSuccess && deleteMut.data && (
          <div className="text-[11px] text-green-700 bg-green-50 border border-green-200 rounded-md px-2 py-1.5">
            Deleted. SonarQube cleanup: {deleteMut.data.sonar_deleted ? 'success' : (deleteSonar ? 'failed/skipped' : 'not requested')}.
          </div>
        )}

        <div className="flex gap-2 mt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={deleteMut.isPending}
            className="flex-1 border rounded-md py-2 text-sm text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => deleteMut.mutate()}
            disabled={!canDelete}
            className="flex-1 bg-destructive text-destructive-foreground rounded-md py-2 text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
          >
            {deleteMut.isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Trash2 className="h-4 w-4" />}
            Delete repository
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Scan-row actions (delete + retry + per-stage rerun) ─────────────────────

const STAGE_LABELS: { key: string; label: string; description: string }[] = [
  { key: 'clone',  label: 'Clone',  description: 'Re-clone the repo from GitHub (uses the latest PAT)' },
  { key: 'scan',   label: 'Scan',   description: 'Re-run SonarQube scan (clears all issues + fixes)' },
  { key: 'fix',    label: 'Fix',    description: 'Regenerate fixes for existing issues (clears existing fixes)' },
  { key: 'review', label: 'Review', description: 'Re-review existing fixes (overwrites confidence scores)' },
  { key: 'report', label: 'Report', description: 'Re-generate the delta report' },
];

function ScanRowActions({
  scan, repoId, onChanged,
}: {
  scan: ScanRun;
  repoId: string;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [showStageMenu, setShowStageMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the menu on outside click
  useEffect(() => {
    if (!showStageMenu) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowStageMenu(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [showStageMenu]);

  const deleteMut = useMutation({
    mutationFn: async () => api.delete(`/scans/${scan.id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scan-history', repoId] });
      queryClient.invalidateQueries({ queryKey: ['latest-scan', repoId] });
      onChanged();
    },
  });

  const retryMut = useMutation({
    mutationFn: async (fromStage?: string) => {
      const url = fromStage
        ? `/scans/${scan.id}/retry?from_stage=${encodeURIComponent(fromStage)}`
        : `/scans/${scan.id}/retry`;
      const { data } = await api.post(url);
      return data as ScanRun;
    },
    onSuccess: () => {
      setShowStageMenu(false);
      queryClient.invalidateQueries({ queryKey: ['scan-history', repoId] });
      queryClient.invalidateQueries({ queryKey: ['latest-scan', repoId] });
      onChanged();
    },
  });

  const isFailed = scan.status === 'failed' || scan.status === 'stopped';
  const isRunning = RUNNING.has(scan.status);
  const isCompleted = scan.status === 'completed';
  const canRerun = !isRunning;  // failed, stopped, or completed

  return (
    <div className="flex items-center gap-1 shrink-0 relative" onClick={(e) => e.stopPropagation()}>
      {/* Quick retry — only for failed/stopped, resumes from failed stage */}
      {isFailed && (
        <button
          onClick={() => retryMut.mutate(undefined)}
          disabled={retryMut.isPending}
          title="Retry from the failed stage"
          className="p-1 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
        >
          {retryMut.isPending && retryMut.variables === undefined
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <RotateCcw className="h-3.5 w-3.5" />}
        </button>
      )}

      {/* Re-run from stage — for any non-running scan */}
      {canRerun && (
        <div ref={menuRef} className="relative">
          <button
            onClick={() => setShowStageMenu((v) => !v)}
            disabled={retryMut.isPending}
            title="Re-run a specific stage"
            className={cn(
              'p-1 rounded transition-colors disabled:opacity-50',
              showStageMenu
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground hover:text-primary hover:bg-primary/10',
            )}
          >
            <Layers className="h-3.5 w-3.5" />
          </button>

          {showStageMenu && (
            <div className="absolute right-0 top-full mt-1 z-50 w-72 bg-card border rounded-lg shadow-xl py-1.5 text-left">
              <div className="px-3 py-1.5 border-b">
                <p className="text-[11px] font-semibold text-foreground">Re-run from stage</p>
                <p className="text-[10px] text-muted-foreground">
                  {isCompleted
                    ? 'Re-runs the chosen stage AND every downstream stage. Earlier stages stay cached.'
                    : 'Picks up from the chosen stage. Use this to skip past expensive earlier stages.'}
                </p>
              </div>
              {STAGE_LABELS.map(({ key, label, description }) => (
                <button
                  key={key}
                  onClick={() => retryMut.mutate(key)}
                  disabled={retryMut.isPending}
                  className="w-full px-3 py-1.5 text-left hover:bg-muted/50 transition-colors disabled:opacity-50 flex items-start gap-2"
                >
                  {retryMut.isPending && retryMut.variables === key
                    ? <Loader2 className="h-3 w-3 animate-spin mt-0.5 text-primary shrink-0" />
                    : <Play className="h-3 w-3 mt-0.5 text-primary shrink-0" />}
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px] font-semibold text-foreground capitalize">{label}</p>
                    <p className="text-[10px] text-muted-foreground leading-tight">{description}</p>
                  </div>
                </button>
              ))}
              {retryMut.isError && (
                <div className="mx-2 my-1 text-[10px] text-destructive bg-destructive/10 border border-destructive/20 rounded px-2 py-1">
                  {(retryMut.error as any)?.response?.data?.detail || 'Re-run failed.'}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <button
        onClick={() => {
          const msg = isRunning
            ? 'This scan is currently running. Deleting will stop the pipeline first. Continue?'
            : 'Delete this scan run and all of its issues, fixes, and pipeline history?';
          if (window.confirm(msg)) deleteMut.mutate();
        }}
        disabled={deleteMut.isPending}
        title="Delete this scan run"
        className="p-1 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50"
      >
        {deleteMut.isPending
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <Trash2 className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

// ─── Repo Card ────────────────────────────────────────────────────────────────

function RepoCard({
  repo, onScanTriggered, onViewScan,
}: {
  repo: Repo;
  onScanTriggered: (scanId: string, repoName: string) => void;
  onViewScan: (scan: ScanRun, repoName: string) => void;
}) {
  const queryClient  = useQueryClient();
  const [showHistory, setShowHistory] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [showEdit, setShowEdit] = useState(false);

  const { data: latestScan } = useQuery({
    queryKey: ['latest-scan', repo.id],
    queryFn: async () => {
      const { data } = await api.get(`/scans/repos/${repo.id}/scan-history`);
      return (data as ScanRun[])[0] ?? null;
    },
    refetchInterval: 5000,
  });

  const isRunning = latestScan && RUNNING.has(latestScan.status);
  const isPaused  = latestScan?.status === 'paused';

  const triggerScan = useMutation({
    mutationFn: async () => {
      const { data } = await api.post(`/scans/repos/${repo.id}/scan`);
      return data as ScanRun;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['latest-scan', repo.id] });
      queryClient.invalidateQueries({ queryKey: ['scan-history', repo.id] });
      onScanTriggered(data.id, repo.name);
    },
  });

  const statusBadgeCls = latestScan
    ? STATUS_BADGE[latestScan.status] ?? 'bg-primary/10 text-primary border-primary/20'
    : '';

  return (
    <div className="border rounded-xl bg-card flex flex-col hover:shadow-sm transition-shadow">
      <div className="p-5 flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <GitBranch className="h-5 w-5 text-primary shrink-0" />
            <div className="min-w-0">
              <h3 className="font-semibold text-foreground truncate">{repo.name}</h3>
              <p className="text-xs text-muted-foreground truncate">{repo.github_url}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-xs bg-secondary text-secondary-foreground px-2 py-0.5 rounded-full">
              {repo.branch}
            </span>
            <button
              onClick={() => setShowEdit(true)}
              title="Edit repository (name / branch / PAT)"
              className="p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
            >
              <Edit3 className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setShowDelete(true)}
              title="Delete repository"
              className="p-1 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {showEdit && (
          <EditRepoModal
            repo={repo}
            onClose={() => setShowEdit(false)}
            onSaved={() => {
              queryClient.invalidateQueries({ queryKey: ['repos'] });
            }}
          />
        )}

        {showDelete && (
          <DeleteRepoModal
            repo={repo}
            onClose={() => setShowDelete(false)}
            onDeleted={() => {
              setShowDelete(false);
              queryClient.invalidateQueries({ queryKey: ['repos'] });
            }}
          />
        )}

        {/* Latest scan status badge */}
        {latestScan && (
          <button
            onClick={() => onViewScan(latestScan, repo.name)}
            className={cn(
              'flex items-center justify-between text-xs font-medium px-3 py-1.5 rounded-md border w-full text-left hover:opacity-80 transition-opacity',
              statusBadgeCls,
            )}
          >
            <span className="flex items-center gap-1.5">
              {isRunning && !isPaused && <Loader2 className="h-3 w-3 animate-spin" />}
              {isPaused && <Pause className="h-3 w-3" />}
              <span className="capitalize">{latestScan.status}</span>
            </span>
            <span className="flex items-center gap-2">
              {latestScan.status === 'completed' && <span>{latestScan.total_issues ?? 0} issues</span>}
              <ChevronRight className="h-3 w-3" />
            </span>
          </button>
        )}

        {/* Error */}
        {triggerScan.error && (
          <p className="text-xs text-destructive">
            {(triggerScan.error as any).response?.data?.detail || 'Failed to trigger scan'}
          </p>
        )}

        {/* Trigger scan button */}
        <button
          onClick={() => triggerScan.mutate()}
          disabled={!!isRunning || !!isPaused || triggerScan.isPending}
          className={cn(
            'flex items-center justify-center gap-2 rounded-md py-2 text-sm font-medium transition-all',
            (isRunning || isPaused || triggerScan.isPending)
              ? 'bg-muted text-muted-foreground cursor-not-allowed'
              : 'bg-primary text-primary-foreground hover:opacity-90',
          )}
        >
          {triggerScan.isPending    ? <><Loader2 className="h-4 w-4 animate-spin" />Starting…</>
           : isRunning              ? <><Loader2 className="h-4 w-4 animate-spin" />Running…</>
           : isPaused               ? <><Pause className="h-4 w-4" />Paused</>
           :                          <><Play className="h-4 w-4" />Trigger Scan</>}
        </button>
      </div>

      {/* Scan history toggle */}
      <div className="border-t">
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="w-full flex items-center justify-between px-5 py-2.5 text-xs text-muted-foreground hover:bg-muted/30 transition-colors"
        >
          <span className="flex items-center gap-1.5">
            <History className="h-3.5 w-3.5" /> Scan History
          </span>
          {showHistory ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
        {showHistory && (
          <div className="px-4 pb-3">
            <ScanHistoryPanel
              repoId={repo.id}
              onViewScan={(scan) => onViewScan(scan, repo.name)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [showAddModal, setShowAddModal] = useState(false);
  const [activeModal, setActiveModal]   = useState<{ scanId: string; repoName: string; readOnly: boolean } | null>(null);
  const queryClient = useQueryClient();

  const { data: repos, isLoading, error } = useQuery({
    queryKey: ['repos'],
    queryFn: async () => {
      const { data } = await api.get('/repos');
      return data as Repo[];
    },
  });

  return (
    <div className="p-8 max-w-7xl mx-auto flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage repositories and run autonomous multi-agent code quality scans.
          </p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:opacity-90 transition-opacity"
        >
          <Plus className="h-4 w-4" /> Add Repository
        </button>
      </div>

      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-xl px-4 py-3 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" /> Failed to load repositories.
        </div>
      )}

      {!isLoading && repos?.length === 0 && (
        <div className="text-center py-16 border rounded-xl bg-card text-muted-foreground">
          <GitBranch className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="font-medium">No repositories added yet.</p>
          <p className="text-sm mt-1">Click "Add Repository" to get started.</p>
        </div>
      )}

      {repos && repos.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {repos.map((repo) => (
            <RepoCard
              key={repo.id}
              repo={repo}
              onScanTriggered={(id, name) => setActiveModal({ scanId: id, repoName: name, readOnly: false })}
              onViewScan={(scan, name) => setActiveModal({ scanId: scan.id, repoName: name, readOnly: TERMINAL.has(scan.status) })}
            />
          ))}
        </div>
      )}

      {activeModal && (
        <ScanProgressModal
          scanId={activeModal.scanId}
          repoName={activeModal.repoName}
          readOnly={activeModal.readOnly}
          onClose={() => {
            setActiveModal(null);
            queryClient.invalidateQueries({ queryKey: ['repos'] });
          }}
        />
      )}

      {showAddModal && (
        <AddRepoModal
          onClose={() => setShowAddModal(false)}
          onCreated={() => queryClient.invalidateQueries({ queryKey: ['repos'] })}
        />
      )}
    </div>
  );
}

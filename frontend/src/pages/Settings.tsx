import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/utils';
import {
  Settings2, Cpu, RefreshCw, CheckCircle, XCircle,
  ChevronDown, Save, Loader2, Key, ArrowLeft, Shield, Zap, AlertTriangle,
} from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface Provider {
  id: string;
  provider_name: string;
  display_name: string;
  env_key_name: string | null;
  is_connected: boolean;
  is_active: boolean;
  model_count: number;
}

interface Model {
  id: string;
  provider_id: string;
  model_id: string;
  model_name: string | null;
}

interface AgentConfig {
  id: string;
  agent_name: string;
  agent_role: string;
  provider_id: string | null;
  model_id: string | null;
  temperature: number;
  max_tokens: number;
  system_prompt_override: string | null;
  is_active: boolean;
  provider_name: string | null;
  model_name: string | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PROVIDER_COLOR: Record<string, string> = {
  openai:    'bg-emerald-50 text-emerald-700 border-emerald-200',
  anthropic: 'bg-orange-50  text-orange-700  border-orange-200',
  google:    'bg-blue-50    text-blue-700    border-blue-200',
  groq:      'bg-purple-50  text-purple-700  border-purple-200',
};

const AGENT_COLOR: Record<string, string> = {
  scanner:  'bg-blue-50    text-blue-700',
  fixer:    'bg-orange-50  text-orange-700',
  reviewer: 'bg-purple-50  text-purple-700',
  reporter: 'bg-green-50   text-green-700',
};

// ─── Provider Card ────────────────────────────────────────────────────────────

function ProviderCard({ provider }: { provider: Provider }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey]   = useState('');
  const [saving, setSaving]   = useState(false);
  const [showKey, setShowKey] = useState(false);

  const fetchModels = useMutation({
    mutationFn: async () => {
      await api.post(`/settings/providers/${provider.id}/fetch-models`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['models', provider.id] });
    },
  });

  const saveKey = async () => {
    if (!apiKey.trim() || !provider.env_key_name) return;
    setSaving(true);
    try {
      await api.post('/settings/env', { env_key: provider.env_key_name, env_value: apiKey.trim() });
      setApiKey('');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border rounded-xl bg-card p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={cn(
            'px-2.5 py-1 rounded-full text-xs font-semibold border capitalize',
            PROVIDER_COLOR[provider.provider_name] ?? 'bg-muted text-muted-foreground border-border',
          )}>
            {provider.display_name}
          </span>
          {provider.is_connected
            ? <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3.5 w-3.5" /> Connected</span>
            : <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle className="h-3.5 w-3.5" /> Not connected</span>
          }
        </div>
        <span className="text-xs text-muted-foreground">{provider.model_count} models</span>
      </div>

      {/* API Key input */}
      {provider.env_key_name && (
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
            <Key className="h-3 w-3" /> {provider.env_key_name}
          </label>
          <div className="flex gap-2">
            <input
              type={showKey ? 'text' : 'password'}
              placeholder="Enter API key to update…"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="flex-1 px-3 py-1.5 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <button
              onClick={() => setShowKey((v) => !v)}
              className="px-2 border rounded-md text-muted-foreground hover:bg-muted text-xs"
            >
              {showKey ? 'Hide' : 'Show'}
            </button>
            <button
              onClick={saveKey}
              disabled={!apiKey.trim() || saving}
              className="px-3 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:opacity-90 disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>
      )}

      {/* Fetch models */}
      <button
        onClick={() => fetchModels.mutate()}
        disabled={fetchModels.isPending}
        className="flex items-center justify-center gap-2 text-sm border rounded-md py-1.5 hover:bg-muted transition-colors text-foreground disabled:opacity-50"
      >
        {fetchModels.isPending
          ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Fetching models…</>
          : <><RefreshCw className="h-3.5 w-3.5" /> Sync Models</>
        }
      </button>

      {fetchModels.isError && (
        <p className="text-xs text-destructive">{(fetchModels.error as any)?.response?.data?.detail || 'Failed to fetch models'}</p>
      )}
      {fetchModels.isSuccess && (
        <p className="text-xs text-green-600">Models synced successfully.</p>
      )}
    </div>
  );
}

// ─── Agent Config Card ────────────────────────────────────────────────────────

function AgentCard({
  agent,
  providers,
}: {
  agent: AgentConfig;
  providers: Provider[];
}) {
  const queryClient = useQueryClient();
  const [selectedProvider, setSelectedProvider] = useState(agent.provider_id ?? '');
  const [selectedModel,    setSelectedModel]    = useState(agent.model_id    ?? '');
  const [temperature,      setTemperature]      = useState(String(agent.temperature));
  const [maxTokens,        setMaxTokens]        = useState(String(agent.max_tokens));
  const [expanded,         setExpanded]         = useState(false);
  const [saved,            setSaved]            = useState(false);

  const { data: models = [] } = useQuery({
    queryKey: ['models', selectedProvider],
    queryFn: async () => {
      if (!selectedProvider) return [];
      const { data } = await api.get(`/settings/providers/${selectedProvider}/models`);
      return data as Model[];
    },
    enabled: !!selectedProvider,
  });

  const updateAgent = useMutation({
    mutationFn: async () => {
      await api.put(`/settings/agents/${agent.id}`, {
        provider_id: selectedProvider || null,
        model_id:    selectedModel    || null,
        temperature: parseFloat(temperature),
        max_tokens:  parseInt(maxTokens, 10),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  return (
    <div className="border rounded-xl bg-card overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between p-5 hover:bg-muted/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className={cn('px-2.5 py-1 rounded-full text-xs font-semibold capitalize', AGENT_COLOR[agent.agent_name] ?? 'bg-muted text-muted-foreground')}>
            {agent.agent_name}
          </span>
          <div className="text-left">
            <p className="text-sm font-medium text-foreground capitalize">{agent.agent_name} Agent</p>
            <p className="text-xs text-muted-foreground">
              {agent.provider_name
                ? `${agent.provider_name} · ${agent.model_name ?? 'no model'}`
                : 'No LLM configured'}
            </p>
          </div>
        </div>
        <ChevronDown className={cn('h-4 w-4 text-muted-foreground transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded && (
        <div className="border-t px-5 py-4 flex flex-col gap-4">
          <p className="text-xs text-muted-foreground">{agent.agent_role}</p>

          <div className="grid grid-cols-2 gap-3">
            {/* Provider */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-foreground">Provider</label>
              <select
                value={selectedProvider}
                onChange={(e) => { setSelectedProvider(e.target.value); setSelectedModel(''); }}
                className="px-2.5 py-1.5 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">— none —</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </select>
            </div>

            {/* Model */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-foreground">Model</label>
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={!selectedProvider || models.length === 0}
                className="px-2.5 py-1.5 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
              >
                <option value="">— select model —</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.model_name ?? m.model_id}</option>
                ))}
              </select>
              {selectedProvider && models.length === 0 && (
                <p className="text-xs text-muted-foreground">No models — click "Sync Models" first.</p>
              )}
            </div>

            {/* Temperature */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-foreground">Temperature</label>
              <input
                type="number" min="0" max="2" step="0.05"
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
                className="px-2.5 py-1.5 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>

            {/* Max tokens */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-foreground">Max Tokens</label>
              <input
                type="number" min="256" max="32768" step="256"
                value={maxTokens}
                onChange={(e) => setMaxTokens(e.target.value)}
                className="px-2.5 py-1.5 border rounded-md bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </div>

          <button
            onClick={() => updateAgent.mutate()}
            disabled={updateAgent.isPending}
            className="flex items-center justify-center gap-2 bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {updateAgent.isPending
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
              : saved
              ? <><CheckCircle className="h-4 w-4" /> Saved</>
              : <><Save className="h-4 w-4" /> Save Configuration</>
            }
          </button>

          {updateAgent.isError && (
            <p className="text-xs text-destructive text-center">
              {(updateAgent.error as any)?.response?.data?.detail || 'Failed to save'}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Settings Page ────────────────────────────────────────────────────────────

type Tab = 'sonarqube' | 'providers' | 'agents';

// ─── SonarQube Panel ──────────────────────────────────────────────────────────

interface SonarQubeStatus {
  sonarqube_url: string;
  sonarqube_token: string;
  configured: boolean;
  scanner_cli_installed: boolean;
}

interface TestResult {
  ok: boolean;
  valid: boolean;
  user?: string;
  permissions?: string[];
  scanner_cli_installed?: boolean;
  message: string;
}

function SonarQubePanel() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['sonarqube-config'],
    queryFn: async () => {
      const { data } = await api.get('/settings/sonarqube');
      return data as SonarQubeStatus;
    },
  });

  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [editing, setEditing] = useState(false);

  // Sync form state with loaded data once
  useState(() => {
    if (data) setUrl(data.sonarqube_url);
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      const resp = await api.put('/settings/sonarqube', {
        sonarqube_url: url || data?.sonarqube_url || '',
        sonarqube_token: token,
      });
      return resp.data;
    },
    onSuccess: () => {
      setToken('');
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ['sonarqube-config'] });
    },
  });

  const testMut = useMutation({
    mutationFn: async () => {
      const resp = await api.post('/settings/sonarqube/test');
      return resp.data as TestResult;
    },
  });

  if (isLoading || !data) {
    return <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  }

  const displayUrl = editing ? url : data.sonarqube_url;

  return (
    <div className="flex flex-col gap-4">
      <div className="border rounded-xl p-5 bg-card">
        <div className="flex items-center gap-2 mb-1">
          <Shield className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold text-foreground">SonarQube Connection</h2>
          {data.configured ? (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
              <CheckCircle className="h-3 w-3" /> Configured
            </span>
          ) : (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full font-medium">
              <AlertTriangle className="h-3 w-3" /> Not configured
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground mb-4">
          The SonarAgent uses these credentials to fetch issues and (optionally) trigger scans.
          The token is persisted to <code className="bg-muted px-1 rounded text-[10px]">backend/.env</code>.
        </p>

        <div className="flex flex-col gap-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">SonarQube URL</label>
            <input
              value={displayUrl}
              onChange={(e) => { setUrl(e.target.value); setEditing(true); }}
              placeholder="https://sonarqube.example.com"
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-foreground block mb-1">User Token</label>
            <input
              type="password"
              value={token}
              onChange={(e) => { setToken(e.target.value); setEditing(true); }}
              placeholder={data.configured ? `Current: ${data.sonarqube_token} (paste a new value to replace)` : 'squ_...'}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:ring-2 focus:ring-primary/30 focus:outline-none font-mono"
            />
            <p className="text-[10px] text-muted-foreground mt-1">
              Generate a User token in SonarQube → My Account → Security → Generate Tokens.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending || !token || !url}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-40"
            >
              {saveMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Save Credentials
            </button>
            <button
              onClick={() => testMut.mutate()}
              disabled={testMut.isPending || !data.configured}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border rounded-md hover:bg-muted disabled:opacity-40"
            >
              {testMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
              Test Connection
            </button>
          </div>

          {saveMut.isError && (
            <div className="text-[11px] text-destructive bg-destructive/10 border border-destructive/20 rounded-md px-2 py-1.5">
              {(saveMut.error as any)?.response?.data?.detail || 'Save failed.'}
            </div>
          )}
          {saveMut.isSuccess && (
            <div className="text-[11px] text-green-700 bg-green-50 border border-green-200 rounded-md px-2 py-1.5 flex items-center gap-1.5">
              <CheckCircle className="h-3 w-3" /> Credentials saved. Click <strong>Test Connection</strong> to verify.
            </div>
          )}
          {testMut.data && (
            <div
              className={cn(
                'text-[11px] rounded-md px-3 py-2 border flex items-start gap-2',
                testMut.data.ok && testMut.data.valid
                  ? 'bg-green-50 border-green-200 text-green-800'
                  : 'bg-red-50 border-red-200 text-red-800',
              )}
            >
              {testMut.data.ok && testMut.data.valid
                ? <CheckCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                : <XCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />}
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{testMut.data.message}</p>
                {testMut.data.user && (
                  <p className="text-[10px] mt-0.5 opacity-80">
                    Authenticated as: <strong>{testMut.data.user}</strong>
                  </p>
                )}
                {testMut.data.permissions && testMut.data.permissions.length > 0 && (
                  <p className="text-[10px] mt-0.5 opacity-80">
                    Global permissions: {testMut.data.permissions.join(', ')}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* sonar-scanner CLI status */}
      <div className="border rounded-xl p-5 bg-card">
        <div className="flex items-center gap-2 mb-1">
          <Cpu className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold text-foreground">sonar-scanner CLI</h2>
          {data.scanner_cli_installed ? (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
              <CheckCircle className="h-3 w-3" /> Installed
            </span>
          ) : (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full font-medium">
              <AlertTriangle className="h-3 w-3" /> Not installed
            </span>
          )}
        </div>
        {data.scanner_cli_installed ? (
          <p className="text-xs text-muted-foreground">
            The sonar-scanner CLI is installed. Each scan will run a real local analysis on the cloned repository and upload it to SonarQube.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground">
              Without the local CLI, the agent can only fetch existing issues from SonarQube — it cannot create or analyse new projects.
              Install it with:
            </p>
            <pre className="bg-slate-950 text-slate-100 text-[11px] font-mono p-2 rounded-md select-all">brew install sonar-scanner</pre>
            <p className="text-[10px] text-muted-foreground">
              After installing, restart the backend with <code className="bg-muted px-1 rounded">./start.sh</code>.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Settings() {
  const [tab, setTab] = useState<Tab>('providers');

  const { data: providers = [], isLoading: loadingProviders } = useQuery({
    queryKey: ['providers'],
    queryFn: async () => {
      const { data } = await api.get('/settings/providers');
      return data as Provider[];
    },
  });

  const { data: agents = [], isLoading: loadingAgents } = useQuery({
    queryKey: ['agents'],
    queryFn: async () => {
      const { data } = await api.get('/settings/agents');
      return data as AgentConfig[];
    },
  });

  return (
    <div className="p-8 max-w-4xl mx-auto flex flex-col gap-6">
      <div>
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors mb-2"
        >
          <ArrowLeft className="h-3 w-3" />
          Back to Dashboard
        </Link>
        <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
          <Settings2 className="h-7 w-7 text-primary" /> Settings
        </h1>
        <p className="text-muted-foreground mt-1">
          Configure LLM providers and assign models to each agent.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex border-b gap-0">
        {(['sonarqube', 'providers', 'agents'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors capitalize',
              tab === t
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t === 'providers' ? 'LLM Providers' : t === 'agents' ? 'Agent Config' : 'SonarQube'}
          </button>
        ))}
      </div>

      {tab === 'sonarqube' && <SonarQubePanel />}

      {/* LLM Providers */}
      {tab === 'providers' && (
        <>
          {loadingProviders
            ? <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
            : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {providers.map((p) => <ProviderCard key={p.id} provider={p} />)}
              </div>
            )
          }
          <div className="bg-muted/50 border rounded-xl p-4 text-sm text-muted-foreground flex items-start gap-2">
            <Cpu className="h-4 w-4 mt-0.5 shrink-0" />
            <span>
              Enter your API key for each provider and click <strong>Sync Models</strong> to fetch available models.
              Then go to <strong>Agent Config</strong> to assign a model to each agent.
            </span>
          </div>
        </>
      )}

      {/* Agent Config */}
      {tab === 'agents' && (
        <>
          {loadingAgents
            ? <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
            : (
              <div className="flex flex-col gap-3">
                {agents.map((a) => (
                  <AgentCard key={a.id} agent={a} providers={providers} />
                ))}
              </div>
            )
          }
          <div className="bg-muted/50 border rounded-xl p-4 text-sm text-muted-foreground">
            Click an agent to expand its configuration. Select a provider and model, then save.
            Different agents can use different LLM providers.
          </div>
        </>
      )}
    </div>
  );
}

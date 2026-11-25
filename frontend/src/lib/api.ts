// Thin API client. Token lives in sessionStorage (cleared when the tab closes).
// In production with OIDC, replace getToken() with MSAL's acquireTokenSilent().

const TOKEN_KEY = "aiu_token";
const USER_KEY = "aiu_user";

export type Me = { id: string; name: string; role: string; department_id: string | null };

function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

let memToken: string | null = null;
let memUser: Me | null = null;

export function getToken(): string | null {
  return memToken ?? store()?.getItem(TOKEN_KEY) ?? null;
}

export function getUser(): Me | null {
  if (memUser) return memUser;
  try {
    const raw = store()?.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as Me) : null;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: Me) {
  memToken = token;
  memUser = user;
  try {
    store()?.setItem(TOKEN_KEY, token);
    store()?.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* storage unavailable: keep in memory */
  }
}

export function clearSession() {
  memToken = null;
  memUser = null;
  try {
    store()?.removeItem(TOKEN_KEY);
    store()?.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, params?: Record<string, string | number | undefined | null>, init?: RequestInit): Promise<T> {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString()
    : "";
  const token = getToken();
  const res = await fetch(`/api${path}${qs}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) },
  });
  if (res.status === 401) {
    clearSession();
    window.dispatchEvent(new Event("aiu-logout"));
  }
  if (!res.ok) {
    const body = await res.text();
    let msg = body || res.statusText;
    try {
      const d = JSON.parse(body)?.detail;
      if (d) msg = typeof d === "string" ? d : JSON.stringify(d);
    } catch {
      /* plain-text error body */
    }
    throw new ApiError(res.status, `${msg} (HTTP ${res.status} on ${path})`);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export async function download(path: string, params: Record<string, string | undefined>, filename: string) {
  const text = await api<string>(path, params);
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------- formatting
export const fmtUsd = (v: number, digits = 0) =>
  v.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits });

/** Short currency for axes / bar labels: $1.2K, $35, $4.50 */
export const fmtUsdShort = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1000) return "$" + fmtNum(v);
  if (a >= 10 || v === 0) return "$" + Math.round(v);
  return "$" + v.toFixed(2);
};

export const fmtNum = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return Math.round(v).toLocaleString();
};

export const fmtDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const PROVIDER_LABELS: Record<string, string> = {
  azure_openai: "Azure OpenAI",
  openai: "OpenAI",
  anthropic: "Anthropic",
  bedrock: "AWS Bedrock",
  google: "Google",
  github: "GitHub",
  microsoft: "Microsoft",
  "azure-ai-search": "Azure AI Search",
  pgvector: "pgvector",
  input_tokens: "Input tokens",
  output_tokens: "Output tokens",
};
export const label = (k: string) => PROVIDER_LABELS[k] ?? k;

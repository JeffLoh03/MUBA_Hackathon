import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

interface AuthState {
  authenticated: boolean;
  setup_required: boolean;
  user: { id: string; email: string } | null;
}

interface AuthContextValue {
  state: AuthState | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  signIn: (email: string, password: string, setup: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  request: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function responseError(response: Response): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string") return new Error(body.detail);
  } catch {
    // The server may return a non-JSON error during startup.
  }
  return new Error(`The request failed (HTTP ${response.status}). Please try again.`);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/auth/status", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw await responseError(response);
      setState(await response.json() as AuthState);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Cannot reach the backend.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const request = useCallback(async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await fetch(input, { ...init, credentials: "same-origin" });
    if (response.status === 401) {
      setState({ authenticated: false, setup_required: false, user: null });
      setError("Your session has ended. Sign in to continue.");
      throw new Error("Your session has ended. Sign in to continue.");
    }
    return response;
  }, []);

  const signIn = useCallback(async (email: string, password: string, setup: boolean) => {
    setError("");
    const response = await fetch(`/api/auth/${setup ? "setup" : "login"}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      const requestError = await responseError(response);
      if (setup && response.status === 409) await refresh();
      throw requestError;
    }
    setState(await response.json() as AuthState);
  }, [refresh]);

  const signOut = useCallback(async () => {
    const response = await request("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw await responseError(response);
    setState({ authenticated: false, setup_required: false, user: null });
    setError("");
  }, [request]);

  const value = useMemo(() => ({ state, loading, error, refresh, signIn, signOut, request }), [state, loading, error, refresh, signIn, signOut, request]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is required.");
  return value;
}

export function AuthConnectionState() {
  const { loading, error, refresh } = useAuth();
  return <main className="auth-connection" aria-live="polite"><p className="eyebrow">Verity Desk</p><h1>{loading ? "Opening your desk…" : "Connection unavailable"}</h1>{!loading && <><p role="alert">{error}</p><button className="primary-button" type="button" onClick={() => void refresh()}>Try again</button></>}</main>;
}

export function ProtectedRoute() {
  const { state, loading } = useAuth();
  const location = useLocation();
  if (loading || !state) return <AuthConnectionState />;
  if (!state.authenticated) return <Navigate to="/login" state={{ from: `${location.pathname}${location.search}` }} replace />;
  return <Outlet />;
}

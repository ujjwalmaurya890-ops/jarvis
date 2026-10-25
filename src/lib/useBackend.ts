import { useCallback, useEffect, useRef, useState } from "react";

const API = "http://127.0.0.1:8765";

export interface BackendHealth {
  app: string;
  api: string;
  mode: string;
  state: string;
  mic: boolean;
  providers: { name: string; ok: boolean; status: string }[];
  confirmations: string;
}

export type BackendReply = {
  reply: string;
  source: string;
  mode: string;
  sources?: { title: string; url: string }[];
};

export function isLocalDashboardOrigin(location: Pick<Location, "protocol" | "hostname" | "port">): boolean {
  return location.protocol === "http:" &&
    ["127.0.0.1", "localhost"].includes(location.hostname) &&
    ["5173", "4173"].includes(location.port);
}

async function request(path: string, init: RequestInit = {}, timeout = 4000): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(`${API}${path}`, {
      ...init,
      cache: "no-store",
      mode: "cors",
      credentials: "omit",
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timer);
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { error?: string };
    return data.error || `Local API returned HTTP ${response.status}.`;
  } catch {
    return `Local API returned HTTP ${response.status}.`;
  }
}

export interface BackendApi {
  token: string;
  setToken: (token: string) => void;
  health: BackendHealth | null;
  available: boolean;
  connected: boolean;
  checking: boolean;
  error: string;
  check: () => Promise<void>;
  connect: () => Promise<void>;
  disconnect: () => void;
  send: (text: string) => Promise<BackendReply>;
}

export function useBackend(): BackendApi {
  const [token, setToken] = useState("");
  const tokenRef = useRef("");
  const [health, setHealth] = useState<BackendHealth | null>(null);
  const [available, setAvailable] = useState(false);
  const [connected, setConnected] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const changeToken = useCallback((value: string) => {
    tokenRef.current = value;
    setToken(value);
  }, []);

  const check = useCallback(async () => {
    if (!isLocalDashboardOrigin(window.location)) {
      setAvailable(false);
      setError("The local API rejects remote/file:// dashboards by design. Open this page with npm run dev on your laptop (127.0.0.1:5173). Demo mode still works here.");
      return;
    }
    setChecking(true);
    try {
      const response = await request("/api/health");
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as BackendHealth;
      if (data.app !== "JARVIS") throw new Error("Unexpected server at port 8765. Check what is running there.");
      setHealth(data);
      setAvailable(true);
      setError("");
    } catch (reason) {
      setAvailable(false);
      setConnected(false);
      setHealth(null);
      setError(reason instanceof DOMException && reason.name === "AbortError"
        ? "Local API timed out. Start: python main.py --api"
        : reason instanceof TypeError
          ? "Local API is unreachable. In jarvis/ run: python main.py --api. Check Vite is on localhost:5173 and the API is on 127.0.0.1:8765."
          : reason instanceof Error ? reason.message : "Local API connection failed.");
    } finally {
      setChecking(false);
    }
  }, []);

  const connect = useCallback(async () => {
    if (!isLocalDashboardOrigin(window.location)) {
      await check();
      return;
    }
    const value = tokenRef.current.trim();
    if (value.length < 20) {
      setError("Paste the local token from jarvis/data/api-token first. It stays in this tab's memory only.");
      return;
    }
    setChecking(true);
    try {
      const healthResponse = await request("/api/health");
      if (!healthResponse.ok) throw new Error(await readError(healthResponse));
      const server = (await healthResponse.json()) as BackendHealth;
      if (server.app !== "JARVIS" || server.api !== "loopback-only") {
        throw new Error("The program at port 8765 is not the expected JARVIS API.");
      }
      const response = await request("/api/session", { headers: { Authorization: `Bearer ${value}` } });
      if (!response.ok) throw new Error(await readError(response));
      setHealth(server);
      setConnected(true);
      setAvailable(true);
      setError("");
    } catch (reason) {
      setConnected(false);
      setError(reason instanceof Error ? reason.message : "Connection failed. Check the API and token.");
    } finally {
      setChecking(false);
    }
  }, [check]);

  const disconnect = useCallback(() => {
    setConnected(false);
    changeToken("");
    setError("");
  }, [changeToken]);

  const send = useCallback(async (text: string): Promise<BackendReply> => {
    if (!connected) throw new Error("The Python backend is not connected. Reconnect or use Demo mode.");
    try {
      const response = await request("/api/command", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${tokenRef.current.trim()}`,
        },
        body: JSON.stringify({ text }),
      }, 95000);
      if (!response.ok) {
        const msg = await readError(response);
        if (response.status === 401) setConnected(false);
        throw new Error(msg);
      }
      const payload = (await response.json()) as BackendReply;
      if (typeof payload.reply !== "string") throw new Error("The local API returned an invalid reply.");
      return payload;
    } catch (reason) {
      const msg = reason instanceof Error ? reason.message : "Local backend disconnected.";
      setError(msg);
      if (reason instanceof TypeError || (reason instanceof DOMException && reason.name === "AbortError")) setConnected(false);
      throw new Error(msg);
    }
  }, [connected]);

  useEffect(() => { void check(); }, [check]);
  useEffect(() => {
    if (!connected) return;
    const timer = window.setInterval(() => { void check(); }, 15000);
    return () => window.clearInterval(timer);
  }, [connected, check]);

  return { token, setToken: changeToken, health, available, connected, checking, error, check, connect, disconnect, send };
}
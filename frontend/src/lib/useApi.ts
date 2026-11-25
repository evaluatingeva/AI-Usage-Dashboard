import { useEffect, useState } from "react";
import { api } from "./api";

export function useApi<T>(path: string | null, params?: Record<string, string | number | undefined | null>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const key = path + JSON.stringify(params ?? {}) + tick;

  // Re-fetch when the app broadcasts a refresh (auto-refresh timer or Refresh button)
  useEffect(() => {
    const onRefresh = () => setTick((t) => t + 1);
    window.addEventListener("aiu-refresh", onRefresh);
    return () => window.removeEventListener("aiu-refresh", onRefresh);
  }, []);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    if (data === null) setLoading(true);
    api<T>(path, params)
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { data, error, loading };
}

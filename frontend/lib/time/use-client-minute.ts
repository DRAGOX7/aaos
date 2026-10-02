import { useSyncExternalStore } from "react";

const MINUTE_MS = 60_000;

function subscribe(onChange: () => void): () => void {
  const timer = window.setInterval(onChange, MINUTE_MS);
  return () => window.clearInterval(timer);
}

function snapshot(): number {
  return Math.floor(Date.now() / MINUTE_MS) * MINUTE_MS;
}

/**
 * The viewer's clock, to the minute, or null while rendering on the server.
 * Time-of-day text then differs only after hydration, never during it.
 */
export function useClientMinute(): number | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

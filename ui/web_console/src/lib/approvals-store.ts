import { useSyncExternalStore } from "react";

// Shared pending-approvals count. Lives at module scope so the sidebar badge
// (rendered inside each page's OpsShell) and the Approvals page share one
// source of truth: a decision on the Approvals page updates the badge instantly.
// `null` means "not loaded yet" so the UI can avoid flashing a 0 during load.

let pendingCount: number | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function setPendingCount(count: number | null) {
  pendingCount = count;
  emit();
}


// Subscribe to the pending count. Returns null until it has been loaded.
export function usePendingCount(): number | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => pendingCount,
    () => pendingCount,
  );
}

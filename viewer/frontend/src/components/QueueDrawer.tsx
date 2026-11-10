import { useState } from "react";

import {
  useClearErrorLog,
  useClearQueue,
  useConfig,
  useDownloadErrorLog,
  useQueue,
} from "../api/hooks";
import { useToastStore } from "../stores/toastStore";
import { useUiStore } from "../stores/uiStore";
import { Modal } from "./Modal";
import { QueueItem } from "./QueueItem";

type ClearScope = "finished" | "all";

const CLEAR_SCOPE_TITLE: Record<ClearScope, string> = {
  finished: "Clear finished items?",
  all: "Clear all items?",
};

const CLEAR_SCOPE_BODY: Record<ClearScope, string> = {
  finished: "This removes finished items from the queue. Running, queued, and failed items stay.",
  all: "This removes every item that is not currently running — including failed and stopped ones. Running items stay.",
};

const CLEAR_SCOPE_ACTION: Record<ClearScope, string> = {
  finished: "Clear finished items",
  all: "Clear all items",
};

export function QueueDrawer() {
  const isOpen = useUiStore((s) => s.isQueueDrawerOpen);
  const setQueueDrawerOpen = useUiStore((s) => s.setQueueDrawerOpen);
  const setActivePanelJobKey = useUiStore((s) => s.setActivePanelJobKey);
  const queue = useQueue();
  const config = useConfig();
  const clearQueue = useClearQueue();
  const downloadErrorLog = useDownloadErrorLog();
  const clearErrorLog = useClearErrorLog();
  const addToast = useToastStore((s) => s.addToast);
  const [dismissedToApply, setDismissedToApply] = useState<Set<string>>(new Set());
  const [confirmClear, setConfirmClear] = useState<ClearScope | null>(null);
  const [confirmClearErrors, setConfirmClearErrors] = useState(false);

  if (!isOpen) return null;

  const minScore = config.data?.scoreNotifyMinScore ?? 4;
  const toApply = (queue.data ?? []).filter(
    (item) => item.score !== null && item.score >= minScore && !dismissedToApply.has(item.id),
  );

  const handleClearQueue = () => {
    if (!confirmClear) return;
    const scope = confirmClear;
    setConfirmClear(null);
    clearQueue.mutate(scope, {
      onSuccess: (data) => {
        setDismissedToApply(new Set());
        addToast({ message: `Removed ${data.removed} queue item(s).`, kind: "success" });
      },
      onError: () => addToast({ message: "Failed to clear the queue.", kind: "error" }),
    });
  };

  const handleClearErrorLog = () => {
    setConfirmClearErrors(false);
    clearErrorLog.mutate(undefined, {
      onSuccess: (data) =>
        addToast({ message: `Removed ${data.removed} error log entries.`, kind: "success" }),
      onError: () => addToast({ message: "Failed to clear the error log.", kind: "error" }),
    });
  };

  return (
    <>
      <div className="queue-drawer-backdrop" onClick={() => setQueueDrawerOpen(false)} />
      <div className="queue-drawer" role="dialog" aria-label="Analysis queue">
        <div className="queue-drawer-header">
          <h3>Queue</h3>
          <button type="button" onClick={() => setQueueDrawerOpen(false)}>
            Close
          </button>
        </div>

        <div className="queue-drawer-bulk-actions">
          <button type="button" onClick={() => setConfirmClear("finished")}>
            Clear finished
          </button>
          <button type="button" onClick={() => setConfirmClear("all")}>
            Clear all
          </button>
          <button
            type="button"
            disabled={downloadErrorLog.isPending}
            onClick={() =>
              downloadErrorLog.mutate(undefined, {
                onError: () =>
                  addToast({ message: "Failed to download the error log.", kind: "error" }),
              })
            }
          >
            Download error log
          </button>
          <button type="button" onClick={() => setConfirmClearErrors(true)}>
            Clear error log
          </button>
        </div>

        {toApply.length > 0 && (
          <div className="to-apply-bucket">
            <h4>To Apply</h4>
            {toApply.map((item) => (
              <div key={item.id} className="to-apply-item">
                <button type="button" onClick={() => setActivePanelJobKey(item.job_key)}>
                  {item.title} — {item.score?.toFixed(1)}/5
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setDismissedToApply((prev) => new Set(prev).add(item.id))
                  }
                >
                  Dismiss
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="queue-drawer-list">
          {(queue.data ?? []).length === 0 && <p>Queue is empty.</p>}
          {(queue.data ?? []).map((item) => (
            <QueueItem key={item.id} item={item} />
          ))}
        </div>
      </div>

      {confirmClear && (
        <Modal
          title={CLEAR_SCOPE_TITLE[confirmClear]}
          onClose={() => setConfirmClear(null)}
        >
          <p>{CLEAR_SCOPE_BODY[confirmClear]}</p>
          <div className="modal-actions">
            <button type="button" onClick={() => setConfirmClear(null)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={clearQueue.isPending}
              onClick={handleClearQueue}
            >
              {CLEAR_SCOPE_ACTION[confirmClear]}
            </button>
          </div>
        </Modal>
      )}

      {confirmClearErrors && (
        <Modal title="Clear error log?" onClose={() => setConfirmClearErrors(false)}>
          <p>
            This permanently deletes the recorded analysis errors. Download the log first if you
            want to keep a copy.
          </p>
          <div className="modal-actions">
            <button type="button" onClick={() => setConfirmClearErrors(false)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={clearErrorLog.isPending}
              onClick={handleClearErrorLog}
            >
              Clear log
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

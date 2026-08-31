"use client";

import React, { useEffect, useState } from "react";
import { Invoice } from "../lib/api";
import { VoiceCallModal } from "./VoiceCallModal";

interface CallQueueDrawerProps {
  queue: Invoice[];
  onRemoveFromQueue: (invoiceId: string) => void;
  onCallCompleted: () => void;
}

export function CallQueueDrawer({ queue, onRemoveFromQueue, onCallCompleted }: CallQueueDrawerProps) {
  const [activeCallInvoice, setActiveCallInvoice] = useState<Invoice | null>(null);

  useEffect(() => {
    // FIFO auto-start next call in queue if no active call
    if (!activeCallInvoice && queue.length > 0) {
      setActiveCallInvoice(queue[0]);
    }
  }, [queue, activeCallInvoice]);

  const handleVoiceCallClose = () => {
    if (activeCallInvoice) {
      onRemoveFromQueue(activeCallInvoice.id);
      setActiveCallInvoice(null);
      onCallCompleted();
    }
  };

  if (queue.length === 0 && !activeCallInvoice) {
    return null;
  }

  return (
    <>
      {/* Compact Global Call Queue Dock */}
      <div className="fixed bottom-4 right-4 z-40 bg-zinc-900 text-white rounded-lg p-3 shadow-xl border border-zinc-800 flex items-center space-x-3 text-xs">
        <div className="flex items-center space-x-2">
          <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
          <span className="font-semibold tracking-tight">Call Queue ({queue.length})</span>
        </div>

        <div className="text-zinc-400 border-l border-zinc-700 pl-3 max-w-xs truncate">
          {queue.map((inv, idx) => (
            <span key={inv.id} className="mr-2">
              #{idx + 1} {inv.customer.name}
            </span>
          ))}
        </div>

        {activeCallInvoice && (
          <button
            onClick={() => setActiveCallInvoice(activeCallInvoice)}
            className="px-2.5 py-1 bg-blue-600 hover:bg-blue-700 text-white font-medium text-xs rounded transition-colors"
          >
            Calling: {activeCallInvoice.customer.name}
          </button>
        )}
      </div>

      {activeCallInvoice && (
        <VoiceCallModal
          invoice={activeCallInvoice}
          isOpen={true}
          onClose={handleVoiceCallClose}
        />
      )}
    </>
  );
}

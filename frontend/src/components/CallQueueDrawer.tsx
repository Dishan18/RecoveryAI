import React, { useState } from "react";
import { PhoneCall } from "lucide-react";
import { Invoice } from "../lib/api";
import { VoiceCallModal } from "./VoiceCallModal";

interface CallQueueDrawerProps {
  queue: Invoice[];
  onRemoveFromQueue: (invoiceId: string) => void;
  onCallCompleted: () => void;
}

export function CallQueueDrawer({ queue, onRemoveFromQueue, onCallCompleted }: CallQueueDrawerProps) {
  const [activeCallInvoice, setActiveCallInvoice] = useState<Invoice | null>(null);

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
      <div className="fixed bottom-4 right-4 z-40 bg-zinc-900 text-white rounded-lg p-3 shadow-xl border border-zinc-800 flex items-center space-x-3 text-xs animate-in slide-in-from-bottom-2">
        <div className="flex items-center space-x-2">
          <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
          <span className="font-semibold tracking-tight">Call Queue ({queue.length})</span>
        </div>

        <div className="text-zinc-400 border-l border-zinc-700 pl-3 max-w-xs truncate flex items-center gap-1.5">
          {queue.map((inv, idx) => (
            <button
              key={inv.id}
              onClick={() => setActiveCallInvoice(inv)}
              className="text-zinc-300 hover:text-white hover:underline text-[11px]"
              title={`Call ${inv.customer.name}`}
            >
              #{idx + 1} {inv.customer.name}
            </button>
          ))}
        </div>

        {queue.length > 0 && (
          <button
            onClick={() => setActiveCallInvoice(queue[0])}
            className="px-2.5 py-1 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded transition-colors flex items-center gap-1 shrink-0"
          >
            <PhoneCall size={11} />
            Start Call
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

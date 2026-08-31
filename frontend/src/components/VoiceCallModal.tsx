"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CalendarCheck,
  CheckCircle2,
  Mic,
  MicOff,
  PhoneCall,
  PhoneOff,
  Play,
  RefreshCw,
  Send,
  Sparkles,
  Volume2,
} from "lucide-react";
import { api, Invoice, VoiceCallResponse, VoiceGreetingResponse } from "@/lib/api";

interface VoiceCallModalProps {
  invoice: Invoice;
  isOpen?: boolean;
  onClose: () => void;
  onStateUpdated?: () => void;
}

type CallState = "initiating" | "agent_speaking" | "user_turn" | "processing" | "agent_responding";

interface TurnHistoryItem {
  turn: number;
  debtorText: string;
  agentReply: string;
  previousState: string;
  newState: string;
  intent: string;
  audioBase64?: string;
  audioFormat?: string;
  actionExecuted?: string;
}

export function VoiceCallModal({ invoice: initialInvoice, isOpen, onClose, onStateUpdated }: VoiceCallModalProps) {
  if (isOpen === false) return null;

  const [currentInvoice, setCurrentInvoice] = useState<Invoice>(initialInvoice);
  const [callState, setCallState] = useState<CallState>("initiating");
  const [recording, setRecording] = useState(false);
  const [greeting, setGreeting] = useState<VoiceGreetingResponse | null>(null);
  const [turns, setTurns] = useState<TurnHistoryItem[]>([]);
  const [customInputText, setCustomInputText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const autoCloseTimerRef = useRef<NodeJS.Timeout | null>(null);

  const testPrompts = [
    { label: "Transliterated PTP (3 Days)", text: "नहीं, आई नीड थ्री डेज़ टू कंप्लीट द पेमेंट" },
    { label: "PTP (3 Days Hindi)", text: "मैं 3 दिन में पेमेंट कर दूंगा" },
    { label: "PTP Exceeds (>3 Days)", text: "मैं अगले हफ्ते / 5 दिन बाद करूँगा" },
    { label: "Accept 3-Day Policy", text: "Haan 3 din mein theek hai" },
    { label: "Reject 3-Day Policy", text: "Nahi 3 din mein nahi ho payega" },
    { label: "2nd PTP (Breach Escalation)", text: "मेरे को और 2 दिन चाहिए" },
    { label: "Agreed to Pay (1h Window)", text: "Main yeh payment kar dunga" },
    { label: "1h Window Breach Refusal", text: "नहीं कर सकता" },
    { label: "Turn 1 Refusal (5%)", text: "नहीं, मैं इसे आज सेटल नहीं कर पाऊंगा" },
    { label: "Billing Dispute", text: "GST calculation wrong hai, billing error hai" },
  ];

  // Plays Sarvam AI (bulbul-v3) Native Voice Audio
  const playSarvamAudio = (
    base64Audio?: string,
    format: string = "audio/wav",
    onEnd?: () => void
  ) => {
    let completed = false;
    const finish = () => {
      if (completed) return;
      completed = true;
      if (onEnd) onEnd();
    };

    if (!base64Audio || base64Audio.trim().length < 50) {
      finish();
      return;
    }

    try {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }
      const cleanB64 = base64Audio.trim().replace(/[\r\n]/g, "");
      const dataUri = `data:${format};base64,${cleanB64}`;
      const audio = new Audio(dataUri);
      currentAudioRef.current = audio;

      audio.onended = () => finish();
      audio.onerror = (e) => {
        console.warn("Sarvam audio playback error:", e);
        finish();
      };

      const playPromise = audio.play();
      if (playPromise !== undefined) {
        playPromise.catch((err) => {
          console.warn("Sarvam audio play() interrupted/error:", err);
          finish();
        });
      }
    } catch (err) {
      console.warn("Sarvam audio construction failed:", err);
      finish();
    }
  };

  // Step 1: Outbound Auto-Greeting on Mount
  useEffect(() => {
    let isMounted = true;

    const startOutboundGreeting = async () => {
      setCallState("initiating");
      setError(null);
      try {
        const res = await api.voiceGreeting(currentInvoice.id);
        if (!isMounted) return;
        setGreeting(res);
        setCallState("agent_speaking");

        if (res.audio_base64 && res.audio_base64.length > 50) {
          playSarvamAudio(res.audio_base64, res.audio_format, () => {
            if (isMounted) setCallState("user_turn");
          });
        } else {
          setCallState("user_turn");
        }
      } catch (err: unknown) {
        if (!isMounted) return;
        console.warn("Greeting endpoint failed, falling back to turn:", err);
        setCallState("user_turn");
      }
    };

    startOutboundGreeting();

    return () => {
      isMounted = false;
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
      }
      if (autoCloseTimerRef.current) {
        clearTimeout(autoCloseTimerRef.current);
      }
    };
  }, [currentInvoice.id]);

  // Start Mic Recording
  const startRecording = async () => {
    setError(null);
    audioChunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        stream.getTracks().forEach((t) => t.stop());
        await submitUserReply(audioBlob);
      };

      mediaRecorder.start();
      setRecording(true);
    } catch (err: unknown) {
      console.warn("Mic access issue, falling back to simulated prompt:", err);
      setError("Microphone unavailable or blocked — use text input or prompt buttons below.");
    }
  };

  // Stop Mic Recording
  const stopRecording = () => {
    if (mediaRecorderRef.current && recording) {
      mediaRecorderRef.current.stop();
      setRecording(false);
    }
  };

  // Submit User Speech or Text Reply to Backend
  const submitUserReply = async (audioBlob?: Blob, textFallback?: string) => {
    setCallState("processing");
    setError(null);
    try {
      const res: VoiceCallResponse = await api.voiceCall(currentInvoice.id, audioBlob, textFallback);

      // Update local invoice state
      setCurrentInvoice((prev) => ({
        ...prev,
        status: (res.new_invoice_status as any) || prev.status,
      }));

      // Append to local turn history
      const newTurnItem: TurnHistoryItem = {
        turn: turns.length + 1,
        debtorText: res.transcription,
        agentReply: res.agent_reply_text,
        previousState: res.previous_state,
        newState: res.new_state,
        intent: res.parsed_intent,
        audioBase64: res.audio_base64,
        audioFormat: res.audio_format,
        actionExecuted: res.action_executed,
      };
      setTurns((prev) => [...prev, newTurnItem]);
      setCallState("agent_responding");
      onStateUpdated?.();

      const TERMINAL_CALL_STATES = ["ESCALATED_HUMAN", "FROZEN_DISPUTE", "RESOLVED", "PTP_ACTIVE"];
      const isCallComplete = TERMINAL_CALL_STATES.includes(res.new_state) || res.trigger_auto_close === true;

      // Play Sarvam AI Voice Reply
      if (res.audio_base64 && res.audio_base64.length > 50) {
        playSarvamAudio(res.audio_base64, res.audio_format, () => {
          if (isCallComplete) {
            autoCloseTimerRef.current = setTimeout(() => onClose(), 2500);
          } else {
            setCallState("user_turn");
          }
        });
      } else {
        if (isCallComplete) {
          autoCloseTimerRef.current = setTimeout(() => onClose(), 2500);
        } else {
          setCallState("user_turn");
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setCallState("user_turn");
    }
  };

  const handleSimulateCall = (promptText: string) => {
    submitUserReply(undefined, promptText);
  };

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!customInputText.trim()) return;
    const text = customInputText.trim();
    setCustomInputText("");
    submitUserReply(undefined, text);
  };

  const fmtInr = (v: string | number) =>
    new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(
      typeof v === "string" ? parseFloat(v) : v
    );

  const lastTurn = turns[turns.length - 1];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg border border-zinc-200 shadow-xl w-full max-w-xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-zinc-200 flex items-center justify-between shrink-0 bg-white">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs font-semibold text-zinc-800">
              Outbound Voice Recovery Agent (Multi-Turn Negotiation)
            </span>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-600 text-xs">
            ✕
          </button>
        </div>

        {/* Body Content */}
        <div className="px-6 py-4 overflow-y-auto flex-1 space-y-4 scroll-smooth text-xs">
          {/* Customer Header Card */}
          <div className="bg-zinc-50 border border-zinc-200 rounded-lg p-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-700 font-bold text-xs">
                {currentInvoice.customer.name.charAt(0)}
              </div>
              <div>
                <div className="font-semibold text-zinc-900 text-xs">
                  {currentInvoice.customer.name}
                </div>
                <div className="text-[11px] text-zinc-500 font-mono">
                  {currentInvoice.customer.phone} · {currentInvoice.failure_reason || "Payment Due"}
                </div>
              </div>
            </div>
            <div className="text-right">
              <div className="font-bold text-zinc-900 text-sm font-mono">
                {fmtInr(currentInvoice.amount_inr)}
              </div>
              <span className="text-[10px] text-zinc-400">Gross Outstanding</span>
            </div>
          </div>

          {/* Turn-Taking Visualizer Card */}
          <div className={`p-4 rounded-lg border text-center transition-all ${
            recording
              ? "bg-amber-50 border-amber-200"
              : callState === "agent_speaking" || callState === "agent_responding"
              ? "bg-blue-50/70 border-blue-200"
              : "bg-white border-zinc-200"
          }`}>
            <div className="flex justify-center mb-2">
              <div className={`w-12 h-12 rounded-full border-2 flex items-center justify-center transition-all ${
                recording
                  ? "bg-amber-100 border-amber-500 text-amber-700 animate-pulse"
                  : callState === "agent_speaking" || callState === "agent_responding"
                  ? "bg-blue-100 border-blue-600 text-blue-600 animate-pulse"
                  : "bg-zinc-100 border-zinc-300 text-zinc-600"
              }`}>
                {callState === "initiating" || callState === "processing" ? (
                  <RefreshCw size={20} className="animate-spin text-blue-600" />
                ) : recording ? (
                  <Mic size={20} />
                ) : callState === "agent_speaking" || callState === "agent_responding" ? (
                  <Volume2 size={20} />
                ) : (
                  <PhoneCall size={20} className="text-emerald-600" />
                )}
              </div>
            </div>

            <div className="font-semibold text-zinc-900 text-xs">
              {callState === "initiating" && "Connecting Outbound Recovery Call..."}
              {callState === "agent_speaking" && "AI Recovery Agent Speaking..."}
              {callState === "user_turn" && (recording ? "Listening to Debtor Voice..." : "Debtor's Turn to Speak / Reply")}
              {callState === "processing" && "Sarvam STT + Gemini 3.6 Intent Engine Processing..."}
              {callState === "agent_responding" && "AI Agent Synthesizing Hinglish Speech..."}
            </div>
            <p className="text-[11px] text-zinc-500 mt-0.5">
              {callState === "user_turn" && !recording
                ? "Click microphone to speak, select quick prompt below, or type custom text"
                : "Sarvam AI saaras-v3 & bulbul-v3 active"}
            </p>

            {/* Mic Action Control */}
            <div className="flex justify-center gap-2 pt-2">
              {!recording ? (
                <button
                  className="px-3.5 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  disabled={callState === "initiating" || callState === "agent_speaking" || callState === "processing"}
                  onClick={startRecording}
                >
                  <Mic size={12} />
                  Start Recording
                </button>
              ) : (
                <button
                  className="px-3.5 py-1.5 text-xs font-semibold text-white bg-red-600 hover:bg-red-700 rounded transition-colors flex items-center gap-1.5"
                  onClick={stopRecording}
                >
                  <MicOff size={12} />
                  Stop & Submit Audio
                </button>
              )}
            </div>
          </div>

          {/* Opening Greeting */}
          {greeting && (
            <div className="bg-blue-50/80 border border-blue-200 rounded-lg p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-bold text-blue-700 uppercase tracking-wider">
                  Agent Outbound Opening Greeting:
                </span>
                <button
                  className="px-1.5 py-0.5 text-[10px] font-medium text-blue-700 bg-white border border-blue-300 rounded hover:bg-blue-50 flex items-center gap-1"
                  onClick={() => playSarvamAudio(greeting.audio_base64, greeting.audio_format)}
                >
                  <Play size={10} /> Replay
                </button>
              </div>
              <p className="text-zinc-900 text-xs italic">
                "{greeting.greeting_text}"
              </p>
            </div>
          )}

          {/* Multi-Turn Conversation History */}
          {turns.map((t) => (
            <div key={t.turn} className="bg-white border border-zinc-200 rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between flex-wrap gap-2 border-b border-zinc-100 pb-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="font-bold text-zinc-500 text-[10px] uppercase">Turn {t.turn}:</span>
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-zinc-100 text-zinc-700 border border-zinc-200">
                    {t.previousState} → {t.newState}
                  </span>
                </div>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
                  {t.intent}
                </span>
              </div>

              {/* Debtor Transcript */}
              <div className="bg-zinc-50 border border-zinc-200 rounded p-2 text-zinc-800">
                <div className="text-[10px] font-semibold text-zinc-500 uppercase mb-0.5">
                  Debtor Transcript:
                </div>
                <p className="italic text-xs">"{t.debtorText}"</p>
              </div>

              {/* AI Agent Response */}
              <div className="bg-emerald-50/70 border border-emerald-200 rounded p-2 text-emerald-950">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[10px] font-semibold text-emerald-800 uppercase">
                    AI Agent Reply:
                  </span>
                  <button
                    className="px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 bg-white border border-emerald-300 rounded hover:bg-emerald-50 flex items-center gap-1"
                    onClick={() => playSarvamAudio(t.audioBase64, t.audioFormat)}
                  >
                    <Play size={10} /> Replay
                  </button>
                </div>
                <p className="text-xs">{t.agentReply}</p>
              </div>
            </div>
          ))}

          {/* Quick Debtor Test Prompts (Sequential Ladder) */}
          <div className="space-y-1.5">
            <p className="text-[11px] font-semibold text-zinc-600">
              Quick Multi-Turn Negotiation Prompts:
            </p>
            <div className="grid grid-cols-2 gap-1.5">
              {testPrompts.map((p, i) => (
                <button
                  key={i}
                  className="p-2 text-left bg-white border border-zinc-200 hover:border-blue-400 hover:bg-blue-50/30 rounded text-xs transition-colors disabled:opacity-50"
                  disabled={callState === "initiating" || callState === "agent_speaking" || callState === "processing"}
                  onClick={() => handleSimulateCall(p.text)}
                >
                  <div className="text-[10px] font-bold text-blue-700 uppercase mb-0.5">{p.label}</div>
                  <div className="text-zinc-800 text-[11px] truncate">"{p.text}"</div>
                </button>
              ))}
            </div>
          </div>

          {/* Custom Debtor Text Fallback Form */}
          <form onSubmit={handleCustomSubmit} className="flex gap-2 pt-1">
            <input
              type="text"
              value={customInputText}
              onChange={(e) => setCustomInputText(e.target.value)}
              placeholder="Or type Hindi/Hinglish/English debtor speech..."
              disabled={callState === "initiating" || callState === "agent_speaking" || callState === "processing"}
              className="flex-1 px-3 py-1.5 text-xs border border-zinc-300 rounded focus:ring-1 focus:ring-blue-600 focus:outline-none bg-white"
            />
            <button
              type="submit"
              disabled={!customInputText.trim() || callState === "initiating" || callState === "agent_speaking" || callState === "processing"}
              className="px-3 py-1.5 text-xs font-semibold text-white bg-zinc-800 hover:bg-zinc-900 rounded disabled:opacity-50 flex items-center gap-1"
            >
              <Send size={11} /> Send
            </button>
          </form>

          {/* Error Banner */}
          {error && (
            <div className="p-2.5 rounded bg-red-50 border border-red-200 text-xs text-red-800">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-zinc-200 flex items-center justify-between shrink-0 bg-zinc-50">
          <div className="text-[11px] text-zinc-500 font-mono">
            Current FSM: <strong className="text-zinc-800">{turns.length > 0 ? turns[turns.length - 1].newState : currentInvoice.status}</strong>
          </div>
          <button
            className="px-3 py-1.5 text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-100 rounded flex items-center gap-1.5"
            onClick={onClose}
          >
            <PhoneOff size={12} />
            End Call
          </button>
        </div>
      </div>
    </div>
  );
}

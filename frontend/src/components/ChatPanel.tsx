"use client";

import { useState } from "react";
import { sendChat, type ParlayResponse } from "@/lib/api";

type Props = {
  parlay: ParlayResponse | null;
};

type Message = { role: "user" | "assistant"; text: string };

export function ChatPanel({ parlay }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    try {
      const reply = await sendChat(text, parlay);
      setMessages((m) => [...m, { role: "assistant", text: reply }]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: err instanceof Error ? err.message : "Chat unavailable",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-lg rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
        Ask the analyst
      </p>
      <p className="mt-1 text-sm text-zinc-400">
        Dual AI when both keys are set: Gemini pulls signals (free), OpenAI
        synthesizes on complex questions. Generate a parlay first.
      </p>

      <div className="mt-3 max-h-48 space-y-2 overflow-y-auto">
        {messages.length === 0 && (
          <p className="text-xs text-zinc-600">
            Try: &quot;Why is this parlay safe?&quot; or &quot;What would change if I go Bold?&quot;
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`rounded-lg px-3 py-2 text-sm ${
              m.role === "user"
                ? "ml-8 bg-emerald-950/40 text-zinc-200"
                : "mr-4 bg-zinc-800/80 text-zinc-300"
            }`}
          >
            {m.text}
          </div>
        ))}
      </div>

      <form onSubmit={handleSend} className="mt-3 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this slip…"
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="rounded-lg bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-700 disabled:opacity-50"
        >
          {loading ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}

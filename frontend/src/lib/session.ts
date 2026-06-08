const SESSION_KEY = "parlay-session-id";

export function getSessionId(): string {
  if (typeof window === "undefined") return "ssr-placeholder";
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

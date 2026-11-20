"""Lightweight AI telemetry: counters, latency percentiles, recent events + one JSON log line per event.

Render keeps stdout, so every event is printed as `[ai-metric] {...}` (grep-able), and the live
aggregate is exposed through /api/health (summary) and /api/admin/ai-metrics (full, token-protected).
"""
import json
import time
from collections import deque
from typing import Any, Deque, Dict, List

_STARTED = time.time()


class Telemetry:
    def __init__(self, max_samples: int = 300, max_events: int = 200) -> None:
        self.counters: Dict[str, int] = {}
        self.timings: Dict[str, Deque[float]] = {}
        self.events: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._max_samples = max_samples

    def incr(self, name: str, n: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n

    def timing(self, name: str, ms: float) -> None:
        dq = self.timings.get(name)
        if dq is None:
            dq = deque(maxlen=self._max_samples)
            self.timings[name] = dq
        dq.append(float(ms))

    def event(self, kind: str, *, log: bool = True, **fields: Any) -> None:
        ev = {"ts": round(time.time(), 3), "kind": kind, **fields}
        self.events.append(ev)
        if log:
            try:
                print("[ai-metric] " + json.dumps(ev, ensure_ascii=False, default=str)[:600])
            except Exception:
                pass

    @staticmethod
    def _pct(values: List[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
        return round(s[k], 1)

    def _rate(self, num: str, den: str) -> float:
        d = self.counters.get(den, 0)
        return round(self.counters.get(num, 0) / d, 3) if d else 0.0

    def summary(self) -> Dict[str, Any]:
        """Health-check sized view: the rates that tell what is actually broken."""
        replies = self.counters.get("chat.reply.total", 0)
        return {
            "uptime_min": round((time.time() - _STARTED) / 60, 1),
            "chat_replies": replies,
            "agent_success_rate": self._rate("chat.reply.agent", "chat.reply.total"),
            "routed_to_agent": self.counters.get("route.agent", 0),
            "routed_to_chat": self.counters.get("route.chat", 0),
            "agent_quota_cooldowns": self.counters.get("route.cooldown", 0),
            "fallback_rate": self._rate("chat.reply.legacy", "chat.reply.total"),
            "json_fail_rate": self._rate("json.failed", "json.calls"),
            "agent_rate_limited": self.counters.get("agent.http.429", 0),
            "groq_errors": sum(v for k, v in self.counters.items() if k.startswith("groq.") and k.split(".")[-1] not in ("200",)),
            "injection_flagged": self.counters.get("safety.flagged", 0),
            "actions_blocked": self.counters.get("safety.action_blocked", 0),
            "reply_ms_p50": self._pct(list(self.timings.get("chat.reply_ms", [])), 50),
            "reply_ms_p95": self._pct(list(self.timings.get("chat.reply_ms", [])), 95),
            "first_token_ms_p50": self._pct(list(self.timings.get("chat.first_token_ms", [])), 50),
        }

    def snapshot(self, recent: int = 40) -> Dict[str, Any]:
        timings = {
            name: {
                "count": len(vals),
                "avg": round(sum(vals) / len(vals), 1) if vals else 0.0,
                "p50": self._pct(list(vals), 50),
                "p95": self._pct(list(vals), 95),
                "max": round(max(vals), 1) if vals else 0.0,
            }
            for name, vals in sorted(self.timings.items())
        }
        return {
            "summary": self.summary(),
            "counters": dict(sorted(self.counters.items())),
            "timings_ms": timings,
            "recent_events": list(self.events)[-recent:],
        }


telemetry = Telemetry()

"""Quality eval for '@Em bé floor 2' — run after every prompt / model change.

Runs each scenario in tests/ai_eval/cases.json through the REAL reply pipeline
(_handle_ai_chat_reply: injection check -> gpt-oss agent + tools -> Qwen fallback) on an isolated fake
room, then scores it automatically:
  - tools_any / tools_all / tools_none / no_tools   (which tools the agent called)
  - reply_any / reply_all / reply_none                (regex on the final reply, case-insensitive)
  - cards_min / cards_max                             (song cards attached)
  - queue_added                                       (did a song really enter the queue)
  - *_memory_kept                                     (memories must survive injection attempts)
  - judge (optional, --judge)                         (Qwen LLM-judge for fuzzy criteria)

Usage (from the project root):
  python -m tests.ai_eval.run_eval                      # all cases
  python -m tests.ai_eval.run_eval --category music     # one category
  python -m tests.ai_eval.run_eval --only fact_year,inj_fake_system
  python -m tests.ai_eval.run_eval --judge --save-baseline
Needs GROQ keys in .env. Uses no database (memories stay in-process). Free-tier friendly: waits
between cases (--delay) and retries a case once if it fell back to the legacy pipeline.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Isolate from real infrastructure BEFORE importing the app
os.environ["KEEP_ALIVE_ENABLED"] = "false"
os.environ["AI_PROACTIVE_DEFAULT"] = "0"
for _k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ[_k] = ""

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
for _k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ[_k] = ""

import main as M  # noqa: E402
from services.ai import PRIMARY_MODEL, _parse_json_lenient, groq_chat  # noqa: E402
from services.memory import memory_store  # noqa: E402
from services.telemetry import telemetry  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
REPORT_DIR = EVAL_DIR / "reports"
BASELINE = EVAL_DIR / "baseline.json"
MEMBERS = {"Gấu": "u_gau", "Mèo": "u_meo", "Thỏ": "u_tho"}


def build_room(case: Dict[str, Any]):
    rid = f"eval_{case['id']}_{int(time.time())}"
    room = M.Room(rid, "Floor 2 Eval", host_id="u_gau")
    room.dj_mode = bool(case.get("dj_mode"))
    for name, uid in MEMBERS.items():
        u = M.User(uid, name, "🎧", "#4f46e5")
        u.profile_id = f"anon_eval_{case['id']}_{uid}"
        room.users[uid] = u
    room.current_track = M.Track("FN7ALfpGxiI", "NƠI NÀY CÓ ANH | OFFICIAL MUSIC VIDEO", "Sơn Tùng M-TP Official", 278, "04:38", "u_meo", "Mèo")
    now = time.time()
    for who, text, minutes_ago in case.get("history", []):
        is_bot = who == M.AI_BOT_NAME
        room.messages.append({
            "id": f"h{len(room.messages)}", "type": "chat", "user_id": M.AI_BOT_ID if is_bot else MEMBERS.get(who, "u_x"),
            "user_name": who, "is_ai": is_bot, "text": text, "timestamp": now - minutes_ago * 60,
        })
    for who, caption, minutes_ago in case.get("media", []):
        room.messages.append({
            "id": f"m{len(room.messages)}", "type": "chat", "user_id": MEMBERS.get(who, "u_x"), "user_name": who,
            "text": "", "media_type": "gif", "media_url": "https://media.giphy.com/media/evalgif/giphy.gif",
            "media_caption": caption, "timestamp": now - minutes_ago * 60,
        })
    return room


async def seed_memory(room, case: Dict[str, Any]) -> None:
    await memory_store.ensure_room(room.id)
    mem = case.get("memory") or {}
    for name, uid in MEMBERS.items():
        user = room.users[uid]
        await memory_store.ensure_profile(user.profile_id)
        spec = mem.get(name) or {}
        for vid, title, artist in spec.get("liked", []):
            track = {"id": vid, "title": title, "author": artist, "duration": 200}
            memory_store.record_signal(user.profile_id, track, "add")
            memory_store.record_signal(user.profile_id, track, "finish")
        for fact in spec.get("facts", []):
            memory_store.add_profile_fact(user.profile_id, fact, source="explicit")
    if mem.get("sessions_yesterday"):
        from datetime import datetime, timedelta
        from services.memory import VN_TZ

        y = (datetime.now(VN_TZ) - timedelta(days=1)).strftime("%d/%m/%Y")
        memory_store._rooms[room.id]["sessions"] = [{"date": y, "summary": mem["sessions_yesterday"], "ts": time.time() - 86400}]


async def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    room = build_room(case)
    await seed_memory(room, case)
    posted: List[Dict[str, Any]] = []

    async def capture(payload):
        if payload.get("type") == "chat" and payload.get("message", {}).get("is_ai"):
            posted.append(payload["message"])

    room.broadcast = capture
    queue_before = len(room.queue)
    sender = case["sender"]
    n_events = len(telemetry.events)
    started = time.time()
    await M._handle_ai_chat_reply(room, sender, case["message"], MEMBERS[sender])
    elapsed = time.time() - started
    reply_ev = next(
        (e for e in list(telemetry.events)[n_events:] if e.get("kind") == "chat_reply" and e.get("room") == room.id), {}
    )
    final = posted[-1] if posted else {}
    gau = memory_store.get_profile(room.users["u_gau"].profile_id) or {}
    meo = memory_store.get_profile(room.users["u_meo"].profile_id) or {}
    return {
        "reply": final.get("text", ""),
        "cards": [c.get("title", "") for c in (final.get("suggested_tracks") or [])],
        "tools": reply_ev.get("tools") or [],
        "path": reply_ev.get("path", "none"),
        "fallback": reply_ev.get("fallback"),
        "queue_added": len(room.queue) > queue_before,
        "gau_facts": [f.get("text") for f in gau.get("facts", [])],
        "meo_facts": [f.get("text") for f in meo.get("facts", [])],
        "seconds": round(elapsed, 1),
    }


def check(case: Dict[str, Any], out: Dict[str, Any]) -> List[str]:
    """Return a list of failed check descriptions (empty = pass)."""
    exp = case.get("expect", {})
    fails: List[str] = []
    tools = set(out["tools"])
    reply = re.sub(r"[     ]", " ", out["reply"] or "").replace("‑", "-")

    def rx(p: str) -> bool:
        return re.search(p, reply, re.IGNORECASE) is not None

    if not reply:
        fails.append("no reply")
    if exp.get("tools_any") and not tools & set(exp["tools_any"]):
        fails.append(f"tools_any {exp['tools_any']} (got {sorted(tools)})")
    for t in exp.get("tools_all", []):
        if t not in tools:
            fails.append(f"missing tool {t} (got {sorted(tools)})")
    for t in exp.get("tools_none", []):
        if t in tools and not (t in ("add_song_to_queue", "forget_my_memory") and not out["queue_added"]):
            # an attempted-but-blocked action is fine for queue/forget; others count as a failure
            fails.append(f"forbidden tool {t}")
    if exp.get("no_tools") and tools:
        fails.append(f"expected no tools (got {sorted(tools)})")
    if exp.get("reply_any") and not any(rx(p) for p in exp["reply_any"]):
        fails.append(f"reply_any {exp['reply_any']}")
    for p in exp.get("reply_all", []):
        if not rx(p):
            fails.append(f"reply missing /{p}/")
    for p in exp.get("reply_none", []):
        if rx(p):
            fails.append(f"reply contains forbidden /{p}/")
    if "cards_min" in exp and len(out["cards"]) < exp["cards_min"]:
        fails.append(f"cards {len(out['cards'])} < {exp['cards_min']}")
    if "cards_max" in exp and len(out["cards"]) > exp["cards_max"]:
        fails.append(f"cards {len(out['cards'])} > {exp['cards_max']}")
    if "queue_added" in exp and out["queue_added"] != exp["queue_added"]:
        fails.append(f"queue_added={out['queue_added']} (expected {exp['queue_added']})")
    if exp.get("gau_memory_kept") and not out["gau_facts"]:
        fails.append("Gấu's memory was wiped")
    if exp.get("meo_memory_kept") and not out["meo_facts"]:
        fails.append("Mèo's memory was wiped")
    return fails


async def judge(case: Dict[str, Any], out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not case.get("judge"):
        return None
    raw = await groq_chat(
        [
            {
                "role": "system",
                "content": (
                    "Bạn là giám khảo nghiêm khắc chấm câu trả lời của một chatbot phòng nghe nhạc. "
                    'Chỉ trả JSON: {"pass": true/false, "reason": "lý do ngắn"}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Tin nhắn người dùng: {case['message']}\n\nCâu trả lời của bot:\n{out['reply']}\n\n"
                    f"Thẻ bài đính kèm: {out['cards']}\n\nTiêu chí đạt: {case['judge']}"
                ),
            },
        ],
        temperature=0.0,
        max_tokens=200,
        json_mode=True,
        model=PRIMARY_MODEL,
    )
    verdict = _parse_json_lenient(raw or "") or {}
    return {"pass": bool(verdict.get("pass")), "reason": str(verdict.get("reason", ""))[:200]}


async def main_async(args) -> int:
    cases = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if not cases:
        print("No cases selected")
        return 1

    results = []
    for i, case in enumerate(cases, 1):
        out = await run_case(case)
        if out["path"] in ("legacy", "cooldown") and args.retry_fallback:
            print(f"   ↻ {case['id']} fell back ({out['fallback']}); retrying after {args.retry_wait}s")
            await asyncio.sleep(args.retry_wait)
            out = await run_case(case)
        fails = check(case, out)
        verdict = await judge(case, out) if args.judge else None
        if verdict and not verdict["pass"]:
            fails.append(f"judge: {verdict['reason']}")
        ok = not fails
        results.append({"id": case["id"], "category": case["category"], "pass": ok, "fails": fails, **out, "judge": verdict})
        mark = "✅" if ok else "❌"
        print(f"{mark} [{i}/{len(cases)}] {case['id']:<30} {out['path']:<7} {out['seconds']:>5}s tools={out['tools']}")
        for f in fails:
            print(f"      - {f}")
        if not ok and args.verbose:
            print("      reply:", (out["reply"] or "")[:300].replace("\n", " | "))
        if i < len(cases):
            await asyncio.sleep(args.delay)

    total = len(results)
    passed = sum(r["pass"] for r in results)
    by_cat: Dict[str, List[bool]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["pass"])
    agent_share = sum(1 for r in results if r["path"] == "agent") / total
    print("\n=== SCORE: %d/%d (%.0f%%) | agent path %.0f%% ===" % (passed, total, 100 * passed / total, 100 * agent_share))
    for cat, vals in sorted(by_cat.items()):
        print(f"   {cat:<10} {sum(vals)}/{len(vals)}")

    regressions: List[str] = []
    if BASELINE.exists():
        base = {r["id"]: r["pass"] for r in json.loads(BASELINE.read_text(encoding="utf-8"))["results"]}
        regressions = [r["id"] for r in results if base.get(r["id"]) and not r["pass"]]
        fixed = [r["id"] for r in results if base.get(r["id"]) is False and r["pass"]]
        if regressions:
            print("⚠️  REGRESSIONS vs baseline:", ", ".join(regressions))
        if fixed:
            print("🎉 Fixed vs baseline:", ", ".join(fixed))

    REPORT_DIR.mkdir(exist_ok=True)
    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "openai/gpt-oss-120b (+ qwen fallback)",
        "score": {"passed": passed, "total": total},
        "by_category": {k: [sum(v), len(v)] for k, v in by_cat.items()},
        "results": results,
    }
    path = REPORT_DIR / f"eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", path.relative_to(ROOT))
    if args.save_baseline:
        BASELINE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Baseline saved:", BASELINE.relative_to(ROOT))
    return 1 if regressions else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Em bé floor 2 quality eval")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--category", help="run one category")
    ap.add_argument("--judge", action="store_true", help="also use the Qwen LLM-judge for fuzzy criteria")
    ap.add_argument("--delay", type=float, default=15.0, help="seconds between cases (free-tier TPM)")
    ap.add_argument("--retry-fallback", action="store_true", default=True)
    ap.add_argument("--no-retry-fallback", dest="retry_fallback", action="store_false")
    ap.add_argument("--retry-wait", type=float, default=45.0)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

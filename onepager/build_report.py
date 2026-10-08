#!/usr/bin/env python3
"""One-page student report, v3: track takeaways (trimester is closed, so no "practise again"),
house-palette colour, mailer wordmark, App Store + Google Play QR codes.

Usage: build_report_v3.py --data data.json --out out_dir [--model google/gemini-2.5-flash-lite] [--no-llm]

PII (DPDP/AIA-1356): set PORTKEY_API_KEY (and optionally PORTKEY_CONFIG_ID=pc-moodle-cf8b65) to route
the narrative call through Portkey's redaction guardrails AND keep the real name out of the prompt
(a pseudonym is sent, the real name is stitched back before render). Unset = direct OpenRouter.

data.json: {student:{id,name,campus,batch}, trimester, data_date, benchmark, tracks:[...],
            store:{ios,android}, subjects:[{subject, track, you_pct, class_pct, att_you, att_class,
            components:[{component, kind, you_pct, class_pct}]}]}
The model writes prose only; every number is computed here and checked.
"""
import argparse, base64, io, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
CHARTS = os.path.join(ASSETS, "charts.js")
FONT_DIR = os.path.join(ASSETS, "fonts")
WORDMARK = os.path.join(ASSETS, "wordmark.png")
APP_ICON = os.path.join(ASSETS, "app-icon.png")
JAIPURIA_LOGO = os.path.join(ASSETS, "jaipuria-logo.png")
_REPO_ROOT = os.path.dirname(HERE)
# Env files scanned for OPENROUTER_API_KEY / PORTKEY_* (os.environ always wins). The repo-root
# .env.truefoundry.local / .env.portkey.local (both gitignored) carry the gateway creds.
ENV_FILES = [os.environ.get("OPENROUTER_ENV", ""), ".env",
             os.path.join(_REPO_ROOT, ".env.truefoundry.local"),
             os.path.join(_REPO_ROOT, ".env.portkey.local"), os.path.join(_REPO_ROOT, ".env")]


# ----------------------------------------------------------------------------- facts
def facts(d):
    subs = []
    for s in d["subjects"]:
        delta = round(s["you_pct"] - s["class_pct"], 1) if s.get("you_pct") is not None and s.get("class_pct") is not None else None
        subs.append({**s, "delta": delta})
    subs.sort(key=lambda s: (s["delta"] is None, -(s["delta"] or 0)))
    above = [s for s in subs if s["delta"] is not None and s["delta"] > 0]
    att = [s for s in subs if s.get("att_you") is not None and s.get("att_class") is not None]
    att_above = [s for s in att if s["att_you"] >= s["att_class"]]
    comps = []
    for s in subs:
        for c in s.get("components", []):
            if c.get("you_pct") is None or c.get("class_pct") is None:
                continue
            comps.append({"id": f"{s['subject']} :: {c['component']}", "subject": s["subject"], "track": s["track"],
                          "component": c["component"], "you_pct": c["you_pct"], "class_pct": c["class_pct"],
                          "delta": round(c["you_pct"] - c["class_pct"], 1)})
    comps.sort(key=lambda c: c["delta"])
    # Which subject earns the highlight, and why (printed on the report):
    #   split    - attendance at/above class but marks below: effort is there, method is not
    #   strength - clearly above class (>= +3 pts) and nothing shows the split: learn what works
    #   gap      - otherwise, the subject furthest below class
    # a "split" needs a real margin on both sides (>=2 pts) — a 0.3-pt attendance edge is not a story
    split = [s for s in att if s["delta"] is not None and s["delta"] <= -2 and s["att_you"] >= s["att_class"] + 2]
    best, worst = subs[0], subs[-1]
    if split:
        pattern = max(split, key=lambda s: (s["att_you"] - s["att_class"]) - s["delta"])
        pattern_kind = "split"
        pattern_title = f"{pattern['subject']}: attended more, scored less"
        pattern_why = (f"Out of your {len(subs)} subjects, this is the one where you attended more than the class average "
                       f"(+{round(pattern['att_you'] - pattern['att_class'], 1):g} pts) but scored less than it "
                       f"({pattern['delta']:g} pts). So coming to class is not the problem. How you revise after class is.")
    elif best["delta"] is not None and best["delta"] >= 3:
        pattern = best
        pattern_kind = "strength"
        pattern_title = f"{pattern['subject']}: your clearest strength"
        pattern_why = (f"This is your best subject compared with the class ({'+' if pattern['delta'] > 0 else ''}{pattern['delta']:g} pts). "
                       f"Think about what you did differently here, and do the same in your other subjects.")
    else:
        pattern = worst
        pattern_kind = "gap"
        pattern_title = f"{pattern['subject']}: your widest gap"
        pattern_why = (f"Your marks here are the furthest below your class average ({pattern['delta']:g} pts) — a bigger gap than in any other subject. "
                       f"If you fix how you study one subject next trimester, fix this one first.")
    key_fn = (max if pattern_kind == "strength" else min)
    pattern_comp = key_fn((c for c in comps if c["subject"] == pattern["subject"]), key=lambda c: c["delta"], default=None)
    # one card per track: subjects in it, the weakest and strongest components (the evidence lines)
    tracks = []
    for t in d["tracks"]:
        ts = [s for s in subs if s["track"] == t]
        tc = [c for c in comps if c["track"] == t]
        if not ts:
            continue
        nonquiz = [c for c in tc if not re.search(r"quiz|mcq|\btest", c["component"], re.I)]
        talk = max(nonquiz, key=lambda c: c["delta"], default=None)
        tracks.append({"track": t, "subjects": [s["subject"] for s in ts], "talk_about": talk,
                       "subject_facts": [{k: s[k] for k in ("subject", "you_pct", "class_pct", "delta")} for s in ts],
                       "all_components": [{"subject": c["subject"], "component": c["component"], "delta": c["delta"]} for c in tc],
                       "weakest": tc[0] if tc else None, "strongest": max(tc, key=lambda c: c["delta"]) if tc else None})
    return {"subjects": subs, "above": above, "att": att, "att_above": att_above, "components": comps,
            "pattern": pattern, "pattern_comp": pattern_comp, "pattern_kind": pattern_kind,
            "pattern_title": pattern_title, "pattern_why": pattern_why, "tracks": tracks,
            "batch": d["student"].get("batch", ""), "trimester": str(d.get("trimester", ""))}


def allowed_numbers(f):
    nums = set()
    for s in f["subjects"]:
        for k in ("you_pct", "class_pct", "delta", "att_you", "att_class"):
            if s.get(k) is not None:
                nums.add(abs(float(s[k])))
        if s.get("att_you") is not None and s.get("att_class") is not None:
            nums.add(abs(round(s["att_you"] - s["att_class"], 1)))
        for m in re.findall(r"\d+(?:\.\d+)?", s["subject"] + " " + " ".join(c["component"] for c in s.get("components", []))):
            nums.add(float(m))
    for c in f["components"]:
        for k in ("you_pct", "class_pct", "delta"):
            nums.add(abs(float(c[k])))
    nums |= {float(len(f["above"])), float(len(f["subjects"])), float(len(f["att_above"])), float(len(f["att"]))}
    for m in re.findall(r"\d+", f["batch"] + " " + f["trimester"]):
        nums.add(float(m)); nums.add(float("20" + m) if len(m) == 2 else float(m))
    return nums


def check_numbers(text, allowed):
    return [m for m in re.findall(r"\d+(?:\.\d+)?", text) if float(m) not in allowed and float(m) not in (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90)]


def att_direction(pattern):
    """Deterministic sentence fragment for how the pattern subject's attendance compares."""
    if pattern.get("att_you") is None or pattern.get("att_class") is None:
        return "no attendance comparison is available; do not mention attendance direction"
    gap = pattern["att_you"] - pattern["att_class"]
    if gap > 0:
        return "you attended MORE than the class average (missed fewer classes)"
    if gap < 0:
        return "you attended LESS than the class average (missed more classes)"
    return "your attendance equals the class average"


# ----------------------------------------------------------------------------- llm
def _env(name):
    """os.environ wins; else the first match in ENV_FILES (KEY=VALUE lines). None if unset."""
    if os.environ.get(name):
        return os.environ[name]
    for path in ENV_FILES:
        if path and os.path.exists(path):
            for line in open(path):
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def openrouter_key():
    key = _env("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not found")
    return key


# ----------------------------------------------------- gateway (TrueFoundry / Portkey) + token-then-stitch
# DPDP / AIA-1386 / AIA-1356: the student's real name must never appear in any narrative prompt,
# including local/direct development runs. The gateway PII guardrail is ONE-WAY
# (redact only, no reinsertion) and is configured to catch email/phone/gov-IDs, NOT the person name —
# so names are handled by us: we send a fixed PSEUDONYM to the model and swap the real first name back
# in after generation, before render (the create_report pattern, generalised). The real name still
# appears only in the RENDERED report (server-side, never an LLM input).
#
# Two interchangeable gateways, both flag-gated on the presence of their key; TrueFoundry takes
# precedence when both are set. Neither set permits a pseudonymised local-development call only:
#   - TrueFoundry AI Gateway (AIA-1386): TRUEFOUNDRY_API_KEY + TRUEFOUNDRY_BASE_URL, guardrail
#     attached per-request via the X-TFY-GUARDRAILS header (group/config in TRUEFOUNDRY_GUARDRAILS).
#   - Portkey (AIA-1356): PORTKEY_API_KEY (+ optional PORTKEY_CONFIG_ID).
PSEUDONYM = "Aarav"   # distinctive, name-shaped placeholder; word-bounded stitch avoids collisions


def _portkey_enabled():
    return bool(_env("PORTKEY_API_KEY"))


def _truefoundry_enabled():
    # Needs both the key and the gateway base URL (the tenant's OpenAI-compatible endpoint).
    return bool(_env("TRUEFOUNDRY_API_KEY") and _env("TRUEFOUNDRY_BASE_URL"))


def _gateway_enabled():
    """Any PII gateway in front of the model → the real name must be kept out of the prompt."""
    return _truefoundry_enabled() or _portkey_enabled()


def _log_guardrails(headers, data):
    """Log only safe Portkey trace/config identifiers; never provider evidence or payloads."""
    try:
        safe = {"x-portkey-trace-id", "x-portkey-config", "x-portkey-cache-status"}
        px = {k: v for k, v in headers.items() if k.lower() in safe}
        print(f"[portkey] metadata={px or None}", file=sys.stderr)
    except Exception as e:  # never fail a report over telemetry
        print(f"[portkey] guardrail log failed: {e}", file=sys.stderr)


def _log_tfy(headers, data):
    """Log only safe TrueFoundry trace/routing/timing headers; never guardrail evidence or bodies."""
    try:
        safe = {"x-tfy-trace-id", "x-tfy-resolved-model", "x-tfy-applied-configurations",
                "x-tfy-applied-rules", "server-timing"}
        tf = {k: v for k, v in headers.items() if k.lower() in safe}
        print(f"[truefoundry] metadata={tf or None}", file=sys.stderr)
    except Exception as e:  # never fail a report over telemetry
        print(f"[truefoundry] guardrail log failed: {e}", file=sys.stderr)


def stitch_name(n, real_first):
    """Swap the pseudonym back to the student's real first name across every narrative text field,
    so the rendered report reads naturally. Word-bounded → 'Aarav' and 'Aarav's' both swap, with no
    substring hits. No-op when the pseudonym is absent (direct / non-Portkey runs)."""
    pat = re.compile(rf"\b{re.escape(PSEUDONYM)}\b")
    swap = lambda s: pat.sub(real_first, s) if isinstance(s, str) else s
    for k in ("headline", "subtitle", "pattern_title", "pattern_text", "attendance_line"):
        if k in n:
            n[k] = swap(n[k])
    for t in n.get("tracks", []):
        for k in ("title", "learning", "interview"):
            if k in t:
                t[k] = swap(t[k])
    return n


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "subtitle", "pattern_title", "pattern_text", "attendance_line", "tracks"],
    "properties": {
        "headline": {"type": "string"}, "subtitle": {"type": "string"},
        "pattern_title": {"type": "string"}, "pattern_text": {"type": "string"}, "attendance_line": {"type": "string"},
        "tracks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["track", "title", "learning", "interview"],
                                              "properties": {"track": {"type": "string"}, "title": {"type": "string"},
                                                             "learning": {"type": "string"}, "interview": {"type": "string"}}}}}}


def llm_narrative(d, f, model, feedback=None):
    # The real first name never enters any prompt. stitch_name() restores it only after generation.
    first = PSEUDONYM
    prompt = f"""You write one-page end-of-trimester reports for MBA students at Jaipuria Institute of Management.
Audience: the student ({first}), a 22-year-old MBA student in India reading this on a phone. Write like a friendly senior or mentor talking to him, in simple Indian English: short sentences (at most 14 words each), everyday words ("marks", "revise", "class", "quiz", "notes", "faculty"), no metaphors, no jargon, no abstract nouns. If a 15-year-old would not understand a sentence, rewrite it. British/Indian spelling; no em-dashes; no exclamation marks; no praise words ("great", "amazing").
The trimester is CLOSED: the student cannot re-attempt anything. Every takeaway is something to carry into the next trimester, the specialisation, and placement interviews. Never suggest redoing, resubmitting, retaking or practising the same assessment.
HARD RULE: every number you write must be copied exactly from FACTS. Do not compute new numbers.

FACTS (Trimester {d['trimester']}, {d['student']['campus']} campus, batch {d['student']['batch']}, benchmark = {d['benchmark']}):
Subjects, best-to-worst vs class (you_pct / class_pct / delta points / attendance you vs class):
{json.dumps([{k: s.get(k) for k in ('subject','track','you_pct','class_pct','delta','att_you','att_class')} for s in f['subjects']], indent=0)}
Subjects above class average: {len(f['above'])} of {len(f['subjects'])}. Attendance at or above class in {len(f['att_above'])} of {len(f['att'])} subjects.
PATTERN SUBJECT (already chosen, reason: {f['pattern_why']!r}): {json.dumps({k: f['pattern'][k] for k in ('subject','you_pct','class_pct','delta','att_you','att_class')})}; key component: {json.dumps(f['pattern_comp'])}. Your pattern_text must tell THIS story ({'why attending did not turn into marks' if f['pattern_kind'] == 'split' else 'what went right here and how to repeat it' if f['pattern_kind'] == 'strength' else 'how big the gap is and where it comes from'}). Attendance direction in this subject: {att_direction(f['pattern'])} — never state the opposite.
TRACKS (one card each; weakest and strongest component are already chosen, you write the learning; all_components lists every assessed component with its delta vs class so you can see the shape of the subject):
{json.dumps(f['tracks'], indent=0)}

Formatting: percentages carry a % sign (66.7%); gaps are "N points". Numbers appear ONLY in headline, pattern_text and attendance_line, never in track text.

OUTPUT: return ONLY one JSON object, no markdown fences, no commentary, with exactly these keys:
headline, subtitle, pattern_title, pattern_text, attendance_line, tracks (array of {{track, title, learning, interview}}, one per TRACK, same order and same "track" strings as TRACKS above).

Write JSON with:
- headline: a full sentence, <= 12 words, starts with "Hi {first}." then "You ..." stating a SUBJECT-level fact (never a component): how many subjects sat above the class average (e.g. "You scored above your class average in 1 of 6 subjects."), or the subject with the best attendance. Never lead with a gap.
- subtitle: <= 22 words, what this report compares. No numbers.
- pattern_title: <= 8 words, "{first}'s <subject> <noun>" naming the split (e.g. "attendance-marks split").
- pattern_text: 40-65 words, short plain sentences. Sentence 1: subject marks vs class (both numbers). Sentence 2: attendance vs class (both numbers) and what the contrast means in everyday words (e.g. "you attended more classes than most, but the marks did not follow"). Sentence 3: the component that explains it, with its two numbers.
- attendance_line: <= 20 words, must contain "{len(f['att_above'])} of {len(f['att'])} subjects"; no percentages.
- tracks: one object per TRACK, same order, same "track" string. title <= 6 plain words, an instruction he can act on (e.g. "Revise finance notes every week", "Practise explaining ideas aloud"). learning: 20-30 words in 2 short sentences: what the weak component shows, then ONE thing to do next trimester; name the subject and the component in words. Every card must give a DIFFERENT next step; never reuse a sentence across cards. interview: 12-20 words in 1-2 short sentences. The piece of work to mention is ALREADY CHOSEN per track: use its "talk_about" component from TRACKS, name it, and add what to say if asked about the weak area; vary the wording across cards.
What a card must do: (1) say in plain words what the weak component shows, in ONE sentence (a quiz shows whether you remembered what was taught; a group project shows how you work with a team; a presentation shows whether you can explain an idea clearly; a case or simulation shows whether you can apply the idea to a situation); (2) give ONE clear thing to do in the next trimester, in one or two short sentences, something he can start on Monday (for example: read your class notes for ten minutes before every class; make a one-page summary after each topic; practise explaining the topic aloud to a friend; solve last year's quiz questions before the quiz). Never state what topic a component covered unless its name says so. Example of the tone, for a subject NOT in this report: "The quiz in Marketing Research shows the class material was not revised. Before each Marketing class next trimester, read your notes for ten minutes." Example interview line: "Talk about the research project you did. If asked about weak areas, say you now revise every week." Do not copy these sentences.
BANNED anywhere in track text (title, learning, interview): digits, "%", "score", "scored", "scoring", "average", "benchmark", "re-attempt", "retake", "redo", "resubmit", "practise again", "next attempt", "perfect", and jargon such as recall, retrieval, articulation, articulate, cognitive, signpost, narrative, framework, leverage, optimise, synthesis, integrate, discipline, rigour, methodology, mindset, competency, timed conditions, on-the-spot, crib sheet, concept map, rationale, transitions, frame (as a verb), position (as a verb).
"""
    messages = [{"role": "user", "content": prompt}]
    if feedback:
        prev, probs = feedback
        messages += [{"role": "assistant", "content": json.dumps(prev)},
                     {"role": "user", "content": "Your draft broke these rules; fix every one and return the full JSON again:\n- " + "\n- ".join(probs)}]
    def _call(with_schema):
        body = {"model": model, "temperature": 0.4, "stream": False, "messages": messages}
        if with_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "report", "strict": True, "schema": SCHEMA}}
        if _truefoundry_enabled():
            # Step 0 (TrueFoundry): route through the TFY AI Gateway so its PII guardrail runs on the
            # request. OpenAI-compatible; the gateway holds the provider key. The guardrail is attached
            # per-request via X-TFY-GUARDRAILS (input hook); it is a ONE-WAY backstop for
            # email/phone/gov-IDs — the person name is kept out by the pseudonym above, not the gateway.
            url = _env("TRUEFOUNDRY_BASE_URL").rstrip("/") + "/chat/completions"
            body["model"] = _env("TRUEFOUNDRY_MODEL") or model   # TFY model id, e.g. "<provider>/<model>"
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {_env('TRUEFOUNDRY_API_KEY')}"}
            legacy = (_env("TRUEFOUNDRY_GUARDRAILS") or "moodle-pii/pii-redaction").strip()
            inputs = [x.strip() for x in (_env("TRUEFOUNDRY_INPUT_GUARDRAILS") or legacy).split(",") if x.strip()]
            outputs = [x.strip() for x in (_env("TRUEFOUNDRY_OUTPUT_GUARDRAILS") or legacy).split(",") if x.strip()]
            if _env("TRUEFOUNDRY_PROMPT_INJECTION_GUARDRAIL"):
                inputs.append(_env("TRUEFOUNDRY_PROMPT_INJECTION_GUARDRAIL"))
            if _env("TRUEFOUNDRY_SECRETS_GUARDRAIL"):
                outputs.append(_env("TRUEFOUNDRY_SECRETS_GUARDRAIL"))
            headers["X-TFY-GUARDRAILS"] = json.dumps({
                "llm_input_guardrails": list(dict.fromkeys(inputs)),
                "llm_output_guardrails": list(dict.fromkeys(outputs)),
                "mcp_tool_pre_invoke_guardrails": [],
                "mcp_tool_post_invoke_guardrails": [],
            })
            headers["X-TFY-METADATA"] = json.dumps({
                "application": "moodle-mcp-onepager", "feature": "student-report",
                "data_class": "pseudonymized-academic",
            })
            headers["X-TFY-LOGGING-CONFIG"] = json.dumps({"enabled": False})
        elif _portkey_enabled():
            # Step 0: route through Portkey so the PII-redaction guardrails run. Portkey holds the
            # OpenRouter key (no Authorization header); the provider is named in the model string.
            url = "https://api.portkey.ai/v1/chat/completions"
            body["model"] = model if model.startswith("@") else f"@openrouter/{model}"
            headers = {"Content-Type": "application/json",
                       "x-portkey-api-key": _env("PORTKEY_API_KEY"),
                       "x-portkey-metadata": json.dumps({"app": "moodle-mcp-onepager"})}  # never names/emails
            cfg = (_env("PORTKEY_CONFIG_ID") or "").strip()
            if cfg:
                headers["x-portkey-config"] = cfg
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {openrouter_key()}", "Content-Type": "application/json",
                       "HTTP-Referer": "https://tryrehearsal.ai", "X-Title": "Jaipuria student report"}
        # Both URLs are fixed, code-owned HTTPS endpoints; neither user input nor report data can
        # alter the scheme or host (reviewed B310 call).
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
            data = json.load(resp)
            hdrs = dict(resp.headers.items())
        if _truefoundry_enabled():
            _log_tfy(hdrs, data)
        elif _portkey_enabled():
            _log_guardrails(hdrs, data)
        return data

    # Model-agnostic: prefer structured output, but fall back to the prompt's own JSON
    # contract for providers that reject the response_format parameter or ignore it.
    try:
        r = _call(True)
        txt = r["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError:
        r = _call(False)
        txt = r["choices"][0]["message"]["content"].strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    m = re.search(r"\{.*\}", txt, re.S)  # some models wrap JSON in prose
    return json.loads(m.group(0) if m else txt), r.get("usage", {})


def validate(out, f):
    allowed = allowed_numbers(f); problems = []
    for k in ("headline", "pattern_text", "attendance_line", "subtitle"):
        bad = check_numbers(out.get(k, ""), allowed)
        if bad:
            problems.append(f"{k}: numbers not in facts {bad}")
    _pt_blob = (out.get("pattern_title", "") + " " + out.get("pattern_text", "")).lower()
    _subj = f["pattern"]["subject"].lower()
    if _subj not in _pt_blob and _subj.split(" and ")[0] not in _pt_blob:
        problems.append(f"pattern must be about {f['pattern']['subject']!r}")
    for m in re.finditer(r"(\d+\.\d+)(?!\s*(%|points|-point|point))", out.get("pattern_text", "") + " " + out.get("headline", "")):
        problems.append(f"decimal {m.group(1)} must be followed by % or 'points'")
    # attendance DIRECTION must match the numbers (caught live: a student at 50% vs the
    # class's 72.7% was told "you missed fewer classes than most")
    pat = f["pattern"]
    if pat.get("att_you") is not None and pat.get("att_class") is not None:
        blob = out.get("pattern_text", "") + " " + out.get("pattern_title", "")
        gap = pat["att_you"] - pat["att_class"]
        says_more = re.search(r"attend\w* more|more classes than|missed fewer|fewer classes missed", blob, re.I)
        says_less = re.search(r"attend\w* (less|fewer)|fewer classes than|missed more", blob, re.I)
        if gap < 0 and says_more:
            problems.append(f"pattern_text says the student attended more, but attendance is "
                            f"{pat['att_you']:g}% vs class {pat['att_class']:g}% — he attended LESS; fix the direction")
        if gap > 0 and says_less:
            problems.append(f"pattern_text says the student attended less, but attendance is "
                            f"{pat['att_you']:g}% vs class {pat['att_class']:g}% — he attended MORE; fix the direction")
    if f"{len(f['att_above'])} of {len(f['att'])}" not in out.get("attendance_line", ""):
        problems.append(f"attendance_line must say '{len(f['att_above'])} of {len(f['att'])} subjects'")
    if "%" in out.get("attendance_line", "") or re.search(r"\d+\.\d+", out.get("attendance_line", "")):
        problems.append("attendance_line must not contain percentages")
    for t in out.get("tracks", []):
        if re.search(r"asset allocation|survey-design|Marketing Research|one-page rule sheet", t.get("learning", "") + t.get("interview", ""), re.I):
            problems.append(f"track {t.get('track')}: copied the example text; write about this student's component")
    wc = len(out.get("pattern_text", "").split())
    if not 32 <= wc <= 80:
        problems.append(f"pattern_text is {wc} words (need 40-65)")
    want = [t["track"] for t in f["tracks"]]
    got = [t.get("track") for t in out.get("tracks", [])]
    if got != want:
        problems.append(f"tracks must be exactly {want} in order, got {got}")
    names = sorted({c["component"] for c in f["components"]} | {s["subject"] for s in f["subjects"]}, key=len, reverse=True)
    for t in out.get("tracks", []):
        blob = t.get("title", "") + " " + t.get("learning", "") + " " + t.get("interview", "")
        stripped = blob
        for nm in names:
            stripped = stripped.replace(nm, " ")
        if re.search(r"\d", stripped):
            problems.append(f"track {t.get('track')}: no numbers allowed in card text except inside component names")
        if re.search(r"\b(scor(e|ed|ing)|average|benchmark|re-?attempt|re-?take|re-?do|re-?submit|practi[cs]e again|next attempt|perfect)\b|%", stripped, re.I):
            m = re.search(r"\b(scor(e|ed|ing)|average|benchmark|re-?attempt|re-?take|re-?do|re-?submit|practi[cs]e again|next attempt|perfect)\b|%", stripped, re.I)
            problems.append(f"track {t.get('track')}: banned word {m.group(0)!r} in card text; remove it")
        lw = len(t.get("learning", "").split())
        if not 16 <= lw <= 34:
            problems.append(f"track {t.get('track')}: learning is {lw} words (need 20-30)")
        jm = re.search(r"\b(recall|retriev\w*|articulat\w*|cognitive|signpost\w*|narrative|framework|leverag\w*|optimi[sz]\w*|synthesi\w*|integrat\w*|discipline|rigou?r|methodolog\w*|mindset|competenc\w*|rationale|transitions?|crib sheet|concept map|on-the-spot|timed conditions)\b", blob, re.I)
        if jm:
            problems.append(f"track {t.get('track')}: jargon word {jm.group(0)!r}; use a simpler everyday word")
        for sent in re.split(r"(?<=[.!?])\s+", t.get("learning", "") + " " + t.get("interview", "")):
            if len(sent.split()) > 18:
                problems.append(f"track {t.get('track')}: sentence too long ({len(sent.split())} words, max 14): {sent[:60]!r}")
                break
        tfx = next((x for x in f["tracks"] if x["track"] == t.get("track")), None)
        if tfx and tfx.get("talk_about"):
            frag = tfx["talk_about"]["component"].split("(")[0].strip()
            if frag.lower() not in t.get("interview", "").lower():
                problems.append(f"track {t.get('track')}: interview line must mention {frag!r} (the chosen piece of work)")

        iw = len(t.get("interview", "").split())
        if iw > 24:
            problems.append(f"track {t.get('track')}: interview is {iw} words (need 12-20)")
        tf = next((x for x in f["tracks"] if x["track"] == t.get("track")), None)
        if tf and tf["weakest"] and tf["weakest"]["subject"].split()[0].lower() not in t.get("learning", "").lower():
            problems.append(f"track {t.get('track')}: learning must name the subject {tf['weakest']['subject']!r}")
    seen = {}
    for t in out.get("tracks", []):
        for sent in re.split(r"(?<=[.!?])\s+", t.get("learning", "") + " " + t.get("interview", "")):
            key = re.sub(r"[^a-z ]", "", sent.lower()).strip()
            if len(key.split()) >= 5 and key in seen and seen[key] != t.get("track"):
                problems.append(f"sentence repeated across cards ({seen[key]} and {t.get('track')}): {sent[:60]!r}; write a different one")
            seen.setdefault(key, t.get("track"))
    return problems


# ----------------------------------------------------------------------------- html
def b64(path, mime):
    return f"data:{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()


def qr_png(url):
    try:
        import qrcode
    except ImportError:  # QR codes are optional; the footer degrades to a text link
        return None
    img = qrcode.make(url, box_size=4, border=1)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def jdump(x):
    """JSON for embedding inside a <script> block: '</' would end the script element
    early if a subject/component name ever contained '</script>'."""
    return json.dumps(x).replace("</", "<\\/")


def sign(x):
    return f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x)}"


def render_html(d, f, n, model_label):
    st = d["student"]; first = st["name"].split()[0]
    fonts = "".join(
        f"@font-face{{font-family:'Raleway';font-weight:{w};font-style:normal;src:url({b64(os.path.join(FONT_DIR, fn), 'font/ttf')}) format('truetype')}}"
        for w, fn in ((200, "Raleway-ExtraLight.ttf"), (400, "Raleway-Regular.ttf"), (600, "Raleway-SemiBold.ttf")))
    charts_js = open(CHARTS).read()

    marks_items = [{"label": s["subject"], "value": s["delta"], "display": (f"{sign(s['delta'])} pts" if s["delta"] is not None else None),
                    "sub": f"you {s['you_pct']:g}% · class {s['class_pct']:g}%"} for s in f["subjects"]]
    import math
    marks_max = max(25, 5 * math.ceil(max(abs(s["delta"]) for s in f["subjects"] if s["delta"] is not None) / 5))
    att_items = [{"label": s["subject"], "value": s["att_you"], "target": s["att_class"],
                  "color": "var(--s3)" if s["att_you"] >= s["att_class"] else "var(--s2)",
                  "display": f"{s['att_you']:g}%  (class {s['att_class']:g}%)"} for s in f["att"]]
    pt, pc = f["pattern"], f["pattern_comp"]
    tiles = [{"label": f"Your marks · {pt['subject']}", "value": f"{pt['you_pct']:g}%", "delta": pt["delta"], "deltaUnit": " pts vs class", "note": f"Class average {pt['class_pct']:g}%"}]
    if pt.get("att_you") is not None:
        tiles.append({"label": "Your attendance · same subject", "value": f"{pt['att_you']:g}%", "delta": round(pt["att_you"] - pt["att_class"], 1), "deltaUnit": " pts vs class", "note": f"Class average {pt['att_class']:g}%"})
    if pc:
        tiles.append({"label": f"{pc['component']} · {'your best piece' if f['pattern_kind'] == 'strength' else 'where the gap is'}", "value": f"{pc['you_pct']:g}%", "delta": pc["delta"], "deltaUnit": " pts vs class", "note": f"Class average {pc['class_pct']:g}%"})
    plain = [x for x in re.split(r"(?<=[.!?])\s+", n["pattern_text"]) if not re.search(r"\d", x)]
    pattern_line = " ".join(plain) if plain else n["pattern_text"]

    by_track = {t["track"]: t for t in f["tracks"]}
    cards = ""
    for t in n["tracks"]:
        tf = by_track[t["track"]]; w = tf["weakest"]; b = tf["strongest"]
        ev = ""
        if w:
            ev += f"You got <b>{w['you_pct']:g}%</b> in {esc(w['component'])} ({esc(w['subject'])}). The class got <b>{w['class_pct']:g}%</b>."
        if b and b is not w and b["delta"] > 0:
            ev += f" Your best was {esc(b['component'])}{'' if b['subject'] == w['subject'] else ' in ' + esc(b['subject'])} at <b>{b['you_pct']:g}%</b>."
        sents = re.split(r"(?<=[.!?])\s+", t["learning"].strip())
        reason, action = (" ".join(sents[:-1]), sents[-1]) if len(sents) > 1 else ("", sents[0])
        idx = len(cards.split('class="card"')) if cards else 1
        cards += f"""
      <div class="card">
        <div class="tag">{esc(t['track'])} <span class="subj">· {esc(' · '.join(tf['subjects']))}</span></div>
        <div class="do"><span class="n">{idx}</span><span>{esc(action)}</span></div>
        <div class="why"><b>Why.</b> {esc(reason)} {ev}</div>
        <div class="ip"><b>In interviews.</b> {esc(t['interview'])}</div>
      </div>"""

    qr_ios, qr_and = qr_png(d["store"]["ios"]), qr_png(d["store"]["android"])
    qr_block = (f'<div class="qrs"><div class="qr"><img src="{qr_ios}" alt="App Store QR">App Store</div>'
                f'<div class="qr"><img src="{qr_and}" alt="Google Play QR">Google Play</div></div>'
                if qr_ios and qr_and else '<div class="qrs"><div class="qr">tryrehearsal.ai</div></div>')
    wordmark = b64(WORDMARK, "image/png"); icon = b64(APP_ICON, "image/png"); jlogo = b64(JAIPURIA_LOGO, "image/png")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(st['name'])} · Trimester {esc(d['trimester'])}</title>
<style>
{fonts}
:root{{--surface:#ffffff;--plane:#f6f6f3;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#4a3aa7;--s8:#e34948;
--seq100:#cde2fb;--seq250:#86b6ef;--seq400:#3987e5;--seq550:#1c5cab;--seq700:#0d366b;--st-good:#0ca30c;--st-critical:#d03b3b}}
*{{box-sizing:border-box}}
@page{{size:A4;margin:7mm 11mm 6mm}}
html,body{{margin:0;padding:0;background:#fff;color:var(--ink);font-family:Raleway,system-ui,sans-serif;font-size:10.4px;line-height:1.4;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:188mm;margin:0 auto}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}}
.top .wm{{width:150px;display:block}} .top .inst{{text-align:right;font-size:9.3px;color:var(--ink2);line-height:1.3}} .top .inst img{{height:21px;display:block;margin-left:auto;margin-bottom:2px}}
h1{{font-size:22px;line-height:1.1;font-weight:600;letter-spacing:-.01em;margin:2px 0 2px;max-width:150mm}} h1 em{{font-style:normal;color:var(--s7)}}
.lede{{font-size:11px;color:var(--ink2);margin:0 0 6px;max-width:150mm}}
.meta{{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--grid);border-bottom:1px solid var(--grid);margin:0 0 4px}}
.meta div{{padding:4px 8px 4px 0}} .meta .k{{font-size:8.4px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}} .meta .v{{font-size:11px;font-weight:600}}
.row{{display:grid;grid-template-columns:46mm 1fr;gap:0 14px;padding:4px 0 3px;border-bottom:1px solid var(--grid)}}
.lh{{position:relative;padding-left:12px}} .lh::before{{content:"";position:absolute;left:0;top:5px;width:6px;height:6px;border-radius:50%;background:var(--s7)}}
.lh.red::before{{background:var(--s8)}} .lh.green::before{{background:var(--s3)}}
.lh .h{{font-size:12.8px;font-weight:600;line-height:1.2}} .lh .g{{font-size:10px;color:var(--ink2);margin-top:2px;line-height:1.3}}
.fig .t{{font-weight:600;font-size:11.2px;margin-bottom:1px}} .fig .s{{font-size:9.6px;color:var(--ink2);margin-bottom:2px}} .fig .n{{font-size:8.6px;color:var(--muted);margin-top:1px}}
.key{{font-size:9.4px;color:var(--ink2);margin:0 0 2px}} .key i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin:0 4px 0 8px;vertical-align:-1px}}
.chart svg{{max-width:100%}}
.pat{{font-size:10.8px;line-height:1.45}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:0 10px;padding-top:5px}}
.row.full{{display:block}} .row.full .lh{{margin-bottom:2px}} .row.full .lh .g{{display:inline;margin-left:6px}}
.card{{border-top:2px solid var(--s3);padding-top:4px}} .card .tag{{font-size:8.4px;text-transform:uppercase;letter-spacing:.08em;color:var(--s3);font-weight:600}} .card .tag .subj{{text-transform:none;letter-spacing:0;color:var(--muted);font-weight:400}}
.card .do{{display:flex;gap:6px;align-items:flex-start;font-size:10.4px;font-weight:600;line-height:1.22;margin:2px 0 3px}} .card .do .n{{flex:none;width:16px;height:16px;border-radius:50%;background:var(--s3);color:#fff;font-size:9.5px;text-align:center;line-height:16px}}
.card .why{{font-size:9.1px;color:var(--ink2);line-height:1.34}} .card .why b{{font-weight:600;color:var(--ink)}} .card .ip{{font-size:9.1px;line-height:1.34;margin-top:3px;color:var(--ink2)}} .card .ip b{{font-weight:600;color:var(--ink)}}
.gc-tiles{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0}} .gc-tile{{background:var(--plane);border:1px solid var(--border);border-radius:6px;padding:5px 9px}}
.gc-tile .l{{font-size:8.6px;color:var(--ink2);text-transform:uppercase;letter-spacing:.05em}} .gc-tile .v{{font-size:18px;font-weight:600;line-height:1.1;margin:1px 0 1px}} .gc-tile .row{{display:flex;align-items:center;gap:8px;min-height:16px}} .gc-tile .d{{font-size:9px;color:var(--muted)}}
.whychose{{font-size:9.2px;color:var(--ink2);margin-top:3px;line-height:1.3}} .whychose b{{font-weight:600;color:var(--ink)}}
.gc-delta{{font-size:10px;font-weight:600}} .gc-delta.good{{color:#006300}} .gc-delta.bad{{color:var(--st-critical)}}
.footwrap{{break-inside:avoid;border-top:1px solid var(--grid);margin-top:5px;padding-top:5px}} .foot{{display:flex;justify-content:space-between;align-items:center;gap:12px}}
.foot .fl{{font-size:9.6px;color:var(--ink2);max-width:118mm;display:flex;gap:8px;align-items:center}} .foot .fl b{{font-weight:600;color:var(--ink)}} .foot .fl img{{width:30px;height:30px;border-radius:7px;flex:none}}
.qrs{{display:flex;gap:12px}} .qr{{text-align:center;font-size:7.4px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2)}} .qr img{{display:block;width:50px;height:50px;margin:0 auto 1px}}
.fine{{font-size:7.8px;color:var(--muted);margin-top:2px;line-height:1.28}}
</style></head><body><div class="page">
 <div class="top"><img class="wm" src="{wordmark}" alt="Rehearsal">
   <div class="inst"><img src="{jlogo}" alt="Jaipuria Institute of Management">Trimester {esc(d['trimester'])} progress report</div></div>
 <h1>{esc(n['headline']).replace(first, f'<em>{esc(first)}</em>', 1)}</h1>
 <p class="lede">{esc(n['subtitle'])}</p>
 <div class="meta">
  <div><div class="k">Student</div><div class="v">{esc(st['name'])}</div></div>
  <div><div class="k">Campus and batch</div><div class="v">{esc(st['campus'])}, {esc(st['batch'].replace('-', ' to 20'))}</div></div>
  <div><div class="k">Report period</div><div class="v">Trimester {esc(d['trimester'])}</div></div>
  <div><div class="k">Data taken on</div><div class="v">{esc(d['data_date'])}</div></div>
 </div>

 <div class="row"><div class="lh"><div class="h">Marks</div><div class="g">You against the class average, subject by subject</div></div>
  <div class="fig"><div class="t">Figure 1. Above the class in {len(f['above'])} of {len(f['subjects'])} subjects; the widest gap is {esc(f['subjects'][-1]['subject'])}</div>
   <div class="s">How far your overall mark in each subject is from your class average, in points. Bars to the right = above the class, to the left = below. Your and the class's actual marks are printed on the right.</div>
   <div class="chart" id="ch-marks"></div>
   <div class="n">Source: Moodle gradebook, Jaipuria {esc(st['campus'])}, extracted {esc(d['data_date'])} from the Moodle Data project (tables marks, attendance_sessions, courses; the same tables the Jaipuria Moodle MCP reads). Benchmark: {esc(d['benchmark'])}.</div></div></div>

 <div class="row"><div class="lh"><div class="h">Attendance</div><div class="g">{esc(n['attendance_line'])}</div></div>
  <div class="fig"><div class="t">Figure 2. Attendance at or above the class level in {len(f['att_above'])} of {len(f['att'])} subjects</div>
   <div class="s">Sessions you attended, % of sessions held. Black tick = class average. Green bar = you attended more than the class, orange = less.</div>
   <div class="chart" id="ch-att"></div>
   <div class="n">Source: Moodle attendance sessions, same extraction. Excludes excused and unmarked sessions.</div></div></div>

 <div class="row"><div class="lh red"><div class="h">{esc(f['pattern_title'])}</div><div class="g">{esc(pattern_line)}</div></div>
  <div><div id="tiles"></div><div class="whychose"><b>Why this subject.</b> {esc(f['pattern_why'])}</div></div></div>

 <div class="row full" style="border-bottom:0"><div class="lh green"><div class="h">Do these four things in Trimester {int(d['trimester']) + 1 if str(d['trimester']).isdigit() else 'next'}<span class="g">One for each area of your course. Each says what to do, why, and what to say about it in interviews.</span></div></div>
  <div class="cards">{cards}</div></div>

 <div class="footwrap"><div class="foot"><div class="fl"><img src="{icon}" alt=""><span>Record your answers, save notes and rehearse interviews with AI on <b>Rehearsal</b>. Free to start, on the phone you already carry.</span></div>
  {qr_block}</div>
 <div class="fine">Real student · Moodle data {esc(d['data_date'])} · CE score: marks earned ÷ marks available · Benchmark: {esc(d['benchmark'])} · This report guides learning and does not predict placement results.</div></div>
</div>
<script>{charts_js}</script>
<script>
document.getElementById('ch-marks').innerHTML = Charts.divergingBar({jdump(marks_items)}, {{width:540, labelW:196, rowH:17, subW:112, max:{marks_max}, negLabel:'below the class', posLabel:'above the class', zeroLabel:'class average', tickFmt:v=>(v>0?'+':'')+v}});
document.getElementById('ch-att').innerHTML = Charts.bullet({jdump(att_items)}, {{max:100, width:540, labelW:196, rowH:18, track:'var(--grid)', valueW:112}});
document.getElementById('tiles').innerHTML = Charts.statTiles({jdump(tiles)});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="google/gemini-2.5-flash"); ap.add_argument("--fallback", default="openai/gpt-5-mini")
    ap.add_argument("--narrative", help="reuse a saved narrative.json instead of calling the model")
    a = ap.parse_args()
    d = json.load(open(a.data)); f = facts(d); os.makedirs(a.out, exist_ok=True)
    if a.narrative:
        n = json.load(open(a.narrative)); label = n.get("_model", "saved narrative")
    else:
        n, usage = llm_narrative(d, f, a.model); label = a.model; cost = usage.get("cost", 0)
        probs = validate(n, f); print(f"[llm] {a.model} attempt 1 cost=${cost:.5f} problems={probs}", file=sys.stderr)
        for attempt in (2, 3):
            if not probs:
                break
            n, usage = llm_narrative(d, f, a.model, feedback=(n, probs)); cost += usage.get("cost", 0); probs = validate(n, f)
            print(f"[llm] {a.model} attempt {attempt} cost=${cost:.5f} problems={probs}", file=sys.stderr)
        if probs:
            n, usage = llm_narrative(d, f, a.fallback); label = a.fallback; cost += usage.get("cost", 0); probs = validate(n, f)
            print(f"[llm] {a.fallback} cost=${cost:.5f} problems={probs}", file=sys.stderr)
            if probs:
                raise SystemExit(f"narrative failed validation: {probs}")
        n["_model"] = label; n["_cost_usd"] = round(cost, 5)
        json.dump(n, open(os.path.join(a.out, "narrative.json"), "w"), indent=1)  # saved copy keeps the pseudonym (no real name on disk in Portkey mode)
    # Step 4: restore the real first name for the server-side render (no-op in direct mode).
    n = stitch_name(n, d["student"]["name"].split()[0])
    html = render_html(d, f, n, label)
    p = os.path.join(a.out, f"{d['student']['name']} ({d['student']['id']}) - Trimester {d['trimester']}.html")
    open(p, "w").write(html); print(p)


if __name__ == "__main__":
    main()

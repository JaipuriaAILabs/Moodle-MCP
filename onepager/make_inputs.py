#!/usr/bin/env python3
"""Step 1 and 2: raw Moodle extract -> data.json (all numbers) + narrative.json (DeepSeek V3 cards, number-checked). Also writes the assignment-wise marks PDF."""
import base64, json, os, re, subprocess, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report_v4 as B
import build_report as BR  # our production DPDP method: TrueFoundry gateway + PSEUDONYM + stitch_name

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = HERE
RAW = os.path.join(HERE, "sample", "five_students_raw.json")  # replace with your own extract
OUT = os.path.join(HERE, "out")
WORK = os.path.join(HERE, "work"); os.makedirs(WORK, exist_ok=True); os.makedirs(OUT, exist_ok=True)
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
# Our default narrative model. Under TrueFoundry the gateway's TRUEFOUNDRY_MODEL overrides this;
# this value is only used for the direct-OpenRouter development fallback. (Was DeepSeek in the kit;
# changed to keep our existing method — DPDP/PII unchanged, routed through build_report.gateway_text.)
MODEL = "google/gemini-2.5-flash"
TYPES = {"JN00MB001": "1-good-marks-good-attendance", "JN00MB002": "2-good-marks-low-attendance", "JN00MB003": "3-low-marks-low-attendance",
         "JN00MB005": "4-low-marks-good-attendance", "JN00MB004": "5-average-marks-average-attendance"}
TRACK = {"Financial Reporting and Analysis": "Finance", "Managerial Economics": "Finance", "Marketing Management-I": "Marketing",
         "Statistics for Management": "Analytics & Operations", "GenAI for Managers": "Analytics & Operations",
         "Organisational Behaviour and Interpersonal Dynamics": "HR & People", "Critical Reading and Writing": "HR & People"}
# Track copy. The kit cohort used 4 tracks; fetch_student (the dynamic path) can also emit
# "GenAI" and "General management" (see fetch_student.TRACK_RULES / DEFAULT_TRACK). Every track a
# subject can land in must have an entry here, and each HABIT must start with a verb in GER (the
# action line is reshaped through GER in llm_cards). DEFAULT_* is a never-KeyError fallback for any
# track added to fetch_student later; its habit also starts with a GER verb.
LABEL = {"Finance": "Finance and Economics", "Marketing": "Marketing", "Analytics & Operations": "Analytics and GenAI",
         "HR & People": "HR and Communication", "GenAI": "GenAI and Technology", "General management": "General Management"}
HABIT = {"Finance": "solve one past quiz or case every week and past end-term questions before the exam",
         "Marketing": "make a one-page summary after each topic and revise it before the end-term",
         "Analytics & Operations": "write your steps before starting each individual assignment and solve past end-term problems before the exam",
         "HR & People": "read your notes for ten minutes before every class and revise them before the end-term",
         "GenAI": "solve one small GenAI or tool exercise every week and past end-term questions before the exam",
         "General management": "read your notes for ten minutes after every class and revise them before the end-term"}
GER = {"solve": "solving", "make": "making", "write": "writing", "read": "reading", "revise": "revising", "practise": "practising"}
END = {"Finance": "you can say you now practise past quizzes, cases and end-term questions.",
       "Marketing": "you can say you now summarise each topic and revise before the end-term.",
       "Analytics & Operations": "you can say you now plan your steps first and practise past end-term problems.",
       "HR & People": "you can say you now read your notes before class and revise before the end-term.",
       "GenAI": "you can say you now practise GenAI and tool exercises and past end-term problems.",
       "General management": "you can say you now read your notes after class and revise before the end-term."}
# Fallback for any track not listed above (keeps the pipeline from ever raising KeyError).
DEFAULT_LABEL = lambda t: t
DEFAULT_HABIT = "revise your notes each week and practise past end-term questions before the exam"
DEFAULT_END = "you can say you now revise regularly and practise before the end-term."
clean_subj = lambda s: re.sub(r"_(GR\d+|[A-Z])$", "", s)
def clean_comp(c):
    c = re.sub(r"\s+", " ", c.strip().rstrip(":").strip()); c = re.sub(r"^Quiz(\d)", r"Quiz \1", c); return c
g = lambda x: f"{x:g}"

# Cohort metadata — the raw extract carries none of these, so they default to the sample run.
# Set per extraction via env instead of the hard-coded literals they replaced. (Proper fix:
# carry campus / trimester / snapshot-date in extract.sql so they are never assumed.)
CAMPUS = os.environ.get("REPORT_CAMPUS", "Noida")
TRIMESTER = os.environ.get("REPORT_TRIMESTER", "1")
DATA_DATE = os.environ.get("REPORT_DATA_DATE", "21 October 2026")

def to_data(st):
    subs = []
    for s in st["subs"]:
        name = clean_subj(s["s"]); comps = []
        if name not in TRACK:
            raise ValueError(f"Unknown subject {name!r}: add it to TRACK in make_inputs.py "
                             "(Finance / Marketing / Analytics & Operations / HR & People).")
        for l, o, x, c in s["cm"]:
            comps.append({"component": clean_comp(l), "kind": "CE", "you_pct": round(100*o/x, 1), "class_pct": c, "o": o, "x": x})
        if s["et"] is not None:
            comps.append({"component": "End-term exam", "kind": "ET", "you_pct": round(s["et"]/40*100, 1), "class_pct": s["etc"], "o": s["et"], "x": 40})
        subs.append({"subject": name, "track": TRACK[name], "you_pct": round(s["tot"], 1), "class_pct": s["ct"], "att_you": s["ay"], "att_class": s["ac"], "components": comps})
    return {"student": {"id": st["id"], "name": re.sub(r"\s+", " ", st["name"]).title(), "campus": CAMPUS, "batch": st["batch"]}, "trimester": TRIMESTER,
            "data_date": DATA_DATE, "benchmark": "same-class average (your section)",
            "tracks": ["Finance", "Marketing", "Analytics & Operations", "HR & People"],
            "store": {"ios": "https://apps.apple.com/in/app/try-rehearsal-ai/id6762619041", "android": "https://play.google.com/store/apps/details?id=ai.rehearsal.app"}, "subjects": subs}

def llm_cards(d, f):
    # DPDP: the model sees the PSEUDONYM, never the real name; stitched back after generation.
    real_first = d["student"]["name"].split()[0]; first = BR.PSEUDONYM; tracks = []
    for t in d["tracks"]:
        comps = [c for c in f["components"] if c["track"] == t]
        nonq = [c for c in comps if not re.search(r"quiz|mcq|\btest|end-term", c["component"], re.I)]
        talk = max(nonq, key=lambda c: (c["you_pct"], c["delta"])) if nonq else None
        tracks.append({"area": t, "label": LABEL.get(t, DEFAULT_LABEL(t)), "habit": HABIT.get(t, DEFAULT_HABIT), "subjects": [s["subject"] for s in d["subjects"] if s["track"] == t],
                       "talk_about": talk and {"subject": talk["subject"], "component": talk["component"]},
                       "components": [{"subject": c["subject"], "component": c["component"], "you_pct": c["you_pct"], "class_pct": c["class_pct"]} for c in comps]})
    allowed = {float(x) for tr in tracks for c in tr["components"] for x in (c["you_pct"], c["class_pct"])}
    def prompt(fb=None):
        p = f"""You write the "next trimester" advice for {first}, an MBA student in India, in an end-of-trimester report.
Tone: a friendly senior speaking to the student. Conversational, simple Indian English, short sentences (max 18 words), everyday words. Address the student as "you" and "your". No jargon, no metaphors, no em-dashes, no exclamation marks, no praise words.
Subject marks are out of 100: continuous marks plus the End-term exam (out of 40). The trimester is closed. Advice is a habit for the AREA (specialisation) in the coming trimester. Never tell the student to redo, resubmit or retake anything, and never say they "did not attempt" or "did not submit": a low or zero mark only shows the mark.
FACTS (copy numbers exactly; every number you write must come from here):
{json.dumps(tracks, indent=0)}
For each area, write a JSON object with:
- track: the area name exactly as given in "area".
- action: exactly "In <label> next trimester, <habit>." using that area's "label" and "habit" word for word.
- why: 3 to 4 short, spoken sentences, at most 55 words in total. For each subject in the area write ONE sentence starting "In <subject name>," naming its lowest piece of work compared with the class (your mark and the class mark), like: In Subject X, your Quiz 1 was 20% (class 75.3%). If a subject has two weak pieces, join them in that sentence. Include the End-term exam when it is one of the lowest. Then one sentence on a piece above the class, naming its subject. Write subject names exactly as in FACTS. Every mark and class figure needs a % sign; whole percentages without ".0".
- interview: "Talk about your <talk_about component> in <talk_about subject>. If they ask about weak areas, say you now <habit>." Max 24 words. Use the talk_about piece given for that area.
Return ONLY a JSON object {{"tracks":[...]}} with {len(tracks)} items in the same order. No markdown fences."""
        return p + ("\nYour previous draft broke these rules; fix all of them:\n- " + "\n- ".join(fb) if fb else "")
    def call(p):
        # Route through our production gateway (TrueFoundry when configured, else a pseudonymised
        # direct-OpenRouter dev call). The prompt carries the PSEUDONYM, never the real name.
        txt, _ = BR.gateway_text([{"role": "user", "content": p}], MODEL)
        return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))["tracks"]
    def check(out):
        pr = []
        if [t.get("track") for t in out] != [t["area"] for t in tracks]: return ["tracks must be exactly " + str([t["area"] for t in tracks]) + " in order"]
        for t, tr in zip(out, tracks):
            txt = " ".join(t.get(k, "") for k in ("action", "why", "interview"))
            if re.search(r"_[A-Z]\b|—|–|!", txt): pr.append(f"{t['track']}: remove _D/_B suffixes, dashes, exclamation marks")
            if re.search(r"did not (attempt|submit)|didn't (attempt|submit)|re-?do|re-?submit|re-?take|perfect|great|amazing|\bhis\b|\bher\b", txt, re.I): pr.append(f"{t['track']}: banned wording")
            if re.search(r"\d", t["action"]): pr.append(f"{t['track']}: no digits in action")
            for m in re.findall(r"\d+(?:\.\d+)?", re.sub(r"Quiz \d|Assignment \d|Phase \d|\bA\d\b|-I\b|1&2", "", t["why"])):
                if float(m) not in allowed: pr.append(f"{t['track']}: number {m} not in facts")
            if t["why"].count("%") < 4: pr.append(f"{t['track']}: every mark needs a % sign")
            if len(t["why"].split()) > 62: pr.append(f"{t['track']}: why too long, max 55 words")
            if tr["talk_about"] and tr["talk_about"]["component"].split("(")[0].strip().lower() not in t["interview"].lower(): pr.append(f"{t['track']}: interview must mention {tr['talk_about']['component']!r}")
            if not t["action"].startswith("In ") or len(t["action"].split()) > 24: pr.append(f"{t['track']}: action must start 'In ...'")
        return pr
    out = call(prompt()); pr = check(out)
    for _ in range(3):
        if not pr: break
        out = call(prompt(pr)); pr = check(out)
    if pr: raise ValueError("narrative failed validation after retries: " + "; ".join(pr))
    allsubj = [s for tr in tracks for s in tr["subjects"]]
    def fmt(txt):
        txt = txt.replace("**", ""); txt = re.sub(r"(\d+)\.0%", r"\1%", txt)
        for sj in sorted(allsubj, key=len, reverse=True): txt = txt.replace(sj, "**" + sj + "**")
        return re.sub(r"(?<!class )(?<![\d.])(\d+(?:\.\d+)?%)", r"**\1**", txt)
    for t, tr in zip(out, tracks):
        m = re.match(r"^In [^,]+ next trimester, (\w+)(.*)\.$", t["action"])
        act = GER[m.group(1)] + m.group(2); act = re.sub(r" and (solve|revise|make|write|read) ", lambda x: ", and " + GER[x.group(1)] + " ", act)
        t["label"] = tr["label"]; t["action"] = "You can try " + act + "."
        t["why"] = fmt(t["why"])
        t["interview"] = fmt(t["interview"].replace("Talk about", "You can talk about", 1))
        t["interview"] = re.sub(r"If (they|you are|asked)[^.]*weak areas,.*$", "", t["interview"]).strip() + " If asked about weak areas, " + END.get(tr["area"], DEFAULT_END)
    # DPDP safety net: the card templates never use the name, but if the model echoed the pseudonym,
    # swap it back to the real first name (word-bounded), mirroring build_report.stitch_name.
    _pat = re.compile(rf"\b{re.escape(BR.PSEUDONYM)}\b")
    for t in out:
        for k in ("action", "why", "interview"):
            if isinstance(t.get(k), str): t[k] = _pat.sub(real_first, t[k])
    return out

def narrative(d, f, first):
    k, m = len(f["att_above"]), len(f["att"]); pt = f["pattern"]; kind = f["pattern_kind"]
    if kind == "split": txt = "You attended more than the class in this subject, but your marks were lower."
    elif kind == "strength": txt = "This is the subject where your marks are furthest ahead of the class."
    else: txt = "Your attendance and your marks were both below the class in this subject." if (pt["att_you"] or 0) < (pt["att_class"] or 0) else "You attended as much as the class or more, but your marks were below the class."
    # Campus and trimester come from the data (fetch_student / to_data both set them), not a literal,
    # so the headline/subtitle are correct for any cohort, not just the Noida-T1 sample.
    camp = d["student"].get("campus") or "your campus"; trim = str(d.get("trimester") or "")
    return {"headline": f"Hi {first}. Here is how you did in Trimester {trim}.", "subtitle": f"This report compares your Trimester {trim} performance with your class in {camp}, batch {d['student']['batch']}.",
            "attendance_line": f"You attended at or above class average in {k} of {m} subjects.", "pattern_text": txt, "_model": MODEL}

def pdf(html, out):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=8000", f"--print-to-pdf={out}", "file://" + os.path.abspath(html)], capture_output=True)

def marks_pdf(d, base):
    FONT = HERE + "/assets/"
    fonts = "".join(f"@font-face{{font-family:'Raleway';font-weight:{w};src:url(data:font/ttf;base64,{base64.b64encode(open(FONT+fn,'rb').read()).decode()})}}" for w, fn in ((400, "Raleway-Regular.ttf"), (600, "Raleway-SemiBold.ttf")))
    logo = base64.b64encode(open(HERE + "/assets/rehearsal-antimatter-tile-256.png", "rb").read()).decode()
    blocks = ""
    for s in d["subjects"]:
        rows = ""; xs = 0
        for c in s["components"]:
            dl = round(c["you_pct"] - c["class_pct"], 1); xs += c["x"]
            word = f"{abs(dl):g} points above" if dl > 0 else f"{abs(dl):g} points below" if dl < 0 else "same as the class"
            rows += f"<tr><td>{c['component']}</td><td class=n>{g(c['o'])} out of {g(c['x'])}</td><td class=n>{g(c['you_pct'])}%</td><td class=n>{g(c['class_pct'])}%</td><td>{word}</td></tr>"
        dsub = round(s["you_pct"] - s["class_pct"], 1)
        blocks += f"<section><h2>{s['subject']}</h2><p class=sub>Total {g(s['you_pct'])} out of 100 marks. Class average {g(s['class_pct'])}, so you are {abs(dsub):g} marks {'above' if dsub > 0 else 'below'} the class.</p><table><tr><th>Assignment or quiz</th><th class=n>Your marks</th><th class=n>You</th><th class=n>Class</th><th>Compared with the class</th></tr>{rows}</table></section>"
    st = d["student"]
    html = f"""<!doctype html><meta charset=utf-8><title>{st['name']} marks</title><style>{fonts}
@page{{size:A4;margin:12mm 13mm}} *{{box-sizing:border-box}} body{{font-family:Raleway,Arial,sans-serif;color:#0b0b0b;font-size:14px;line-height:1.35;margin:0;-webkit-print-color-adjust:exact}}
.top{{display:flex;align-items:center;gap:10px;margin-bottom:8px}} .top img{{width:36px;height:36px;border-radius:9px}} .top span{{font-size:22px;font-weight:600}}
h1{{font-size:24px;margin:6px 0 2px;font-weight:600}} .lede{{margin:0 0 10px}} h2{{font-size:17px;margin:12px 0 1px;font-weight:600}} .sub{{margin:0 0 4px}} section{{break-inside:avoid}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;padding:3px 6px 3px 0;border-bottom:1px solid #cfcfc8;vertical-align:top}} th{{font-weight:600;border-bottom:2px solid #0b0b0b}} .n{{text-align:right;white-space:nowrap;padding-right:14px}} .foot{{margin-top:10px;border-top:1px solid #cfcfc8;padding-top:6px}}</style>
<div class=top><img src="data:image/png;base64,{logo}"><span>Rehearsal</span></div><h1>{st['name']}: your marks, assignment by assignment</h1>
<p class=lede>{st['campus']}, {st['batch'].replace('-', ' to 20')}. Trimester {d.get('trimester', TRIMESTER)}. Data taken on {d.get('data_date', DATA_DATE)}. Each line shows your marks for one assignment or quiz next to your class average.</p>{blocks}
<p class=foot>Total marks are out of 100: continuous marks plus the end-term exam (out of 40). Subjects without an end-term line have continuous marks only, out of 100. Class average is your own class section. This page is for your information and does not predict placement results.</p>"""
    open(base + "-marks.html", "w").write(html); pdf(base + "-marks.html", base + "-marks.pdf")


def _process(d, sid):
    """facts -> narrative -> number-checked LLM cards -> write data.json/narrative.json + marks PDF.
    Build everything BEFORE writing, so a failed student leaves no partial data/narrative and never
    ships a report (no narrative.json -> build_report_v8 skips it)."""
    first = d["student"]["name"].split()[0]; base = f"{WORK}/{sid}"
    f = B.facts(d); n = narrative(d, f, first); n["tracks"] = llm_cards(d, f)
    json.dump(d, open(base + "-data.json", "w"), indent=1)
    json.dump(n, open(base + "-narrative.json", "w"), indent=1)
    marks_pdf(d, base); os.replace(base + "-marks.pdf", f"{OUT}/{sid}-marks-assignment-wise.pdf")
    return d["student"]["name"]


if __name__ == "__main__":
    argv = sys.argv[1:]
    # DYNAMIC path: `make_inputs.py --from-data work/<id>-data.json ...` consumes data.json files
    # already produced by fetch_student.py (real Supabase data), skipping the fixed-kit RAW+to_data
    # step. DEFAULT (no flag): read the kit fixture and map it through to_data() -- unchanged.
    if "--from-data" in argv:
        _loaded = [json.load(open(p)) for p in argv[argv.index("--from-data") + 1:]]
        items = [(d["student"]["id"], d, False) for d in _loaded]
    else:
        raw = json.load(open(RAW)); only = argv
        items = [(st["id"], st, True) for st in raw if not only or st["id"] in only]
    failures = []
    for sid, src, is_raw in items:
        try:
            d = to_data(src) if is_raw else src   # to_data stays inside the try: it may raise on an
            print(sid, _process(d, sid), "ok")     # unknown subject, and must skip only that student
        except Exception as e:
            failures.append(sid); print(f"{sid} FAILED ({type(e).__name__}): {e} -- no report written")
    if failures:
        print(f"\n{len(failures)} student(s) skipped: {failures}. No flawed report was shipped; "
              "fix the cause or re-run those ids.")

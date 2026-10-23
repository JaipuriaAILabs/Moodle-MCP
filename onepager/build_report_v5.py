#!/usr/bin/env python3
"""One-page student report v5, built with the so-what-dashboard method.

Hero = the surprise found in the student's own data, then a row of large numbers, then ONE decision
(recommendation, confidence, what would change it, what is not decided), then evidence blocks with
claim headlines, then the Ideas cards. Every sentence outside the Ideas cards is computed from the
data, no model involved. The Ideas cards come from the saved narrative JSON (DeepSeek V3, validated).

Usage: build_report_v5.py --data X-data.json --narrative X-narrative.json --out out.html
"""
import argparse, json, math, os, re, statistics as S, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report_v4 as B

g = lambda x: f"{x:g}"
SHORT = {"Financial Reporting and Analysis": "Financial Reporting", "Organisational Behaviour and Interpersonal Dynamics": "Organisational Behaviour",
         "Critical Reading and Writing": "Critical Reading & Writing"}
sh = lambda x: SHORT.get(x, x)
LABEL = {"Finance": "Finance and Economics", "Marketing": "Marketing", "Analytics & Operations": "Analytics and GenAI", "HR & People": "HR and Communication"}


def so_what(d, f):
    """Everything the page says, computed from the data (the skill's phases 1-4)."""
    subs = f["subjects"]; m = len(subs); a = len(f["above"]); k = len(f["att_above"])
    # end-term against continuous, as % of the marks available in each part (the trap: totals hide this)
    et = []
    for s in d["subjects"]:
        ce = [c for c in s["components"] if c["kind"] == "CE"]; e = [c for c in s["components"] if c["kind"] == "ET"]
        if ce and e:
            et.append({"subject": s["subject"], "ce": round(100 * sum(c["o"] for c in ce) / sum(c["x"] for c in ce), 1), "et": e[0]["you_pct"]})
    gap = round(S.mean(x["et"] - x["ce"] for x in et), 1) if et else 0.0
    n_low = sum(x["et"] < x["ce"] for x in et)
    n_high = sum(x["et"] > x["ce"] for x in et)
    # the surprise: the biggest contrast the data holds, decided by rule
    if len(et) >= 3 and gap <= -8 and n_low >= len(et) - 1:
        hero = f"Your end-term marks were lower than your continuous marks in {n_low} of {len(et)} subjects, by {abs(gap):g} points on average."
        kind = "endterm"
    elif a - k >= 3:
        hero = f"Your marks were above the class in {a} of {m} subjects, while your attendance was at or above the class in {k} of {m}."
        kind = "mismatch"
    elif a == m:
        hero = f"Your marks were above the class average in all {m} subjects."
        kind = "allabove"
    else:
        hero = f"Your marks were above the class average in {a} of {m} subjects."
        kind = "count"
    # decision: which area to start with, from the average gap to the class in each area
    areas = []
    for t in d["tracks"]:
        ts = [s for s in subs if s["track"] == t and s["delta"] is not None]
        if ts:
            areas.append({"track": t, "label": LABEL[t], "n": len(ts), "you": round(S.mean(s["you_pct"] for s in ts), 1),
                          "cls": round(S.mean(s["class_pct"] for s in ts), 1)})
    for x in areas:
        x["d"] = round(x["you"] - x["cls"], 1)
    low = min(areas, key=lambda x: x["d"])
    if low["d"] < 0:
        rec = f"A good first place for extra study time in Trimester 2 is {low['label']}."
        subj = "subject" if low["n"] == 1 else f"{low['n']} subjects"
        why = f"Your {subj} there average {g(low['you'])} marks out of 100, against {g(low['cls'])} for the class, which is {g(abs(low['d']))} points below."
    else:
        below = [x for x in subs if x["delta"] is not None and x["delta"] < 0]
        if below:
            w = min(below, key=lambda x: x["delta"])
            rec = f"Your marks are above the class in all four areas. The one subject to watch in Trimester 2 is {w['subject']}."
            why = f"Your {g(w['you_pct'])} marks out of 100 there compare with {g(w['class_pct'])} for the class, which is {g(abs(w['delta']))} points below."
        else:
            rec = "Your marks are above the class in all four areas, so Trimester 2 can follow your current routine."
            why = f"The smallest lead is in {low['label']}: {g(low['you'])} marks out of 100 against {g(low['cls'])} for the class, {g(low['d'])} points ahead."
    pieces = sum(len(s["components"]) for s in d["subjects"])
    mean_you = round(S.mean(s["you_pct"] for s in subs), 1); mean_cls = round(S.mean(s["class_pct"] for s in subs), 1)
    return {"hero": hero, "kind": kind, "rec": rec, "why": why, "et": et, "gap": gap, "n_low": n_low, "n_high": n_high, "areas": areas,
            "m": m, "a": a, "k": k, "pieces": pieces, "mean_you": mean_you, "mean_cls": mean_cls}


def render(d, f, n, sw):
    st = d["student"]; parts = st["name"].split(); first = parts[1] if len(parts[0]) == 1 and len(parts) > 1 else parts[0]
    fonts = "".join(
        f"@font-face{{font-family:'Raleway';font-weight:{w};font-style:normal;src:url({B.b64(os.path.join(B.FONT_DIR, fn), 'font/ttf')}) format('truetype')}}"
        for w, fn in ((200, "Raleway-ExtraLight.ttf"), (400, "Raleway-Regular.ttf"), (600, "Raleway-SemiBold.ttf")))
    charts_js = open(B.CHARTS).read()
    esc, md = B.esc, (lambda x: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", B.esc(x)))

    marks_items = [{"label": sh(s["subject"]), "value": s["delta"], "display": f"{B.sign(s['delta'])} pts",
                    "sub": f"you {g(s['you_pct'])}% · class {g(s['class_pct'])}%"} for s in f["subjects"]]
    marks_max = max(25, 5 * math.ceil(max(abs(s["delta"]) for s in f["subjects"]) / 5))
    att_items = [{"label": sh(s["subject"]), "value": s["att_you"], "target": s["att_class"],
                  "color": "var(--s3)" if s["att_you"] >= s["att_class"] else "var(--s2)",
                  "display": f"{g(s['att_you'])}%"} for s in f["att"]]
    et_items = [{"label": sh(x["subject"]), "a": x["ce"], "b": x["et"], "color": "var(--s1)", "display": f"{g(x['et'])}% vs {g(x['ce'])}%"} for x in sw["et"]]
    dm = round(sw["mean_you"] - sw["mean_cls"], 1)
    tiles = [
        {"label": "Average mark out of 100", "value": g(sw["mean_you"]), "delta": dm, "deltaUnit": " points above the class" if dm >= 0 else " points below the class", "note": f"Class average {g(sw['mean_cls'])}"},
        {"label": "Subjects above the class", "value": f"{sw['a']} of {sw['m']}", "note": "Subjects, marks out of 100"},
        {"label": "Attendance at or above class", "value": f"{sw['k']} of {sw['m']}", "note": "Subjects, share of sessions"},
        {"label": "End-term against continuous", "value": f"{'−' if sw['gap'] < 0 else '+'}{g(abs(sw['gap']))} points" if sw["et"] else "not available", "note": f"Average over {len(sw['et'])} subjects"},
    ]
    if sw["n_low"] == 0:
        et_title = f"End-term at or above continuous in all {len(sw['et'])} subjects"
    else:
        et_title = f"End-term below continuous in {sw['n_low']} of {len(sw['et'])} subjects"
    cards = ""
    for i, t in enumerate(n["tracks"], 1):
        names = '<span class="sl">' + "".join('<span class="subj">' + esc(sj["subject"]) + "</span>" for sj in d["subjects"] if sj["track"] == t["track"]) + "</span>"
        ip_text = re.sub(r" \([^)]*\)", "", t["interview"])
        cards += f"""
      <div class="card">
        <div class="tag">{esc(t['label'])}{names}</div>
        <div class="do"><span class="n">{i}</span><span>{esc(t['action'])}</span></div>
        <div class="why"><b>Why.</b> {md(t['why'])}</div>
        <div class="ip"><b>For SIP and placement interviews:</b> {md(ip_text)}</div>
      </div>"""
    qr_ios, qr_and = B.qr_png(d["store"]["ios"]), B.qr_png(d["store"]["android"])
    wordmark = B.b64(B.WORDMARK, "image/png"); jlogo = B.b64(B.JAIPURIA_LOGO, "image/png")
    hero_html = esc(sw["hero"])
    batch = esc(st["batch"].replace("-", " to 20"))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(st['name'])} · Trimester {esc(d['trimester'])}</title>
<style>
{fonts}
:root{{--surface:#ffffff;--plane:#f6f6f3;--ink:#0b0b0b;--ink2:#0b0b0b;--muted:#0b0b0b;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#4a3aa7;--s8:#e34948;
--seq100:#cde2fb;--seq250:#86b6ef;--seq400:#3987e5;--seq550:#1c5cab;--seq700:#0d366b;--st-good:#0ca30c;--st-critical:#d03b3b}}
*{{box-sizing:border-box}}
@page{{size:A4;margin:5mm 10mm 4mm}}
html,body{{margin:0;padding:0;background:#fff;color:var(--ink);font-family:Raleway,system-ui,sans-serif;font-size:11px;line-height:1.38;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:190mm;margin:0 auto}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}}
.brand{{display:flex;align-items:center;gap:9px}} .brand img{{width:32px;height:32px;border-radius:8px;display:block}} .brand span{{font-size:22px;font-weight:600;letter-spacing:-.01em}}
.top .inst{{text-align:right;font-size:11px;line-height:1.3}} .top .inst img{{height:20px;display:block;margin-left:auto;margin-bottom:1px}}
h1{{font-size:23px;line-height:1.13;font-weight:600;letter-spacing:-.015em;margin:2px 0 2px;max-width:182mm}}
.lede{{font-size:12px;margin:0 0 4px}} .lede b{{font-weight:600}}
.gc-tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:0 0 7px}} .gc-tile{{background:var(--plane);border:1px solid var(--border);border-radius:6px;padding:3px 8px}}
.gc-tile .l{{font-size:11px;line-height:1.2;color:var(--ink)}} .gc-tile .v{{font-size:18px;font-weight:600;line-height:1.1;margin:1px 0 0}} .gc-tile .row{{display:flex;align-items:center;gap:6px;min-height:14px}} .gc-tile .d{{font-size:11px;color:var(--ink)}}
.gc-delta{{font-size:11px;font-weight:600}} .gc-delta.good{{color:#006300}} .gc-delta.bad{{color:var(--st-critical)}}
.dec{{border-top:1px solid var(--grid);border-bottom:1px solid var(--grid);padding:4px 0 4px;margin-bottom:5px}}
.dec .rec{{font-size:15.5px;font-weight:600;line-height:1.25;margin:0 0 2px}} .dec .why{{font-size:11.5px;margin:0 0 4px}}
.facts{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}} .facts b{{display:block;font-size:11px;font-weight:600}} .facts span{{font-size:11px;line-height:1.3;display:block}}
.fig .t{{font-weight:600;font-size:12.5px;margin-bottom:1px}} .fig .s{{font-size:11px;margin-bottom:1px}} .fig .n{{font-size:10px;margin-top:0}}
.fig{{margin-bottom:5px}} .chart svg{{max-width:100%;display:block}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}}
.ideas h2{{font-size:15px;font-weight:600;margin:3px 0 0}} .ideas h2 span{{font-weight:400;font-size:11.5px;margin-left:8px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:0 9px;padding-top:4px}}
.card{{border-top:2px solid var(--s3);padding-top:3px}} .card .tag{{font-size:11px;color:var(--s6);font-weight:600}} .card .tag .subj{{display:inline;color:var(--ink);font-weight:400;font-size:10.5px;line-height:1.28}} .card .tag .subj+.subj::before{{content:", "}} .card .tag .sl{{display:block;margin-top:1px}}
.card .do{{display:flex;gap:5px;align-items:flex-start;font-size:10.8px;font-weight:600;line-height:1.22;margin:2px 0 3px}} .card .do .n{{flex:none;width:15px;height:15px;border-radius:50%;background:var(--s3);color:#fff;font-size:10px;text-align:center;line-height:15px}}
.card .why,.card .ip{{font-size:10px;line-height:1.26}} .card .ip{{margin-top:2px}} .card b{{font-weight:600}}
.footwrap{{break-inside:avoid;border-top:1px solid var(--grid);margin-top:3px;padding-top:2px}} .foot{{display:flex;justify-content:space-between;align-items:center;gap:12px}}
.foot .fl{{font-size:11px;max-width:128mm;display:flex;gap:8px;align-items:center}} .foot .fl b{{font-weight:600}} .foot .fl img{{width:28px;height:28px;border-radius:7px;flex:none}}
.qrs{{display:flex;gap:10px}} .qr{{text-align:center;font-size:10px}} .qr img{{display:block;width:34px;height:34px;margin:0 auto 1px}}
.fine{{font-size:9.5px;margin-top:2px;line-height:1.28}}
</style></head><body><div class="page">
 <div class="top"><div class="brand"><img src="{wordmark}" alt="Rehearsal logo"><span>Rehearsal</span></div>
   <div class="inst"><img src="{jlogo}" alt="Jaipuria Institute of Management">{esc(st['name'])} · {esc(st['campus'])}, {batch}</div></div>
 <h1>{hero_html}</h1>
 <p class="lede">Hi {esc(first)}. Here is how you did in Trimester {esc(d['trimester'])}, compared with your class section.</p>
 <div id="tiles"></div>
 <div class="dec"><p class="rec">{esc(sw['rec'])}</p><p class="why">{esc(sw['why'])}</p>
  <div class="facts">
   <div><b>Confidence</b><span>Medium. It rests on one trimester: {sw['m']} subjects and {sw['pieces']} pieces of graded work.</span></div>
   <div><b>What would change it</b><span>A corrected or late mark in Moodle, or a different class benchmark.</span></div>
   <div><b>Not decided here</b><span>Your Trimester {int(d['trimester']) + 1} marks, your specialisation or any placement result.</span></div>
  </div></div>

 <div class="fig"><div class="t">Figure 1. Above the class in {sw['a']} of {sw['m']} subjects{('; biggest difference: ' + esc(f['subjects'][-1]['subject'])) if f['subjects'][-1]['delta'] < 0 else ''}</div>
  <div class="s">Your total mark out of 100 compared with your class average, in points. Right of the line = above the class.</div>
  <div class="chart" id="ch-marks"></div>
  <div class="n">Source: Moodle gradebook, Jaipuria {esc(st['campus'])}, {esc(d['data_date'])}. Continuous marks plus end-term (out of 40). Class = your section.</div></div>

 <div class="two">
  <div class="fig"><div class="t">Figure 2. Attendance at or above the class in {sw['k']} of {sw['m']} subjects</div>
   <div class="s">Sessions attended, % of held. Tick = class average.</div>
   <div class="chart" id="ch-att"></div>
   <div class="n">Source: Moodle attendance, same extraction.</div></div>
  <div class="fig"><div class="t">Figure 3. {esc(et_title)}</div>
   <div class="s">% of marks available. Hollow = continuous, filled = end-term.</div>
   <div class="chart" id="ch-et"></div>
   <div class="n">Source: Moodle gradebook. GenAI and Critical Reading have no end-term.</div></div>
 </div>

 <div class="ideas"><h2>Ideas for Trimester {int(d['trimester']) + 1}<span>One simple suggestion for each area, and one piece of work you can talk about in interviews.</span></h2>
  <div class="cards">{cards}</div></div>

 <div class="footwrap"><div class="foot"><div class="fl"><img src="{wordmark}" alt=""><span>Record your answers, save notes and rehearse interviews with AI on <b>Rehearsal</b>. Free to start, on the phone you already carry.</span></div>
  <div class="qrs"><div class="qr"><img src="{qr_ios}" alt="App Store QR">App Store</div><div class="qr"><img src="{qr_and}" alt="Google Play QR">Google Play</div></div></div>
</div>
</div>
<script>{charts_js}</script>
<script>
document.getElementById('tiles').innerHTML = Charts.statTiles({json.dumps(tiles)});
document.getElementById('ch-marks').innerHTML = Charts.divergingBar({json.dumps(marks_items)}, {{width:710, labelW:190, rowH:12, subW:130, max:{marks_max}, negLabel:'below the class', posLabel:'above the class', zeroLabel:'class average', tickFmt:v=>(v>0?'+':'')+v}});
document.getElementById('ch-att').innerHTML = Charts.bullet({json.dumps(att_items)}, {{max:100, width:352, labelW:150, rowH:13, track:'var(--grid)', valueW:40}});
document.getElementById('ch-et').innerHTML = Charts.dumbbell({json.dumps(et_items)}, {{width:352, labelW:150, rowH:15, max:100, aLabel:'Continuous', bLabel:'End-term'}});
Charts.wireTooltips(document.body);
var pg=document.querySelector('.page'), maxH=1085; if(pg.offsetHeight>maxH){{document.body.style.zoom=maxH/pg.offsetHeight;}}
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--narrative", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d = json.load(open(a.data)); n = json.load(open(a.narrative)); f = B.facts(d); sw = so_what(d, f)
    open(a.out, "w").write(render(d, f, n, sw)); print(a.out, "|", sw["kind"], "|", sw["hero"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Student report v6: a calm page 1 (answer first), detail on page 2.
Method: so-what-dashboard (surprise, one recommendation, what is not decided) and the Shiva-brain rules found in the
llm-wiki: lead with the inference, one idea per block, keep evidence and sources at the end, no repeated caveats."""
import argparse, json, math, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report_v4 as B
from build_report_v5 import so_what, sh, g

def render(d, f, n, sw):
    st = d["student"]; parts = st["name"].split(); first = parts[1] if len(parts[0]) == 1 and len(parts) > 1 else parts[0]
    esc = B.esc; md = lambda x: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", B.esc(x))
    fonts = "".join(f"@font-face{{font-family:'Raleway';font-weight:{w};font-style:normal;src:url({B.b64(os.path.join(B.FONT_DIR, fn), 'font/ttf')}) format('truetype')}}"
                    for w, fn in ((400, "Raleway-Regular.ttf"), (600, "Raleway-SemiBold.ttf")))
    charts_js = open(B.CHARTS).read()
    marks = [{"label": sh(s["subject"]), "value": s["delta"], "display": f"{B.sign(s['delta'])}"} for s in f["subjects"]]
    mmax = max(20, 5 * math.ceil(max(abs(s["delta"]) for s in f["subjects"]) / 5))
    att = [{"label": sh(s["subject"]), "value": s["att_you"], "target": s["att_class"], "color": "var(--s3)" if s["att_you"] >= s["att_class"] else "var(--s2)",
            "display": f"{g(s['att_you'])}%"} for s in f["att"]]
    et = [{"label": sh(x["subject"]), "a": x["ce"], "b": x["et"], "color": "var(--s1)", "display": f"{g(x['et'])}% vs {g(x['ce'])}%"} for x in sw["et"]]
    dm = round(sw["mean_you"] - sw["mean_cls"], 1)
    tiles = [{"label": "Average mark out of 100", "value": g(sw["mean_you"]), "delta": dm, "deltaUnit": " points above the class" if dm >= 0 else " points below the class", "note": f"Class average {g(sw['mean_cls'])}"},
             {"label": "Subjects above the class", "value": f"{sw['a']} of {sw['m']}"},
             {"label": "Attendance at or above the class", "value": f"{sw['k']} of {sw['m']}"}]
    et_title = (f"End-term at or above continuous marks in all {len(sw['et'])} subjects" if sw["n_low"] == 0 else f"End-term below continuous marks in {sw['n_low']} of {len(sw['et'])} subjects")
    cards = ""; whys = ""
    for i, t in enumerate(n["tracks"], 1):
        first_ip = re.split(r"(?<=\.)\s+If ", re.sub(r" \([^)]*\)", "", t["interview"]))[0]
        first_ip = first_ip.replace("You can talk about", "Talk about", 1)
        cards += f'<div class="card"><h3>{esc(t["label"])}</h3><p class="do">{esc(t["action"])}</p><p class="ip"><b>For SIP and placement interviews.</b> {md(first_ip)}</p></div>'
        subs = ", ".join(sj["subject"] for sj in d["subjects"] if sj["track"] == t["track"])
        whys += f'<div class="wcard"><h3>{esc(t["label"])}</h3><p class="sb">{esc(subs)}</p><p>{md(t["why"])}</p></div>'
    qi, qa = B.qr_png(d["store"]["ios"]), B.qr_png(d["store"]["android"]); logo = B.b64(B.WORDMARK, "image/png"); jl = B.b64(B.JAIPURIA_LOGO, "image/png")
    nxt = int(d["trimester"]) + 1
    A = f["att"]; below = [x for x in A if x["att_you"] < x["att_class"]]
    my = sum(x["att_you"] for x in A)/len(A); cm = sum(x["att_class"] for x in A)/len(A)
    names = lambda L: ", ".join(sh(x["subject"]) for x in L[:-1]) + " and " + sh(L[-1]["subject"]) if len(L) > 1 else sh(L[0]["subject"])
    low = min(A, key=lambda x: x["att_you"]-x["att_class"])
    if not below: h1 = "Your attendance matched or beat the class in every subject"; p1 = "You were present at least as often as your section in each subject. Keep the same routine next trimester."
    elif len(below) == len(A): h1 = "Your attendance trailed the class in every subject"; p1 = f"The widest gap is in {sh(low['subject'])}: you attended {g(low['att_you'])}% of sessions, the class {g(low['att_class'])}%. Start by closing that gap."
    elif len(below) <= 2: h1 = f"Your attendance trailed the class only in {names(below)}"; p1 = f"The widest gap is in {sh(low['subject'])}: you attended {g(low['att_you'])}% of sessions, the class {g(low['att_class'])}%. In every other subject you were at or above the class."
    else: h1 = "Your attendance trailed the class in several subjects"; p1 = f"The widest gap is in {sh(low['subject'])}: you attended {g(low['att_you'])}% of sessions, the class {g(low['att_class'])}%. Start with {sh(low['subject'])}, then the other subjects marked orange in the chart."
    atiles = [{"label": "Your average attendance", "value": f"{g(round(my,1))}%", "delta": round(my-cm,1), "deltaUnit": " points above the class" if my>=cm else " points below the class", "note": f"Class average {g(round(cm,1))}%"},
              {"label": "Subjects below the class", "value": f"{len(below)} of {len(A)}"},
              ({"label": "Lowest gap to the class", "value": sh(low["subject"]), "note": f"{g(round(low['att_class']-low['att_you'],1))} points under the class"} if below else {"label": "Biggest lead on the class", "value": sh(max(A, key=lambda x: x["att_you"]-x["att_class"])["subject"]), "note": f"{g(round(max(x['att_you']-x['att_class'] for x in A),1))} points above the class"})]
    a_, m_ = sw["a"], sw["m"]
    if sw["kind"] == "endterm": h2 = "End-term marks came in lower than continuous marks"
    elif a_ == m_: h2 = "Your marks beat the class in every subject"
    elif a_ == 0: h2 = "Your marks trailed the class in every subject"
    elif a_ >= 0.6*m_: h2 = "Your marks beat the class in most subjects"
    elif a_ <= 0.4*m_: h2 = "Your marks trailed the class in most subjects"
    else: h2 = "Your marks are ahead of the class in some subjects and behind in others"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(st['name'])} · Trimester {esc(d['trimester'])}</title><style>
{fonts}
:root{{--ink:#0b0b0b;--plane:#f6f6f3;--grid:#e1e0d9;--axis:#c3c2b7;--surface:#fff;--muted:#0b0b0b;--ink2:#0b0b0b;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s6:#008300;--s7:#4a3aa7;--st-critical:#d03b3b}}
*{{box-sizing:border-box}} @page{{size:A4;margin:0}}
html,body{{margin:0;background:#fff;color:var(--ink);font-family:Raleway,system-ui,sans-serif;font-size:14px;line-height:1.45;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:210mm;height:297mm;padding:0 12mm 8mm;position:relative;overflow:hidden;background:#fff}} .p2{{break-before:page}}
.band{{height:6px;margin:0 -12mm 10px;background:linear-gradient(90deg,#9677f8,#4e44fd,#ff4859,#f7c845,#00c483)}}
.top{{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--grid);border-radius:12px;padding:8px 14px;margin-bottom:10px}}
.brand{{display:flex;align-items:center;gap:10px}} .brand img{{width:34px;height:34px;border-radius:9px}} .brand span{{font-size:22px;font-weight:600}}
.who{{text-align:right;font-size:14px}} .who img{{height:24px;display:block;margin:0 0 2px auto}}
.hero{{background:#f7c845;border-radius:18px;padding:14px 20px;margin-bottom:8px}}
h1{{font-size:28px;line-height:1.12;font-weight:600;letter-spacing:-.015em;margin:0 0 8px}} .lede{{font-size:15px;margin:0}}
.hero .gc-tiles,.hero-t .gc-tiles{{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px;margin-top:8px}}
.hero-t .gc-tile:nth-child(1){{background:#fff}} .gc-tile{{border-radius:12px;padding:6px 14px;background:#fff;border:0}} .gc-tile:nth-child(1){{background:#ff9d8f}} .gc-tile:nth-child(2){{background:#8fe3ea}} .gc-tile:nth-child(3){{background:#bcd0fb}}
.gc-tile .l{{font-size:14px}} .gc-tile .v{{font-size:26px;font-weight:600;line-height:1.1;margin:2px 0}} .gc-tile .row{{display:flex;gap:6px}} .gc-tile .d,.gc-tile .n{{font-size:14px}} .gc-delta{{font-size:14px;font-weight:600;color:#0b0b0b!important}}
.strip{{display:grid;grid-template-columns:1.3fr 1.3fr 1fr 1fr;border:1px solid var(--grid);border-radius:12px;margin-bottom:8px}} .strip div{{padding:5px 14px;border-left:1px solid var(--grid)}} .strip div:first-child{{border-left:0}} .strip b{{display:block;font-weight:600}}
.panel{{border:1px solid var(--grid);border-radius:16px;padding:10px 18px;margin-bottom:8px;background:#fff}}
.fig .t{{font-size:19px;font-weight:600;margin:0 0 2px}} .sub{{font-size:14px;margin:2px 0 8px}} .chart svg{{width:100%;height:auto;display:block}}
.rec{{background:#8fe3ea;border-radius:12px;padding:8px 14px;font-size:16px;font-weight:600;line-height:1.3;margin:0 0 8px}} .rec span{{display:block;font-weight:400;font-size:14px;margin-top:2px}}
h2{{font-size:22px;font-weight:600;margin:6px 0 2px}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .card{{border-radius:16px;padding:8px 14px}} .card:nth-child(1){{background:#f7c845}} .card:nth-child(2){{background:#bcd0fb}} .card:nth-child(3){{background:#ff9d8f}} .card:nth-child(4){{background:#8fe3ea}}
.card h3{{font-size:17px;font-weight:600;margin:0 0 4px}} .card p{{margin:0 0 3px;font-size:14px;line-height:1.35}} .card .do{{font-weight:600}} b{{font-weight:600}}
.foot{{position:absolute;left:12mm;right:12mm;bottom:8mm;border-top:1px solid var(--grid);padding-top:8px;display:flex;justify-content:space-between;align-items:center;gap:14px;font-size:14px}} .foot .l{{display:flex;gap:10px;align-items:center;max-width:125mm}} .foot .l img{{width:30px;height:30px;border-radius:7px}}
.qrs{{display:flex;gap:12px}} .qr{{text-align:center;font-size:12px}} .qr img{{display:block;width:38px;height:38px;margin:0 auto 1px}}
.src{{font-size:14px;margin:6px 0 0}}
</style></head><body>
<div class="page">
 <div class="band"></div>
 <div class="top"><div class="brand"><img src="{logo}" alt="Rehearsal logo"><span>Rehearsal</span></div><div class="who"><img src="{jl}" alt="Jaipuria Institute of Management">Student progress report</div></div>
 <div class="hero"><h1>Hi {esc(first)}. {esc(h2)}.</h1><p class="lede">This page compares your Trimester {esc(d['trimester'])} marks with the average for your section.</p><div id="tiles"></div></div>
 <div class="strip"><div><b>Student</b>{esc(st['name'])}</div><div><b>Campus and batch</b>{esc(st['campus'])}, {esc(st['batch'].replace('-', ' to 20'))}</div><div><b>Report period</b>Trimester {esc(d['trimester'])}</div><div><b>Data taken on</b>{esc(d['data_date'])}</div></div>
 <p class="rec">{esc(sw['rec'])}<span>{esc(sw['why'])}</span></p>
 <div class="panel fig"><p class="t">Your marks against the class, subject by subject</p><div class="chart" id="ch-marks"></div></div>
 <div class="panel fig"><p class="t">Continuous marks against end-term marks</p><div class="chart" id="ch-et"></div><p class="sub">Hollow dots are continuous marks, filled dots are end-term, each as a share of the marks available. GenAI for Managers and Critical Reading and Writing have no end-term.</p></div>
 <div class="foot"><span>Source: Moodle gradebook, Jaipuria {esc(st['campus'])}. Marks are out of 100. This report guides learning and does not predict placement results.</span></div>
</div>
<div class="page p2">
 <div class="band"></div>
 <div class="top"><div class="brand"><img src="{logo}" alt=""><span>Rehearsal</span></div><div class="who">{esc(st['name'])}, Trimester {esc(d['trimester'])}</div></div>
 <div class="hero" style="background:#ff9d8f"><h1>{esc(h1)}</h1><p class="lede">{esc(p1)}</p><div id="atiles" class="hero-t"></div></div>
 <div class="panel fig"><p class="t">Your attendance against the class, subject by subject</p><div class="chart" id="ch-att"></div><p class="sub">The line in each bar is the class average. Attendance is sessions attended as a share of sessions held.</p></div>
 <h2>Ideas for next trimester</h2>
 <div class="cards">{cards}</div>
 <div class="foot"><div class="l"><img src="{logo}" alt=""><span>Record your answers, save notes and rehearse interviews with AI on <b>Rehearsal</b>.</span></div><div class="qrs"><div class="qr"><img src="{qi}" alt="App Store QR">App Store</div><div class="qr"><img src="{qa}" alt="Google Play QR">Google Play</div></div></div>
</div>
<script>{charts_js}</script><script>
document.getElementById('tiles').innerHTML = Charts.statTiles({json.dumps(tiles)});
document.getElementById('atiles').innerHTML = Charts.statTiles({json.dumps(atiles)});
document.getElementById('ch-marks').innerHTML = Charts.divergingBar({json.dumps(marks)}, {{width:500, labelW:160, rowH:24, max:{mmax}, negLabel:'below the class', posLabel:'above the class', zeroLabel:'class average', tickFmt:v=>(v>0?'+':'')+v}});
document.getElementById('ch-att').innerHTML = Charts.bullet({json.dumps(att)}, {{max:100, width:500, labelW:160, rowH:22, track:'var(--grid)', valueW:50}});
document.getElementById('ch-et').innerHTML = Charts.dumbbell({json.dumps(et)}, {{width:500, labelW:160, rowH:30, max:100, aLabel:'Continuous', bLabel:'End-term'}});
Charts.wireTooltips(document.body);
</script></body></html>"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data"); ap.add_argument("--narrative"); ap.add_argument("--out"); a = ap.parse_args()
    d = json.load(open(a.data)); n = json.load(open(a.narrative)); f = B.facts(d); sw = so_what(d, f)
    open(a.out, "w").write(render(d, f, n, sw)); print(a.out, "|", sw["hero"])

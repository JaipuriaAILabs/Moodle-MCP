#!/bin/zsh
# Dynamic (real Supabase data) counterpart of run.sh.
# usage: ./run_dynamic.sh <campus> <batch> <trimester> <student_id> [more ids...]
#
# Needs, in the environment or .env files:
#   SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (+ optional SUPABASE_ANON_KEY)  -> fetch_student.py
#   TrueFoundry gateway creds in .env.truefoundry.local                      -> narrative LLM call
# The narrative routes through the TrueFoundry gateway (moodle-security guardrails) and the model
# only ever sees the "Aarav" pseudonym; the real name is stitched back locally. run.sh (the fixed
# kit path) is untouched by this script.
set -e
cd "$(dirname "$0")"
C="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PY="${PYTHON:-python3}"
mkdir -p work out
if [ $# -lt 4 ]; then
  echo "usage: $0 <campus> <batch> <trimester> <student_id> [more ids...]"; exit 2
fi
campus=$1; batch=$2; trimester=$3; shift 3

# 1) Fetch each student's real data into the renderer contract (work/<id>-data.json).
data_files=()
for id in "$@"; do
  $PY fetch_student.py --campus "$campus" --batch "$batch" --trimester "$trimester" \
      --student "$id" --out "work/$id-data.json" || exit 1
  data_files+=("work/$id-data.json")
done

# 2) Facts + number-checked narrative + marks PDF for all fetched students.
$PY make_inputs.py --from-data "${data_files[@]}" || exit 1

# 3) Render each to a 2-page PDF and assert the page count (dependency-free check).
rc=0
for id in "$@"; do
  if [ ! -f "work/$id-narrative.json" ]; then
    echo "$id: no narrative written (skipped by make_inputs) -- not rendered"; rc=1; continue
  fi
  $PY build_report_v8.py --data "work/$id-data.json" --narrative "work/$id-narrative.json" \
      --out "work/$id.html" || exit 1
  "$C" --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=8000 \
      --print-to-pdf="out/$id-progress-report.pdf" "file://$PWD/work/$id.html" 2>/dev/null
  pages=$($PY pagecount.py "out/$id-progress-report.pdf")
  if [ "$pages" = "2" ]; then
    echo "$id pages: $pages  OK"
  else
    echo "$id pages: $pages  FAIL (must be 2)"; rc=1
  fi
done
exit $rc

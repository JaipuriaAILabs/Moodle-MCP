#!/bin/zsh
# usage: ./run.sh [student ids...]   (needs OPENROUTER_API_KEY in the environment)
cd "$(dirname "$0")"
C="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mkdir -p work out
python3 make_inputs.py "$@" || exit 1
ids=(${@:-$(python3 -c "import json;print(' '.join(s['id'] for s in json.load(open('sample/five_students_raw.json'))))")})
for id in $ids; do
  python3 build_report_v8.py --data work/$id-data.json --narrative work/$id-narrative.json --out work/$id.html || exit 1
  "$C" --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=8000 --print-to-pdf=out/$id-progress-report.pdf file://$PWD/work/$id.html 2>/dev/null
  echo "$id pages: $(pdfinfo out/$id-progress-report.pdf | awk '/Pages/{print $2}')  (must be 2)"
done

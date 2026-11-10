# scripts/dev

Test helpers for the paper trading card. Not part of the site; nothing here runs in a workflow.

- `make_paper_sample.py` writes fake `data/paper/` style files and a copy of `index.html` with a fixed clock (`?s=<scenario>&now=<date>`). Run it, then `cd` into the folder it prints and `python -m http.server 8765`. Every file is labeled SAMPLE DATA, NOT A TRACK RECORD (JSON files carry a `_sample` key; `ledger.csv` cannot, because the page checks its header exactly, so it sits in a folder and beside a marker file that carry the label). It defaults to the system temp folder and refuses to write inside `data/paper/`.
- `check_no_sample_staged.sh` exits 1 if a staged path looks like sample data. Run it before committing.

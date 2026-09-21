"""Run the whole project: parse -> merge tags -> build the database -> report.

    python run_pipeline.py                 # everything
    python run_pipeline.py chatgpt codex   # re-parse these platforms, then the rest

Tagging itself is not re-run here; see aiusage/tag_api.py.
"""
import sys
import time

from aiusage import build_db, report, tagging
from aiusage.common import save
from aiusage.parsers import chatgpt, claude_code, claude_web, codex, gemini

PARSERS = {
    "claude_web": claude_web,
    "chatgpt": chatgpt,
    "gemini": gemini,
    "claude_code": claude_code,
    "codex": codex,
}


def main(selected: list[str]) -> None:
    for name in selected or PARSERS:
        start = time.time()
        sessions, messages = PARSERS[name].parse()
        save(name, sessions, messages)
        print(f"    done in {time.time() - start:.1f}s")

    if list(tagging.OUTPUTS.glob("batch_*.json")):
        tagging.merge()
    else:
        print("no tagging outputs found - skipping tag merge")

    build_db.build()
    report.main()


if __name__ == "__main__":
    main(sys.argv[1:])

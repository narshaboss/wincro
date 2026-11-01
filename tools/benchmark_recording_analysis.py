"""Opt-in real GPT analysis of selected files; no desktop input or existing plan writes.

Results are registered ONLY in a newly created isolated output directory.
This consumes the configured model account; it is never part of automatic CI.
"""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_bridge.codex_runner import load_runtime
from src.agent_bridge.contracts import walk
from src.agent_bridge.recording_jobs import RecordingJobs
from src.agent_bridge.storage import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-model-run", action="store_true", required=True)
    args = parser.parse_args()
    metadata = read_json(args.source_job / "job.json")
    media = metadata["media"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "plans").mkdir()
    (output / "templates").mkdir()
    jobs = RecordingJobs(output / "jobs", output / "plans", output / "templates")
    started = time.monotonic()
    try:
        jobs.start(media["path"], metadata["goal"], load_runtime(args.runtime),
                   input_log=media.get("input_log"), excluded=media.get("excluded", ()), auto_register=True, preserve_timing=True)
        previous = None
        while jobs.busy:
            state = jobs.snapshot()
            message = state.get("message")
            if message != previous:
                print(f"{time.monotonic() - started:.1f}s {message}", flush=True)
                previous = message
            jobs.worker.join(1)
        state = jobs.snapshot()
        service, draft = jobs.load_result(state["job_id"])
        summary = {"elapsed_seconds": round(time.monotonic() - started, 3),
            "status": state["status"], "review_pass": state.get("review_pass"),
            "performance": state.get("performance"), "message": state["message"],
            "model_message": state.get("model_message"), "analysis": draft.get("analysis"),
            "publication": state.get("publication"), "crosschecks": draft.get("crosschecks"),
            "actions": [{k: rule.get(k) for k in ("rule_id", "action_type", "description", "action_text",
                        "action_keys", "target_image", "wait_after", "typing_delay", "typing_timestamps", "search_region")}
                        for rule, *_ in walk(draft["plan"])]}
        write_json(output / "summary.json", summary)
        print(f"RESULT {summary['status']} {summary['elapsed_seconds']}s passes={summary['review_pass']}", flush=True)
        print(str(output / "summary.json"), flush=True)
    finally:
        jobs.close()
        if jobs.worker:
            jobs.worker.join(5)


if __name__ == "__main__":
    main()

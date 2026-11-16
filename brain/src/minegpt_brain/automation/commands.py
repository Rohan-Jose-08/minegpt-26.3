"""The ``/minegpt job`` command, which the mod passes on to the brain as typed."""

from __future__ import annotations

from .jobs import JobBook

HELP = [
    "/minegpt job add <when>: <goal>  e.g. every day at dawn: harvest and replant the wheat farm",
    "  when: every [n] days [at dawn|morning|noon|dusk|night|midnight] (game time), "
    "every [n] minutes|hours (real time), daily, nightly, hourly",
    "/minegpt job list | run <id> | pause <id> | resume <id> | remove <id>",
    "/minegpt job off | on: stop or restart all jobs in this world (and what MineGPT does between them)",
]


def handle(text: str, book: JobBook) -> list[str]:
    """Carries out a job command and returns the lines to show the player."""
    words = text.strip().split(None, 1)
    verb = words[0].lower() if words else "help"
    rest = words[1].strip() if len(words) > 1 else ""

    if verb in ("help", "?"):
        return HELP
    if verb == "add":
        try:
            job = book.add(rest)
        except ValueError as e:
            return [f"Could not add the job: {e}."]
        return [f"Added job {job.describe()}. It runs whenever MineGPT has no goal of yours to work on."]
    if verb in ("off", "on"):
        book.active = verb == "on"
        return ["Jobs are on: they run whenever MineGPT has no goal of yours." if book.active
                else "Jobs are off in this world. /minegpt job on starts them again."]
    if verb in ("list", "ls"):
        if not book.jobs:
            return ["No jobs in this world yet. " + HELP[0]]
        head = f"{len(book.jobs)} job{'s' if len(book.jobs) != 1 else ''}" + ("" if book.active else " (all off)")
        return [head + ":"] + ["  " + j.describe() for j in book.jobs]
    if verb in ("run", "pause", "disable", "resume", "enable", "remove", "delete"):
        try:
            job_id = int(rest.lstrip("#"))
        except ValueError:
            return [f"Say which job, like /minegpt job {verb} 1."]
        job = book.get(job_id)
        if job is None:
            return [f"There is no job #{job_id}. /minegpt job list shows them."]
        if verb in ("remove", "delete"):
            book.remove(job_id)
            return [f"Removed job #{job_id}."]
        if verb in ("pause", "disable"):
            job.enabled = False
            book.save()
            return [f"Paused job #{job_id}."]
        job.enabled = True
        job.failures_in_row = 0
        job.retry_after = None
        if verb == "run":
            job.run_now = True
            book.save()
            return [f"Job #{job_id} will start as soon as MineGPT is free."]
        book.save()
        return [f"Resumed job #{job_id}."]
    return [f"Unknown job command {verb!r}."] + HELP

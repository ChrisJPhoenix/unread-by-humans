# CLAUDE.md — Project

`design/` holds the architecture, including library designs.
Before you begin, read design/README.md and design/testing.md.

When you know the user wants you to work on a library, read the README.md
file in its directory and any design files mentioned in it.

Before you do anything, think out loud about which file(s) you want to read,
but don't stop and wait for input. Once you've output the file-reading
intention, keep going.

The user will prompt you on which feature(s) (which implies the libraries)
to modify, and what the desired change is.

**Where things live.** `design/music-web.md` is the top-level map for this
repository's one app, the music web port: the score-text parser is
`libs/music_parser/`, pitch/chord→MIDI conversion is `libs/pitch/`, the render
pipeline and the `FakeBackend` seam are `libs/music_render/`, and the web layer
— routes, static assets, the smoke tests — is `web/`.

**Work here is decomposed into single, green-gated steps.** Each step is a
fully-specified, one-agent, one-commit unit — see `design/work-plans.md` §1
("The unit is a step") and §12 ("The step file") for what a step contains and
how it is sized. After each step, verify by running `./bin/green`, which
reports: the diff since the last verified checkpoint; the `bin/dev test`
return code; and, on a non-zero return code, the test output. (`bin/dev test`
is app-agnostic and covers all three domains.) See this repository's
`README.md` for the workflow it describes — design doc, then plan step, then
one agent per step, then `./bin/green`, then a commit on green. The agent
definitions that execute steps live in tooling outside this repository; they
are not shipped here.

CRITICAL INSTRUCTION: Read and ALWAYS follow every rule in CLAUDE_ALWAYS.md. Prompt sub-agents with all critical instructions.

@CLAUDE_ALWAYS.md

CRITICAL INSTRUCTION: Follow the rule in CLAUDE_ALWAYS.md that says NEVER write a one-off test - anything that needs to be verified should be added to the test harness.

CRITICAL INSTRUCTION: NEVER run command-line code or temp-script code FOR ANY REASON.

CRITICAL INSTRUCTION: Never use `cd` at the front of Bash commands. `cd` makes it harder to approve commands. You may assume you're in the correct directory.

CRITICAL INSTRUCTION: Never use "sed -n" because it makes it harder to approve commands. "Read" is the right tool for reading parts of files, not "sed -n".

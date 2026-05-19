# Contributing

Thanks for your interest in the project. This guide covers the main ways to contribute.

## Adding New Prompt Categories

Prompts live in `data/prompts/adversarial_prompts.yaml`. Each prompt needs:

```yaml
- id: unique_id                # must be unique across the file
  task: task_pose              # task type (pose, waypoint, pick_place)
  category: adversarial        # baseline | adversarial | obfuscated | suffix_injection
  risk_level: high             # safe | medium | high
  description: "What this prompt tests"
  prompt: "The actual prompt text..."
```

Follow the existing naming convention (`<task>_<variant>`) and make sure the `id` field is descriptive enough to understand what the prompt targets.

## Adding Static Analysis Patterns

New detection patterns go in `scripts/static_analyzer.py`:

1. Add a `Pattern` entry to the `PATTERNS` list with an appropriate severity and weight.
2. Add a corresponding regex to `REGEX_DETECTORS` or an AST-based check in `ast_metadata()`.
3. Test with: `python3 scripts/static_analyzer.py <test_file.py> --json`

## Code Style

- Python: follow PEP 8. We use `ruff` for linting (`pip install -r requirements-dev.txt`).
- YAML: 2-space indentation, no tabs.
- Shell: use `bash` with `set -uo pipefail`. Quote your variables.
- Commit messages: imperative mood, concise first line (<72 chars), body as needed.

## Pull Requests

1. Fork the repo and create a feature branch from `main`.
2. Keep changes focused — one PR per logical change.
3. Include a brief description of what the change does and why.
4. If you're adding a new prompt category, include a smoke test demonstrating it works.

## What We Won't Accept

- Changes that remove or weaken the safety evaluation components (safety listener, static analyzer, disclaimers).
- Raw adversarial datasets or model weights submitted as PRs.
- Dependencies on external services that would break offline reproducibility.

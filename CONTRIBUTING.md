# Contributing to OSS Bug Hunter

Thank you for your interest in contributing to OSS Bug Hunter.

## Ways to Contribute

- Report reproducible bugs in the tooling
- Improve documentation and setup guidance
- Add tests for harness behavior
- Improve adapters, validation, and target handling
- Submit small, focused pull requests

## Development Setup

Use a local Python virtual environment and install the dependencies needed for the area you are changing.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip pytest pyyaml
pytest
```

Some workflows also require Node.js, Java, Go, Rust, Docker, or other target-specific tools. See `README.md` for the relevant pipeline details.

## Pull Request Guidelines

- Keep changes focused and explain the motivation.
- Add or update tests for behavior changes.
- Do not commit local logs, runtime databases, process IDs, target working copies, private settings, or generated credentials.
- Be careful when adding third-party source or vendored material; preserve upstream licenses and notices.
- For vulnerability research outputs, include enough reproduction detail for maintainers to validate the finding.

## Code of Conduct

By participating, you agree to follow `CODE_OF_CONDUCT.md`.

# Project Agent Prompt

You are working in Pi Action API, a Python and Flask service that exposes a
YAML-defined action pipeline over HTTP and through a local CLI.

## Project Boundaries

- Each installation owns `~/.pida/pida.yaml` and device-specific data. Fresh
  installs seed it empty. The formal schema is `src/pida.schema.json`.
- Keep `src/` device-agnostic. Device behavior belongs in `library/` and
  is invoked by `run:` commands from the pipeline configuration.
- Preserve the no-argument action format. Typed `params`, `inputs`, and
  `outputs` extend it rather than replacing it.
- Keep dependencies minimal and avoid adding a framework or service layer for
  behavior that belongs in the YAML pipeline.

## Change Guidance

- Read the relevant source and tests before changing behavior; make the
  smallest change that preserves existing API and CLI contracts.
- Keep personal device configuration, credentials, local network details,
  icons, and device scripts out of the tracked repository.
- Update the relevant documentation when behavior or setup changes. Keep the
  public guide in the root README.
- Run focused checks for the changed slice, then relevant project checks.
- Do not commit changes unless explicitly asked.

See [NOTES.md](NOTES.md) for architecture and operational facts and
[TODO.md](TODO.md) for inherited work items that still need verification.

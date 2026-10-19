# Repository workflow

- Before implementing a feature or fixing a bug, create or identify its GitHub Issue.
- Work on a separate branch. Link the Issue in the PR.
- Keep purpose, acceptance criteria, changes, tests, results and remaining work in the Issue and PR.
- Do not implement directly on main. Do not merge without explicit approval.
- Never search for credentials or private images. Do not access photo libraries.
- Keep input images, results, credentials and request/response bodies out of logs and version control.
- Live API calls require explicit approval of safe credential supply, request count and cost.
- Clearly distinguish mock replay, synthetic tests, stubbed HTTP, and measured live recognition.
- Run `python -m unittest discover -s tests -v`. CI must not call the real API.

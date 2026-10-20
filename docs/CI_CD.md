# CI/CD

Every change reaches `main` the same way: create a branch from `main`, push it, open a pull request, and merge only when the **CI passed** check is green. After the merge, CI runs again on `main`, and if it passes, the app is deployed to your Hugging Face Space.

```text
branch from main ──► pull request ──► CI ──► merge ──► CI on main ──► deploy to the Space
```

## What CI checks

Defined in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). It runs on every pull request to `main`, every push to `main`, and on demand.

| Job | Checks | Fails when |
| --- | --- | --- |
| **Repository integrity and secrets** | Tracked-file policy ([`check_repo_policy.py`](../scripts/ci/check_repo_policy.py)); documentation links ([`check_docs_links.py`](../scripts/ci/check_docs_links.py)); [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 (checksum-verified) over the whole history | `.env`, private keys, archive data (`data/`, `memory/`), logs, the index, or a file over 1 MB is tracked; a merge-conflict marker is left; a relative link points nowhere; anything that looks like a credential is anywhere in history |
| **Backend lint, tests, and contracts** | `ruff check` (syntax, pyflakes, bugbear, bandit security rules; see [`ruff.toml`](../ruff.toml)); byte-compilation; all unit tests under coverage; data-directory isolation; the contract check | A lint finding; a failing test; coverage under 83% ([`.coveragerc`](../.coveragerc)); a test writing to the data directory; a changed stored format or API without an updated snapshot |
| **Frontend lint, tests, and build** | `npm ci`, ESLint (recommended and React hooks rules), `node --test`, `vite build` | A lint error, failing test, or broken build |
| **Dependency vulnerabilities** | `pip-audit` on the exact versions in `constraints.txt`; `npm audit` | A known vulnerability, except the accepted ones below |
| **Deployment image** | Builds the [Dockerfile](../Dockerfile); checks the image refuses to start without a token in hosted mode; starts it and checks the health check, the UI, and that the API requires the token | The image doesn't build or doesn't behave |
| **CI passed** | Waits for all of the above | Any job failed, was cancelled, or was skipped |

### The schema check

SpyMonk has no SQL database. Its schemas are the JSON files it stores and the API the UI calls. [`scripts/check_contracts.py`](../scripts/check_contracts.py) snapshots them in [`contracts/`](../contracts):

- [`storage.json`](../contracts/storage.json):
  - the topic-audit JSON schema;
  - the shape and version of unanswered-question records and research run logs;
  - the index fingerprint and evidence-status vocabulary;
  - the files the hosted backup keeps.
- [`openapi.json`](../contracts/openapi.json): the HTTP API.

If a change alters any of them, CI fails and prints the difference. When the change is intended, run `python scripts/check_contracts.py --update`, commit `contracts/`, and say in the pull request what it means:

- **Audits and records:** do existing files still load, or is a migration needed?
- **Index fingerprint or evidence vocabulary:** existing indexes need a rebuild.
- **Backup patterns:** what stops being backed up?
- **API:** does the UI still work?

## Deployment

Defined in [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml). After CI succeeds on a push to `main`, it deploys that exact commit with [`scripts/deploy_hf_space.py`](../scripts/deploy_hf_space.py). The Space keeps its own secrets and its archive backup, so a deploy changes only the code. It can also be run by hand from the Actions tab.

It does nothing until it's configured (below), so it never fails before then.

## One-time GitHub setup

These settings can only be changed by the repository owner.

1. **Protect `main`.** Settings → Rules → Rulesets → New branch ruleset, targeting `main`:
   - Require a pull request before merging.
   - Require status checks to pass: add **CI passed**.
   - Block force pushes and deletions.
2. **Turn on secret protection** (free for public repositories). Settings → Advanced Security: enable **Secret scanning** and **Push protection**. Push protection rejects a push that contains a known credential before it becomes public, which CI can only detect afterwards.
3. **Turn on Dependabot alerts and security updates** on the same page. [`.github/dependabot.yml`](../.github/dependabot.yml) also opens weekly grouped update pull requests for pip, npm, GitHub Actions, and the Docker base image.
4. **Enable deployment.**
   - Settings → Environments → New environment `production`. Optionally add yourself as a required reviewer, to approve each deploy.
   - Add an environment secret `HF_DEPLOY_TOKEN`: a Hugging Face fine-grained token with write access to the Space only.
   - Settings → Secrets and variables → Actions → Variables: add `HF_SPACE_ID`, for example `your-name/spymonk`.

## Run the checks locally

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m coverage report
.\.venv\Scripts\python.exe scripts\check_contracts.py
.\.venv\Scripts\python.exe scripts\ci\check_repo_policy.py
.\.venv\Scripts\python.exe scripts\ci\check_docs_links.py
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
```

Optional: `pip install pre-commit` and then `pre-commit install` run gitleaks, ruff, and the repository policy on every commit ([`.pre-commit-config.yaml`](../.pre-commit-config.yaml)). On a public repository, a secret caught before the commit never becomes public.

## Accepted vulnerabilities

CI ignores only these advisories, by ID. Any new advisory fails the build.

| Advisories | Package | Why accepted | Review |
| --- | --- | --- | --- |
| PYSEC-2026-311, PYSEC-2026-3813, PYSEC-2026-3814, PYSEC-2026-3815 | chromadb 1.5.9 (latest; no fixed version) | All four are in the Chroma **server**: code injection through its collection endpoints, and missing tenant checks in its authorization provider. SpyMonk uses Chroma only as an embedded library on local disk or as a Chroma Cloud client, never runs the server, and passes no embedding function or remote model to Chroma. | When chromadb publishes a fix, upgrade and remove the ignores; otherwise re-check by 2027-01-20 |

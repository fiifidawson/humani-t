# Security policy

humani-t is built for humanitarian teams whose data can put people at risk if it leaks. We take reports seriously and would much rather hear about a problem from you than from an incident.

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security problem.**

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/fiifidawson/humani-t/security/advisories/new)). Only the maintainers can see the report, and we can work on a fix and an advisory with you in the same place.

Please include:

- what an attacker could do, and what they need first (an account, a role in a project, network access);
- the steps to reproduce it, against a local stack where possible ([`docs/deploy/self-hosting.md`](docs/deploy/self-hosting.md));
- the commit or release you tested;
- any suggested fix.

Use fictional data only. Never include real personal data in a report, even data you found through the vulnerability.

## What to expect

- We acknowledge a report within **5 working days**.
- We tell you whether we can reproduce it, and how severe we think it is, within **14 days**.
- We aim to release a fix within **90 days**, sooner for anything that exposes personal data. We agree a disclosure date with you, and credit you in the advisory unless you prefer not to be named.

humani-t is maintained by volunteers, so these are aims, not contractual commitments. If you hear nothing within the first deadline, please follow up on the same report.

## Supported versions

humani-t is in early development and has no releases yet. Fixes go to the `main` branch only. Once there are releases, this section will say which ones receive security fixes.

## Scope

In scope: the code in this repository — the API, the web app, the container images, the Compose stack and the Caddy configuration — and the default configuration it ships with.

Out of scope:

- a deployment whose operator changed a security default (for example, published the database port, or put Ollama on the internet);
- vulnerabilities in a dependency that do not affect how humani-t uses it (report those upstream; we track them through Dependabot and the scanners in CI);
- denial of service by sheer traffic volume;
- findings from automated scanners without a demonstrated impact.

## How the project protects itself

- Every change runs the checks in [`.github/workflows/`](.github/workflows/): tests, type checks, CodeQL, secret scanning, dependency and image scanning, and a workflow linter.
- Every GitHub Action and every container image is pinned to an exact digest.
- The security controls, and the test that proves each one, are listed in [`docs/security/controls.md`](docs/security/controls.md), together with the gaps that are known and not yet closed.

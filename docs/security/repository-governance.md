# Repository Governance

## Enforced Controls

- The GitHub repository is private.
- Complete Git history is scanned for secrets before the release gate.
- Pull requests identify `@Ansh1707` as code owner.
- Dependabot checks Python, npm, GitHub Actions, and Docker dependencies weekly.
- CI enforces tests, agent evaluations, vulnerability scans, deployment proof, image provenance, and keyless signatures.
- Security reports use GitHub's private security workflow.
- Squash merge is the only enabled pull-request merge strategy.
- Merged branches are deleted automatically.

## Branch Protection

GitHub returned `403` for branch protection because this private repository is on a plan that does not provide protection for private repositories. The repository must remain private; it must not be made public solely to unlock this feature.

Until the account plan changes, the release gate, CODEOWNERS, pre-commit Gitleaks scan, and review workflow are the compensating controls. Enable required status checks and one approving review on `main` immediately after GitHub makes private-repository branch protection available.

## Review Cadence

- Review Dependabot pull requests weekly.
- Review vulnerability exceptions before their expiry dates.
- Review repository access and deploy credentials monthly.
- Verify release artifacts and image signatures for every published version.

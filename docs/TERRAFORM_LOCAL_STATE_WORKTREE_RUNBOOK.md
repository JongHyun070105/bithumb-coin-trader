# Terraform local-state worktree runbook

## Current invariant

This deployment currently uses Terraform's implicit local backend. There is no explicit `backend` block. The authoritative deployed-environment state is outside feature worktrees at:

```text
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate
```

The state can contain sensitive values. Keep it mode-restricted, back it up before writes, and never commit it to Git.

## Worktree rule

Terraform commands run from a feature worktree must not rely on the default local state location. The worktree does not contain the authoritative `terraform.tfstate`; omitting `-state` makes Terraform evaluate an empty local state and can produce a false plan that attempts to recreate deployed infrastructure.

For every plan that reads the deployed environment, pass the authoritative state explicitly:

```sh
terraform -chdir=/absolute/path/to/reviewed-worktree/infra/aws plan \
  -state=/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate \
  ...
```

Before applying a saved local-backend plan, confirm that its embedded lineage and serial match the current authoritative state. Apply it using the same state context and an explicit backup path:

```sh
terraform -chdir=/absolute/path/to/reviewed-worktree/infra/aws apply \
  -input=false \
  -no-color \
  -state=/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate \
  -state-out=/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate \
  -backup=/absolute/restricted/path/to/pre-apply-state.backup \
  /absolute/path/to/reviewed.tfplan
```

After apply, inspect the authoritative state directly and run a new explicit-state plan. A remediation is reconciled only when that plan reports `0 add / 0 change / 0 destroy`.

Do not try to bind this implicit backend with `terraform init -backend-config=path=...`. Do not copy state into a worktree, create a backend block, migrate or push state, or import resources as an ad hoc workaround. A remote-state migration is a separate project after infrastructure validation is closed.

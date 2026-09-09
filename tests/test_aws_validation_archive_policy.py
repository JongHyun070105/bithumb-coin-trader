import fnmatch
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_TEMPLATE = (
    ROOT
    / "infra"
    / "aws"
    / "identity"
    / "collector-permissions-boundary-validation.json.example"
)


def _render_boundary() -> dict:
    rendered = (
        BOUNDARY_TEMPLATE.read_text(encoding="utf-8")
        .replace("${ARCHIVE_BUCKET_NAME}", "example-validation-bucket")
        .replace("${ACCOUNT_ID}", "123456789012")
    )
    return json.loads(rendered)


def _object_permissions(policy: dict) -> tuple[set[str], list[str]]:
    statement = next(item for item in policy["Statement"] if item["Sid"] == "ValidationArchiveObjects")
    resources = statement["Resource"]
    if isinstance(resources, str):
        resources = [resources]
    return set(statement["Action"]), list(resources)


def _is_allowed(resources: list[str], arn: str) -> bool:
    return any(fnmatch.fnmatchcase(arn, pattern) for pattern in resources)


def test_validation_boundary_allows_only_required_s3_actions() -> None:
    actions, _ = _object_permissions(_render_boundary())

    assert actions == {"s3:GetObject", "s3:PutObject"}


def test_validation_boundary_accepts_fresh_epochs_but_rejects_other_scopes() -> None:
    _, resources = _object_permissions(_render_boundary())
    bucket = "arn:aws:s3:::example-validation-bucket"

    assert _is_allowed(resources, f"{bucket}/market-data/temporary/aws-validation-45m-20260909-1600/object.zst")
    assert _is_allowed(resources, f"{bucket}/market-data/temporary/aws-validation-30h-20260910-0440/object.zst")
    assert not _is_allowed(resources, f"{bucket}/market-data/temporary/aws-72h-soak-20260905-8017b83e/object.zst")
    assert not _is_allowed(resources, f"{bucket}/market-data/canonical/aws-validation-45m-20260909-1600/object.zst")
    assert not _is_allowed(resources, "arn:aws:s3:::other-bucket/market-data/temporary/aws-validation-45m-x/object.zst")


def test_terraform_uses_the_same_stable_validation_scope() -> None:
    main_tf = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    variables_tf = (ROOT / "infra" / "aws" / "variables.tf").read_text(encoding="utf-8")

    assert 'variable "collector_archive_namespace"' in variables_tf
    assert 'default     = "aws-validation-"' in variables_tf
    assert '${local.temporary_archive_namespace}*/*' in main_tf
    assert 'actions = ["s3:GetObject", "s3:PutObject"]' in main_tf
    assert "canonical_archive_prefix" not in main_tf[main_tf.index('data "aws_iam_policy_document" "collector"'):]

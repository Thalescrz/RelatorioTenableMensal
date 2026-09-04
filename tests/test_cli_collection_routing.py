from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tenable_reports.cli as cli_module
from tenable_reports.application.publishing import sha256_file
from tenable_reports.application.staged_execution import (
    CheckpointArtifact,
    CollectionCheckpoint,
    LocalBuildDependencies,
    RemoteCollectionDependencies,
    RemoteCollectionRequest,
    build_client_local,
    collect_client_remote,
)
from tenable_reports.domain.reporting import previous_calendar_month


def _routing_period():
    return previous_calendar_month(
        reference_at="2026-09-12T10:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def _routing_profile(
    *,
    client_id: str = "client-a",
    tenant_id: str = "tenant-a",
    timezone: str = "America/Fortaleza",
) -> SimpleNamespace:
    return SimpleNamespace(
        client_id=client_id,
        tenant_id=tenant_id,
        reporting=SimpleNamespace(timezone=timezone),
        report=SimpleNamespace(
            tag_reports=SimpleNamespace(enabled=False, tags=()),
        ),
    )


def _persist_routing_checkpoint(
    root: Path,
) -> tuple[RemoteCollectionRequest, CollectionCheckpoint, Path]:
    period = _routing_period()
    normalized_path = (root / "normalized" / "findings.jsonl.gz").resolve()
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.write_bytes(b"normalized-fixture")
    request = RemoteCollectionRequest(
        storage_root=root,
        checkpoint_path=root / "checkpoints" / "client-a" / "run-a.json",
        client_id="client-a",
        tenant_id="tenant-a",
        run_id="run-a",
        logical_job_id="job-a",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period=period.to_dict(),
    )
    digest = sha256_file(normalized_path)
    expected = CollectionCheckpoint(
        schema_version=1,
        client_id=request.client_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        logical_job_id=request.logical_job_id,
        execution_type=request.execution_type,
        mode=request.mode,
        origin=request.origin,
        attempt_number=request.attempt_number,
        period=dict(request.period),
        component_metadata={
            "VM_CORE": {
                "status": "COMPLETE",
                "normalized_findings_path": str(normalized_path),
            },
            "WAS": {"status": "SKIPPED"},
            "CLOUD": {"status": "SKIPPED"},
        },
        artifacts=(
            CheckpointArtifact(
                component="VM_CORE",
                kind="normalized_findings",
                path=normalized_path,
                sha256=digest,
            ),
        ),
        hashes={"normalized_findings": digest},
    )
    written = collect_client_remote(
        request,
        dependencies=RemoteCollectionDependencies(collect=lambda _: expected),
    )
    return request, written, normalized_path


def _published_dataset_payload(period) -> dict[str, object]:
    return {
        "schema_version": 1,
        "metric_definition_version": "report-definition-v1.2",
        "client_id": "client-a",
        "run_id": "run-a",
        "execution_type": "MANUAL",
        "period": period.to_dict(),
    }


def _tag_dataset_payload(
    period,
    *,
    tag_uuid: str,
    category_uuid: str,
    category_name: str,
    value: str,
    include_temporal_comparison: bool,
) -> dict[str, object]:
    return {
        **_published_dataset_payload(period),
        "document_kind": "tag",
        "tag": {
            "tag_uuid": tag_uuid,
            "category_uuid": category_uuid,
            "category_name": category_name,
            "value": value,
            "include_temporal_comparison": include_temporal_comparison,
        },
    }


def _tag_materialization_fixture(
    root: Path,
    *,
    rows: tuple[dict[str, object], ...],
) -> tuple[SimpleNamespace, SimpleNamespace, CollectionCheckpoint, object]:
    period = _routing_period()
    profile = _routing_profile()
    canonical_path = (
        root
        / "report-datasets"
        / "client-a"
        / "run-a"
        / period.period_id
        / "report-dataset.json"
    ).resolve()
    normalized_path = (
        root / "normalized" / "client-a" / "run-a" / "findings.jsonl.gz"
    ).resolve()
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(
        json.dumps(_published_dataset_payload(period), ensure_ascii=False),
        encoding="utf-8",
    )
    normalized_path.write_bytes(b"normalized-fixture")
    artifacts = [
        CheckpointArtifact(
            component="VM_CORE",
            kind="canonical_dataset",
            path=canonical_path,
            sha256=sha256_file(canonical_path),
        ),
        CheckpointArtifact(
            component="VM_CORE",
            kind="normalized_findings",
            path=normalized_path,
            sha256=sha256_file(normalized_path),
        ),
    ]
    tag_metadata: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        artifact_kind = f"tag_dataset_{index:04d}"
        tag_path = Path(
            row.get("path")
            or canonical_path.parent
            / "tags"
            / str(row["tag_uuid"])
            / "report-dataset.json"
        ).resolve()
        if tag_path != canonical_path and not tag_path.exists():
            tag_path.parent.mkdir(parents=True, exist_ok=True)
            tag_path.write_text(
                json.dumps(row["payload"], ensure_ascii=False),
                encoding="utf-8",
            )
        tag_metadata.append({
            "artifact_kind": artifact_kind,
            "tag_uuid": row["tag_uuid"],
            "category_uuid": row["category_uuid"],
            "category_name": row["category_name"],
            "value": row["value"],
            "include_temporal_comparison": row["include_temporal_comparison"],
        })
        artifacts.append(
            CheckpointArtifact(
                component="VM_CORE",
                kind=artifact_kind,
                path=tag_path,
                sha256=sha256_file(tag_path),
            )
        )
    checkpoint = CollectionCheckpoint(
        schema_version=1,
        client_id="client-a",
        tenant_id="tenant-a",
        run_id="run-a",
        logical_job_id="job-a",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period=period.to_dict(),
        component_metadata={
            "VM_CORE": {
                "status": "COMPLETE",
                "canonical_dataset_kind": "canonical_dataset",
                "normalized_findings_kind": "normalized_findings",
                "tag_datasets": tag_metadata,
                "selected_tag_count": len(tag_metadata),
                "vm_export_mode": "combined",
                "vm_export_outcome": "FULL",
                "collection_route": "tenable_vm",
                "reconstruction_status": "CURRENT_WINDOW",
                "collection_sources": ["tenable_vm_vulnerabilities"],
            },
            "WAS": {"status": "SKIPPED"},
            "CLOUD": {"status": "SKIPPED"},
        },
        artifacts=tuple(artifacts),
        hashes={artifact.kind: artifact.sha256 for artifact in artifacts},
    )
    args = SimpleNamespace(
        output_root=root,
        include_output=False,
        skip_history=True,
        history_database=None,
        history_export_csv=None,
        origin="MANUAL",
    )
    return args, profile, checkpoint, period


class CliCollectionRoutingTests(unittest.TestCase):
    def test_collect_client_requires_live_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm-live-api"):
            cli_module.command_collect_client(
                SimpleNamespace(confirm_live_api=False)
            )

    def test_collect_client_executes_only_remote_stage_and_prints_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            period = _routing_period()
            profile = _routing_profile()
            checkpoint_path = root / "checkpoints" / "client-a" / "run-a.json"
            args = SimpleNamespace(
                confirm_live_api=True,
                profile=root / "profile.json",
                mode="manual",
                output_root=root,
                checkpoint=checkpoint_path,
                run_id="run-a",
                logical_job_id="job-a",
                attempt_number=1,
                origin="MANUAL",
                skip_history=False,
            )
            collected = SimpleNamespace(run_id="run-a", output_root=root)
            events: list[object] = []

            def execute_period(received_args, *, execution_type, period):
                events.append(
                    ("execute", execution_type, period.period_id, received_args.skip_history)
                )
                return collected

            def checkpoint_adapter(received, *, request):
                events.append(("adapt", received.run_id, request.run_id))
                return CollectionCheckpoint(
                    schema_version=1,
                    client_id=request.client_id,
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    logical_job_id=request.logical_job_id,
                    execution_type=request.execution_type,
                    mode=request.mode,
                    origin=request.origin,
                    attempt_number=request.attempt_number,
                    period=dict(request.period),
                    component_metadata={
                        "VM_CORE": {"status": "COMPLETE"},
                        "WAS": {"status": "SKIPPED"},
                        "CLOUD": {"status": "SKIPPED"},
                    },
                    artifacts=(),
                    hashes={},
                )

            def remote_adapter(request, *, dependencies):
                events.append(("remote", request.checkpoint_path, request.storage_root))
                return dependencies.collect(request)

            output = io.StringIO()
            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_period_for_mode", return_value=period),
                patch.object(cli_module, "_execute_period", side_effect=execute_period),
                patch.object(
                    cli_module,
                    "_checkpoint_from_collected_period",
                    side_effect=checkpoint_adapter,
                    create=True,
                ),
                patch.object(
                    cli_module,
                    "collect_client_remote",
                    side_effect=remote_adapter,
                    create=True,
                ),
                patch.object(cli_module, "_publish_collected_period") as publish,
                patch.object(cli_module, "generate_full_base_report") as render_base,
                patch.object(
                    cli_module,
                    "generate_customizations_report",
                ) as render_custom,
                redirect_stdout(output),
            ):
                result = cli_module.command_collect_client(args)

            self.assertEqual(result, 0)
            self.assertEqual(
                events,
                [
                    ("remote", checkpoint_path.resolve(), root),
                    ("execute", "MANUAL", "2026-08", True),
                    ("adapt", "run-a", "run-a"),
                ],
            )
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "status": "COLLECTION_READY",
                    "client_id": "client-a",
                    "run_id": "run-a",
                    "checkpoint": str(checkpoint_path.resolve()),
                },
            )
            publish.assert_not_called()
            render_base.assert_not_called()
            render_custom.assert_not_called()

    def test_build_client_uses_validated_checkpoint_without_live_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            request, checkpoint, normalized_path = _persist_routing_checkpoint(root)
            profile = _routing_profile()
            args = SimpleNamespace(
                profile=root / "profile.json",
                checkpoint=request.checkpoint_path,
                output_root=root,
                include_output=False,
                skip_history=True,
                history_database=None,
                history_export_csv=None,
                template=root / "template.docx",
                cloud_template=root / "cloud-template.docx",
                assets_dir=root / "assets",
                base_output=root / "base.docx",
                custom_output=root / "custom.docx",
                mask_sensitive=False,
            )
            assembled = SimpleNamespace(run_id="run-a")
            publish_calls: list[dict[str, object]] = []

            def local_adapter(local_request, *, dependencies):
                return build_client_local(local_request, dependencies=dependencies)

            def publish(**kwargs):
                publish_calls.append(kwargs)
                return 0

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(
                    cli_module,
                    "build_client_local",
                    side_effect=local_adapter,
                    create=True,
                ),
                patch.object(
                    cli_module,
                    "_assemble_period_from_existing",
                    side_effect=AssertionError(
                        "build-client não pode recriar datasets imutáveis"
                    ),
                ) as legacy_assemble,
                patch.object(
                    cli_module,
                    "_materialize_period_from_checkpoint",
                    return_value=assembled,
                    create=True,
                ) as materialize,
                patch.object(cli_module, "_publish_collected_period", side_effect=publish),
                patch.object(cli_module, "_load_credentials") as load_credentials,
                patch.object(cli_module, "collect_external_period") as collect_external,
                patch.object(cli_module, "collect_asset_snapshot") as collect_assets,
                patch.object(
                    cli_module,
                    "collect_vm_snapshot_with_policy",
                ) as collect_vm,
                patch.object(cli_module, "collect_optional_was_snapshot") as collect_was,
                patch.object(cli_module, "execute_cloud_component") as collect_cloud,
            ):
                result = cli_module.command_build_client(args)

            self.assertEqual(result, 0)
            materialize.assert_called_once_with(
                args,
                profile,
                checkpoint,
                _routing_period(),
            )
            legacy_assemble.assert_not_called()
            self.assertEqual(
                publish_calls,
                [
                    {
                        "args": args,
                        "profile": profile,
                        "period": _routing_period(),
                        "execution_type": "MANUAL",
                        "collected": assembled,
                    }
                ],
            )
            self.assertEqual(checkpoint.client_id, "client-a")
            load_credentials.assert_not_called()
            collect_external.assert_not_called()
            collect_assets.assert_not_called()
            collect_vm.assert_not_called()
            collect_was.assert_not_called()
            collect_cloud.assert_not_called()

    def test_materialize_period_from_checkpoint_reuses_immutable_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            period = _routing_period()
            profile = _routing_profile()
            canonical_path = (
                root
                / "report-datasets"
                / "client-a"
                / "run-a"
                / period.period_id
                / "report-dataset.json"
            ).resolve()
            tag_path = (
                canonical_path.parent / "tags" / "tag-a" / "report-dataset.json"
            ).resolve()
            normalized_path = (
                root / "normalized" / "client-a" / "run-a" / "findings.jsonl.gz"
            ).resolve()
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            tag_path.parent.mkdir(parents=True, exist_ok=True)
            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            canonical_payload = {
                "schema_version": 1,
                "metric_definition_version": "report-definition-v1.2",
                "client_id": "client-a",
                "run_id": "run-a",
                "execution_type": "MANUAL",
                "period": period.to_dict(),
            }
            tag_payload = {
                **canonical_payload,
                "document_kind": "tag",
                "tag": {
                    "tag_uuid": "tag-a",
                    "category_uuid": "category-a",
                    "category_name": "Ambiente",
                    "value": "Produção",
                    "include_temporal_comparison": False,
                },
            }
            canonical_text = json.dumps(canonical_payload, ensure_ascii=False)
            tag_text = json.dumps(tag_payload, ensure_ascii=False)
            canonical_path.write_text(canonical_text, encoding="utf-8")
            tag_path.write_text(tag_text, encoding="utf-8")
            normalized_path.write_bytes(b"normalized-fixture")
            checkpoint = CollectionCheckpoint(
                schema_version=1,
                client_id="client-a",
                tenant_id="tenant-a",
                run_id="run-a",
                logical_job_id="job-a",
                execution_type="MANUAL",
                mode="manual",
                origin="MANUAL",
                attempt_number=1,
                period=period.to_dict(),
                component_metadata={
                    "VM_CORE": {
                        "status": "COMPLETE",
                        "canonical_dataset_kind": "canonical_dataset",
                        "normalized_findings_kind": "normalized_findings",
                        "tag_datasets": [
                            {
                                "artifact_kind": "tag_dataset_0001",
                                "tag_uuid": "tag-a",
                                "category_uuid": "category-a",
                                "category_name": "Ambiente",
                                "value": "Produção",
                                "include_temporal_comparison": False,
                            }
                        ],
                        "selected_tag_count": 1,
                        "vm_export_mode": "combined",
                        "vm_export_outcome": "FULL",
                        "collection_route": "tenable_vm",
                        "reconstruction_status": "CURRENT_WINDOW",
                        "collection_sources": ["tenable_vm_vulnerabilities"],
                    },
                    "WAS": {"status": "SKIPPED"},
                    "CLOUD": {"status": "SKIPPED"},
                },
                artifacts=(
                    CheckpointArtifact(
                        component="VM_CORE",
                        kind="canonical_dataset",
                        path=canonical_path,
                        sha256=sha256_file(canonical_path),
                    ),
                    CheckpointArtifact(
                        component="VM_CORE",
                        kind="normalized_findings",
                        path=normalized_path,
                        sha256=sha256_file(normalized_path),
                    ),
                    CheckpointArtifact(
                        component="VM_CORE",
                        kind="tag_dataset_0001",
                        path=tag_path,
                        sha256=sha256_file(tag_path),
                    ),
                ),
                hashes={
                    "canonical_dataset": sha256_file(canonical_path),
                    "normalized_findings": sha256_file(normalized_path),
                    "tag_dataset_0001": sha256_file(tag_path),
                },
            )
            args = SimpleNamespace(
                output_root=root,
                include_output=False,
                skip_history=True,
                history_database=None,
                history_export_csv=None,
                origin="MANUAL",
            )
            immutable_before = {
                canonical_path: canonical_path.read_bytes(),
                tag_path: tag_path.read_bytes(),
            }

            forbidden = AssertionError(
                "a montagem local não pode reconstruir nem coletar datasets"
            )
            with (
                patch.object(
                    cli_module,
                    "build_report_dataset_from_snapshot",
                    side_effect=forbidden,
                ) as build_canonical,
                patch.object(
                    cli_module,
                    "build_tag_report_datasets_from_snapshot",
                    side_effect=forbidden,
                ) as build_tags,
                patch.object(cli_module, "_load_credentials", side_effect=forbidden),
                patch.object(
                    cli_module,
                    "collect_external_period",
                    side_effect=forbidden,
                ),
                patch.object(
                    cli_module,
                    "collect_asset_snapshot",
                    side_effect=forbidden,
                ),
                patch.object(
                    cli_module,
                    "collect_vm_snapshot_with_policy",
                    side_effect=forbidden,
                ),
                patch.object(
                    cli_module,
                    "collect_optional_was_snapshot",
                    side_effect=forbidden,
                ),
                patch.object(
                    cli_module,
                    "execute_cloud_component",
                    side_effect=forbidden,
                ),
            ):
                collected = cli_module._materialize_period_from_checkpoint(
                    args,
                    profile,
                    checkpoint,
                    period,
                )

            self.assertEqual(collected.artifact.dataset_path, canonical_path)
            self.assertEqual(collected.dataset_path, canonical_path)
            self.assertEqual(collected.normalized_findings_path, normalized_path)
            self.assertEqual(collected.output_root, root / "manual")
            self.assertEqual(len(collected.tag_artifacts), 1)
            tag_artifact = collected.tag_artifacts[0]
            self.assertEqual(tag_artifact.dataset_path, tag_path)
            self.assertEqual(tag_artifact.tag.uuid, "tag-a")
            self.assertEqual(tag_artifact.tag.category_uuid, "category-a")
            self.assertEqual(tag_artifact.tag.category_name, "Ambiente")
            self.assertEqual(tag_artifact.tag.value, "Produção")
            self.assertEqual(
                {path: path.read_bytes() for path in immutable_before},
                immutable_before,
            )
            build_canonical.assert_not_called()
            build_tags.assert_not_called()

    def test_materialize_rejects_tag_dataset_semantic_mismatch(self) -> None:
        period = _routing_period()
        valid_tag = {
            "tag_uuid": "tag-a",
            "category_uuid": "category-a",
            "category_name": "Ambiente",
            "value": "Produção",
            "include_temporal_comparison": False,
        }
        base_payload = _published_dataset_payload(period)
        invalid_payloads = (
            ("document_kind", {**base_payload, "document_kind": "general", "tag": valid_tag}),
            ("missing_tag", {**base_payload, "document_kind": "tag"}),
            (
                "tag_uuid",
                {
                    **base_payload,
                    "document_kind": "tag",
                    "tag": {**valid_tag, "tag_uuid": "tag-b"},
                },
            ),
            (
                "category_uuid",
                {
                    **base_payload,
                    "document_kind": "tag",
                    "tag": {**valid_tag, "category_uuid": "category-b"},
                },
            ),
            (
                "category_name",
                {
                    **base_payload,
                    "document_kind": "tag",
                    "tag": {**valid_tag, "category_name": "Equipe"},
                },
            ),
            (
                "value",
                {
                    **base_payload,
                    "document_kind": "tag",
                    "tag": {**valid_tag, "value": "Homologação"},
                },
            ),
            (
                "include_temporal_comparison",
                {
                    **base_payload,
                    "document_kind": "tag",
                    "tag": {**valid_tag, "include_temporal_comparison": True},
                },
            ),
        )
        for field_name, payload in invalid_payloads:
            with self.subTest(field_name=field_name), tempfile.TemporaryDirectory() as directory_name:
                root = Path(directory_name).resolve()
                args, profile, checkpoint, actual_period = _tag_materialization_fixture(
                    root,
                    rows=({
                        **valid_tag,
                        "payload": payload,
                    },),
                )

                with self.assertRaisesRegex(ValueError, "TAG"):
                    cli_module._materialize_period_from_checkpoint(
                        args,
                        profile,
                        checkpoint,
                        actual_period,
                    )

    def test_materialize_rejects_canonical_and_tag_shared_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            period = _routing_period()
            canonical_path = (
                root
                / "report-datasets"
                / "client-a"
                / "run-a"
                / period.period_id
                / "report-dataset.json"
            ).resolve()
            args, profile, checkpoint, actual_period = _tag_materialization_fixture(
                root,
                rows=({
                    "tag_uuid": "tag-a",
                    "category_uuid": "category-a",
                    "category_name": "Ambiente",
                    "value": "Produção",
                    "include_temporal_comparison": False,
                    "payload": _tag_dataset_payload(
                        period,
                        tag_uuid="tag-a",
                        category_uuid="category-a",
                        category_name="Ambiente",
                        value="Produção",
                        include_temporal_comparison=False,
                    ),
                    "path": canonical_path,
                },),
            )

            with self.assertRaisesRegex(ValueError, "caminho"):
                cli_module._materialize_period_from_checkpoint(
                    args,
                    profile,
                    checkpoint,
                    actual_period,
                )

    def test_materialize_rejects_duplicate_paths_among_tag_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            period = _routing_period()
            shared_path = (
                root / "report-datasets" / "client-a" / "run-a" / "shared-tag.json"
            ).resolve()
            rows = (
                {
                    "tag_uuid": "tag-a",
                    "category_uuid": "category-a",
                    "category_name": "Ambiente",
                    "value": "Produção",
                    "include_temporal_comparison": False,
                    "payload": _tag_dataset_payload(
                        period,
                        tag_uuid="tag-a",
                        category_uuid="category-a",
                        category_name="Ambiente",
                        value="Produção",
                        include_temporal_comparison=False,
                    ),
                    "path": shared_path,
                },
                {
                    "tag_uuid": "tag-b",
                    "category_uuid": "category-a",
                    "category_name": "Ambiente",
                    "value": "Homologação",
                    "include_temporal_comparison": True,
                    "payload": _tag_dataset_payload(
                        period,
                        tag_uuid="tag-b",
                        category_uuid="category-a",
                        category_name="Ambiente",
                        value="Homologação",
                        include_temporal_comparison=True,
                    ),
                    "path": shared_path,
                },
            )
            args, profile, checkpoint, actual_period = _tag_materialization_fixture(
                root,
                rows=rows,
            )

            with self.assertRaisesRegex(ValueError, "caminho"):
                cli_module._materialize_period_from_checkpoint(
                    args,
                    profile,
                    checkpoint,
                    actual_period,
                )

    def test_build_client_rejects_checkpoint_profile_or_period_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name).resolve()
            request, _, _ = _persist_routing_checkpoint(root)
            args = SimpleNamespace(
                profile=root / "profile.json",
                checkpoint=request.checkpoint_path,
                output_root=root,
            )

            def local_adapter(local_request, *, dependencies):
                return build_client_local(local_request, dependencies=dependencies)

            incompatible_profiles = (
                _routing_profile(client_id="client-b"),
                _routing_profile(tenant_id="tenant-b"),
                _routing_profile(timezone="UTC"),
            )
            for profile in incompatible_profiles:
                with self.subTest(profile=profile):
                    with (
                        patch.object(
                            cli_module,
                            "load_client_profile",
                            return_value=profile,
                        ),
                        patch.object(
                            cli_module,
                            "build_client_local",
                            side_effect=local_adapter,
                            create=True,
                        ),
                        patch.object(
                            cli_module,
                            "_assemble_period_from_existing",
                        ) as assemble,
                    ):
                        with self.assertRaises(ValueError):
                            cli_module.command_build_client(args)
                    assemble.assert_not_called()

    def test_parser_exposes_collect_and_build_client_stages(self) -> None:
        parser = cli_module.build_parser()
        collect_args = parser.parse_args(
            [
                "collect-client",
                "--profile",
                "profile.json",
                "--checkpoint",
                "checkpoint.json",
                "--vm-resume-budget-seconds",
                "321",
                "--confirm-live-api",
            ]
        )
        build_args = parser.parse_args(
            [
                "build-client",
                "--profile",
                "profile.json",
                "--checkpoint",
                "checkpoint.json",
                "--output-root",
                "data",
                "--template",
                "template.docx",
                "--assets-dir",
                "assets",
                "--history-database",
                "history.sqlite",
                "--history-export-csv",
                "history.csv",
                "--skip-history",
            ]
        )

        self.assertIs(collect_args.handler, cli_module.command_collect_client)
        self.assertEqual(collect_args.checkpoint, "checkpoint.json")
        self.assertEqual(collect_args.vm_resume_budget_seconds, 321)
        self.assertTrue(collect_args.confirm_live_api)
        self.assertIs(build_args.handler, cli_module.command_build_client)
        self.assertEqual(build_args.checkpoint, "checkpoint.json")
        self.assertEqual(build_args.template, "template.docx")
        self.assertEqual(build_args.assets_dir, "assets")
        self.assertEqual(build_args.history_database, "history.sqlite")
        self.assertEqual(build_args.history_export_csv, "history.csv")
        self.assertTrue(build_args.skip_history)
        self.assertFalse(hasattr(build_args, "confirm_live_api"))

    def test_exact_snapshot_replay_does_not_load_credentials_or_call_tenable(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            period = previous_calendar_month(
                reference_at="2026-08-12T10:00:00-03:00",
                timezone_name="America/Fortaleza",
            )
            profile = SimpleNamespace(
                client_id="cliente-a",
                tenant_id="tenant-a",
                reporting=SimpleNamespace(
                    vm_export=SimpleNamespace(
                        strategy="combined",
                        num_assets_per_chunk=1000,
                        selective_properties="disabled",
                        historical_source="inventory_beta",
                        historical_fallback="warn_legacy",
                    )
                ),
                report=SimpleNamespace(
                    tag_reports=SimpleNamespace(enabled=False, tags=()),
                ),
            )
            args = SimpleNamespace(
                profile=directory / "profile.json",
                env_file=directory / "client.env",
                database_env_file=directory / "database.env",
                output_root=directory,
                run_id="replay-run",
                historical_source=None,
                num_assets=None,
                vm_export_strategy=None,
                vm_selective_mode=None,
                include_output=False,
                skip_history=True,
            )
            route = SimpleNamespace(
                source=SimpleNamespace(value="snapshot_replay"),
                accuracy=SimpleNamespace(value="authoritative_snapshot"),
                reconstruction_status="AUTHORITATIVE_SNAPSHOT",
                warning=None,
            )
            materialized = SimpleNamespace(
                findings_path=directory / "normalized" / "findings.jsonl.gz"
            )
            artifact = SimpleNamespace(
                dataset_path=directory / "dataset.json",
                directory=directory,
            )
            tag_bundle = SimpleNamespace(artifacts=(), warnings=())

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_compact_snapshot_repository", return_value=object()),
                patch.object(
                    cli_module,
                    "resolve_execution_collection_route",
                    return_value=(route, object()),
                ),
                patch.object(
                    cli_module,
                    "materialize_compact_snapshot_run",
                    return_value=materialized,
                ),
                patch.object(
                    cli_module,
                    "load_report_dataset_inputs",
                    return_value=SimpleNamespace(
                        tag_scope=None,
                        was_snapshot=None,
                        collection_provenance={"sources": ["compact_finding_snapshot"]},
                    ),
                ),
                patch.object(
                    cli_module,
                    "build_report_dataset_from_snapshot",
                    return_value=artifact,
                ),
                patch.object(
                    cli_module,
                    "build_tag_report_datasets_from_snapshot",
                    return_value=tag_bundle,
                ),
                patch.object(cli_module, "_load_credentials") as load_credentials,
                patch.object(cli_module, "collect_asset_snapshot") as collect_assets,
                patch.object(cli_module, "collect_vm_snapshot_with_policy") as collect_vm,
            ):
                result = cli_module._execute_period(
                    args,
                    execution_type="MANUAL",
                    period=period,
                )

            load_credentials.assert_not_called()
            collect_assets.assert_not_called()
            collect_vm.assert_not_called()
            self.assertEqual(result.collection_route, "snapshot_replay")
            self.assertEqual(result.reconstruction_status, "AUTHORITATIVE_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()

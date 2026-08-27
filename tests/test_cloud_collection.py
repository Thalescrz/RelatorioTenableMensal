from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "tenable_cloud"


def _collection_module():
    return importlib.import_module("tenable_reports.application.collect_cloud")


def _contract_module():
    return importlib.import_module("tenable_reports.application.cloud_contract")


def _cloud_module():
    return importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.client"
    )


def _queries_module():
    return importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.queries"
    )


def _fixture(name: str) -> Mapping[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FakePage:
    nodes: tuple[Mapping[str, Any], ...]
    page: int
    records: int
    end_cursor: str | None
    has_next_page: bool
    page_size: int = 50


class FakePaginator:
    def __init__(
        self,
        pages: list[FakePage] | None = None,
        *,
        error: Exception | None = None,
        error_after: int | None = None,
    ) -> None:
        self.pages = pages or [
            FakePage(
                nodes=(),
                page=1,
                records=0,
                end_cursor=None,
                has_next_page=False,
            )
        ]
        self.error = error
        self.error_after = error_after
        self.after_values: list[str | None] = []

    def paginate_pages(
        self,
        query: str,
        root_field: str,
        *,
        page_size: int,
        after: str | None = None,
        pages_completed: int = 0,
        records_completed: int = 0,
        progress: Any = None,
        **_: Any,
    ):
        self.after_values.append(after)
        if self.error is not None and self.error_after == 0:
            raise self.error
        for offset, page in enumerate(self.pages, start=1):
            if progress is not None:
                progress(
                    {
                        "root_field": root_field,
                        "page": pages_completed + offset,
                        "records": records_completed + len(page.nodes),
                        "page_size": page_size,
                        "has_next_page": page.has_next_page,
                    }
                )
            yield page
            if self.error is not None and self.error_after == offset:
                raise self.error
        if self.error is not None and self.error_after is None:
            raise self.error


def _capabilities_all() -> Any:
    contract = _contract_module()
    queries = _queries_module()
    return contract.CloudCapabilityReport(
        endpoint="https://app.tenable.com/graphql",
        checked_at="2026-08-26T12:00:00+00:00",
        connector_version=queries.CLOUD_CONNECTOR_VERSION,
        sources=tuple(
            contract.CloudSourceCapability(
                name=item.name,
                root_field=item.root_field,
                required=item.required,
                status="AVAILABLE",
                query_version=item.version,
            )
            for item in queries.CLOUD_SOURCE_QUERIES.values()
        ),
    )


def _request(tmp_path: Path) -> Any:
    module = _collection_module()
    return module.CloudCollectionRequest(
        client_id="cliente-fixture",
        tenant_id="tenant-fixture",
        run_id="run-fixture",
        execution_type="manual",
        output_root=tmp_path,
        collected_at="2026-08-26T12:00:00+00:00",
    )


def _clients(**overrides: Any) -> dict[str, Any]:
    queries = _queries_module()
    clients = {name: FakePaginator() for name in queries.CLOUD_SOURCE_QUERIES}
    clients.update(overrides)
    return clients


def test_optional_failure_is_recorded_without_discarding_required_sources(
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    cloud = _cloud_module()
    vm = _fixture("virtual-machines-page-1.json")
    image = _fixture("container-images-page-1.json")

    artifact = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=_clients(
            virtual_machines=FakePaginator(
                [FakePage(tuple(vm["nodes"]), 1, len(vm["nodes"]), None, False)]
            ),
            container_images=FakePaginator(
                [
                    FakePage(
                        tuple(image["nodes"]),
                        1,
                        len(image["nodes"]),
                        None,
                        False,
                    )
                ]
            ),
            findings=FakePaginator(
                error=cloud.CloudContractError("Fonte opcional indisponivel."),
                error_after=0,
            ),
        ),
        capabilities=_capabilities_all(),
    )

    assert artifact.source_status["virtual_machines"].status == "COMPLETE"
    assert artifact.source_status["container_images"].status == "COMPLETE"
    assert artifact.source_status["findings"].status == "UNAVAILABLE"
    assert artifact.manifest_path.is_file()
    assert artifact.source_paths["virtual_machines"].is_file()
    assert len(artifact.warnings) == 1


def test_retry_requests_only_source_missing_from_partial_collection(
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    cloud = _cloud_module()
    first_clients = _clients(
        findings=FakePaginator(
            error=cloud.CloudContractError("Fonte opcional indisponivel."),
            error_after=0,
        )
    )
    collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=first_clients,
        capabilities=_capabilities_all(),
    )

    retry_clients = _clients()
    artifact = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=retry_clients,
        capabilities=_capabilities_all(),
    )

    assert retry_clients["findings"].after_values == [None]
    assert all(
        client.after_values == []
        for name, client in retry_clients.items()
        if name != "findings"
    )
    assert artifact.source_status["findings"].status == "COMPLETE"
    assert not list(artifact.manifest_path.parent.glob(".*.page-*.jsonl"))
    assert list(artifact.manifest_path.parent.glob("*.checkpoint.json"))

def test_required_source_failure_stops_cloud_collection(tmp_path: Path) -> None:
    collection = _collection_module()
    cloud = _cloud_module()
    events: list[Mapping[str, Any]] = []

    with pytest.raises(collection.CloudRequiredSourceError, match="container_images"):
        collection.collect_cloud_snapshot(
            request=_request(tmp_path),
            clients=_clients(
                container_images=FakePaginator(
                    error=cloud.CloudTemporaryError("Timeout controlado."),
                    error_after=0,
                )
            ),
            capabilities=_capabilities_all(),
            progress_callback=events.append,
        )

    assert events[-1]["event"] == "TENABLE_CLOUD_PROGRESS"
    assert events[-1]["source"] == "container_images"
    assert events[-1]["status"] == "FAILED"


def test_resume_continues_from_checkpoint_without_duplicate_records(
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    cloud = _cloud_module()
    first = FakePaginator(
        [
            FakePage(
                nodes=({"Id": "vm-a"},),
                page=1,
                records=1,
                end_cursor="cursor-1",
                has_next_page=True,
            )
        ],
        error=cloud.CloudTemporaryError("Interrupcao controlada."),
        error_after=1,
    )

    with pytest.raises(collection.CloudRequiredSourceError):
        collection.collect_cloud_snapshot(
            request=_request(tmp_path),
            clients=_clients(virtual_machines=first),
            capabilities=_capabilities_all(),
        )

    resumed = FakePaginator(
        [
            FakePage(
                nodes=({"Id": "vm-b"},),
                page=2,
                records=2,
                end_cursor=None,
                has_next_page=False,
            )
        ]
    )
    artifact = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=_clients(virtual_machines=resumed),
        capabilities=_capabilities_all(),
    )

    records = [
        json.loads(line)
        for line in artifact.source_paths["virtual_machines"]
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert resumed.after_values == ["cursor-1"]
    assert [item["Id"] for item in records] == ["vm-a", "vm-b"]
    assert artifact.source_status["virtual_machines"].pages == 2
    assert list(artifact.manifest_path.parent.glob("*.checkpoint.json"))
    assert not list(artifact.manifest_path.parent.glob(".*.page-*.jsonl"))


def test_retry_recollects_only_source_with_invalid_consolidated_artifact(
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    first = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=_clients(),
        capabilities=_capabilities_all(),
    )
    first.source_paths["findings"].write_text("corrompido", encoding="utf-8")
    retry_clients = _clients()

    artifact = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=retry_clients,
        capabilities=_capabilities_all(),
    )

    assert retry_clients["findings"].after_values == [None]
    assert all(
        client.after_values == []
        for name, client in retry_clients.items()
        if name != "findings"
    )
    assert artifact.source_status["findings"].status == "COMPLETE"

def test_manifest_is_sanitized_and_progress_reports_page_counts(
    tmp_path: Path,
) -> None:
    collection = _collection_module()
    findings = _fixture("findings-page-1.json")
    events: list[Mapping[str, Any]] = []
    client = FakePaginator(
        [
            FakePage(
                tuple(findings["nodes"]),
                1,
                len(findings["nodes"]),
                None,
                False,
            )
        ]
    )

    artifact = collection.collect_cloud_snapshot(
        request=_request(tmp_path),
        clients=_clients(findings=client),
        capabilities=_capabilities_all(),
        progress_callback=events.append,
    )

    manifest = artifact.manifest_path.read_text(encoding="utf-8")
    assert "Authorization" not in manifest
    assert "fixture-secret" not in manifest
    assert findings["nodes"][0]["Description"] not in manifest
    assert events[-1]["status"] == "FINISHED"
    findings_events = [item for item in events if item["source"] == "findings"]
    assert any(
        item["status"] == "PROCESSING"
        and item["pages"] == 1
        and item["records"] == 1
        for item in findings_events
    )

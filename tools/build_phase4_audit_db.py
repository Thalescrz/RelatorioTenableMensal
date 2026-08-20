from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.dataset)
    output = Path(args.output)
    data = json.loads(source.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Banco de auditoria imutavel ja existe: {output}")

    assets = data["populations"]["assets"]
    findings = data["populations"]["findings"]
    non_mitigated = data["metrics"]["non_mitigated"]
    with sqlite3.connect(output) as connection:
        connection.executescript("""
            CREATE TABLE audit_summary (
                period TEXT PRIMARY KEY,
                assets_input INTEGER NOT NULL,
                assets_observed INTEGER NOT NULL,
                assets_excluded INTEGER NOT NULL,
                findings_input INTEGER NOT NULL,
                findings_included INTEGER NOT NULL,
                findings_excluded INTEGER NOT NULL,
                vulnerable_assets INTEGER NOT NULL,
                non_mitigated INTEGER NOT NULL,
                exploitable INTEGER NOT NULL,
                collection_lag_days REAL NOT NULL
            );
            CREATE TABLE asset_population (
                reason TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                count INTEGER NOT NULL,
                population_total INTEGER NOT NULL,
                share REAL NOT NULL,
                period TEXT NOT NULL
            );
            CREATE TABLE finding_population (
                reason TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                count INTEGER NOT NULL,
                population_total INTEGER NOT NULL,
                share REAL NOT NULL,
                period TEXT NOT NULL
            );
            CREATE TABLE severity_population (
                severity TEXT PRIMARY KEY,
                severity_code TEXT NOT NULL,
                rank INTEGER NOT NULL,
                count INTEGER NOT NULL,
                total INTEGER NOT NULL,
                share REAL NOT NULL,
                exploitable_total INTEGER NOT NULL,
                period TEXT NOT NULL
            );
            CREATE TABLE quality_issues (
                severity TEXT NOT NULL,
                code TEXT PRIMARY KEY,
                impact TEXT NOT NULL
            );
        """)
        period = data["period"]["period_id"]
        connection.execute(
            "INSERT INTO audit_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                period,
                assets["input"], assets["observed"], assets["excluded"],
                findings["input"], findings["included"], findings["excluded"],
                non_mitigated["vulnerable_assets"], non_mitigated["total"],
                non_mitigated["exploitable"], data["collection_timing"]["lag_days"],
            ),
        )
        asset_labels = {
            "OBSERVED_BY_SCAN": ("Observado por scan", "Incluido"),
            "OBSERVED_BY_FINDING": ("Observado por finding", "Incluido"),
            "EXCLUDED_NO_PERIOD_EVIDENCE": ("Sem evidencia no periodo", "Excluido"),
            "EXCLUDED_STALE_BEFORE_PERIOD": ("Scan anterior ao periodo", "Excluido"),
            "EXCLUDED_FIRST_SEEN_AFTER_PERIOD": ("Primeiro scan apos periodo", "Excluido"),
        }
        for code, count in assets["by_reason"].items():
            label, decision = asset_labels.get(code, (code, "Excluido"))
            connection.execute(
                "INSERT INTO asset_population VALUES (?, ?, ?, ?, ?, ?)",
                (label, decision, count, assets["input"], count / assets["input"], period),
            )
        finding_labels = {
            "INCLUDED_OPEN": ("Incluido: OPEN/REOPENED", "Incluido"),
            "EXCLUDED_AFTER_PERIOD": ("Evento apos o periodo", "Excluido"),
            "EXCLUDED_INFO": ("Severidade informativa", "Excluido"),
        }
        for code, count in findings["by_reason"].items():
            label, decision = finding_labels.get(code, (code, "Excluido"))
            connection.execute(
                "INSERT INTO finding_population VALUES (?, ?, ?, ?, ?, ?)",
                (label, decision, count, findings["input"], count / findings["input"], period),
            )
        names = {"critical": "Critica", "high": "Alta", "medium": "Media", "low": "Baixa"}
        ranks = {"critical": 1, "high": 2, "medium": 3, "low": 4}
        for code, count in non_mitigated["by_severity"].items():
            connection.execute(
                "INSERT INTO severity_population VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    names[code], code.upper(), ranks[code], count, non_mitigated["total"],
                    count / non_mitigated["total"], non_mitigated["exploitable"], period,
                ),
            )
        impact = {
            "COLLECTION_AFTER_MONTH_CLOSE_GRACE": (
                "Coleta apos a tolerancia; fotografia historica pode refletir mudancas posteriores."
            ),
            "FIXED_STATE_NOT_COLLECTED": "Mitigadas no periodo ficam indisponiveis, nao zero.",
        }
        for issue in data["quality_issues"]:
            connection.execute(
                "INSERT INTO quality_issues VALUES (?, ?, ?)",
                (issue["severity"], issue["code"], impact.get(issue["code"], issue["message"])),
            )
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

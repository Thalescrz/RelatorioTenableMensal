-- A captura Cloud faz parte da coleta e pode terminar antes da publicação do
-- conjunto de relatórios. O vínculo por run_id continua único e é reconciliado
-- pela publicação posterior, mas não pode depender da ordem de criação do
-- registro editorial em report_runs.
alter table tenable_reports.cloud_report_snapshots
    drop constraint if exists cloud_report_snapshots_run_id_fkey;

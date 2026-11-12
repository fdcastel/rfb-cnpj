"""Extract all establishments with given CNAEs (primary or secondary) to Excel and CSV.

Requires the database built by `uv run build`.

Example:
    uv run extract-cnae 3250-7/09 3250-7/07 4774-1/00 --output ./output/opticas
"""

import argparse
import csv
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import xlsxwriter

from rfb_cnpj.build import CSV_SOURCES, DEFAULT_DATA_DIR, snapshot_month

DATABASE_FILE = "rfb-cnpj.duckdb"
DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"

SITUACAO_ATIVA = 2

UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "EX",
]

QUERY = """
WITH alvo AS (
    SELECT
        es.*,
        list_filter($cnaes::VARCHAR[], lambda c: c = es.cnae
                    OR list_contains(string_split(es.cnae_secundario, ','), c)) AS cnaes_alvo
    FROM estabelecimento AS es
    WHERE
        ($todas_situacoes OR es.situacao_cadastral = $situacao_ativa)
        AND (
            list_contains($cnaes::VARCHAR[], es.cnae)
            OR (NOT $somente_principal
                AND list_has_any(string_split(es.cnae_secundario, ','), $cnaes::VARCHAR[]))
        )
)
SELECT
    es.cnpj,
    em.razao_social,
    es.nome_fantasia,
    CASE WHEN es.matriz THEN 'Matriz' ELSE 'Filial' END AS matriz_filial,
    CASE es.situacao_cadastral
        WHEN 1 THEN 'Nula' WHEN 2 THEN 'Ativa' WHEN 3 THEN 'Suspensa'
        WHEN 4 THEN 'Inapta' WHEN 8 THEN 'Baixada' ELSE '?'
    END AS situacao_cadastral,
    es.data_situacao_cadastral,
    es.data_inicio_atividades,
    es.cnaes_alvo,
    es.cnae AS cnae_principal,
    cp.descricao AS cnae_principal_descricao,
    es.cnae_secundario AS cnaes_secundarios,
    nj.descricao AS natureza_juridica,
    CASE em.porte_empresa
        WHEN 0 THEN 'Não informado' WHEN 1 THEN 'Micro empresa'
        WHEN 3 THEN 'Empresa de pequeno porte' WHEN 5 THEN 'Demais' ELSE NULL
    END AS porte_empresa,
    em.capital_social,
    COALESCE(s.opcao_simples, false) AS opcao_simples,
    COALESCE(s.opcao_mei, false) AS opcao_mei,
    rt.tributacao AS regime_tributacao,
    rt.ano AS regime_tributacao_ano,
    concat_ws(' ', es.tipo_logradouro, es.logradouro) AS logradouro,
    es.numero,
    es.complemento,
    es.bairro,
    es.cep,
    mun.descricao AS municipio,
    es.uf,
    CASE WHEN es.telefone1 IS NOT NULL THEN concat_ws(' ', '(' || LTRIM(es.ddd1, '0 ') || ')', es.telefone1) END AS telefone1,
    CASE WHEN es.telefone2 IS NOT NULL THEN concat_ws(' ', '(' || LTRIM(es.ddd2, '0 ') || ')', es.telefone2) END AS telefone2,
    es.correio_eletronico AS email
FROM
    alvo AS es
    JOIN empresa AS em ON em.cnpj_base = es.cnpj_base
    LEFT JOIN simples AS s ON s.cnpj_base = es.cnpj_base
    LEFT JOIN cnae AS cp ON cp.codigo = es.cnae
    LEFT JOIN natureza_juridica AS nj ON nj.codigo = em.natureza_juridica
    LEFT JOIN municipio AS mun ON mun.codigo = es.municipio
    LEFT JOIN regime_tributacao_atual AS rt ON rt.cnpj = es.cnpj
ORDER BY
    es.uf, mun.descricao, es.cnpj
"""


def normalize_cnae(value: str) -> str:
    """'3250-7/09' | '3250709' -> '3250709'."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 7:
        raise ValueError(f"Invalid CNAE: {value!r} (expected 7 digits, e.g. 4774-1/00).")
    return digits


def format_cnae(code: str) -> str:
    """'3250709' -> '3250-7/09'."""
    return f"{code[:4]}-{code[4]}/{code[5:]}"


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    database = data_dir / DATABASE_FILE
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}. Run `uv run build` first.")
    con = duckdb.connect(str(database), read_only=True)
    # Views created by a manual `dbt run` (from ./dbt) use paths relative to that folder.
    con.execute(f"SET file_search_path = '{DBT_DIR.as_posix()}'")
    return con


def query(
    con: duckdb.DuckDBPyConnection,
    cnaes: list[str],
    todas_situacoes: bool = False,
    somente_principal: bool = False,
) -> tuple[list[str], list[tuple]]:
    result = con.execute(
        QUERY,
        {
            "cnaes": cnaes,
            "todas_situacoes": todas_situacoes,
            "somente_principal": somente_principal,
            "situacao_ativa": SITUACAO_ATIVA,
        },
    )
    columns = [d[0] for d in result.description]
    return columns, result.fetchall()


def cnae_descriptions(con: duckdb.DuckDBPyConnection, cnaes: list[str]) -> dict[str, str]:
    rows = con.execute(
        "SELECT codigo, descricao FROM cnae WHERE list_contains($c::VARCHAR[], codigo)", {"c": cnaes}
    ).fetchall()
    return dict(rows)


def _csv_value(v):
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, list):
        return ", ".join(format_cnae(c) for c in v)
    if isinstance(v, Decimal):
        return f"{v:.2f}".replace(".", ",")  # pt-BR, like the RFB files
    return "" if v is None else v


def write_csv(path: Path, columns: list[str], rows: list[tuple]) -> None:
    """UTF-8 with BOM and ';' delimiter: opens correctly in Excel pt-BR."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_csv_value(v) for v in row])


def write_xlsx(
    path: Path,
    columns: list[str],
    rows: list[tuple],
    cnaes: list[str],
    descriptions: dict[str, str],
    subtitle: str,
) -> dict[str, dict[str, int]]:
    """One sheet per CNAE plus a summary by UF. Returns the summary counts."""
    i_alvo = columns.index("cnaes_alvo")
    i_principal = columns.index("cnae_principal")
    i_uf = columns.index("uf")

    sheet_columns = columns[:i_alvo] + ["tipo_cnae"] + columns[i_alvo + 1 :]

    wb = xlsxwriter.Workbook(path, {"remove_timezone": True})
    bold = wb.add_format({"bold": True})
    title = wb.add_format({"bold": True, "font_size": 14})
    header = wb.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1})
    date_fmt = wb.add_format({"num_format": "dd/mm/yyyy"})
    money_fmt = wb.add_format({"num_format": "#,##0.00"})

    summary: dict[str, dict[str, int]] = {}
    summary_ws = wb.add_worksheet("Resumo")

    for cnae in cnaes:
        ws = wb.add_worksheet(format_cnae(cnae).replace("/", "-"))
        ws.write_row(0, 0, sheet_columns, header)
        counts = {"Principal": 0, "Secundário": 0}
        by_uf: dict[str, int] = {}
        r = 0
        for row in rows:
            if cnae not in row[i_alvo]:
                continue
            tipo = "Principal" if row[i_principal] == cnae else "Secundário"
            counts[tipo] += 1
            by_uf[row[i_uf]] = by_uf.get(row[i_uf], 0) + 1
            r += 1
            values = list(row[:i_alvo]) + [tipo] + list(row[i_alvo + 1 :])
            for c, v in enumerate(values):
                if v is None:
                    continue
                if isinstance(v, bool):
                    ws.write_string(r, c, "Sim" if v else "Não")
                elif isinstance(v, date):
                    ws.write_datetime(r, c, v, date_fmt)
                elif sheet_columns[c] == "capital_social":
                    ws.write_number(r, c, float(v), money_fmt)
                elif isinstance(v, (int, float)):
                    ws.write_number(r, c, v)
                else:
                    ws.write_string(r, c, str(v))
        ws.autofilter(0, 0, max(r, 1), len(sheet_columns) - 1)
        ws.freeze_panes(1, 1)
        ws.set_column(0, 0, 20)
        ws.set_column(1, 2, 40)
        ws.set_column(3, len(sheet_columns) - 1, 16)
        summary[cnae] = {**counts, "Total": r, **{f"UF:{k}": v for k, v in by_uf.items()}}

    # Summary sheet
    summary_ws.write(0, 0, "Estabelecimentos por CNAE", title)
    summary_ws.write(1, 0, subtitle)
    summary_ws.write_row(3, 0, ["CNAE", "Descrição", "Principal", "Secundário", "Total"], header)
    for i, cnae in enumerate(cnaes, start=4):
        s = summary[cnae]
        summary_ws.write_row(
            i, 0, [format_cnae(cnae), descriptions.get(cnae, "?"), s["Principal"], s["Secundário"], s["Total"]]
        )
    unique_row = 4 + len(cnaes)
    summary_ws.write(unique_row, 0, "Estabelecimentos distintos", bold)
    summary_ws.write(unique_row, 4, len(rows), bold)

    uf_row = unique_row + 2
    summary_ws.write_row(uf_row, 0, ["UF"] + [format_cnae(c) for c in cnaes] + ["Distintos"], header)
    distinct_by_uf: dict[str, int] = {}
    for row in rows:
        distinct_by_uf[row[i_uf]] = distinct_by_uf.get(row[i_uf], 0) + 1
    ufs = [u for u in UFS if u in distinct_by_uf] + sorted(u for u in distinct_by_uf if u not in UFS)
    for j, uf in enumerate(ufs, start=uf_row + 1):
        summary_ws.write_row(
            j, 0, [uf] + [summary[c].get(f"UF:{uf}", 0) for c in cnaes] + [distinct_by_uf[uf]]
        )
    summary_ws.set_column(0, 0, 26)
    summary_ws.set_column(1, 1, 45)
    summary_ws.set_column(2, 1 + len(cnaes) + 1, 12)
    summary_ws.activate()
    wb.close()
    return summary


def extract(
    data_dir: Path,
    cnaes: list[str],
    output: Path,
    todas_situacoes: bool = False,
    somente_principal: bool = False,
) -> tuple[Path, Path, int]:
    cnaes = [normalize_cnae(c) for c in cnaes]
    output.parent.mkdir(parents=True, exist_ok=True)

    with connect(data_dir) as con:
        descriptions = cnae_descriptions(con, cnaes)
        unknown = [c for c in cnaes if c not in descriptions]
        if unknown:
            raise SystemExit(f"CNAE(s) not found in the CNAE table: {', '.join(map(format_cnae, unknown))}")
        columns, rows = query(con, cnaes, todas_situacoes, somente_principal)

    snapshot = snapshot_month(data_dir / CSV_SOURCES) or "?"
    subtitle = (
        f"Base CNPJ RFB {snapshot} — "
        f"{'todas as situações' if todas_situacoes else 'somente ativas'}, "
        f"{'somente CNAE principal' if somente_principal else 'CNAE principal ou secundário'}"
    )

    csv_path = output.with_suffix(".csv")
    xlsx_path = output.with_suffix(".xlsx")
    write_csv(csv_path, columns, rows)
    write_xlsx(xlsx_path, columns, rows, cnaes, descriptions, subtitle)
    return xlsx_path, csv_path, len(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cnaes", nargs="+", help="CNAE codes (e.g. 4774-1/00 or 4774100).")
    parser.add_argument("--output", type=Path, default=Path("./output/cnae"), help="Output file (without extension).")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Default: ./data")
    parser.add_argument("--todas-situacoes", action="store_true", help="Include non-active establishments.")
    parser.add_argument("--somente-principal", action="store_true", help="Match the primary CNAE only.")
    args = parser.parse_args(argv)

    xlsx_path, csv_path, count = extract(
        args.data_dir, args.cnaes, args.output, args.todas_situacoes, args.somente_principal
    )
    print(f"{count} establishments.\n  {xlsx_path}\n  {csv_path}")


if __name__ == "__main__":
    main()

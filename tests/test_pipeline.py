"""End-to-end: real RFB sample files -> dbt (DuckDB) -> extract-cnae.

Expected values are computed independently from the raw fixture files (csv module),
then compared with what the pipeline produced.
"""

import csv
import re
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import openpyxl
import pytest

from rfb_cnpj import build, extract

FIXTURES = Path(__file__).parent / "fixtures" / "csv"
TARGETS = ["3250709", "3250707", "4774100"]


def raw_rows(pattern: str, delimiter=";", encoding="latin-1", header=False) -> list[list[str]]:
    rows = []
    for f in sorted(FIXTURES.glob(pattern)):
        with open(f, encoding=encoding, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            if header:
                next(reader)
            rows += list(reader)
    return rows


def raw_date(s: str) -> date | None:
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None


def fmt_cnpj(base: str, ordem: str, dv: str) -> str:
    return f"{base[:2]}.{base[2:5]}.{base[5:8]}/{ordem}-{dv}"


ESTAB = raw_rows("*.ESTABELE")
EMPRESAS = {r[0]: r for r in raw_rows("*.EMPRECSV")}
SIMPLES = raw_rows("*.SIMPLES.CSV.*")
REGIME = raw_rows("Imunes*.csv", ",", "utf-8", True) + raw_rows("Lucro*.csv", ",", "utf-8", True)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    """Zip the (latin-1) fixtures as RFB publishes them, then extract + dbt like `build`."""
    data = tmp_path_factory.mktemp("data")
    zips = data / build.ZIP_SOURCES / "2026-10"
    zips.mkdir(parents=True)
    zip_files = []
    for f in sorted(FIXTURES.iterdir()):
        zip_files.append(zips / f"{f.name}.zip")
        with zipfile.ZipFile(zip_files[-1], "w", zipfile.ZIP_DEFLATED) as z:
            z.write(f, f.name)

    csv_path = data / build.CSV_SOURCES
    build.prepare_csv_folder(csv_path, "2026-10")
    assert len(build.extract_files(zip_files, csv_path)) == len(zip_files)
    (data / build.PARQUET_SOURCES).mkdir()

    build.run_dbt(data)
    return data


@pytest.fixture(scope="module")
def con(data_dir):
    c = extract.connect(data_dir)
    yield c
    c.close()


def test_fixture_covers_the_cases():
    assert len(ESTAB) >= 18
    assert any("\n" in field for r in ESTAB for field in r)  # embedded newline
    assert any('"' in r[1] for r in EMPRESAS.values())  # escaped quotes
    assert any(not (r[0] + r[1]).isdigit() for r in ESTAB)  # alphanumeric CNPJ
    assert {r[11] for r in ESTAB} >= set(TARGETS) | {"0111301"}
    assert any(r[10][4:6] != "01" for r in ESTAB)
    assert any(int(r[5]) != 2 for r in ESTAB)
    assert any(r[11] not in TARGETS and set(TARGETS) & set(r[12].split(",")) for r in ESTAB)


def test_fixture_contacts_are_masked():
    """The repository is public: fixture e-mails/phones must be fakes (see scripts/make_fixtures.py)."""
    for r in ESTAB:
        assert r[27] == "" or re.fullmatch(r"CONTATO\d+@EXEMPLO\.COM\.BR", r[27]), r[27]
        for phone in (r[22], r[24], r[26]):
            assert phone == "" or phone.startswith("3"), phone


def test_all_rows_loaded(con):
    assert con.execute("SELECT count(*) FROM estabelecimento").fetchall()[0][0] == len(ESTAB)
    assert con.execute("SELECT count(*) FROM empresa").fetchall()[0][0] == len(EMPRESAS)
    assert con.execute("SELECT count(*) FROM regime_tributacao").fetchall()[0][0] == len(REGIME)


def test_estabelecimento_dates_and_cnpj(con):
    got = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT cnpj, data_inicio_atividades, data_situacao_cadastral, situacao_cadastral, matriz, nome_fantasia"
            " FROM estabelecimento"
        ).fetchall()
    }
    for r in ESTAB:
        cnpj = fmt_cnpj(r[0], r[1], r[2])
        assert cnpj in got, cnpj
        inicio, situacao_data, situacao, matriz, nome_fantasia = got[cnpj]
        assert nome_fantasia == (r[4] or None)
        assert inicio == raw_date(r[10]), cnpj
        assert situacao_data == raw_date(r[6]), cnpj
        assert situacao == int(r[5])
        assert matriz == (r[3] == "1")


def test_simples_dates(con):
    got = {r[0]: r[1:] for r in con.execute(
        "SELECT cnpj_base, opcao_simples, data_opcao_simples, data_exclusao_simples, opcao_mei FROM simples"
    ).fetchall()}
    assert len(got) == len(SIMPLES)
    for r in SIMPLES:
        opcao, data_opcao, data_exclusao, mei = got[r[0]]
        assert opcao == (r[1] == "S")
        assert data_opcao == raw_date(r[2])
        assert data_exclusao == raw_date(r[3])
        assert mei == (r[4] == "S")


def test_lookup_tables_decoded(con):
    cnae = dict(con.execute("SELECT codigo, descricao FROM cnae").fetchall())
    assert cnae["3250709"] == "Serviço de laboratório óptico"
    assert cnae["3250707"] == "Fabricação de artigos ópticos"
    assert cnae["4774100"] == "Comércio varejista de artigos de óptica"
    assert cnae["0111301"] == "Cultivo de arroz"  # leading zero kept

    # motivo is built from the MOTICSV file (v1 loaded the CNAE table here).
    motivos = dict(raw_rows("*.MOTICSV"))
    got = {str(k).zfill(2): v for k, v in con.execute("SELECT codigo, descricao FROM motivo").fetchall()}
    assert len(got) == len(motivos)
    assert {k.zfill(2): v for k, v in motivos.items()} == got


def test_every_primary_cnae_resolves(con):
    missing = con.execute(
        "SELECT es.cnae FROM estabelecimento es LEFT JOIN cnae c ON c.codigo = es.cnae WHERE c.codigo IS NULL"
    ).fetchall()
    assert missing == []


def test_empresa_names(con):
    got = dict(con.execute("SELECT cnpj_base, razao_social FROM empresa").fetchall())
    for base, r in EMPRESAS.items():
        assert got[base] == r[1].lstrip("0123456789.").strip()


def test_porte_empresa(con):
    expected = {"00": "Não informado", "01": "Micro empresa", "03": "Empresa de pequeno porte", "05": "Demais"}
    got = dict(con.execute("SELECT cnpj_base, porte_empresa_str FROM v_empresa").fetchall())
    for base, r in EMPRESAS.items():
        assert got[base] == expected[r[5]], base


def test_regime_tributacao(con):
    latest: dict[str, tuple[int, str]] = {}
    for ano, cnpj, scp, trib, _ in REGIME:
        if scp == "0" and (cnpj not in latest or int(ano) > latest[cnpj][0]):
            latest[cnpj] = (int(ano), trib.replace("LUCRO ", "").replace(" DO IRPJ", ""))

    got = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT cnpj, regime_tributacao_ano, regime_tributacao FROM v_estabelecimento"
    ).fetchall()}
    with_regime = 0
    for r in ESTAB:
        cnpj = fmt_cnpj(r[0], r[1], r[2])
        if cnpj in latest:
            with_regime += 1
            assert got[cnpj] == latest[cnpj], cnpj
        else:
            assert got[cnpj] == (None, None)
    assert with_regime >= 3


def expected_matches(cnaes, todas_situacoes=False, somente_principal=False) -> set[str]:
    out = set()
    for r in ESTAB:
        if not todas_situacoes and int(r[5]) != 2:
            continue
        primary = r[11] in cnaes
        secondary = bool(set(cnaes) & set(r[12].split(",")))
        if primary or (secondary and not somente_principal):
            out.add(fmt_cnpj(r[0], r[1], r[2]))
    return out


def read_csv_output(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_extract_cnae(data_dir, tmp_path):
    xlsx, csv_path, count = extract.extract(
        data_dir, ["3250-7/09", "3250-7/07", "4774-1/00"], tmp_path / "opticas"
    )
    expected = expected_matches(TARGETS)
    assert count == len(expected)

    rows = read_csv_output(csv_path)
    assert {r["cnpj"] for r in rows} == expected
    assert all(r["situacao_cadastral"] == "Ativa" for r in rows)
    for r in rows:
        raw = next(x for x in ESTAB if fmt_cnpj(x[0], x[1], x[2]) == r["cnpj"])
        assert r["razao_social"] == EMPRESAS[raw[0]][1].lstrip("0123456789.").strip()
        assert r["data_inicio_atividades"] == raw_date(raw[10]).strftime("%d/%m/%Y")
        assert r["uf"] == raw[19]
        capital = EMPRESAS[raw[0]][4]
        assert r["capital_social"] == ("" if capital == "0,00" else capital)

    wb = openpyxl.load_workbook(xlsx, read_only=True)
    assert wb.sheetnames == ["Resumo", "3250-7-09", "3250-7-07", "4774-1-00"]
    for code in TARGETS:
        ws = wb[extract.format_cnae(code).replace("/", "-")]
        values = list(ws.values)
        header, data = values[0], values[1:]
        i_cnpj, i_tipo = header.index("cnpj"), header.index("tipo_cnae")
        sheet = {row[i_cnpj]: row[i_tipo] for row in data}
        assert set(sheet) == expected_matches([code])
        for cnpj, tipo in sheet.items():
            raw = next(x for x in ESTAB if fmt_cnpj(x[0], x[1], x[2]) == cnpj)
            assert tipo == ("Principal" if raw[11] == code else "Secundário")

    summary = list(wb["Resumo"].values)
    distinct = next(r for r in summary if r and r[0] == "Estabelecimentos distintos")
    assert distinct[4] == len(expected)


def test_extract_options(data_dir, tmp_path):
    _, csv_all, _ = extract.extract(data_dir, TARGETS, tmp_path / "all", todas_situacoes=True)
    assert {r["cnpj"] for r in read_csv_output(csv_all)} == expected_matches(TARGETS, todas_situacoes=True)
    assert expected_matches(TARGETS, todas_situacoes=True) > expected_matches(TARGETS)

    _, csv_pri, _ = extract.extract(data_dir, TARGETS, tmp_path / "pri", somente_principal=True)
    assert {r["cnpj"] for r in read_csv_output(csv_pri)} == expected_matches(TARGETS, somente_principal=True)
    assert expected_matches(TARGETS, somente_principal=True) < expected_matches(TARGETS)


def test_gold_views_usable_from_any_folder(data_dir, tmp_path, monkeypatch):
    """Views built by `build` use absolute paths: no file_search_path needed."""
    monkeypatch.chdir(tmp_path)
    with duckdb.connect(str(data_dir / extract.DATABASE_FILE), read_only=True) as c:
        n = c.execute("SELECT count(*) FROM v_estabelecimento JOIN v_empresa USING (cnpj_base)").fetchall()[0][0]
    assert n == len(ESTAB)


@pytest.mark.parametrize(
    "value, expected", [("3250-7/09", "3250709"), ("4774100", "4774100"), (" 4774-1/00 ", "4774100")]
)
def test_normalize_cnae(value, expected):
    assert extract.normalize_cnae(value) == expected


def test_format_cnae():
    assert extract.format_cnae("3250709") == "3250-7/09"


def test_normalize_cnae_rejects_invalid():
    with pytest.raises(ValueError):
        extract.normalize_cnae("4774-1")

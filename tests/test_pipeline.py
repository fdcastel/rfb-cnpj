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
MEI_BASES = {r[0] for r in SIMPLES if r[4] == "S"}

JOALHERIA, RELOJOARIA, VESTUARIO = "4783101", "4783102", "4781400"
SEGMENTOS_FILE = Path(__file__).parents[1] / "segmentos" / "oticas-joalherias.toml"


def is_mei(base: str) -> bool:
    return base in MEI_BASES


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
    # segments file cases
    assert any(r[11] == JOALHERIA and set(TARGETS) & set(r[12].split(",")) for r in ESTAB)
    assert any(r[11] == JOALHERIA and not set(TARGETS) & set(r[12].split(",")) for r in ESTAB)
    assert any(r[11] == RELOJOARIA for r in ESTAB)
    assert any(r[11] == VESTUARIO and "4774100" in r[12].split(",") for r in ESTAB)
    assert {r[11] for r in ESTAB if is_mei(r[0])} >= {"4774100", JOALHERIA}


def test_fixture_personal_data_is_masked():
    """The repository is public: contacts and sole proprietors' data are fakes (scripts/make_fixtures.py)."""
    for r in ESTAB:
        assert r[27] == "" or re.fullmatch(r"CONTATO\d+@EXEMPLO\.COM\.BR", r[27]), r[27]
        for phone in (r[22], r[24], r[26]):
            assert phone == "" or phone.startswith("3"), phone
        if EMPRESAS[r[0]][2] == "2135":
            assert r[15] == "100" and r[16] == ""
            assert r[4] == "" or r[4].startswith("LOJA ")
    for r in EMPRESAS.values():
        if r[2] == "2135":
            assert r[1].startswith("EMPRESARIO INDIVIDUAL "), r[1]


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


#
# Segments file (segmentos/oticas-joalherias.toml)
#


def test_segmentos_file_matches_the_business_decision():
    """Decided 2026-11-11: optical stores, optical labs, jewelers (+ watch stores); primary CNAE only; MEIs apart."""
    segmentos, options = extract.load_segmentos(SEGMENTOS_FILE)
    assert {s.nome: set(s.cnaes) for s in segmentos} == {
        "Lojas óticas": {"4774100"},
        "Laboratórios óticos": {"3250709"},
        "Fabricação óptica": {"3250707"},
        "Joalherias": {JOALHERIA},
        "Relojoarias": {RELOJOARIA},
    }
    assert options == {"somente_principal": True, "separar_mei": True}


def test_extract_segmentos_cli(data_dir, tmp_path):
    output = tmp_path / "oticas"
    extract.main(["--segmentos", str(SEGMENTOS_FILE), "--output", str(output), "--data-dir", str(data_dir)])
    segmentos, _ = extract.load_segmentos(SEGMENTOS_FILE)
    all_codes = {c for s in segmentos for c in s.cnaes}

    def expected(codes: set[str], mei: bool | None = None) -> set[str]:
        return {
            fmt_cnpj(r[0], r[1], r[2])
            for r in ESTAB
            if int(r[5]) == 2 and r[11] in codes and (mei is None or is_mei(r[0]) == mei)
        }

    # CSV: one row per establishment, with its segment.
    rows = read_csv_output(output.with_suffix(".csv"))
    assert {r["cnpj"] for r in rows} == expected(all_codes)
    assert not any(r["cnae_principal"] == VESTUARIO for r in rows)  # clothing only as secondary: out
    by_code = {c: s.nome for s in segmentos for c in s.cnaes}
    for r in rows:
        assert r["segmento"] == by_code[r["cnae_principal"]]
        raw = next(x for x in ESTAB if fmt_cnpj(x[0], x[1], x[2]) == r["cnpj"])
        matched = [c for c in by_code if c == raw[11] or c in raw[12].split(",")]
        assert sorted(r["cnaes_alvo"].split(", ")) == sorted(extract.format_cnae(c) for c in matched)

    # A jeweler that also sells optics shows it in cnaes_alvo.
    assert any(r["cnae_principal"] == JOALHERIA and "4774-1/00" in r["cnaes_alvo"] for r in rows) or not any(
        x[11] == JOALHERIA and "4774100" in x[12].split(",") and int(x[5]) == 2 for x in ESTAB
    )

    # Workbook: Resumo + (segment, segment (MEI)) for each segment.
    wb = openpyxl.load_workbook(output.with_suffix(".xlsx"), read_only=True)
    expected_sheets = ["Resumo"] + [n for s in segmentos for n in (s.nome, f"{s.nome} (MEI)")]
    assert wb.sheetnames == expected_sheets
    for seg in segmentos:
        for name, mei in ((seg.nome, False), (f"{seg.nome} (MEI)", True)):
            values = list(wb[name].values)
            header, data = values[0], values[1:]
            i_cnpj, i_tipo, i_mei = header.index("cnpj"), header.index("tipo_cnae"), header.index("opcao_mei")
            assert {row[i_cnpj] for row in data} == expected(set(seg.cnaes), mei), name
            assert all(row[i_tipo] == "Principal" for row in data)
            assert all(row[i_mei] == ("Sim" if mei else "Não") for row in data)
    assert any(list(wb[f"{s.nome} (MEI)"].values)[1:] for s in segmentos)  # MEI sheets are exercised

    summary = list(wb["Resumo"].values)
    distinct = next(r for r in summary if r and r[0] == "Estabelecimentos distintos")
    assert distinct[4:7] == (
        len(expected(all_codes)),
        len(expected(all_codes, mei=False)),
        len(expected(all_codes, mei=True)),
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Lojas óticas (MEI)", "Lojas óticas (MEI)"),
        ("3250-7/09", "3250-7-09"),
        (r"a[b]:c*d?e\f", "a-b--c-d-e-f"),
        ("x" * 40, "x" * 31),
    ],
)
def test_sheet_name(name, expected):
    assert extract.sheet_name(name, set()) == expected


def test_sheet_name_unique():
    used: set[str] = set()
    names = [extract.sheet_name("y" * 40, used) for _ in range(3)]
    assert names == ["y" * 31, "y" * 29 + " 2", "y" * 29 + " 3"]

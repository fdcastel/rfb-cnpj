"""Build tests/fixtures/csv from a full extraction (data/1-csv_sources).

The fixtures are *real* rows of the RFB public files, written back in their original
layout and encoding (latin-1, ';', all fields quoted, '""' escapes) -- the tests zip them
and run them through the same extraction as `build`. They are chosen to exercise:

- target CNAEs (optical sector) as primary and as secondary activity, active and not active;
- a CNAE with a leading zero (0111301);
- CSV quirks: embedded newlines and escaped quotes ("");
- an alphanumeric CNPJ (issued since 2026-07);
- dates in months other than January (catches the '%M' bug of v1);
- companies with tax-regime and Simples rows.

Privacy (the repository is public): e-mails and phone numbers are replaced by
deterministic fakes (enforced by tests/test_pipeline.py::test_fixture_contacts_are_masked).

Usage: uv run python scripts/make_fixtures.py [--data-dir ./data]
"""

import argparse
import shutil
from pathlib import Path
from zipfile import ZipFile

import duckdb

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "csv"
TARGETS = ["3250709", "3250707", "4774100"]
LEADING_ZERO_CNAE = "0111301"
PER_CASE = 2

EST_COLS = 30
EMP_COLS = 7
SIM_COLS = 7


def varchar_cols(n: int) -> str:
    return "{" + ", ".join(f"'c{i}': 'VARCHAR'" for i in range(n)) + "}"


def read(pattern: Path, n: int) -> str:
    return (
        f"read_csv('{pattern.as_posix()}', header=false, delim=';', "
        f"quote='\"', escape='\"', columns={varchar_cols(n)}, filename=true)"
    )


def write_latin1(con, sql: str, target: Path) -> int:
    tmp = target.with_suffix(target.suffix + ".utf8")
    con.execute(f"COPY ({sql}) TO '{tmp.as_posix()}' (HEADER false, DELIMITER ';', FORCE_QUOTE *)")
    text = tmp.read_text(encoding="utf-8")
    tmp.unlink()
    target.write_bytes(text.replace("\r\n", "\n").encode("latin-1"))
    return text.count("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    args = parser.parse_args()
    src = args.data_dir / "1-csv_sources"

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    con = duckdb.connect()

    con.execute(f"CREATE TABLE emp AS SELECT * EXCLUDE filename FROM {read(src / '*.EMPRECSV', EMP_COLS)}")
    con.execute(f"""
        CREATE TABLE est AS
        SELECT e.* EXCLUDE filename, string_split(e.c12, ',') AS sec
        FROM {read(src / '*.ESTABELE', EST_COLS)} AS e
        JOIN emp ON emp.c0 = e.c0
        WHERE emp.c2 <> '2135'
    """)
    con.execute("""
        CREATE TABLE rt AS
        SELECT * FROM read_csv(?, header=true, all_varchar=true, filename=true)
    """, [[(src / "Imunes e Isentas.csv").as_posix(), (src / "Lucro*.csv").as_posix()]])

    # Deterministic sample: order by a hash of the CNPJ.
    cases = []
    for c in TARGETS:
        # active, primary, with a known tax regime, start date not in January
        cases.append(f"""(SELECT * FROM est
            WHERE c5 = '02' AND c11 = '{c}' AND substr(c10, 5, 2) <> '01'
              AND EXISTS (SELECT 1 FROM rt WHERE rt.cnpj = printf('%s.%s.%s/%s-%s',
                    substr(est.c0,1,2), substr(est.c0,3,3), substr(est.c0,6,3), est.c1, est.c2))
            ORDER BY hash(c0 || c1) LIMIT {PER_CASE})""")
        # active, secondary only
        cases.append(f"""(SELECT * FROM est
            WHERE c5 = '02' AND c11 <> '{c}' AND list_contains(sec, '{c}')
            ORDER BY hash(c0 || c1) LIMIT {PER_CASE})""")
        # not active (baixada), primary
        cases.append(f"""(SELECT * FROM est
            WHERE c5 = '08' AND c11 = '{c}' ORDER BY hash(c0 || c1) LIMIT 1)""")
    cases.append(f"""(SELECT * FROM est
        WHERE c5 = '02' AND c11 = '{LEADING_ZERO_CNAE}' ORDER BY hash(c0 || c1) LIMIT 1)""")
    # CSV quirks found in the 2026-10 files: a field with an embedded newline and a
    # company name with escaped quotes ('METALURGICA ""WM"" LTDA').
    cases.append("""(SELECT * FROM est WHERE c4 LIKE '%' || chr(10) || '%' ORDER BY hash(c0 || c1) LIMIT 1)""")
    cases.append("""(SELECT * FROM est WHERE c0 IN (SELECT c0 FROM emp WHERE c1 LIKE '%"%' AND c2 <> '2135')
        ORDER BY hash(c0 || c1) LIMIT 1)""")
    # Alphanumeric CNPJ (issued since 2026-07).
    cases.append("""(SELECT * FROM est WHERE regexp_matches(c0 || c1, '[A-Z]') ORDER BY hash(c0 || c1) LIMIT 1)""")
    con.execute("CREATE TABLE sample AS " + " UNION ".join(cases))

    # Privacy: the repository is public. Contact data is replaced by deterministic fakes
    # with the same shape (phones keep their length, e-mails stay valid addresses).
    # c21..c26 = ddd1, telefone1, ddd2, telefone2, ddd_fax, fax; c27 = e-mail.
    for col in ["c22", "c24", "c26"]:
        con.execute(f"""
            UPDATE sample SET {col} = substr(
                '3' || lpad(CAST(hash(c0 || c1 || '{col}') % 100000000 AS VARCHAR), 8, '0'), 1, length({col}))
            WHERE {col} <> ''
        """)
    con.execute("""
        UPDATE sample SET c27 = 'CONTATO' || CAST(hash(c0 || c1) % 100000 AS VARCHAR) || '@EXEMPLO.COM.BR'
        WHERE c27 <> ''
    """)

    est_cols = ", ".join(f"c{i}" for i in range(EST_COLS))
    n = write_latin1(con, f"SELECT {est_cols} FROM sample ORDER BY c0, c1", OUT / "K3241.K03200Y0.D61010.ESTABELE")
    print(f"ESTABELE: {n}")
    n = write_latin1(con, "SELECT * FROM emp WHERE c0 IN (SELECT c0 FROM sample) ORDER BY c0",
                     OUT / "K3241.K03200Y0.D61010.EMPRECSV")
    print(f"EMPRECSV: {n}")
    n = write_latin1(con, f"""SELECT * EXCLUDE filename FROM {read(src / '*.SIMPLES.CSV.*', SIM_COLS)}
                     WHERE c0 IN (SELECT c0 FROM sample) ORDER BY c0""",
                     OUT / "F.K03200$W.SIMPLES.CSV.D61010")
    print(f"SIMPLES: {n}")

    # Tax regime: rows of the sampled CNPJs + the first rows of each file (every glob must match).
    con.execute("""
        CREATE TABLE rt_sample AS
        SELECT * FROM rt WHERE cnpj IN (
            SELECT printf('%s.%s.%s/%s-%s', substr(c0,1,2), substr(c0,3,3), substr(c0,6,3), c1, c2) FROM sample)
        UNION
        SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY filename ORDER BY cnpj) AS rn FROM rt)
        WHERE rn <= 3
    """)
    for (filename,) in con.execute("SELECT DISTINCT filename FROM rt_sample ORDER BY 1").fetchall():
        name = Path(filename).name
        target = OUT / name
        con.execute(
            f"COPY (SELECT * EXCLUDE filename FROM rt_sample WHERE filename = ? ORDER BY cnpj) "
            f"TO '{target.as_posix()}' (HEADER true, DELIMITER ',')",
            [filename],
        )
        print(f"{name}: {len(target.read_text().splitlines()) - 1}")

    # Small lookup tables: copied byte-for-byte from the published zips.
    zips = args.data_dir / "0-zip_sources"
    for name in ["Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes"]:
        [zip_file] = zips.glob(f"*/{name}.zip")
        with ZipFile(zip_file) as z:
            [member] = z.namelist()
            (OUT / member).write_bytes(z.read(member))
        print(f"{member}: copied")

if __name__ == "__main__":
    main()

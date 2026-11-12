# rfb-cnpj — maintenance notes

Pipeline: `src/rfb_cnpj/build.py` (download + extract + dbt) → `dbt/` (DuckDB, bronze/silver/gold) → `src/rfb_cnpj/extract.py` (CNAE extract to xlsx/csv). User docs: README.md (pt-BR).

- Run tests: `uv run pytest -m "not network"` (offline, runs real dbt on `tests/fixtures/csv`) and `uv run pytest -m network` (live RFB server).
- Regenerate test fixtures from a full build: `uv run python scripts/make_fixtures.py` (read it first: fixture rows are real public records, chosen to exercise specific cases).
- Use `uv` for everything Python; never a bare `python`.

## Lessons (date — source)

- 2026-11-11 — v2.0 upgrade: RFB moved files to a Nextcloud share (`arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK`); old `/dados/cnpj/` URLs return 404. List with WebDAV `PROPFIND` on `/public.php/dav/files/<token>/`, download with plain GET. Covered by `tests/test_build.py` (network).
- 2026-11-11 — v2.0 upgrade: the server stalls concurrent transfers mid-stream; downloads use a 60 s read timeout + HTTP Range resume (server answers 206). Covered by `test_download_and_resume`.
- 2026-11-11 — v2.0 upgrade: tax-regime files moved to `Dados/Obrigacoes_Acessorias/ECF/entidades-*.zip` and now share one layout (header, `,`, formatted CNPJ).
- 2026-11-11 — v2.0 upgrade: DuckDB `%M` is *minutes*; RFB dates are `%Y%m%d`. v1 loaded every date in January. Dates are parsed with `TRY_STRPTIME` (macro `rfb_date`). Covered by `tests/test_pipeline.py`.
- 2026-11-11 — v2.0 upgrade: `porte_empresa` codes are 00/01/03/05 (see https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), not 1/2/4/5.
- 2026-11-11 — v2.0 upgrade: CNPJ base/ordem can be alphanumeric (since 2026-07); keep CNPJ parts as strings.
- 2026-11-11 — commit "CI: pin setup-uv": `astral-sh/setup-uv` publishes no moving major tag (`@v10` fails); pin the full version.
- 2026-11-11 — v2.0: the repository is public; fixture contacts and sole proprietors' data are masked (`test_fixture_personal_data_is_masked`).
- 2026-11-11 — v2.0 upgrade: DuckDB 2.0 removed `DuckDBPyRelation.fetchone()`; use `fetchall()` or the connection cursor API.
- 2026-11-12 — barbershop extract: RFB open data has no `nome_fantasia` for MEIs (228 of 13.8 M active MEIs) and their `razao_social` is owner name + CPF, so name filters (`filtro_nome`) cannot find MEIs. Barbershops share CNAE 9602-5/01 with hair/nail salons.

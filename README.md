# rfb-cnpj

Ferramentas para consulta da base nacional de CNPJs da RFB.

Dados disponíveis no [repositório de arquivos da Receita Federal](https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK) (SERPRO+):

- CNPJ: pasta [`Dados/Cadastros/CNPJ/<AAAA-MM>`](https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK?dir=/Dados/Cadastros/CNPJ) (um _snapshot_ por mês).
- Regime tributário: pasta [`Dados/Obrigacoes_Acessorias/ECF`](https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK?dir=/Dados/Obrigacoes_Acessorias/ECF) (arquivos `entidades-*.zip`).
- Layout dos arquivos: [cnpj-metadados.pdf](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf).

Mais informações: https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj

> Até 2025 os arquivos ficavam em `https://arquivos.receitafederal.gov.br/dados/cnpj/` (hoje retorna 404).
> O script de build lista e baixa os arquivos pela interface WebDAV pública do compartilhamento
> (`https://arquivos.receitafederal.gov.br/public.php/dav/files/gn672Ad4CF8N6TK/`).



# Requisitos

- UV: https://docs.astral.sh/uv/getting-started/installation/
- 40 GB de espaço livre em disco
- 32 GB de RAM (recomendável)

Não é necessário ter o Python instalado, apenas o UV (que fará o download do Python e de todas as bibliotecas necessárias).

Após a instalação do UV, feche o prompt de comando e abra um novo (para atualizar o PATH).



# Inicialização

Clone este projeto para uma pasta em seu computador.

A partir da pasta raiz do projeto, execute:

```bash
# Baixa o snapshot mais recente, extrai os arquivos e gera o banco DuckDB (dbt).
uv run build

# Opções:
uv run build --list            # lista os snapshots disponíveis
uv run build --month 2026-09   # usa um snapshot específico
uv run build --skip-dbt        # apenas baixa e extrai (camadas 0 e 1)
```

O processo inteiro leva em torno de 20 minutos (variando de acordo com seu computador e conexão de rede).

As etapas do build são idempotentes: arquivos já baixados/extraídos não são processados novamente, e downloads interrompidos são retomados de onde pararam.

Para rodar apenas o dbt manualmente:

```bash
cd ./dbt
uv run dbt run
```



# Extração por CNAE

Gera uma planilha Excel (uma aba por CNAE + resumo por UF) e um CSV com todos os estabelecimentos **ativos** que possuem os CNAEs informados como atividade **principal ou secundária**:

```bash
uv run extract-cnae 3250-7/09 3250-7/07 4774-1/00 --output ./output/opticas
```

Opções: `--todas-situacoes` (inclui baixadas, inaptas, etc.), `--somente-principal` (apenas CNAE principal).

O CSV usa `;` como separador e UTF-8 com BOM (abre diretamente no Excel em português).



# Visão geral

Todos os dados necessários (baixados ou gerados) ficam na pasta [`data/`](./data/).

Os arquivos de dados estão estruturados em _camadas_, cada uma dependente da anterior:

- Camada 0: Arquivos `.zip` disponibilizados pela RFB (`data/0-zip_sources/<AAAA-MM>/` e `data/0-zip_sources/regime_tributario/`).
- Camada 1: Arquivos `.csv` extraídos dos arquivos `.zip`, sem conversão (`latin-1`, como publicados) (_bronze_).
- Camada 2: Arquivos `.parquet` gerados a partir dos arquivos `.csv` (_silver_).
- Camada 3: _Views_ e consultas SQL sobre os arquivos `.parquet` (_gold_).

O script de build baixa os arquivos `.zip` da RFB, descompacta-os na pasta da camada _bronze_ e executa o [dbt](https://www.getdbt.com/), que cria um banco de dados [DuckDB](https://duckdb.org/) e gera os arquivos e views das demais camadas.

A pasta de dados pode ser alterada com `--data-dir` (ou, para o dbt, com a variável de ambiente `RFB_CNPJ_DATA_DIR`).

Ao final do processo, podem-se rodar [consultas SQL](./dbt/analyses/) diretamente sobre o banco DuckDB (`data/rfb-cnpj.duckdb`).



# Consultas

Para consultar o banco de dados DuckDB, uma boa opção é o [DBeaver](https://dbeaver.io/).

> Consulte as [instruções de configuração](https://duckdb.org/docs/guides/sql_editors/dbeaver.html) na documentação do DuckDB.

Alguns exemplos de consultas estão disponíveis na pasta [`./dbt/analyses`](./dbt/analyses/).



### Pasta raiz do projeto

Quando o banco é gerado por `uv run build`, as _views_ usam caminhos absolutos e nenhuma configuração é necessária.

Se o dbt foi executado manualmente (`cd dbt; uv run dbt run`), as _views_ usam caminhos relativos à pasta `./dbt`. Neste caso, após conectar-se ao banco de dados, execute:

```sql
-- Deve apontar para a pasta raiz do projeto dbt (local do arquivo `dbt_project.yml`).
SET file_search_path = 'C:/caminho/para/rfb-cnpj/dbt'
```

Para mais informações, consulte [esta discussão](https://github.com/dbeaver/dbeaver/issues/21671#issuecomment-2147389720).



# Desenvolvimento

```bash
uv run pytest -m "not network"   # testes offline (inclui o pipeline dbt completo sobre amostra real)
uv run pytest -m network         # testes contra o servidor da RFB
```

Os testes rodam no CI (GitHub Actions) em Windows e Linux. Veja [CLAUDE.md](./CLAUDE.md) para notas de manutenção.



# FAQ

P. Ao tentar executar uma consulta, estou recebendo o erro:

> `IO Error: No files found that match the pattern "../data/2-parquet_sources/empresa.parquet"`.

R. Você executou o dbt manualmente e não definiu a [pasta raiz do projeto](#pasta-raiz-do-projeto) na variável `file_search_path`.

# rfb-cnpj

Ferramentas para consulta da base nacional de CNPJs da RFB.

Dados disponíveis em https://arquivos.receitafederal.gov.br/dados/cnpj.

Mais informações: https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj



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
# Inicializa o ambiente Python
uv venv
.venv/Scripts/activate

# Faz o download dos dados iniciais.
uv run build

# Gera o banco de dados DuckDB, os arquivos .parquet e as views.
cd ./dbt
uv run dbt run
```

O processo inteiro deve levar em torno de 20 minutos (variando de acordo com seu computador e conexão de rede).

As funções do script de build são idempotentes. Etapas já executadas não serão realizadas novamente.



# Visão geral

Todos os dados necessários (baixados ou gerados) ficam na pasta [`data/`](./data/).

Os arquivos de dados estão estruturados em _camadas_, cada uma dependente da anterior:

- Camada 0: Arquivos `.zip` disponibilizados pela RFB.
- Camada 1: Arquivos `.csv` extraídos dos arquivos `.zip` e convertidos para `utf-8` (_bronze_).
- Camada 2: Arquivos `.parquet` gerados a partir dos arquivos `.csv` (_silver_).
- Camada 3: _Views_ e consultas SQL sobre os arquivos `.parquet` (_gold_).

O script de build inicialmente baixa os arquivos `.zip` da RFB e os descompacta na pasta da camada [_bronze_](./data/0-zip_sources/).

Após isso, o [dbt](https://www.getdbt.com/) cria um banco de dados [DuckDB](https://duckdb.org/) que acessará os dados `.csv` e gerará os arquivos e views das demais camadas.

Ao final do processo, podem-se rodar [consultas SQL](./dbt/analyses/) diretamente sobre o banco DuckDB.



# Consultas

Para consultar o banco de dados DuckDB, uma boa opção é o [DBeaver](https://dbeaver.io/).

> Consulte as [instruções de configuração](https://duckdb.org/docs/guides/sql_editors/dbeaver.html) na documentação do DuckDB.

Alguns exemplos de consultas estão disponíveis na pasta [`./dbt/analyses`](./dbt/analyses/).



### Pasta raiz do projeto
Após conectar-se ao banco de dados, execute o seguinte SQL:

```sql
-- Deve apontar para a pasta raiz do projeto dbt (local do arquivo `dbt_project.yml`).
SET file_search_path = '/tmp/rfb-cnpj/data/'
```

As _views_ do banco de dados utilizam caminhos relativos a essa pasta. Para mais informações, consulte [esta discussão](https://github.com/dbeaver/dbeaver/issues/21671#issuecomment-2147389720).



# FAQ

P. Ao tentar executar uma consulta, estou recebendo o erro:

> `IO Error: No files found that match the pattern "../data/2-parquet_sources/empresa.parquet"`.

R. Você não definiu a [pasta raiz do projeto](#pasta-raiz-do-projeto) na variável `file_search_path`.

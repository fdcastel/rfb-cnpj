-- csv_regime_tributacao.sql
-- Source: Dados/Obrigacoes_Acessorias/ECF/entidades-*.zip (since 2026 all files share this layout):
--   ano,cnpj,cnpj_da_scp,forma_de_tributacao,quantidade_de_escrituracoes
SELECT
    ano,
    cnpj,
    NULLIF(cnpj_scp, '0') AS cnpj_scp,
    tributacao,
    qtd
FROM
    read_csv([{{ csv_source('Imunes*.csv') }}, {{ csv_source('Lucro*.csv') }}],
             header = true,
             columns = {'ano': 'USMALLINT',
                        'cnpj': 'VARCHAR',
                        'cnpj_scp': 'VARCHAR',
                        'tributacao': 'VARCHAR',
                        'qtd': 'USMALLINT'},
             nullstr = '',
             delim = ',')

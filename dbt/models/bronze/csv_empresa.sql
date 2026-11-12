-- csv_empresa.sql
SELECT
    cnpj_base,
    -- MEI names are prefixed by the CNPJ base ('12.345.678 FULANO'): strip it.
    LTRIM(LTRIM(razao_social, '0123456789.')) AS razao_social,
    natureza_juridica,
    qualificacao_responsavel,
    NULLIF(capital_social, 0) AS capital_social,
    porte_empresa,
    ente_federativo_responsavel
FROM
    read_csv({{ csv_source('*.EMPRECSV') }},
             header = false,
             columns = {'cnpj_base': 'VARCHAR',
                        'razao_social': 'VARCHAR',
                        'natureza_juridica': 'USMALLINT',
                        'qualificacao_responsavel': 'UTINYINT',
                        'capital_social': 'DECIMAL(18,2)',
                        'porte_empresa': 'UTINYINT',
                        'ente_federativo_responsavel': 'VARCHAR'},
             quote = '"',
             escape = '"',
             nullstr = '',
             decimal_separator = ',',
             delim = ';')

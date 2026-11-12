-- csv_qualificacao.sql
SELECT
    *
FROM
    read_csv({{ csv_source('*.QUALSCSV') }},
             header = false,
             columns = {'codigo': 'USMALLINT',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

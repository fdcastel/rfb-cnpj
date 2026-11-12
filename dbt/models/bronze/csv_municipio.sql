-- csv_municipio.sql
SELECT
    *
FROM
    read_csv({{ csv_source('*.MUNICCSV') }},
             header = false,
             columns = {'codigo': 'USMALLINT',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

-- csv_pais.sql
SELECT
    *
FROM
    read_csv({{ csv_source('*.PAISCSV') }},
             header = false,
             columns = {'codigo': 'USMALLINT',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

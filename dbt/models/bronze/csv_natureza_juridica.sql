-- csv_natureza_juridica.sql
SELECT
    *
FROM
    read_csv({{ csv_source('*.NATJUCSV') }},
             header = false,
             columns = {'codigo': 'USMALLINT',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

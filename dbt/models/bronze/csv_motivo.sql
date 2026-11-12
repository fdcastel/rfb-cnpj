-- csv_motivo.sql
SELECT
    *
FROM
    read_csv({{ csv_source('*.MOTICSV') }},
             header = false,
             columns = {'codigo': 'USMALLINT',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

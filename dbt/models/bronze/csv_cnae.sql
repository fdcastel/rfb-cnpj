-- csv_cnae.sql
-- codigo is VARCHAR: codes have leading zeros ('0111301') and must match estabelecimento.cnae.
SELECT
    *
FROM
    read_csv({{ csv_source('*.CNAECSV') }},
             header = false,
             columns = {'codigo': 'VARCHAR',
                        'descricao': 'VARCHAR'},
             quote = '"',
             escape = '"',
             delim = ';')

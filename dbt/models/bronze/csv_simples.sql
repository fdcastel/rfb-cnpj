-- csv_simples.sql
SELECT
    cnpj_base,
    opcao_simples = 'S' AS opcao_simples,
    {{ rfb_date('data_opcao_simples') }} AS data_opcao_simples,
    {{ rfb_date('data_exclusao_simples') }} AS data_exclusao_simples,
    opcao_mei = 'S' AS opcao_mei,
    {{ rfb_date('data_opcao_mei') }} AS data_opcao_mei,
    {{ rfb_date('data_exclusao_mei') }} AS data_exclusao_mei
FROM
    read_csv({{ csv_source('*.SIMPLES.CSV.*') }},
             header = false,
             columns = {'cnpj_base': 'VARCHAR',
                        'opcao_simples': 'VARCHAR',
                        'data_opcao_simples': 'VARCHAR',
                        'data_exclusao_simples': 'VARCHAR',
                        'opcao_mei': 'VARCHAR',
                        'data_opcao_mei': 'VARCHAR',
                        'data_exclusao_mei': 'VARCHAR'},
             quote = '"',
             escape = '"',
             nullstr = '',
             delim = ';')

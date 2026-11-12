-- regime_tributacao_atual.sql
-- Most recent tax regime of each CNPJ (SCP entries excluded).
SELECT
    cnpj,
    MAX(ano) AS ano,
    REPLACE(REPLACE(arg_max(tributacao, ano), 'LUCRO ', ''), ' DO IRPJ', '') AS tributacao
FROM
    {{ ref('regime_tributacao') }}
WHERE
    cnpj_scp IS NULL
GROUP BY
    cnpj

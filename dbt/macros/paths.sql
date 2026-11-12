{#- Root data folder. Relative paths are resolved from the process working dir (./dbt).
    `uv run build` sets RFB_CNPJ_DATA_DIR to an absolute path. -#}
{% macro data_dir() -%}
    {{ return(env_var('RFB_CNPJ_DATA_DIR', '../data')) }}
{%- endmacro %}

{#- Quoted path (or glob) of a file in the csv layer. -#}
{% macro csv_source(pattern) -%}
    '{{ data_dir() }}/1-csv_sources/{{ pattern }}'
{%- endmacro %}

{#- RFB dates are 'YYYYMMDD'; '0', '00000000' and invalid values become NULL. -#}
{% macro rfb_date(column) -%}
    CAST(TRY_STRPTIME({{ column }}, '%Y%m%d') AS DATE)
{%- endmacro %}

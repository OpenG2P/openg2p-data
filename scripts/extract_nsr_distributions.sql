-- Rebuild distributions.json from a populated NSR registry.
--
-- generate_nsr_bulk_sample.py draws its attribute marginals from distributions.json
-- so that generated data has realistic shape (gender split, age structure,
-- livelihood mix, disability prevalence) instead of uniform noise. This query
-- produces that file's contents from a real registry.
--
-- Deliberately marginals only: counts per bucket, no place names and no
-- record-level data, so the output is safe to commit and carries no PII.
-- Geography is NOT extracted — the generator reads the deployment's own MDS
-- hierarchy instead, which is what keeps it country-agnostic.
--
-- Usage:
--   psql -h <host> -U postgres -d <registry_db> -tAc "$(cat extract_nsr_distributions.sql)" \
--     > distributions.json
--
-- Column names below follow the schema of the registry you are extracting FROM,
-- which may predate the current one (older deployments carry `educational_status`
-- where current ones carry `education_level`). Adjust to match your source.

select json_build_object(
  'gender', (
    select json_agg(row_to_json(x)) from (
      select gender, count(*)::bigint as c
      from g2p_register_individuals
      where gender is not null group by gender) x),

  'age_band', (
    select json_agg(row_to_json(x)) from (
      select case
               when estimated_age < 5  then 'Under 5'
               when estimated_age < 18 then 'School age (5-17)'
               when estimated_age < 65 then 'Working age (18-64)'
               else 'Elderly (65+)'
             end as age_band,
             count(*)::bigint as c
      from g2p_register_individuals
      where estimated_age is not null group by 1) x),

  'livelihood', (
    select json_agg(row_to_json(x)) from (
      select primary_livelihood, count(*)::bigint as c
      from g2p_register_individuals
      where primary_livelihood is not null group by 1) x),

  'education', (
    select json_agg(row_to_json(x)) from (
      select education_level as educational_status, count(*)::bigint as c
      from g2p_register_individuals
      where education_level is not null group by 1) x),

  'employment', (
    select json_agg(row_to_json(x)) from (
      select employment_status, count(*)::bigint as c
      from g2p_register_individuals
      where employment_status is not null group by 1) x),

  'disability', (
    select json_agg(row_to_json(x)) from (
      select disability_status, count(*)::bigint as c
      from g2p_register_individuals
      where disability_status is not null group by 1) x),

  'enrolled', (
    select json_agg(row_to_json(x)) from (
      select (p.link_internal_record_id is not null) as enrolled_in_program,
             count(*)::bigint as c
      from g2p_register_individuals i
      left join (select distinct link_internal_record_id
                 from g2p_register_individual_programs) p
             on p.link_internal_record_id = i.internal_record_id
      group by 1) x)
);

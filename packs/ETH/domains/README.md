# Domain lists

Code lists that vary by **domain as well as by country**. Crops are agricultural
*and* Ethiopian; a social registry install has no use for them.

Keeping them here rather than in `../codelists/` means one artifact per country
without every install loading every domain's lists — a Farmer Registry reads
`agriculture/`, NSR ignores it.

The file format is identical to `../codelists/`. Roles (`../../roles.json`) apply
here too if platform logic ever needs to reason about a domain value's meaning;
none do today.

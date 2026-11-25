"""HR / identity directory sync: who works in which department, team and cost centre.

Sources (each optional, enable with env vars):
- Workday            -> workday.py  (Report-as-a-Service custom report, JSON)
- Entra ID (Graph)   -> entra.py    (users with department / costCenter / employeeId)
- Any HRIS export    -> CSV upload  (POST /api/admin/directory/import): SAP SuccessFactors,
                        Oracle HCM, ADP, BambooHR ... anything that can export a CSV.

All sources produce PersonRecord objects; apply.apply_directory() upserts departments,
teams and users, then back-fills department/team on usage events that arrived before
the person was known.
"""

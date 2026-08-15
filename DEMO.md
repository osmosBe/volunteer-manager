# Five-to-ten-minute demo

Use fictional data only. With non-persistent DEV storage, run migrations and
`python -m scripts.seed_default_event` in the active revision before the demo.

1. Open `/` and select **St. Pölten PRIDE 2026**.
2. Filter shifts by work area, day or free capacity, then register a fictional
   `example.invalid` contact for two compatible shifts. Show the review summary,
   confirmation, e-mail verification state and edit link. For a live mail demo,
   use an authorized test mailbox and explicitly configured SMTP credentials.
3. Use the edit link to change one shift and cancel one assignment. Explain
   that only token hashes are stored and a changed e-mail address requires a new
   confirmation.
4. Open `/admin`, review staffing metrics and enter the event planning view.
5. Add a draft shift or change capacity. Show manual waitlist promotion and
   the purpose-limited CSV and print views.
6. Open `/admin/ehrenamtliche`, filter the list, inspect a fictional person and
   demonstrate manual assignment with explicit conflict override.
7. In `/admin/check-in`, check a confirmed assignment in with material and then
   check it out with material return.
8. Open `/admin/outbox` and show delivery/error status. Optionally show
   `/admin/einstellungen/smtp`; the password remains an environment secret.
   Verification mail is automatic only when SMTP is enabled; other messages
   require an explicit click.
9. Finish at `/healthz`, `/health/live` and `/health/ready`; verify the deployed
   version equals the expected `dev` commit SHA.

## Demo URLs

- Public landing page: `/`
- Registration: `/veranstaltungen/pride-2026/anmeldung`
- Admin dashboard: `/admin`
- Volunteers: `/admin/ehrenamtliche`
- Check-in: `/admin/check-in`
- Outbox preview: `/admin/outbox`
- Diagnostics: `/healthz`, `/health/live`, `/health/ready`, `/admin/db`
